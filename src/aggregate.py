"""指数センチメントの集計。

定義:
    sentiment(t) = Σ(score_i × w_i) / Σ(w_i)
    w_i = 0.5^(age_i / half_life) × relevance_i

時間減衰により、直近のニュースほど現在値への寄与が大きい。
記事には発行時刻が付くため、S3の履歴が欠けても直近24時間分を取り直せば
時系列は再構築できる(タブ閉鎖・障害からの復旧設計)。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .models import Article, SentimentPoint


def decay_weight(article: Article, now: datetime, half_life_hours: float) -> float:
    """時間減衰 × 関連度。未来日付の記事は age=0 として扱う。"""
    age_hours = max(0.0, (now - article.published_at).total_seconds() / 3600.0)
    if half_life_hours <= 0:
        return max(0.0, article.relevance)
    return (0.5 ** (age_hours / half_life_hours)) * max(0.0, article.relevance)


#: 配信側の時刻ズレを吸収する許容幅
FUTURE_TOLERANCE = timedelta(minutes=5)


def in_window(articles: list[Article], now: datetime, window_hours: float) -> list[Article]:
    """スコア済みかつ [now-window, now] に発行された記事だけを返す。

    未来側を切るのは時系列の再構築のため。過去の時点 t を計算するときに
    t より後の記事が混ざると、その時点では存在しなかった情報が漏れる。
    """
    cutoff = now - timedelta(hours=window_hours)
    horizon = now + FUTURE_TOLERANCE
    return [
        a for a in articles if a.score is not None and cutoff <= a.published_at <= horizon
    ]


def confidence(weight_sum: float, scale: float) -> float:
    """重みの合計を 0〜1 に写す。記事が少ない/古いほど 0 に近づく。"""
    if scale <= 0:
        return 1.0 if weight_sum > 0 else 0.0
    return 1.0 - math.exp(-weight_sum / scale)


def compute(
    articles: list[Article],
    now: datetime,
    *,
    window_hours: float,
    half_life_hours: float,
    confidence_scale: float,
) -> tuple[SentimentPoint, list[tuple[Article, float]]]:
    """現在のセンチメントと、寄与度つき記事リスト(降順)を返す。"""
    target = in_window(articles, now, window_hours)
    weighted = [(a, decay_weight(a, now, half_life_hours)) for a in target]
    weighted = [(a, w) for a, w in weighted if w > 0]

    weight_sum = sum(w for _, w in weighted)
    score = sum(float(a.score) * w for a, w in weighted) / weight_sum if weight_sum > 0 else 0.0

    point = SentimentPoint(
        t=now,
        score=score,
        n=len(weighted),
        weight=weight_sum,
        confidence=confidence(weight_sum, confidence_scale),
    )
    # 寄与の絶対値が大きい順 = 「なぜこの値になったか」の説明力が高い順
    contributors = sorted(weighted, key=lambda pair: abs(float(pair[0].score) * pair[1]), reverse=True)
    return point, contributors


def rebuild_series(
    articles: list[Article],
    now: datetime,
    *,
    window_hours: float,
    half_life_hours: float,
    confidence_scale: float,
    step_minutes: int = 5,
) -> list[SentimentPoint]:
    """記事の発行時刻から時系列を再構築する(履歴欠損時のフォールバック)。"""
    points: list[SentimentPoint] = []
    step = timedelta(minutes=step_minutes)
    steps = int((window_hours * 60) // step_minutes)
    for i in range(steps, -1, -1):
        t = now - step * i
        point, _ = compute(
            articles,
            t,
            window_hours=window_hours,
            half_life_hours=half_life_hours,
            confidence_scale=confidence_scale,
        )
        points.append(point)
    return points


LABELS = (
    (-0.5, "弱気"),
    (-0.15, "やや弱気"),
    (0.15, "中立"),
    (0.5, "やや強気"),
)


def label(score: float) -> str:
    for threshold, name in LABELS:
        if score < threshold:
            return name
    return "強気"
