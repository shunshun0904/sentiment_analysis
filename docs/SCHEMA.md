# S3 JSONスキーマ定義

バケット構成（`S3_BUCKET` 配下）。

```
state/last_run.json                実行状態（time_from の起点）
state/seen.json                    重複排除キャッシュ（48時間ローリング）
history/sentiment.jsonl            市場センチメント時系列（追記）
history/articles/YYYY-MM-DD.jsonl  採用記事の監査ログ（追記）
public/latest.json                 SPAが5分ごとにfetchするファイル
observe/sources.json               source_domain の出現頻度（ホワイトリスト確定用）
```

---

## state/last_run.json

```json
{
  "last_run_utc": "20260815T1405",
  "last_success_utc": "20260815T1405",
  "requests_used_today": 7,
  "quota_date": "2026-08-15"
}
```

`requests_used_today` はクォータの自己管理用。無料枠25req/日を
超えないよう Lambda 側でガードする。

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

- `sentiment` — 主系列。時間減衰 × トピック relevance で加重平均
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
  "top_articles": [
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
| `top_articles` | **今回の実行で採った記事**のうち \|score\| 上位20件。表示用 |

### `window` を載せている理由（v1 スキーマからの変更点）

記事の `time_published` があるため、**取得は60分間隔でも表示は5分刻みで再構築できる**
──ただしそれには、ウィンドウ内の**全記事**の (時刻, スコア, relevance) が要る。

`top_articles` は \|score\| 上位20件、つまり**両端に偏った標本**なので、
これで再構成すると値が歪む（絶対値が過大に出る）。両者は別物として両方載せている。

`window` は `t` / `s` / `r` の3フィールドのみに削ってある。
実測ベースの概算で 1,400〜2,400件 × 約40バイト = **60〜100KB**、gzip で概ね1/4。
60分に1回の取得なので許容範囲。上限は `config.PUBLIC_WINDOW_ARTICLES`（3,000件）。

**重すぎると分かったときの代案**: 5分刻みの再構成を Lambda 側に移し、
`series` を5分刻みで持つ。SPAは描くだけになるが、S3 の PUT サイズが増え、
半減期を画面で変えて試すことはできなくなる。

`window` の材料は `history/articles/` の当日＋前日ぶんを読み直して作る
（`store.window_articles`）。今回の実行で採った記事だけでは直近1時間ぶんしか無く、
24時間の曲線は引けないため。

---

## 未確定の項目

1. **`time_published` のタイムゾーン** — `time_from` / `time_to` と同一の
   時計系であることは実測で確認済みだが、UTC か US/Eastern かは未確認。
   当面 UTC として扱っている。運用開始後に実記事の配信時刻と突き合わせること。

2. **`SOURCE_WHITELIST`** — 空のまま「観測モード」で開始し、
   `observe/sources.json` に蓄積した分布を見てから確定する。
   実測で見えた source_domain は7件のみで、これで決めるのは早すぎる。

3. **`HALF_LIFE_HOURS` = 6.0** — 暫定値。数日運用してS&P500の実際の
   値動きと目視比較してから調整する。
