"""ローカル確認用のサンプル latest.json を生成する。

    python3 tools/make_sample.py && python3 -m http.server -d web 8000
    → http://localhost:8000/

APIキーもAWSも不要。パイプライン本体をそのまま通し、
ニュース取得とS3だけをインメモリのスタブに差し替える。
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import handler  # noqa: E402
from src.config import Config  # noqa: E402
from src.models import Article  # noqa: E402
from src.sources.base import NewsSource  # noqa: E402

HEADLINES = [
    ("Fed signals patience on rate cuts as inflation cools", "reuters.com", 0.35),
    ("Tech megacaps lead broad rally into the close", "bloomberg.com", 0.72),
    ("Jobless claims rise more than expected", "wsj.com", -0.41),
    ("Chipmakers slide after weak guidance from a sector bellwether", "ft.com", -0.66),
    ("Consumer sentiment index ticks higher for a third month", "cnbc.com", 0.28),
    ("Oil jumps on supply disruption headlines", "reuters.com", -0.22),
    ("Treasury yields ease after soft PPI print", "marketwatch.com", 0.44),
    ("Retailers warn of a cautious holiday season", "bloomberg.com", -0.35),
    ("Buybacks hit a record as corporate cash piles up", "ft.com", 0.51),
    ("Regional bank shares wobble on credit quality concerns", "wsj.com", -0.58),
    ("Manufacturing PMI returns to expansion", "cnbc.com", 0.33),
    ("Options market prices a quiet week ahead of CPI", "marketwatch.com", 0.05),
]


class SampleSource(NewsSource):
    name = "sample"

    def __init__(self, now: datetime) -> None:
        self.now = now

    def fetch(self, since: datetime, limit: int) -> list[Article]:  # noqa: ARG002
        rng = random.Random(42)
        articles = []
        for i in range(48):
            title, source, base = HEADLINES[i % len(HEADLINES)]
            hours_ago = (i / 48) * 24
            # ゆるやかなうねり + ノイズで、それらしい時系列にする
            drift = 0.35 * math.sin((24 - hours_ago) / 24 * math.pi * 1.5)
            articles.append(
                Article(
                    id=f"sample-{i:03d}",
                    title=f"{title}" if i < len(HEADLINES) else f"{title} ({i // len(HEADLINES) + 1})",
                    url="https://example.com/article/%d" % i,
                    source=source,
                    published_at=self.now - timedelta(hours=hours_ago),
                    summary="サンプルデータです。実際のAPIレスポンスではありません。",
                    score=max(-1.0, min(1.0, base * 0.6 + drift + rng.uniform(-0.15, 0.15))),
                    relevance=rng.uniform(0.6, 1.0),
                    scored_by="sample",
                )
            )
        return articles


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, object] = {}

    def get_json(self, key: str, default=None):
        return self.objects.get(key, default)

    def put_json(self, key: str, payload, *, max_age: int = 60, public: bool = True) -> None:
        self.objects[key] = payload


def main() -> None:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    cfg = Config(bucket="local", index_id="SPX", index_name="S&P 500", scorer="passthrough")
    store = MemoryStore()

    handler.get_source = lambda _cfg: SampleSource(now)  # type: ignore[assignment]
    payload = handler.run(cfg, now=now, store=store)

    out = ROOT / "web" / "data" / "SPX" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"書き出し: {out} (score={payload['sentiment']['score']}, points={len(payload['series'])})")


if __name__ == "__main__":
    main()
