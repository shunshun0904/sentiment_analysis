"""環境変数による設定。Lambdaの環境変数 / SSM Parameter Store から注入する想定。

APIキーだけは SSM SecureString を推奨(`APITUBE_KEY_SSM_PARAM` を指定すると起動時に解決)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@lru_cache(maxsize=8)
def _ssm(param_name: str) -> str:
    import boto3  # Lambda ランタイムに同梱

    resp = boto3.client("ssm").get_parameter(Name=param_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


@dataclass(frozen=True)
class Config:
    # --- S3 ---
    bucket: str = field(default_factory=lambda: os.environ.get("DATA_BUCKET", ""))
    prefix: str = field(default_factory=lambda: os.environ.get("DATA_PREFIX", ""))

    # --- 対象指数 ---
    index_id: str = field(default_factory=lambda: os.environ.get("INDEX_ID", "SPX"))
    index_name: str = field(default_factory=lambda: os.environ.get("INDEX_NAME", "S&P 500"))
    #: ニュース検索クエリ(データソースに渡す)
    query: str = field(
        default_factory=lambda: os.environ.get(
            "NEWS_QUERY", '"S&P 500" OR "Wall Street" OR "US stocks" OR "Federal Reserve"'
        )
    )
    language: str = field(default_factory=lambda: os.environ.get("NEWS_LANGUAGE", "en"))

    # --- データソース ---
    source: str = field(default_factory=lambda: os.environ.get("NEWS_SOURCE", "apitube"))
    apitube_key: str = field(default_factory=lambda: os.environ.get("APITUBE_KEY", ""))
    apitube_key_ssm_param: str = field(
        default_factory=lambda: os.environ.get("APITUBE_KEY_SSM_PARAM", "")
    )
    #: 1回の取得で読む最大記事数(無料枠のリクエスト数を意識して控えめに)
    fetch_limit: int = field(default_factory=lambda: _i("FETCH_LIMIT", 100))

    # --- スコア算出 ---
    #: "passthrough"(API付属スコア) / "claude"(Haiku 4.5) / "dual"(並走保存)
    scorer: str = field(default_factory=lambda: os.environ.get("SCORER", "passthrough"))
    claude_model: str = field(
        default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
    )
    anthropic_key_ssm_param: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_KEY_SSM_PARAM", "")
    )
    #: 1回のLLM呼び出しに詰め込む記事数
    claude_batch_size: int = field(default_factory=lambda: _i("CLAUDE_BATCH_SIZE", 20))
    #: 1実行あたりのLLMスコア化上限(暴走時のコスト上限)
    claude_max_articles: int = field(default_factory=lambda: _i("CLAUDE_MAX_ARTICLES", 120))

    # --- 集計 ---
    #: 集計対象とする記事の鮮度(時間)
    window_hours: float = field(default_factory=lambda: _f("WINDOW_HOURS", 24.0))
    #: 時間減衰の半減期(時間)
    half_life_hours: float = field(default_factory=lambda: _f("HALF_LIFE_HOURS", 6.0))
    #: 信頼度が1に近づく重み合計の目安
    confidence_scale: float = field(default_factory=lambda: _f("CONFIDENCE_SCALE", 8.0))
    #: 記事キャッシュの保持時間。window より長くして再取得を防ぐ
    cache_retention_hours: float = field(default_factory=lambda: _f("CACHE_RETENTION_HOURS", 48.0))
    #: latest.json に載せる根拠記事の件数
    top_articles: int = field(default_factory=lambda: _i("TOP_ARTICLES", 12))
    #: 更新間隔(秒)。SPAのポーリング間隔として latest.json に載せる
    update_interval_seconds: int = field(
        default_factory=lambda: _i("UPDATE_INTERVAL_SECONDS", 300)
    )

    def resolve_apitube_key(self) -> str:
        if self.apitube_key:
            return self.apitube_key
        if self.apitube_key_ssm_param:
            return _ssm(self.apitube_key_ssm_param)
        raise RuntimeError("APITUBE_KEY もしくは APITUBE_KEY_SSM_PARAM を設定してください")

    def resolve_anthropic_key(self) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            return key
        if self.anthropic_key_ssm_param:
            return _ssm(self.anthropic_key_ssm_param)
        raise RuntimeError(
            "ANTHROPIC_API_KEY もしくは ANTHROPIC_KEY_SSM_PARAM を設定してください"
        )

    # --- S3 キー ---
    def key_latest(self) -> str:
        return f"{self.prefix}data/{self.index_id}/latest.json"

    def key_history(self, date_str: str) -> str:
        return f"{self.prefix}data/{self.index_id}/history/{date_str}.json"

    def key_daily(self) -> str:
        """日次に畳んだ長期の推移。生の履歴を消しても、ここは残す。"""
        return f"{self.prefix}data/{self.index_id}/daily.json"

    def key_cache(self) -> str:
        return f"{self.prefix}state/{self.index_id}/articles.json"


def load() -> Config:
    return Config()
