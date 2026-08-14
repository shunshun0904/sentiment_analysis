"""APITube ニュース取得クライアント。

⚠️ レスポンスのフィールド名は実測で確定させること(tools/probe_apitube.py)。
本実装は「よくある形」を複数パス試すディフェンシブなマッピングにしてあり、
実測後に `_FIELD_PATHS` を確定値だけに絞れば読みやすくなる。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from ..models import Article, canonical_id, iso, _parse_dt
from .base import NewsSource, http_get_json

ENDPOINT = "https://api.apitube.io/v1/news/everything"

# 実測で1本に絞るまでは候補を順に試す
_FIELD_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "id": (("id",), ("article_id",), ("uuid",)),
    "title": (("title",), ("headline",)),
    "url": (("href",), ("url",), ("link",)),
    "summary": (("description",), ("summary",), ("body",)),
    "published_at": (("published_at",), ("publishedAt",), ("date",)),
    "source": (("source", "domain"), ("source", "name"), ("source",), ("domain",)),
    "language": (("language",), ("lang",)),
    "score": (
        ("sentiment", "overall", "score"),
        ("sentiment", "score"),
        ("sentiment_score",),
    ),
    "polarity": (
        ("sentiment", "overall", "polarity"),
        ("sentiment", "polarity"),
    ),
}

#: 数値スコアが無く方向性のみの場合の代替値
_POLARITY_SCORE = {"positive": 0.5, "neutral": 0.0, "negative": -0.5}


def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def _pick(item: dict[str, Any], field: str) -> Any:
    for path in _FIELD_PATHS.get(field, ()):
        value = _dig(item, path)
        if value not in (None, "", [], {}):
            return value
    return None


def _results(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for key in ("results", "data", "articles"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def to_article(item: dict[str, Any]) -> Article | None:
    """APIの1件を Article に正規化する。必須項目が欠けていれば None。"""
    title = _pick(item, "title")
    published = _pick(item, "published_at")
    if not title or not published:
        return None

    url = str(_pick(item, "url") or "")
    raw_id = _pick(item, "id")
    article_id = str(raw_id) if raw_id else canonical_id(url, str(title))

    score = _pick(item, "score")
    if score is None:
        polarity = str(_pick(item, "polarity") or "").lower()
        score = _POLARITY_SCORE.get(polarity)
    if score is not None:
        # APIによって -1..1 / 0..1 / 0..100 と幅があるため素朴に丸める
        score = max(-1.0, min(1.0, float(score)))

    source = _pick(item, "source")
    if isinstance(source, dict):
        source = source.get("domain") or source.get("name") or ""

    try:
        published_at = _parse_dt(published)
    except (ValueError, TypeError):
        return None

    return Article(
        id=article_id,
        title=str(title).strip(),
        url=url,
        source=str(source or "unknown"),
        published_at=published_at,
        summary=str(_pick(item, "summary") or "").strip()[:600],
        language=str(_pick(item, "language") or "en"),
        score=score,
        scored_by="apitube" if score is not None else "",
    )


class APITubeSource(NewsSource):
    name = "apitube"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def fetch(self, since: datetime, limit: int) -> list[Article]:
        payload = http_get_json(
            ENDPOINT,
            {
                "api_key": self.cfg.resolve_apitube_key(),
                "q": self.cfg.query,
                "language.code": self.cfg.language,
                "published_at.start": iso(since),
                "sort.by": "published_at",
                "sort.order": "desc",
                "per_page": min(limit, 100),
            },
        )
        articles: list[Article] = []
        for item in _results(payload):
            article = to_article(item)
            if article is not None:
                articles.append(article)
        return articles[:limit]
