# 株式市場センチメント可視化

金融ニュースのセンチメントを時間減衰付き加重平均で集計し、指数(まずS&P500)の
「今の空気」を5分間隔で可視化する。Lambda + EventBridge + S3 のサーバレス構成。

```
EventBridge (5分)
  └→ Lambda  取得 → 重複排除 → スコア化 → 集計
        └→ S3  data/{INDEX}/latest.json      ← SPAが5分ごとにfetch
               data/{INDEX}/history/*.json   ← 追記型の履歴
               state/{INDEX}/articles.json   ← スコア済み記事キャッシュ(非公開)
  └→ S3 静的ホスティング  index.html
```

## 構成

| パス | 役割 |
|---|---|
| `src/handler.py` | Lambda本体。パイプラインの各段を順に呼ぶだけ |
| `src/sources/` | ニュース取得(APITube)。`NewsSource` を実装すれば差し替え可能 |
| `src/dedupe.py` | ID重複 + 同一ニュースの配信違いを落とす |
| `src/scoring/` | **スコア算出。ここを差し替えてフェーズ2へ移行する** |
| `src/aggregate.py` | 時間減衰付き加重平均・時系列再構築 |
| `src/storage.py` | S3 I/O(Cache-Control もここで決める) |
| `web/index.html` | SPA(単一HTML、依存ライブラリなし) |
| `infra/template.yaml` | SAMテンプレート(バケット/Lambda/スケジュール/アラーム) |
| `docs/schema.md` | **JSONスキーマの正**。SPAとLambdaの契約 |
| `tools/probe_apitube.py` | APITubeのレスポンス実測スクリプト |
| `tools/make_sample.py` | AWS不要のサンプルデータ生成(ローカル確認用) |

## ローカルで動かす

```bash
python3 -m pytest -q                 # 37件、AWS・ネットワーク不要
python3 tools/make_sample.py         # web/data/SPX/latest.json を生成
python3 -m http.server -d web 8000   # → http://localhost:8000/
```

## デプロイ

```bash
aws ssm put-parameter --name /market-sentiment/apitube-key --type SecureString --value "$APITUBE_KEY"

sam build -t infra/template.yaml
sam deploy --guided --parameter-overrides BucketName=<一意な名前>

aws s3 cp web/index.html s3://<バケット>/index.html --content-type "text/html; charset=utf-8"
```

`sam deploy` の出力 `WebsiteURL` が公開URL。独自ドメイン/HTTPSが必要なら
CloudFront + OAC に差し替える(バケットポリシーの公開読み取りは削除する)。

## 主な環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `SCORER` | `passthrough` | `passthrough` / `claude` / `dual` |
| `WINDOW_HOURS` | `24` | 集計対象とする記事の鮮度 |
| `HALF_LIFE_HOURS` | `6` | 時間減衰の半減期 |
| `CACHE_RETENTION_HOURS` | `48` | 記事キャッシュの保持時間 |
| `CLAUDE_MAX_ARTICLES` | `120` | 1実行あたりのLLMスコア化上限(コストの上限) |
| `FETCH_LIMIT` | `100` | 1回の取得件数 |
| `INDEX_ID` / `INDEX_NAME` / `NEWS_QUERY` | `SPX` / `S&P 500` / 米国市場向け | 日経を足すときは別スタックとして複製する |

## フェーズ2(Claude Haiku 4.5)への移行

`SCORER=claude` に変えるだけで切り替わる。前提条件はコード側で担保済み:

- **新着記事しかLLMに投げない**。`state/articles.json` にIDがある記事はスキップする
  (`handler.run` の第3段)。全記事の再スコアは構造上起きない
- 1リクエストに `CLAUDE_BATCH_SIZE`(既定20)件を詰め、リクエスト数を抑える
- `CLAUDE_MAX_ARTICLES`(既定120)で1実行あたりの上限を固定。暴走時のコストが読める
- 出力は structured outputs でスキーマ固定。パース失敗やレート制限は1バッチ分を
  落として次回に持ち越し、実行全体は止めない
- 日本語ニュースも同じスコアラーで扱える(日経対応はこのフェーズで解決)

`SCORER=dual` にすると API付属スコアと Claude スコアを `article.scores` に
両方保存する。精度の比較検証はこのモードで行う。

追加の依存(`anthropic`)はフェーズ2でのみ必要:

```bash
echo "anthropic>=0.60" >> requirements.txt
aws ssm put-parameter --name /market-sentiment/anthropic-key --type SecureString --value "$ANTHROPIC_API_KEY"
```

## 未確定事項

- **APITubeのレスポンス形式は未実測**。`src/sources/apitube.py` は「よくある形」を
  複数パス試すディフェンシブな実装にしてある。キー取得後に
  `APITUBE_KEY=... python3 tools/probe_apitube.py` を流し、実フィールド名・
  スコアのレンジ・レート制限ヘッダを確認して `_FIELD_PATHS` を確定値に絞ること。
  スコアが付いてこないことが分かった時点で、フェーズ2の前倒しを判断する
- 数日運用後、スコアと実際の指数の動きを目視比較して `HALF_LIFE_HOURS` を調整する。
  日中の反応が鈍ければ短く(3〜4時間)、ノイズが多ければ長く(8〜12時間)

## 設計メモ

- **強気=青 / 弱気=赤**。緑赤は色覚多様性(P型・D型)で判別が難しく、
  金融ダッシュボードで最も起きやすい可読性の失敗のため採用しない。中立はグレー。
  色だけに意味を持たせず、数値とラベル(「やや弱気」等)を必ず併記している
- 同時実行数は1に固定。記事キャッシュへの並行書き込みを起こさないため
- `latest.json` は60秒キャッシュ。5分間隔の更新に対し、CDNを挟んでも遅延は1分以内
