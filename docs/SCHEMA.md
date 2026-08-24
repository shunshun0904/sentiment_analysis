# JSONスキーマ定義

置き場の構成。キーは S3 でも GitHub Actions（`DATA_DIR` 配下のパス）でも同じ。

```
state/last_run.json                実行状態（time_from の起点）
state/seen.json                    重複排除キャッシュ（48時間ローリング）
history/sentiment.jsonl            市場センチメント時系列（追記）
history/articles/YYYY-MM-DD.jsonl  採用記事の監査ログ（追記）
public/latest.json                 SPAが60分ごとにfetchするファイル
observe/sources.json               source_domain の出現頻度（ホワイトリスト確定用）
```

---

## state/last_run.json

```json
{
  "last_run_utc": "20260815T1405",
  "last_success_utc": "20260815T1405",
  "requests_used_today": 7,
  "quota_date": "2026-08-15",
  "rate_limited_at": "20260822T0350"
}
```

`requests_used_today` はクォータの自己管理用。無料枠25req/日を
超えないよう実行側でガードする。

`rate_limited_at` は AV から上限到達を告げられた時刻。これが記録された日は
`requests_used_today` を上限まで進めて打ち止めにする。
**こちらの計数と AV の計数はずれうる** ── 2026-08-22 に、こちらが4回と数えている
状態で AV が上限到達を返した（理由は未解明。AV の日付境界が UTC でない可能性）。
ずれたまま叩き続けると翌日ぶんまで削るため、AV の言い分を優先する。

---

## state/seen.json

```json
{
  "urls":   { "<sha1(url)>": "20260815T1358" },
  "titles": { "<sha1(normalized_title)>": "20260815T1358" }
}
```

**URLとタイトルの二重キー**にしている。実測で同一記事が
`stockstotrade.com` と `timothysykes.com` の2URLで同時刻に配信される例を
確認したため、URLだけでは同一記事を重複計上する。

48時間より古いエントリは各実行時に削除する。

---

## history/sentiment.jsonl

1行1レコードで追記。

```json
{
  "t": "20260815T1400",
  "sentiment": 0.1834,
  "label": "Somewhat-Bullish",
  "raw_mean": 0.2251,
  "n_articles": 47,
  "top_tickers": [{"s": "NVDA", "n": 9}, {"s": "AAPL", "n": 4}]
}
```

- `sentiment` — 主系列。時間減衰 × トピック relevance で加重平均。
  **対象はその時刻の減衰ウィンドウ（24時間）に入っている全記事**であって、
  その回に取得した記事ではない（`store.window_articles()` の返り値）
- `raw_mean` — 単純平均。**検証用の対照系列**。両者が乖離したときに
  減衰・relevance の設定を疑える
- `top_tickers` — 言及銘柄の頻度。スコアには使わない。UI の色付け用

---

## history/articles/YYYY-MM-DD.jsonl

APIレスポンスは206件で約99,000トークン相当あるため、
**必要フィールドのみに削って**保存する。

```json
{
  "url": "https://...",
  "t": "20260814T134308",
  "source": "Reuters",
  "title": "...",
  "overall": 0.4341,
  "rel": 0.8499,
  "tickers": ["NVDA", "MRVL"]
}
```

`summary` / `banner_image` / `authors` / `topics` の生データは破棄。

---

## public/latest.json

SPAが取得する唯一のファイル。

```json
{
  "schema_version": 2,
  "updated_at": "20260815T1405",
  "next_update_at": "20260815T1505",
  "current": 0.1834,
  "label": "Somewhat-Bullish",
  "raw_mean": 0.2251,
  "n_articles": 47,
  "top_tickers": [{"s": "NVDA", "n": 9}],
  "series": [{"t": "20260815T1400", "v": 0.1834, "u": 0.2251}],
  "params": {
    "half_life_hours": 6.0,
    "window_hours": 24,
    "step_min": 5,
    "use_relevance": true,
    "update_interval_seconds": 3600
  },
  "window": [{"t": "20260815T1352", "s": 0.4341, "r": 0.85}],
  "recent_articles": [
    {"title": "...", "source": "Reuters", "url": "https://...",
     "score": 0.42, "t": "20260815T1352"}
  ]
}
```

