"""ローカル確認用のサンプル public/latest.json を生成する。

    python3 tools/make_sample.py && python3 -m http.server -d web 8000
    → http://localhost:8000/

AWSもAPIキーも要らない。S3 だけインメモリに差し替えて、
集計と latest.json の組み立ては**本番と同じコード**（score / store）を通す。
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import fakes  # noqa: E402

fakes.install()

import config  # noqa: E402
import score  # noqa: E402
import store  # noqa: E402

HEADLINES = [
    ("Fed signals patience on rate cuts as inflation cools", "Reuters", 0.35),
    ("Tech megacaps lead broad rally into the close", "Bloomberg", 0.72),
    ("Jobless claims rise more than expected", "Wall Street Journal", -0.41),
    ("Chipmakers slide after weak guidance from a sector bellwether", "Financial Times", -0.66),
    ("Consumer sentiment index ticks higher for a third month", "CNBC", 0.28),
    ("Oil jumps on supply disruption headlines", "Reuters", -0.22),
    ("Treasury yields ease after soft PPI print", "MarketWatch", 0.44),
    ("Retailers warn of a cautious holiday season", "Bloomberg", -0.35),
    ("Buybacks hit a record as corporate cash piles up", "Financial Times", 0.51),
    ("Regional bank shares wobble on credit quality concerns", "Wall Street Journal", -0.58),
    ("Manufacturing PMI returns to expansion", "CNBC", 0.33),
    ("Options market prices a quiet week ahead of CPI", "MarketWatch", 0.05),
]
TICKERS = ["NVDA", "AAPL", "MSFT", "SPY", "AMZN", "META", "JPM", "XOM"]


def av(dt: datetime, seconds: bool = True) -> str:
    return dt.strftime("%Y%m%dT%H%M%S" if seconds else "%Y%m%dT%H%M")


def make_articles(now: datetime, per_hour: int = 40) -> list[dict]:
    """直近24時間ぶんの記事。実測（206件/時）より控えめに、
    relevance フィルタ通過後の量を想定した件数で作る。"""
    rng = random.Random(20260815)
    out: list[dict] = []
    for i in range(24 * per_hour):
        hours_ago = (i / (24 * per_hour)) * 24
        published = now - timedelta(hours=hours_ago)
        title, source, base = HEADLINES[i % len(HEADLINES)]
        # ゆるやかなうねり + ノイズ。それらしい時系列にするため
        drift = 0.30 * math.sin((24 - hours_ago) / 24 * math.pi * 1.5)
        out.append({
            "url": f"https://example.com/article/{i}",
            "t": av(published),
            "source": source,
            "title": title if i < len(HEADLINES) else f"{title} ({i // len(HEADLINES)})",
            "overall": round(max(-1.0, min(1.0, base * 0.6 + drift + rng.uniform(-0.12, 0.12))), 4),
            "rel": round(rng.uniform(0.35, 1.0), 4),
            "tickers": rng.sample(TICKERS, k=rng.randint(0, 3)),
        })
    out.sort(key=lambda a: a["t"])
    return out


def main() -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    articles = make_articles(now)

    # 監査ログ（日付ごと）。store.window_articles がここから読み直す
    by_day: dict[str, list[dict]] = {}
    for a in articles:
        by_day.setdefault(f"{a['t'][:4]}-{a['t'][4:6]}-{a['t'][6:8]}", []).append(a)
    for day, records in by_day.items():
        store.append_jsonl(f"{config.PREFIX_ARTICLES}{day}.jsonl", records)

    # 毎時の集計。本番と同じ score.aggregate を各時刻で呼ぶ
    for hours_ago in range(23, -1, -1):
        t = now - timedelta(hours=hours_ago)
        visible = [a for a in articles if a["t"] <= av(t)]
        store.append_jsonl(config.KEY_SERIES, [score.aggregate(visible, t)])

    last_hour = [a for a in articles if a["t"] >= av(now - timedelta(hours=1))]
    store.build_public(now, last_hour)

    payload = json.loads(store._s3.objects[config.KEY_LATEST].decode("utf-8"))
    out = ROOT / "web" / "public" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    size_kb = out.stat().st_size / 1024
    print(f"書き出し: {out}")
    print(f"  current={payload['current']} raw_mean={payload['raw_mean']} "
          f"n={payload['n_articles']}")
    print(f"  series={len(payload['series'])}点 window={len(payload['window'])}件 "
          f"top_articles={len(payload['top_articles'])}件")
    print(f"  サイズ {size_kb:.1f}KB（gzip で概ね1/4）")


if __name__ == "__main__":
    main()
