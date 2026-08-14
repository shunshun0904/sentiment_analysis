"""ニュース取得層。`get_source(name)` でデータソースを差し替える。"""

from __future__ import annotations

from ..config import Config
from .base import NewsSource
from .apitube import APITubeSource


def get_source(cfg: Config) -> NewsSource:
    name = (cfg.source or "").lower()
    if name == "apitube":
        return APITubeSource(cfg)
    raise ValueError(f"未対応のデータソース: {cfg.source}")


__all__ = ["NewsSource", "APITubeSource", "get_source"]
