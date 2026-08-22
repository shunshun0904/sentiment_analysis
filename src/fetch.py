"""Alpha Vantage NEWS_SENTIMENT からの取得。"""

# 注釈を文字列のまま扱う。`X | None` は Python 3.10 以降の書き方で、
# 3.9 で import すると TypeError になる（AWS CloudShell の python3 が 3.9）。
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import config


class QuotaExceeded(Exception):
    pass


class FetchError(Exception):
    pass


def av_time(dt: datetime) -> str:
    """YYYYMMDDTHHMM 形式。API は分単位までしか受け付けない。"""
    return dt.strftime("%Y%m%dT%H%M")


def parse_published(s: str) -> datetime:
    """time_published は YYYYMMDDTHHMMSS。

    注意: この時刻のタイムゾーンは API ドキュメントに明示がない。
    time_from / time_to と同一の時計系であることは実測で確認済みだが、
    UTC か US/Eastern かは未確認。運用開始後に実際の記事の配信時刻と
    突き合わせて確定すること。当面 UTC として扱う。
    """
    return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def build_window(last_run: datetime | None, now: datetime) -> tuple[str, str]:
    if last_run is None:
        start = now - timedelta(hours=config.COLD_START_LOOKBACK_HOURS)
    else:
        start = last_run - timedelta(minutes=config.LOOKBACK_BUFFER_MIN)
    return av_time(start), av_time(now)


def check_quota(state: dict, today: str) -> int:
    """当日の使用済み回数を返す。上限に達していれば例外。"""
    used = state.get("requests_used_today", 0) if state.get("quota_date") == today else 0
    if used >= config.DAILY_QUOTA:
        raise QuotaExceeded(f"daily quota {config.DAILY_QUOTA} reached ({used} used)")
    return used


def fetch_news(api_key: str, time_from: str, time_to: str) -> dict:
    params = {
        "function": "NEWS_SENTIMENT",
        "topics": config.AV_TOPICS,
        "time_from": time_from,
        "time_to": time_to,
        "limit": config.AV_LIMIT,
        "sort": "LATEST",
        "apikey": api_key,
    }
    url = f"{config.AV_ENDPOINT}?{urllib.parse.urlencode(params)}"

    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    # Alpha Vantage は HTTP 200 のままエラーを本文で返す。
    # レート超過は "Note" / "Information"、パラメータ不正は "Error Message"。
    for key in ("Error Message", "Note", "Information"):
        if key in body:
            raise FetchError(f"{key}: {body[key]}")

    if "feed" not in body:
        raise FetchError(f"unexpected response shape: {list(body.keys())}")

    return body
