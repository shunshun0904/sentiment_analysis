# S3 JSONスキーマ

`schema_version` は破壊的変更のたびに +1 する。SPAは自分が知らないバージョンを
受け取ったら描画せず、更新を促す(現行: 1)。

## S3レイアウト

| キー | 公開 | 内容 | Cache-Control |
|---|---|---|---|
| `index.html` | ○ | SPA本体 | — |
| `data/{INDEX}/latest.json` | ○ | SPAが5分ごとに取得する現在値+24h時系列+根拠記事 | `public, max-age=60` |
| `data/{INDEX}/history/YYYY-MM-DD.json` | ○ | 5分刻みの追記型履歴(日別) | `public, max-age=300` |
| `state/{INDEX}/articles.json` | ✕ | スコア化済み記事キャッシュ(重複排除の要) | `no-store` |

`state/` はバケットポリシーで公開対象から外し、ライフサイクルで7日で削除する。

## latest.json

```jsonc
{
  "schema_version": 1,
  "index": { "id": "SPX", "name": "S&P 500" },
  "generated_at": "2026-08-14T12:20:00Z",
  "next_update_at": "2026-08-14T12:25:00Z",
  "sentiment": {
    "score": -0.0545,        // -1.0(弱気) 〜 +1.0(強気)
    "label": "中立",          // 弱気 / やや弱気 / 中立 / やや強気 / 強気
    "confidence": 0.792,     // 0〜1。記事が少ない/古いほど低い
    "article_count": 48,     // 集計に使った記事数
    "delta": -0.0032         // 前回値との差。初回は null
  },
  "series": [                // 直近24時間、5分刻み(古い順)
    { "t": "2026-08-13T12:20:00Z", "score": 0.0974, "n": 48, "weight": 7.7, "confidence": 0.62 }
  ],
  "articles": [              // 寄与(|score × weight|)の大きい順
    {
      "id": "a1b2c3d4",
      "title": "Jobless claims rise more than expected",
      "url": "https://...",
      "source": "wsj.com",
      "published_at": "2026-08-14T11:20:00Z",
      "score": -0.52,
      "weight": 0.78,        // 時間減衰 × 関連度
      "scored_by": "apitube",
      "scores": { "apitube": -0.52, "claude": -0.41 }  // dualモードのときのみ両方入る
    }
  ],
  "meta": {
    "scorer": "passthrough", // passthrough / claude / dual
    "source": "apitube",
    "window_hours": 24,
    "half_life_hours": 6,
    "update_interval_seconds": 300,
    "fetched": 62,           // API取得件数
    "new_scored": 7,         // うち新規にスコア化した件数(= LLMコストの目安)
    "api_requests": 2,       // この更新で投げたリクエスト数(ページ繰りを含む)
    "api_quota": {           // 提供者が返した残枠。無ければ null
      "x-ratelimit-remaining": "873"
    }
  }
}
```

## history/YYYY-MM-DD.json

```jsonc
{
  "schema_version": 1,
  "index": "SPX",
  "date": "2026-08-14",
  "points": [ { "t": "...", "score": 0.12, "n": 44, "weight": 6.9, "confidence": 0.58 } ]
}
```

1日288点 × 約90バイト ≒ 26KB/日。1年で10MB弱。

## state/articles.json(非公開)

```jsonc
{
  "schema_version": 1,
  "updated_at": "2026-08-14T12:20:00Z",
  "articles": {
    "<article_id>": { "id": "...", "title": "...", "score": -0.52, "published_at": "...", ... }
  }
}
```

**このファイルがコスト管理の要**。ここにIDがある記事はLLMに再投入されない。
保持は48時間(`CACHE_RETENTION_HOURS`)で、集計ウィンドウ24時間より長くとってある。

## センチメントの定義

```
sentiment(t) = Σ(score_i × w_i) / Σ(w_i)
w_i = 0.5^(age_i / half_life) × relevance_i
```

- `age_i` : 時刻 t から見た記事の経過時間。**t より後に出た記事は含めない**
  (過去の点を計算するときに未来の情報が漏れないようにするため)
- `half_life` : 既定6時間。6時間前の記事は重み1/2、12時間前は1/4
- `relevance_i` : 0〜1。APIが返さない場合は1.0。Claudeスコアラーは指数との関連度を返す
- `confidence = 1 - exp(-Σw / 8)` : 重みの合計を0〜1に写したもの

記事には発行時刻が付くため、履歴が欠損しても直近24時間分を取り直せば時系列は
再構築できる(`aggregate.rebuild_series`)。handler は履歴が2点未満のとき
自動的にこの経路へフォールバックする。
