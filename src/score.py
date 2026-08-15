"""市場センチメントの集計。

引き継ぎ資料の「スコア算出部は関数1つに分離し差し替え可能に」という
方針に従い、集計ロジックはこのモジュールに閉じている。
将来 LLM ベースのスコアに差し替える場合も、入出力の形を保てば
他モジュールに変更は要らない。
"""
from datetime import datetime

import config
from fetch import parse_published

LABELS = [
    (-0.35, "Bearish"),
    (-0.15, "Somewhat-Bearish"),
    (0.15, "Neutral"),
    (0.35, "Somewhat-Bullish"),
]


def label_of(x: float) -> str:
    """Alpha Vantage のラベル定義に合わせる。

    注意: -0.35 / +0.35 はラベルの閾値であって、スコアの上下限ではない。
    実測で 0.4341 の記事を確認している（引き継ぎ資料の
    「-0.35〜+0.35」という記載は誤り）。
    """
    for hi, name in LABELS:
        if x < hi:
            return name
    return "Bullish"


def decay(published: datetime, now: datetime) -> float:
    hours = max((now - published).total_seconds() / 3600.0, 0.0)
    return 0.5 ** (hours / config.HALF_LIFE_HOURS)


def aggregate(articles: list[dict], now: datetime) -> dict:
    """記事スコアの時間減衰加重平均。

    weight_j = decay_j * relevance_j
    sentiment = Σ weight_j * score_j / Σ weight_j

    raw_mean（単純平均）を併記する。両者が乖離したときに
    減衰・relevance の設定を疑えるようにするための対照系列。
    """
    num = den = 0.0
    raw_sum = 0.0
    n = 0
    ticker_counts: dict[str, int] = {}

    for art in articles:
        try:
            published = parse_published(art["t"])
        except (KeyError, ValueError):
            continue

        rel = art.get("rel", 1.0) if config.USE_TOPIC_RELEVANCE else 1.0
        w = decay(published, now) * rel
        if w <= 0:
            continue

        num += w * art["overall"]
        den += w
        raw_sum += art["overall"]
        n += 1

        for t in art.get("tickers", []):
            ticker_counts[t] = ticker_counts.get(t, 0) + 1

    sentiment = round(num / den, 4) if den > 0 else None
    top = sorted(ticker_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    return {
        "t": now.strftime("%Y%m%dT%H%M"),
        "sentiment": sentiment,
        "label": label_of(sentiment) if sentiment is not None else None,
        "raw_mean": round(raw_sum / n, 4) if n else None,
        "n_articles": n,
        "top_tickers": [{"s": s, "n": c} for s, c in top],
    }
