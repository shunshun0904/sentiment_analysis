"""S3 の読み書き。"""
import json
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import config

_s3 = boto3.client("s3")


def get_json(key: str, default=None):
    try:
        obj = _s3.get_object(Bucket=config.S3_BUCKET, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return default
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def put_json(key: str, data) -> None:
    _s3.put_object(
        Bucket=config.S3_BUCKET,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
        CacheControl="max-age=60",
    )


def append_jsonl(key: str, records: list[dict]) -> None:
    """S3 に追記APIは無いので read-modify-write。

    毎時1回・単一ライターなので競合しない。並列実行する設計に変えるなら
    ここは日付分割か DynamoDB に置き換えること。
    """
    if not records:
        return
    try:
        obj = _s3.get_object(Bucket=config.S3_BUCKET, Key=key)
        existing = obj["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
            raise
        existing = ""

    lines = [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records]
    body = existing + ("" if existing.endswith("\n") or not existing else "\n")
    body += "\n".join(lines) + "\n"

    _s3.put_object(
        Bucket=config.S3_BUCKET, Key=key,
        Body=body.encode("utf-8"), ContentType="application/x-ndjson",
    )


def read_jsonl(key: str) -> list[dict]:
    try:
        obj = _s3.get_object(Bucket=config.S3_BUCKET, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise
    out = []
    for line in obj["Body"].read().decode("utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def get_api_key() -> str:
    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=config.AV_API_KEY_SSM_PATH, WithDecryption=True)
    return resp["Parameter"]["Value"]


def window_articles(now: datetime) -> list[dict]:
    """減衰ウィンドウ（24時間）に入る記事を、当日＋前日の監査ログから集める。

    SPA が5分刻みの系列を再構成するのに要る。今回の実行で採った記事だけでは
    直近1時間ぶんしか無く、24時間の曲線は引けない。
    """
    cutoff = (now - timedelta(hours=config.DECAY_WINDOW_HOURS)).strftime("%Y%m%dT%H%M%S")
    out: list[dict] = []
    for day in ((now - timedelta(days=1)).date(), now.date()):
        key = f"{config.PREFIX_ARTICLES}{day.isoformat()}.jsonl"
        out.extend(r for r in read_jsonl(key) if r.get("t", "") >= cutoff)
    out.sort(key=lambda r: r["t"])
    return out


def build_public(now: datetime, articles: list[dict]) -> None:
    """SPA が取得する latest.json を生成する。

    `window` は5分刻みの再構成用。`top_articles`（|score|上位）は偏った標本なので、
    そちらで再構成すると値が歪む ── 別物として両方載せる。
    """
    series = read_jsonl(config.KEY_SERIES)
    cutoff = (now - timedelta(hours=config.PUBLIC_SERIES_HOURS)).strftime("%Y%m%dT%H%M")
    series = [r for r in series if r["t"] >= cutoff]

    latest = series[-1] if series else {}
    top = sorted(articles, key=lambda a: abs(a["overall"]), reverse=True)
    top = top[: config.PUBLIC_TOP_ARTICLES]

    window = window_articles(now)[-config.PUBLIC_WINDOW_ARTICLES:]

    put_json(config.KEY_LATEST, {
        "schema_version": 2,
        "updated_at": now.strftime("%Y%m%dT%H%M"),
        "next_update_at": (
            now + timedelta(seconds=config.UPDATE_INTERVAL_SECONDS)
        ).strftime("%Y%m%dT%H%M"),
        "current": latest.get("sentiment"),
        "label": latest.get("label"),
        "raw_mean": latest.get("raw_mean"),
        "n_articles": latest.get("n_articles"),
        "top_tickers": latest.get("top_tickers", []),
        "series": [
            {"t": r["t"], "v": r["sentiment"], "u": r["raw_mean"]}
            for r in series
        ],
        # 再構成のパラメータ。SPA 側に定数を二重持ちさせない
        "params": {
            "half_life_hours": config.HALF_LIFE_HOURS,
            "window_hours": config.DECAY_WINDOW_HOURS,
            "step_min": config.DISPLAY_STEP_MIN,
            "use_relevance": config.USE_TOPIC_RELEVANCE,
            "update_interval_seconds": config.UPDATE_INTERVAL_SECONDS,
        },
        # 5分刻み再構成用。t=時刻, s=スコア, r=relevance のみに削る
        "window": [
            {"t": a["t"], "s": round(float(a["overall"]), 4), "r": round(float(a.get("rel", 1.0)), 3)}
            for a in window
        ],
        "top_articles": [
            {"title": a["title"], "source": a["source"], "url": a["url"],
             "score": a["overall"], "t": a["t"]}
            for a in top
        ],
    })


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)
