"""フェーズ2: Claude Haiku 4.5 でスコア化する。

コスト前提:
- 呼ばれるのは重複排除後の**新着記事のみ**(handler 側で保証)。
- 1回のリクエストに `claude_batch_size` 件を詰め、リクエスト数を抑える。
- 1実行あたりの上限 `claude_max_articles` で暴走時のコストを止める。

日本語ニュース(日経向け)もこのスコアラーがそのまま扱える。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..models import Article
from .base import Scorer, clamp

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは金融市場ニュースのセンチメント評価器です。
各記事について、その記事が示す株式市場全体(指数)への方向性を評価してください。

score: -1.0(強い弱気)〜 0.0(中立)〜 +1.0(強い強気)
  個別企業の好悪ではなく、指数への影響で判断すること。
  例) 大手ハイテクの好決算 → 正、想定超のインフレ指標 → 負、人事異動の報道 → 0付近

relevance: 0.0(指数と無関係)〜 1.0(指数の値動きを直接説明する)
  市場全体・金融政策・マクロ指標の記事は高く、業界コラムや個社の小ネタは低く。

見出しと要約だけで判断し、推測で断定しないこと。判断材料が乏しければ score は 0 付近、
relevance を低くしてください。"""

# 数値制約(minimum/maximum)は structured outputs 非対応のためクライアント側で丸める
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer", "description": "入力記事の番号"},
                    "score": {"type": "number"},
                    "relevance": {"type": "number"},
                },
                "required": ["i", "score", "relevance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def build_user_message(batch: list[Article]) -> str:
    lines = []
    for i, article in enumerate(batch):
        summary = article.summary[:300]
        lines.append(f"[{i}] {article.title}\n    {summary}".rstrip())
    return "以下の記事を評価してください。\n\n" + "\n\n".join(lines)


class ClaudeScorer(Scorer):
    name = "claude"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import anthropic  # フェーズ2でのみ必要な依存

            self._client = anthropic.Anthropic(api_key=self.cfg.resolve_anthropic_key())
        return self._client

    def score(self, articles: list[Article]) -> list[Article]:
        targets = [a for a in articles if a.score is None or self.cfg.scorer == "dual"]
        targets = targets[: self.cfg.claude_max_articles]
        if len(targets) < len([a for a in articles if a.score is None]):
            log.warning("claude_max_articles に達したため一部の記事を未スコアのまま残します")

        size = max(1, self.cfg.claude_batch_size)
        for start in range(0, len(targets), size):
            batch = targets[start : start + size]
            try:
                self._score_batch(batch)
            except Exception:  # 1バッチの失敗で実行全体を落とさない
                log.exception("Claude によるスコア化に失敗しました (batch=%d)", start // size)
        return articles

    def _score_batch(self, batch: list[Article]) -> None:
        import anthropic

        client = self._client_lazy()
        try:
            response = client.messages.create(
                model=self.cfg.claude_model,
                # 構造化された短い出力のみを返させるため、意図的に小さめ
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
                messages=[{"role": "user", "content": build_user_message(batch)}],
            )
        except anthropic.RateLimitError:
            log.warning("Anthropic API がレート制限中。このバッチは次回実行に持ち越します")
            return
        except anthropic.APIStatusError as e:
            log.error("Anthropic API エラー (%s): %s", e.status_code, e.message)
            return
        except anthropic.APIConnectionError:
            log.error("Anthropic API に接続できませんでした")
            return

        if response.stop_reason == "refusal":
            log.warning("Claude がスコア化を拒否しました: %s", response.stop_details)
            return

        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text:
            return
        for entry in json.loads(text).get("scores", []):
            index = int(entry.get("i", -1))
            if not 0 <= index < len(batch):
                continue
            article = batch[index]
            value = clamp(float(entry["score"]))
            article.score = value
            article.relevance = clamp(float(entry.get("relevance", 1.0)), 0.0, 1.0)
            article.scored_by = self.name
            article.scores[self.name] = value
