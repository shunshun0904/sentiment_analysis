"""APITube ニュース取得クライアント。

フィールド名は APITube の公開ドキュメントに載っているレスポンス例に合わせてある:

    {"results": [{"title": ..., "description": ..., "href": ...,
                  "published_at": "2026-05-03T08:14:22.000Z",
                  "language": {"code": "en"}, "source": {"domain": "reuters.com"},
                  "sentiment": {"overall": {"score": 0.41, "polarity": "positive"}}}],
     "has_next_pages": true}

⚠️ ただし**まだ本物のレスポンスでは検証していない**(鍵が要るため)。
鍵を取ったら `tools/probe_apitube.py` を走らせること。実物と食い違っていれば
そこで差分が出るので、`_FIELD_PATHS` を直す。予備の候補パスを1〜2本ずつ残して
あるのはそのため。

認証はヘッダ `X-API-Key`。クエリ文字列に鍵を置くとアクセスログに残るので、
ヘッダのほうが安全でもある。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from ..models import Article, _parse_dt, canonical_id, iso
from .base import NewsSource, http_get_json

ENDPOINT = "https://api.apitube.io/v1/news/everything"

# 先頭がドキュメント記載の形。後続は保険(実測後に1本へ絞ってよい)
_FIELD_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "id": (("id",), ("article_id",), ("uuid",)),
    "title": (("title",), ("headline",)),
    "url": (("href",), ("url",), ("link",)),
    "summary": (("description",), ("summary",)),
    "published_at": (("published_at",), ("publishedAt",)),
    "source": (("source", "domain"), ("source", "name"), ("source",)),
    "language": (("language", "code"), ("language",)),
    "score": (("sentiment", "overall", "score"), ("sentiment", "score")),
    "polarity": (("sentiment", "overall", "polarity"), ("sentiment", "polarity")),
}

#: 数値スコアが無く方向性だけのときの代替値
_POLARITY_SCORE = {"positive": 0.5, "neutral": 0.0, "negative": -0.5}


def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def _pick(item: dict[str, Any], field: str) -> Any:
    """その項目が取れた最初のパスの値を返す。辞書のままなら採らない。"""
    for path in _FIELD_PATHS.get(field, ()):
        value = _dig(item, path)
        if isinstance(value, dict):
            continue  # language や source を丸ごと拾ってしまわないように
        if value not in (None, "", [], {}):
            return value
    return None


def results_of(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
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
        score = max(-1.0, min(1.0, float(score)))

    try:
        published_at = _parse_dt(published)
    except (ValueError, TypeError):
        return None

    return Article(
        id=article_id,
        title=str(title).strip(),
        url=url,
        source=str(_pick(item, "source") or "unknown"),
        published_at=published_at,
        summary=str(_pick(item, "summary") or "").strip()[:600],
        language=str(_pick(item, "language") or "en"),
        score=score,
        scored_by="apitube" if score is not None else "",
    )


class APITubeSource(NewsSource):
    name = "apitube"

    #: 無料枠は1リクエストの取得件数に上限がある。足りなければページを繰る
    PAGE_SIZE = 100

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def fetch(self, since: datetime, limit: int) -> list[Article]:
        key = self.cfg.resolve_apitube_key()
        articles: list[Article] = []
        seen: set[str] = set()

        page = 1
        while len(articles) < limit and page <= 5:  # 暴走防止に5ページで打ち止め
            payload = http_get_json(
                ENDPOINT,
                {
                    "q": self.cfg.query,
                    "language.code": self.cfg.language,
                    "published_at.start": iso(since),
                    "sort.by": "published_at",
                    "sort.order": "desc",
                    "per_page": min(limit, self.PAGE_SIZE),
                    "page": page,
                },
                headers={"X-API-Key": key},
            )
            batch = list(results_of(payload))
            if not batch:
                break
            for item in batch:
                article = to_article(item)
                if article is None or article.id in seen:
                    continue
                seen.add(article.id)
                articles.append(article)
            if not payload.get("has_next_pages"):
                break
            page += 1

        return articles[:limit]
