"""スコアラーのファクトリ。環境変数 `SCORER` で切り替える。

- `passthrough` : フェーズ1。API付属スコアをそのまま使う(¥0)
- `claude`      : フェーズ2。Claude Haiku 4.5 でスコア化(新着のみ)
- `dual`        : 両方式を並走保存し、`article.scores` に両者を残して比較検証する
                  (代表値 `article.score` は Claude 側)
"""

from __future__ import annotations

from ..config import Config
from ..models import Article
from .base import Scorer
from .claude import ClaudeScorer
from .passthrough import PassthroughScorer


class DualScorer(Scorer):
    name = "dual"

    def __init__(self, cfg: Config) -> None:
        self.passthrough = PassthroughScorer()
        self.claude = ClaudeScorer(cfg)

    def score(self, articles: list[Article]) -> list[Article]:
        self.passthrough.score(articles)
        return self.claude.score(articles)


def get_scorer(cfg: Config) -> Scorer:
    name = (cfg.scorer or "").lower()
    if name == "passthrough":
        return PassthroughScorer()
    if name == "claude":
        return ClaudeScorer(cfg)
    if name == "dual":
        return DualScorer(cfg)
    raise ValueError(f"未対応のスコアラー: {cfg.scorer}")


__all__ = ["Scorer", "PassthroughScorer", "ClaudeScorer", "DualScorer", "get_scorer"]
