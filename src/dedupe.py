"""重複排除。

2種類の重複を落とす:
1. 既にスコア化済みの記事(S3キャッシュにID有り)→ LLM呼び出しを避けるための必須処理
2. 同一ニュースの配信違い(タイトルがほぼ同じ)→ 同じ話題が重みを二重計上するのを防ぐ
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import Article

_NOISE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def title_key(title: str) -> str:
    """記号・大小文字・空白差を無視したタイトルキー。"""
    normalized = _SPACE.sub(" ", _NOISE.sub(" ", title.lower())).strip()
    return normalized[:120]


def split_new(articles: list[Article], known_ids: set[str]) -> tuple[list[Article], list[Article]]:
    """(新着, 既知) に分ける。同一実行内のID重複も新着側で1件に畳む。"""
    fresh: list[Article] = []
    seen: set[str] = set()
    known: list[Article] = []
    for article in articles:
        if article.id in known_ids:
            known.append(article)
            continue
        if article.id in seen:
            continue
        seen.add(article.id)
        fresh.append(article)
    return fresh, known


def drop_near_duplicates(articles: list[Article], window: timedelta = timedelta(hours=6)) -> list[Article]:
    """タイトルがほぼ同じ記事を、指定時間窓の中で最も古い1件に代表させる。

    先に発行された記事を残すのは、市場の反応時刻に近いのが初報だから。
    """
    ordered = sorted(articles, key=lambda a: a.published_at)
    kept: list[Article] = []
    last_seen: dict[str, datetime] = {}
    for article in ordered:
        key = title_key(article.title)
        previous = last_seen.get(key)
        if previous is not None and article.published_at - previous <= window:
            continue
        last_seen[key] = article.published_at
        kept.append(article)
    return kept


def prune_cache(cache: dict[str, dict], now: datetime, retention_hours: float) -> dict[str, dict]:
    """保持期間を過ぎた記事をキャッシュから落とす(S3オブジェクトの肥大化防止)。"""
    cutoff = now - timedelta(hours=retention_hours)
    result: dict[str, dict] = {}
    for article_id, raw in cache.items():
        try:
            published_at = Article.from_dict(raw).published_at
        except (KeyError, ValueError, TypeError):
            continue
        if published_at >= cutoff:
            result[article_id] = raw
    return result
