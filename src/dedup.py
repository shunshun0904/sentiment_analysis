"""重複排除・ソースフィルタ・レコード削減。"""
import hashlib
import re
from datetime import datetime, timedelta

import config
from fetch import parse_published

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def title_key(title: str) -> str:
    """同一記事が複数ドメインから配信されるケースを吸収する。

    実測例（2026-08-14）:
      stockstotrade.com と timothysykes.com が同一タイトル・同一時刻で配信。
      URL だけで重複排除すると同じ記事を2回計上してしまう。
    """
    t = _PUNCT.sub("", title.lower())
    return _h(_WS.sub(" ", t).strip())


def prune_seen(seen: dict, now: datetime) -> dict:
    cutoff = (now - timedelta(hours=config.SEEN_RETENTION_HOURS)).strftime("%Y%m%dT%H%M")
    return {
        bucket: {k: v for k, v in entries.items() if v >= cutoff}
        for bucket, entries in seen.items()
    }


def trim(article: dict) -> dict:
    """S3 保存用に必要フィールドのみ残す。

    APIレスポンスは206件で約99,000トークン相当。
    summary / banner_image / authors を落とすと概ね1/5になる。
    """
    rel = 0.0
    for t in article.get("topics", []):
        if t.get("topic") in config.RELEVANCE_TOPICS:
            try:
                rel = max(rel, float(t["relevance_score"]))
            except (KeyError, TypeError, ValueError):
                continue

    # 言及銘柄は記事一覧パネル用。スコアには使わない。
    pairs = sorted(
        article.get("ticker_sentiment", []),
        key=lambda ts: float(ts.get("relevance_score", 0) or 0),
        reverse=True,
    )[: config.TICKERS_PER_ARTICLE]

    return {
        "url": article["url"],
        "t": article["time_published"],
        "source": article.get("source_domain") or article.get("source", ""),
        "title": article.get("title", ""),
        "overall": float(article.get("overall_sentiment_score", 0.0)),
        "rel": round(rel, 4),
        "tickers": [ts["ticker"] for ts in pairs],
    }


def filter_articles(feed: list[dict], seen: dict, now: datetime) -> tuple[list[dict], dict]:
    """(採用記事, ソース出現頻度) を返す。seen は破壊的に更新する。"""
    kept: list[dict] = []
    source_counts: dict[str, int] = {}
    cutoff = now - timedelta(hours=config.DECAY_WINDOW_HOURS)

    seen.setdefault("urls", {})
    seen.setdefault("titles", {})

    for art in feed:
        rec = trim(art)
        source_counts[rec["source"]] = source_counts.get(rec["source"], 0) + 1

        try:
            published = parse_published(rec["t"])
        except ValueError:
            continue
        if published < cutoff:
            continue
        if rec["rel"] < config.MIN_TOPIC_RELEVANCE:
            continue

        uk, tk = _h(rec["url"]), title_key(rec["title"])
        if uk in seen["urls"] or tk in seen["titles"]:
            continue

        seen["urls"][uk] = rec["t"][:13]
        seen["titles"][tk] = rec["t"][:13]

        # 観測モード（ホワイトリストが空）では全件通す
        if config.SOURCE_WHITELIST and rec["source"] not in config.SOURCE_WHITELIST:
            continue

        kept.append(rec)

    return kept, source_counts
