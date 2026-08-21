"""保存の読み書き。

置き場そのもの（S3 / ローカルファイル）は backend_* に閉じてあり、
ここから下はどちらでも同じコードが動く。JSON と JSONL の組み立て、
減衰ウィンドウの読み直し、latest.json の生成がこのファイルの仕事。
"""
import json
from datetime import datetime, timedelta, timezone

import config

if config.STORE_BACKEND == "s3":
    import backend_s3 as _be
else:
    import backend_fs as _be


def get_json(key: str, default=None):
    raw = _be.read_bytes(key)
    if raw is None:
        return default
    return json.loads(raw.decode("utf-8"))


def put_json(key: str, data) -> None:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _be.write_bytes(key, body, "application/json")


def append_jsonl(key: str, records: list[dict]) -> None:
    """追記APIが無い置き場（S3）に合わせて read-modify-write で統一する。

    毎時1回・単一ライターなので競合しない。並列実行する設計に変えるなら
    ここは日付分割か DynamoDB に置き換えること。
    """
    if not records:
        return
    raw = _be.read_bytes(key)
    existing = raw.decode("utf-8") if raw else ""

    lines = [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records]
    body = existing + ("" if existing.endswith("\n") or not existing else "\n")
    body += "\n".join(lines) + "\n"

    _be.write_bytes(key, body.encode("utf-8"), "application/x-ndjson")


def read_jsonl(key: str) -> list[dict]:
    raw = _be.read_bytes(key)
    if raw is None:
        return []
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def get_api_key() -> str:
    """鍵の出どころは置き場によって違う（SSM か 環境変数か）。backend に委ねる。"""
    return _be.get_api_key()


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


def build_public(now: datetime, articles: list[dict], window: list[dict] | None = None) -> None:
    """SPA が取得する latest.json を生成する。

    `window` は5分刻みの再構成用で、減衰ウィンドウ内の**全記事**。
    `articles` は今回の取得ぶんで、`top_articles`（|score|上位）の材料。
    後者は偏った標本なので、そちらで再構成すると値が歪む ── 別物として両方載せる。

    `window` を渡さなければここで読み直す。handler は既に読んでいるので渡す。
    """
    series = read_jsonl(config.KEY_SERIES)
    cutoff = (now - timedelta(hours=config.PUBLIC_SERIES_HOURS)).strftime("%Y%m%dT%H%M")
    series = [r for r in series if r["t"] >= cutoff]

    latest = series[-1] if series else {}
    top = sorted(articles, key=lambda a: abs(a["overall"]), reverse=True)
    top = top[: config.PUBLIC_TOP_ARTICLES]

    if window is None:
        window = window_articles(now)
    window = window[-config.PUBLIC_WINDOW_ARTICLES:]

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
