# 株式市場センチメント可視化

金融ニュースのセンチメントを時間減衰付き加重平均で集計し、指数(まずS&P500)の
「今の空気」を5分間隔で可視化する。Lambda + EventBridge + S3 のサーバレス構成。

```
EventBridge (5分)
  └→ Lambda  取得 → 重複排除 → スコア化 → 集計
        └→ S3  data/{INDEX}/latest.json      ← 画面が5分ごとにfetch
               data/{INDEX}/history/*.json   ← 5分刻みの履歴(400日で自動削除)
               data/{INDEX}/daily.json       ← 1日1点に畳んだ長期の推移(残す)
               state/{INDEX}/articles.json   ← スコア済み記事キャッシュ(非公開・7日)

見せ方は2つ。どちらも同じ latest.json を読む:
  ホームページ  shunshun0904/playbench の「市場センチメント」タブ ← こちらが本番
  単体のSPA     web/index.html(バケットに置けば単体でも見られる)
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
| `docs/aws-setup.md` | **AWS側でやる手続き**。鍵の登録から配備・停止まで |
| `docs/cost.md` | 料金の内訳と、過去データを畳む方針 |
| `tools/probe_apitube.py` | APITubeのレスポンス実測スクリプト |
| `tools/make_sample.py` | AWS不要のサンプルデータ生成(ローカル確認用) |

## ローカルで動かす

```bash
python3 -m pytest -q                 # 40件、AWS・ネットワーク不要
python3 tools/make_sample.py         # web/data/SPX/latest.json を生成
python3 -m http.server -d web 8000   # → http://localhost:8000/
```

## デプロイ

手順の全文は **`docs/aws-setup.md`**(鍵の登録・予算アラート・停止の仕方まで)。
短く書くと:

```bash
aws ssm put-parameter --name /market-sentiment/apitube-key --type SecureString --value "$APITUBE_KEY"

sam build -t infra/template.yaml
sam deploy --guided --parameter-overrides \
  BucketName=<一意な名前> AllowedOrigin=https://shunshun0904.github.io
```

出力の `LatestJsonURL` を、ホームページ側(`playbench`)の `data/sentiment.js` に書く。
これでホームページの「市場センチメント」タブに数字が入る。

`web/index.html` は単体で確認するための版。ホームページに組み込んだあとは要らないが、
バケットに置けばそのままでも見られる:

```bash
aws s3 cp web/index.html s3://<バケット>/index.html --content-type "text/html; charset=utf-8"
```

## 主な環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `SCORER` | `passthrough` | `passthrough` / `claude` / `dual` |
| `WINDOW_HOURS` | `24` | 集計対象とする記事の鮮度 |
| `HALF_LIFE_HOURS` | `6` | 時間減衰の半減期 |
| `CACHE_RETENTION_HOURS` | `48` | 記事キャッシュの保持時間 |
| `CLAUDE_MAX_ARTICLES` | `120` | 1実行あたりのLLMスコア化上限(コストの上限) |
| `FETCH_LIMIT` | `100` | 1回の取得件数 |
| `UPDATE_INTERVAL_SECONDS` | `300` | 画面側のポーリング間隔。スケジュールと揃える |
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

## 貯まるデータと料金

月20〜25円。**主役はストレージではなくPUTリクエスト**なので、過去分を消しても
月額はほとんど変わらない(1年ぶんのストレージが月0.04円)。効くのは更新間隔のほう。
内訳と根拠は `docs/cost.md`。

畳み方は2段階にしてある:

| 置き場 | 粒度 | 保持 |
|---|---|---|
| `history/YYYY-MM-DD.json` | 5分刻み | 400日で自動削除(`HistoryRetentionDays`) |
| `daily.json` | 1日1点(始値/終値/平均/最小/最大) | 消さない。10年で約300KB |
| `state/articles.json` | スコア済み記事 | 7日で自動削除 |

毎回の実行で前日ぶんを `daily.json` に畳むので、**生の履歴が消えても長期の推移は残る**。
削除はS3のライフサイクルがやる。手で消す作業は無い。

## 未確定事項

### APITube 無料プランの制約(要一次確認・設計に直結)

公開ドキュメント由来の情報では、無料プランに次の制限があるとされている。
**apitube.io へは直接到達できていない**（サンドボックスの egress ポリシーで遮断）ため、
検索経由で得た記述であり、一次情報での裏取りが済んでいない。

| 記載 | 事実なら何が起きるか |
|---|---|
| **12時間のデータ遅延** | 「今の空気」にならない。5分更新の前提が崩れる |
| 1リクエスト10件 | 24時間分を集めるのにページ繰りが必要。日次のリクエスト上限を圧迫 |
| 100リクエスト/日 | 288回/日(5分間隔)が回らない。15分間隔=96回/日でぎりぎり |
| 1,000クレジット/日(別ページの記載) | 上と矛盾。どちらが正か不明 |

**鍵を取ったら、まずこれを確かめること。**`tools/probe_apitube.py` が
遅延・件数・スコアの有無を測り、5分更新が成立するかまで判定して出す。

```bash
APITUBE_KEY=xxxx python3 tools/probe_apitube.py
```

遅延が大きかった場合の選択肢は3つ:

1. 更新間隔を延ばす（遅延12時間なら5分更新に意味は無い）
   `sam deploy --parameter-overrides ScheduleExpression="rate(30 minutes)"`
   （画面側と揃えるため `UPDATE_INTERVAL_SECONDS` も合わせる）
2. APITube の有料プランに上げる
3. 別ソースに替える。`NewsSource` を1つ実装すれば以降は無変更で動く

### レスポンス形式

`src/sources/apitube.py` はドキュメント記載のレスポンス例に合わせてあり、
その形はテスト（`test_documented_response_example_maps_cleanly`）で固定してある。
**ただし本物のレスポンスでは未検証。** probe を流すと、いまの `_FIELD_PATHS` の
どのパスが実際に当たったかを照合し、絞り込み後の定義をそのまま出力する。

- スコアが付いてこないことが分かった時点で、フェーズ2の前倒しを判断する
- 数日運用後、スコアと実際の指数の動きを目視比較して `HALF_LIFE_HOURS` を調整する。
  日中の反応が鈍ければ短く(3〜4時間)、ノイズが多ければ長く(8〜12時間)

## 設計メモ

- **緑赤は使わない**。色覚多様性(P型・D型)で判別が難しく、金融ダッシュボードで
  最も起きやすい可読性の失敗のため。単体SPAは青(強気)/赤(弱気)、ホームページ側は
  そちらの版面に合わせて朱(強気)/藍(弱気)。どちらも中立はグレーで、
  色だけに意味を持たせず、数値とラベル(「やや弱気」等)を必ず併記している
- 同時実行数は1に固定。記事キャッシュへの並行書き込みを起こさないため
- `latest.json` は60秒キャッシュ。5分間隔の更新に対し、CDNを挟んでも遅延は1分以内
