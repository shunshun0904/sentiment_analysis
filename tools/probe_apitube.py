"""APITube の実測。鍵を取ったら、まずこれを1回流す。

    APITUBE_KEY=xxxx python3 tools/probe_apitube.py

判定するのは4つ。どれも5分更新が成立するかに直結する:

  1. 認証の通し方   ヘッダ X-API-Key で通るか(通らなければクエリも試す)
  2. **データ遅延**  最新記事は「今」からどれだけ遅れているか
                    ← 無料プランは12時間遅延との記載がある。事実なら5分更新は無意味
  3. 1回の取得件数   per_page を要求どおり返すか(無料は10件との記載あり)
  4. スコアの有無    sentiment が付いてくるか、レンジは -1〜1 か

最後に、いまのコードのフィールド対応が実物と合っているかを照合して、
`src/sources/apitube.py` の `_FIELD_PATHS` に残すべきパスを出力する。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sources.apitube import ENDPOINT, _FIELD_PATHS, _dig, results_of, to_article  # noqa: E402

QUERY = os.environ.get("NEWS_QUERY", '"S&P 500" OR "Wall Street" OR "Federal Reserve"')
WANT = 100  # わざと多めに要求して、実際に何件返るかを見る


def call(key: str, in_header: bool) -> tuple[dict, dict]:
    """(本文, ヘッダ) を返す。認証方式を切り替えて試す。"""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    params = {
        "q": QUERY,
        "language.code": "en",
        "published_at.start": since.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sort.by": "published_at",
        "sort.order": "desc",
        "per_page": WANT,
        "page": 1,
    }
    headers = {"Accept": "application/json", "User-Agent": "market-sentiment/probe"}
    if in_header:
        headers["X-API-Key"] = key
    else:
        params["api_key"] = key

    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), dict(resp.headers)


def probe_fields(items: list[dict]) -> None:
    """いまの `_FIELD_PATHS` のどのパスが実際に当たったかを出す。"""
    print("\n── フィールド対応（実物のどのパスが当たったか）")
    keep: dict[str, str] = {}
    for field, paths in _FIELD_PATHS.items():
        hit = None
        for path in paths:
            found = sum(1 for it in items if _dig(it, path) not in (None, "", [], {}))
            if found:
                hit = (path, found)
                break
        if hit:
            keep[field] = ".".join(hit[0])
            mark = "✅" if hit[0] == paths[0] else "⚠️ 予備のパス"
            print(f"  {mark} {field:13s} → {'.'.join(hit[0]):32s} ({hit[1]}/{len(items)}件)")
        else:
            print(f"  ❌ {field:13s} → どのパスにも無い")
    print("\n  実測後に _FIELD_PATHS をこれだけに絞れる:")
    for field, path in keep.items():
        print(f'    "{field}": (({", ".join(repr(p) for p in path.split("."))},),),'.replace(",,", ","))


def main() -> int:
    key = os.environ.get("APITUBE_KEY")
    if not key:
        print("環境変数 APITUBE_KEY を設定してください", file=sys.stderr)
        return 2

    print(f"GET {ENDPOINT}  (per_page={WANT} を要求)")

    # 1. 認証 ---------------------------------------------------------------
    payload = headers = None
    how = ""
    for in_header, label in ((True, "ヘッダ X-API-Key"), (False, "クエリ api_key")):
        try:
            payload, headers = call(key, in_header)
            how = label
            print(f"\n1. 認証   ✅ {label} で通りました")
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            print(f"\n1. 認証   ❌ {label}: HTTP {e.code} {body}")
    if payload is None:
        print("   → どちらでも通りません。鍵の値とプランを確認してください")
        return 1
    if how != "ヘッダ X-API-Key":
        print("   ⚠️ コードはヘッダ前提です。src/sources/apitube.py の fetch を直してください")

    for name, value in sorted(headers.items()):
        if any(k in name.lower() for k in ("ratelimit", "retry", "quota", "credit", "access-control")):
            print(f"   {name}: {value}")

    items = list(results_of(payload))
    print(f"   トップレベルのキー: {sorted(payload)}")
    print(f"   has_next_pages: {payload.get('has_next_pages')!r}")

    if not items:
        print("\n   記事が0件でした。クエリを緩めて試してください")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:1500])
        return 1

    # 2. データ遅延 ---------------------------------------------------------
    now = datetime.now(timezone.utc)
    articles = [a for a in (to_article(i) for i in items) if a is not None]
    if not articles:
        print("\n2. 遅延   ❌ 1件も正規化できません(下の生レスポンスを見てください)")
        print(json.dumps(items[0], ensure_ascii=False, indent=2)[:2000])
        return 1

    newest = max(a.published_at for a in articles)
    lag = (now - newest).total_seconds() / 60
    print(f"\n2. 遅延   最新記事は {newest:%m/%d %H:%M}Z、いまから {lag:.0f} 分前")
    if lag <= 30:
        print("   ✅ 5分更新が意味を持ちます")
    elif lag <= 120:
        print(f"   ⚠️ {lag/60:.1f}時間の遅れ。5分更新は過剰、15〜30分間隔で十分です")
    else:
        print(f"   ❌ {lag/60:.1f}時間の遅れ。「今の空気」にはなりません。")
        print("      → 有料プランに上げるか、別ソース(Marketaux/Finnhub)を検討")

    # 3. 件数 ---------------------------------------------------------------
    print(f"\n3. 件数   {WANT}件を要求 → {len(items)}件が返りました")
    if len(items) < WANT:
        need = -(-288 // max(1, len(items)))  # 天井除算
        print(f"   ⚠️ 1回{len(items)}件が上限のようです。24時間で288件を賄うにはページ繰りが要ります")
        print(f"      (概算で1回の更新あたり最大{need}リクエスト。無料枠の日次上限に注意)")

    # 4. スコア -------------------------------------------------------------
    scored = [a for a in articles if a.score is not None]
    print(f"\n4. スコア {len(scored)}/{len(articles)} 件に付いています")
    if scored:
        lo = min(a.score for a in scored)
        hi = max(a.score for a in scored)
        print(f"   レンジ {lo:+.2f} 〜 {hi:+.2f}")
        if lo < -1 or hi > 1:
            print("   ⚠️ -1〜1 に収まっていません。正規化の見直しが要ります")
    else:
        print("   ❌ スコアが付いてきません → フェーズ2(Claude Haiku 4.5)を前倒しする判断に")

    # 5. 対応表 -------------------------------------------------------------
    probe_fields(items)

    print("\n── 1件目の生レスポンス(先頭2000字)")
    print(json.dumps(items[0], ensure_ascii=False, indent=2)[:2000])

    print("\n── まとめ")
    print(f"   認証     {how}")
    print(f"   遅延     {lag:.0f}分")
    print(f"   件数     {len(items)}/{WANT}")
    print(f"   スコア   {len(scored)}/{len(articles)}")
    print("   この4つを踏まえて、更新間隔とスコアラーを決めてください")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
