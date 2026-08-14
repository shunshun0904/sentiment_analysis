"""APITube の実測スクリプト(所要15分の確認作業用)。

    APITUBE_KEY=xxxx python3 tools/probe_apitube.py

確認すること:
1. レスポンスの実フィールド名(id / href / published_at / sentiment.*)
2. センチメントスコアの有無とレンジ(-1〜1 か、方向性のみか)
3. CORSヘッダ(ブラウザ完結構成は却下済みだが、将来の判断材料として記録)
4. レート制限ヘッダ(残リクエスト数)

出力を見て `src/sources/apitube.py` の `_FIELD_PATHS` を確定値だけに絞る。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sources.apitube import ENDPOINT, to_article  # noqa: E402

QUERY = os.environ.get("NEWS_QUERY", '"S&P 500" OR "Wall Street" OR "Federal Reserve"')


def main() -> int:
    key = os.environ.get("APITUBE_KEY")
    if not key:
        print("環境変数 APITUBE_KEY を設定してください", file=sys.stderr)
        return 2

    since = datetime.now(timezone.utc) - timedelta(hours=6)
    params = {
        "api_key": key,
        "q": QUERY,
        "language.code": "en",
        "published_at.start": since.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sort.by": "published_at",
        "sort.order": "desc",
        "per_page": 5,
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    print(f"GET {url.replace(key, '***')}\n")

    req = urllib.request.Request(url, headers={"Accept": "application/json", "Origin": "https://example.com"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        headers = dict(resp.headers)

    print("--- 注目ヘッダ ---")
    for name, value in headers.items():
        if any(k in name.lower() for k in ("ratelimit", "access-control", "x-", "retry")):
            print(f"{name}: {value}")

    results = payload.get("results") or payload.get("data") or payload.get("articles") or []
    print(f"\n--- 件数: {len(results)} / トップレベルのキー: {sorted(payload)} ---")
    if not results:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        return 1

    print("\n--- 1件目の生レスポンス ---")
    print(json.dumps(results[0], ensure_ascii=False, indent=2)[:3000])

    print("\n--- Article への正規化結果 ---")
    mapped = 0
    for item in results:
        article = to_article(item)
        if article is None:
            print("  [NG] 必須項目が取れませんでした:", sorted(item)[:12])
            continue
        mapped += 1
        print(f"  [OK] score={article.score} {article.published_at:%m/%d %H:%M} {article.source} :: {article.title[:60]}")

    scored = sum(1 for i in results if (a := to_article(i)) and a.score is not None)
    print(f"\n正規化 {mapped}/{len(results)} 件、うちスコア付き {scored} 件")
    print("→ スコアが0件ならフェーズ2(Claude Haiku 4.5)へ前倒しを検討")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