| キー | 中身 |
|---|---|
| `current` / `label` / `raw_mean` / `n_articles` | 最新の毎時集計。`history/sentiment.jsonl` の最終行 |
| `series` | 直近72時間の**毎時**集計。サーバ側の値 |
| `window` | 減衰ウィンドウ（24時間）内の**全記事**。5分刻み再構成の入力 |
| `params` | 再構成のパラメータ。SPA に定数を二重持ちさせないため |
| `recent_articles` | ウィンドウ内の記事を**公開の新しい順**に20件。表示用 |
| `top_articles` | `recent_articles` と同じ内容。旧名の互換のため当面残す |

### `window` を載せている理由（v1 スキーマからの変更点）

記事の `time_published` があるため、**取得は60分間隔でも表示は5分刻みで再構築できる**
──ただしそれには、ウィンドウ内の**全記事**の (時刻, スコア, relevance) が要る。

記事一覧（`recent_articles`）は新しい順の**先頭20件**にすぎないので、
これで再構成すると当然ながら窓の全体を表さない。両者は別物として両方載せている。

かつては \|score\| 上位20件を載せていたが、**両端に偏った標本**で
「いま何が効いているか」を読み違えやすいためやめた。
材料も「今回の取得ぶん」からウィンドウ全体に変えてある ── 取得ぶんだと、
重複排除で新着が0件になった回に一覧が丸ごと空になる。

`window` は `t` / `s` / `r` の3フィールドのみに削ってある。
実測ベースの概算で 1,400〜2,400件 × 約40バイト = **60〜100KB**、gzip で概ね1/4。
60分に1回の取得なので許容範囲。上限は `config.PUBLIC_WINDOW_ARTICLES`（3,000件）。

**重すぎると分かったときの代案**: 5分刻みの再構成を集計側に移し、
`series` を5分刻みで持つ。SPAは描くだけになるが、書き込むサイズが増え、
半減期を画面で変えて試すことはできなくなる。

`window` の材料は `history/articles/` の当日＋前日ぶんを読み直して作る
（`store.window_articles`）。今回の実行で採った記事だけでは直近1時間ぶんしか無く、
24時間の曲線は引けないため。

---

### 集計の対象範囲（実装で1度間違えた箇所）

`handler` が `score.aggregate()` に渡すのは、**記事を書き込んだあとに読み直した
減衰ウィンドウ全体**でなければならない。その回に取得した記事を渡すと:

- $S(t)$ の定義（24時間の加重平均）から外れる
- 重複排除で新着が0件になった回に `sentiment` が null に落ち、画面から数字が消える

`score.aggregate()` は渡されたリストをそのまま集計するだけで、24時間の切り出しは
呼び出し側（`store.window_articles()`）の責任。テストで固定してある
（`test_the_number_covers_the_whole_window_not_just_this_run`）。

---

## 未確定の項目

1. ~~**`time_published` のタイムゾーン**~~ — **UTC で確定（2026-08-22 実測）。**
   `sort=LATEST` で最新記事を取り、そのときの実UTC時刻と比べたところ26分前だった。
   US/Eastern なら4〜5時間ずれるはずで、そうはならなかった。

2. ~~**取得トピック**~~ — **`topics` はカンマ区切りでも AND（2026-08-22 実測）。**
   `tickers` が AND なのは既知だったが、`topics` も同じだった。

   | クエリ | 最新記事 | 50件を集めるのに遡った幅 |
   |---|---|---|
   | `financial_markets` のみ | 0.4時間前 | 4.1時間（約12件/時） |
   | 3トピック指定 | 8.4時間前 | **68.6時間**（約0.7件/時） |

   3トピック指定のまま60分窓で回すと必ず0件になる（実際に4回連続で0件を記録）。
   OR で取るには1トピック1リクエストが必要で、25req/日 では成立しない。
   `AV_TOPICS = "financial_markets"` の1本に絞り、
   `economy_macro` / `economy_monetary` は `RELEVANCE_TOPICS` 側で重みとして拾う。

3. **`SOURCE_WHITELIST`** — 空のまま「観測モード」で開始し、
   `observe/sources.json` に蓄積した分布を見てから確定する。
   実測で見えた source_domain は7件のみで、これで決めるのは早すぎる。

4. **`HALF_LIFE_HOURS` = 6.0** — 暫定値。数日運用してS&P500の実際の
   値動きと目視比較してから調整する。
