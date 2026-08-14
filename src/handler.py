"""Lambda エントリポイント。EventBridge から5分間隔で起動される。

    取得 → 重複排除 → スコア化 → 集計 → S3(履歴・latest・キャッシュ)

各段は差し替え可能なモジュールに分離してある。特にスコア算出は
`src/scoring/` の実装を差し替えるだけでフェーズ2へ移行できる。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from . import aggregate, config, dedupe
from .models import Article, SentimentPoint, iso
from .scoring import get_scorer
from .sources import get_source
from .storage import S3Store

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _load_cache(store: S3Store, cfg: config.Config) -> dict[str, dict]:
    raw = store.get_json(cfg.key_cache(), default={}) or {}
    return raw.get("articles", {}) if isinstance(raw, dict) else {}


def _load_series(store: S3Store, cfg: config.Config, now: datetime) -> list[SentimentPoint]:
    """当日 + 前日の履歴から、集計ウィンドウ内の点を時系列順に返す。"""
    cutoff = now - timedelta(hours=cfg.window_hours)
    points: list[SentimentPoint] = []
    for day in ((now - timedelta(days=1)).date(), now.date()):
        payload = store.get_json(cfg.key_history(day.isoformat()), default=None)
        if not payload:
            continue
        for raw in payload.get("points", []):
            try:
                point = SentimentPoint.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                continue
            if point.t >= cutoff:
                points.append(point)
    points.sort(key=lambda p: p.t)
    return points


def _append_history(store: S3Store, cfg: config.Config, point: SentimentPoint) -> None:
    key = cfg.key_history(point.t.date().isoformat())
    payload = store.get_json(key, default=None) or {
        "schema_version": SCHEMA_VERSION,
        "index": cfg.index_id,
        "date": point.t.date().isoformat(),
        "points": [],
    }
    payload["points"].append(point.to_dict())
    # 履歴は追記のみ。長めにキャッシュさせる(過去分は変わらない)
    store.put_json(key, payload, max_age=300)


def _rollup_previous_day(store: S3Store, cfg: config.Config, now: datetime) -> None:
    """前日ぶんの履歴を1日1点に要約して `daily.json` に追記する。

    5分刻みの生の履歴は1日288点あり、1年で10万点を超える。長期の推移を見るのに
    その粒度は要らないので、日次に畳んでおく。こうしておけば
    ライフサイクルで生の履歴を消しても、長い目盛りの推移は残る。
    """
    day = (now - timedelta(days=1)).date().isoformat()
    payload = store.get_json(cfg.key_daily(), default=None) or {
        "schema_version": SCHEMA_VERSION,
        "index": cfg.index_id,
        "days": [],
    }
    if any(d.get("d") == day for d in payload["days"]):
        return  # すでに畳んである

    history = store.get_json(cfg.key_history(day), default=None)
    if not history or not history.get("points"):
        return  # その日の記録が無い(初日・停止していた日)

    points = sorted(history["points"], key=lambda p: p["t"])
    scores = [float(p["score"]) for p in points]
    counts = [int(p.get("n", 0)) for p in points]
    payload["days"].append(
        {
            "d": day,
            "open": round(scores[0], 4),
            "close": round(scores[-1], 4),
            "avg": round(sum(scores) / len(scores), 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
            "points": len(points),
            "articles_avg": round(sum(counts) / len(counts), 1) if counts else 0.0,
        }
    )
    payload["days"].sort(key=lambda d: d["d"])
    store.put_json(cfg.key_daily(), payload, max_age=3600)
    log.info("%s を日次に畳みました (%d点)", day, len(points))


def _article_view(article: Article, weight: float) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "url": article.url,
        "source": article.source,
        "published_at": iso(article.published_at),
        "score": round(float(article.score or 0.0), 3),
        "weight": round(weight, 4),
        "scored_by": article.scored_by,
        "scores": {k: round(v, 3) for k, v in article.scores.items()},
    }


def run(cfg: config.Config | None = None, *, now: datetime | None = None, store: S3Store | None = None) -> dict[str, Any]:
    """1回分の更新処理。テストから直接呼べるよう Lambda 引数と切り離してある。"""
    cfg = cfg or config.load()
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    store = store or S3Store(cfg.bucket)

    # 1. 取得 --------------------------------------------------------------
    source = get_source(cfg)
    fetched = source.fetch(since=now - timedelta(hours=cfg.window_hours), limit=cfg.fetch_limit)
    log.info("取得: %d件 (source=%s)", len(fetched), source.name)

    # 2. 重複排除 ----------------------------------------------------------
    cache = _load_cache(store, cfg)
    cached_articles = []
    for raw in cache.values():
        try:
            cached_articles.append(Article.from_dict(raw))
        except (KeyError, ValueError, TypeError):
            continue

    fresh, _ = dedupe.split_new(fetched, set(cache.keys()))
    kept_ids = {a.id for a in dedupe.drop_near_duplicates(cached_articles + fresh)}
    fresh = [a for a in fresh if a.id in kept_ids]
    log.info("新着: %d件 (キャッシュ %d件)", len(fresh), len(cache))

    # 3. スコア化(新着のみ = LLMコストの上限を決める最重要ポイント)---------
    scorer = get_scorer(cfg)
    scored = [a for a in scorer.score(fresh) if a.score is not None]
    log.info("スコア化: %d件 (scorer=%s)", len(scored), scorer.name)

    for article in scored:
        cache[article.id] = article.to_dict()
    cache = dedupe.prune_cache(cache, now, cfg.cache_retention_hours)
    store.put_json(
        cfg.key_cache(),
        {"schema_version": SCHEMA_VERSION, "updated_at": iso(now), "articles": cache},
        public=False,
    )

    # 4. 集計 --------------------------------------------------------------
    articles = []
    for raw in cache.values():
        try:
            articles.append(Article.from_dict(raw))
        except (KeyError, ValueError, TypeError):
            continue

    point, contributors = aggregate.compute(
        articles,
        now,
        window_hours=cfg.window_hours,
        half_life_hours=cfg.half_life_hours,
        confidence_scale=cfg.confidence_scale,
    )

    series = _load_series(store, cfg, now)
    if len(series) < 2:
        # 履歴が無い/欠けている場合は記事の発行時刻から再構築する
        log.info("履歴が不足しているため時系列を再構築します")
        series = aggregate.rebuild_series(
            articles,
            now,
            window_hours=cfg.window_hours,
            half_life_hours=cfg.half_life_hours,
            confidence_scale=cfg.confidence_scale,
        )
    else:
        series.append(point)

    _append_history(store, cfg, point)
    _rollup_previous_day(store, cfg, now)

    # 5. 公開データ --------------------------------------------------------
    previous = series[-2] if len(series) >= 2 else None
    payload = {
        "schema_version": SCHEMA_VERSION,
        "index": {"id": cfg.index_id, "name": cfg.index_name},
        "generated_at": iso(now),
        "next_update_at": iso(now + timedelta(seconds=cfg.update_interval_seconds)),
        "sentiment": {
            "score": round(point.score, 4),
            "label": aggregate.label(point.score),
            "confidence": round(point.confidence, 3),
            "article_count": point.n,
            "delta": round(point.score - previous.score, 4) if previous else None,
        },
        "series": [p.to_dict() for p in series],
        "articles": [_article_view(a, w) for a, w in contributors[: cfg.top_articles]],
        "meta": {
            "scorer": scorer.name,
            "source": source.name,
            "window_hours": cfg.window_hours,
            "half_life_hours": cfg.half_life_hours,
            "update_interval_seconds": cfg.update_interval_seconds,
            "fetched": len(fetched),
            "new_scored": len(scored),
            # 無料枠の日次上限が資料で確定できないので、提供者が返す残枠を毎回記録する。
            # 数日ぶんの latest.json を見れば、実際の上限と消費ペースが分かる。
            "api_requests": getattr(source, "requests", None),
            "api_quota": getattr(source, "quota", None) or None,
        },
    }
    store.put_json(cfg.key_latest(), payload, max_age=60)
    return payload


def lambda_handler(event: dict, context: Any) -> dict[str, Any]:  # noqa: ARG001
    payload = run()
    return {
        "statusCode": 200,
        "score": payload["sentiment"]["score"],
        "articles": payload["sentiment"]["article_count"],
    }
