"""Alpha Vantage NEWS_SENTIMENT を1回だけ叩いて、実際に何が返るかを見る。

    AV_API_KEY=xxxx python3 tools/probe_alphavantage.py

これで決まるのは3つ。いずれも資料からは確定できなかった項目:

  1. GitHub のランナー（データセンターIP）から叩けるか
     BGG は同じ経路を 401 で弾いていた（playbench/.github/workflows/bgg.yml）。
     Alpha Vantage が同じ挙動をしないという保証はどこにも無い
  2. 無料プランで NEWS_SENTIMENT が使えるか
     設計時の検証は MCP 接続経由の別キーで、そのプランが不明だった
  3. time_published のタイムゾーン
     資料に明示が無く、当面 UTC として扱っている

⚠️ 1日25リクエストの枠を1つ消費する。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
import fetch  # noqa: E402


def main() -> int:
    key = os.environ.get("AV_API_KEY", "")
    if not key:
        print("AV_API_KEY が空です。secrets.ALPHAVANTAGE_KEY を渡してください")
        return 2

    now = datetime.now(timezone.utc)
    time_from = fetch.av_time(now - timedelta(hours=2))
    print(f"▸ 窓 {time_from} .. {fetch.av_time(now)} （UTC として送る）")
    print(f"▸ topics {config.AV_TOPICS}")

    try:
        body = fetch.fetch_news(key, time_from, fetch.av_time(now))
    except fetch.FetchError as e:
        # HTTP 200 のまま本文でエラーが返る経路。プラン制限もここに出る
        print(f"\n❌ API がエラーを返した: {e}")
        print("   'premium' を含むなら無料枠では使えない ── 方針の見直しが要る")
        return 1
    except Exception as e:  # noqa: BLE001  ネットワーク層の遮断もここで見たい
        print(f"\n❌ 到達できなかった: {type(e).__name__}: {e}")
        print("   IP で弾かれている可能性がある（BGG と同じ経路の問題）")
        return 1

    feed = body.get("feed", [])
    print(f"\n✅ 到達・取得できた")
    print(f"   items {body.get('items')} / feed {len(feed)} 件")

    if not feed:
        print("   ⚠️ 0件。窓が狭いか、その時間帯に記事が無い。判定は保留")
        return 0

    newest = feed[0]
    published = fetch.parse_published(newest["time_published"])
    lag_min = (now - published).total_seconds() / 60

    print(f"\n▸ 最新記事 {newest['time_published']}  ({newest.get('source')})")
    print(f"   いま（UTC）との差 {lag_min:.0f} 分")
    if lag_min < 90:
        print("   → time_published は UTC。いまの扱いで正しい")
    elif 200 < lag_min < 330:
        print("   → 4〜5時間ずれている。US/Eastern の可能性が高い。")
        print("      fetch.parse_published と web/index.html の parseAV に補正が要る")
    else:
        print("   → 判断できない。記事の遅延そのものかもしれない。数回見ること")

    have = {
        "overall_sentiment_score": "overall_sentiment_score" in newest,
        "topics(relevance)": bool(newest.get("topics")),
        "ticker_sentiment": bool(newest.get("ticker_sentiment")),
    }
    print("\n▸ 使うフィールドの有無")
    for k, v in have.items():
        print(f"   {'✅' if v else '❌'} {k}")

    scores = [a["overall_sentiment_score"] for a in feed
              if "overall_sentiment_score" in a]
    if scores:
        print(f"\n▸ スコアの実測レンジ  min {min(scores):+.4f} / max {max(scores):+.4f}")
        print("   （±0.35 はラベルの境目であって上下限ではない）")

    domains = {}
    for a in feed:
        d = a.get("source_domain") or a.get("source") or "?"
        domains[d] = domains.get(d, 0) + 1
    top = sorted(domains.items(), key=lambda kv: -kv[1])[:8]
    print(f"\n▸ source_domain 上位（全 {len(domains)} 種）")
    for d, n in top:
        print(f"   {n:4d}  {d}")

    print("\n▸ 生データの1件目（先頭800字）")
    print(json.dumps(newest, ensure_ascii=False, indent=2)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
