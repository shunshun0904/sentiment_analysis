# AWS まわりで、こちらでやる手続き

コード側は済んでいます。ここに書いてあるのは**アカウントと鍵の手続き**だけです。
上から順にやれば動きます。所要は初回で1時間ほど、うち待ち時間が半分。

---

## 0. 用意するもの

| もの | 取り方 | 費用 |
|---|---|---|
| AWSアカウント | <https://aws.amazon.com/jp/> | 無料(登録にクレジットカードは要る) |
| Alpha Vantage の鍵 | <https://www.alphavantage.co/support/#api-key> | 無料。**25リクエスト/日・5リクエスト/分** |

### 先にやること: 自分の鍵で `NEWS_SENTIMENT` が叩けるか確認する

設計時の検証はMCP接続経由の鍵で行っており、**そのキーのプランが不明**です。
プレミアム限定なのはリアルタイム株価・イントラデイ・オプション系で、
Alpha Intelligence(ニュース・センチメント)は無料枠に含まれる**はず**ですが、
確定していません。AWSに触る前に1回叩いてください(1リクエストだけ使います):

```bash
curl -s "https://www.alphavantage.co/query?function=NEWS_SENTIMENT\
&topics=financial_markets&limit=5&apikey=$AV_KEY" | head -c 800
```

| 返ってきたもの | 意味 |
|---|---|
| `"feed": [...]` が入っている | **無料枠で使える。** そのまま先へ進む |
| `"Information": "...premium..."` | 有料限定。方針の見直しが要る(このまま進めない) |
| `"Note"` / `"Information"` にレート超過 | その日の25回を使い切っている。翌日に |
| `"Error Message"` | パラメータ不正。上のURLをそのまま使っているか確認 |

ついでに **`time_published` のタイムゾーン**も見てください(未確定事項の2つめ)。
`feed[0].time_published` が実際の配信時刻より4〜5時間ずれていれば US/Eastern、
一致していれば UTC です。UTC でなければ `src/fetch.py` の窓計算に補正が要ります。

---

## 1. リージョンを決める

**東京(ap-northeast-1)** を勧めます。ニュースは米国のものですが、
見るのは日本からなので、レイテンシは近いほうが快適です。料金差はありません。

以降、コンソールの右上が「東京」になっていることを確認してください。
**リージョンがずれていると、作ったものが見つからない**というのが最初の躓きどころです。

## 2. 手元から AWS を触れるようにする

2通りあります。**初回は (b) が早い**です。

**(a) 自分のPCから**

```bash
brew install awscli aws-sam-cli          # macOS
aws configure                            # キー、シークレット、ap-northeast-1
aws sts get-caller-identity              # 誰として繋がっているか確認
```

アクセスキーは IAM → ユーザー → セキュリティ認証情報 で作ります。
**ルートユーザーのキーは作らないこと。** IAMユーザーを1つ作り、
`AdministratorAccess` を付けて、そのキーを使います。

**(b) ブラウザだけで済ませる(AWS CloudShell)**

コンソール右上のターミナルアイコンで、鍵の設定なしに CLI が使える環境が開きます。
`git clone` してそこから `sam deploy` すれば、PCに何も入れずに済みます。
SAM CLI は CloudShell に最初から入っています。

## 3. APIキーを SSM に預ける

キーをコードや環境変数に直書きしないための置き場です。標準パラメータは**無料**。

```bash
aws ssm put-parameter \
  --name /sentiment/alphavantage/api_key \
  --type SecureString \
  --value "取得したAlpha Vantageの鍵" \
  --region ap-northeast-1
```

パラメータ名はテンプレートの `AlphaVantageKeyParam` の既定値と揃えてあります。
変えるなら両方変えてください。

## 4. 配備する

バケット名は**世界で一意**である必要があります。`shun-market-sentiment` のように
自分の名前を混ぜてください。

```bash
sam build -t infra/template.yaml
sam deploy --guided \
  --parameter-overrides \
    BucketName=shun-market-sentiment \
    AllowedOrigin=https://shunshun0904.github.io
```

`--guided` は初回だけ。スタック名(例 `market-sentiment`)とリージョンを聞かれます。
2回目以降は `sam deploy` だけで済みます(設定は `samconfig.toml` に残る)。

`AllowedOrigin` は、ホームページから JSON を読むための許可です。
省略すると誰でも読めます(公開データなので実害はありませんが、絞れるなら絞る)。

**配備が終わると出力に URL が出ます。** `LatestJsonURL` を控えてください。

### スケジュールを変えるときの注意

`ScheduleExpression` の既定は `rate(60 minutes)` です。**短くしても速くなりません。**
無料枠は25リクエスト/日で、`src/config.py` の `DAILY_QUOTA` が上限に達した時点で
Lambda 側がスキップするからです。短くするなら有料プランが前提になります。

延ばすぶんには問題ありません。その場合は画面側と揃えるため、Lambda の
`UPDATE_INTERVAL_SECONDS` も同じ値にしてください。

```bash
sam deploy --parameter-overrides ScheduleExpression="rate(120 minutes)"
# テンプレートの UPDATE_INTERVAL_SECONDS も 7200 に直す
```

## 5. ホームページ側に URL を書く

`playbench` リポジトリの `data/sentiment.js` の `endpoint` に、控えた URL を書きます。

```js
endpoint: 'https://shun-market-sentiment.s3.ap-northeast-1.amazonaws.com/public/latest.json',
```

書いて push すれば、GitHub Pages 側に反映されます。書くまでのあいだ、
ページは「まだ配備していません」とだけ出ます。

**1時間待ってから開いてください。** 最初のLambdaが走るまで数字は入りません。
また、初回は減衰ウィンドウ(24時間)に1時間ぶんの記事しか無いので、
チャートは右端だけの短い線になります。丸1日たつと本来の形になります。

## 6. 予算アラートを置く(これは必ず)

S3とLambdaは無料枠に収まりますが、**気づかない増え方への蓋**として置きます。
月1ドルで通知が来れば、何かおかしいと分かります。

コンソール → 請求とコスト管理 → Budgets → 予算を作成
→ コスト予算 → 月次 1 USD → メール通知(実績が80%超で1通、100%で1通)

Budgets は2つまで無料です。

## 7. 動いているか確かめる

```bash
# 直近のログ
sam logs -n CollectorFunction --stack-name market-sentiment --tail

# 置かれた JSON を直接見る
aws s3 cp s3://shun-market-sentiment/public/latest.json - | head -c 600

# 何が置いてあるか
aws s3 ls s3://shun-market-sentiment/ --recursive --human-readable --summarize

# クォータの使用状況(自己管理している値)
aws s3 cp s3://shun-market-sentiment/state/last_run.json -
```

失敗が続くと CloudWatch アラーム(`ErrorAlarm`)が鳴ります。
通知先は自分で足してください(SNSトピックを作ってアラームに紐づける)。

### 数日たったら見るもの

```bash
# ソースの分布。ここからホワイトリストを決める(いまは観測モード)
aws s3 cp s3://shun-market-sentiment/observe/sources.json - | python3 -m json.tool
```

アルゴ自動生成記事のドメイン(Stock Traders Daily / StocksToTrade / Timothy Sykes /
GuruFocus / Simply Wall Street / 24/7 Wall St. / TradingView など)が上位を占めるはずです。
残すドメインを決めて `src/config.py` の `SOURCE_WHITELIST` に書き、再配備してください。

## 8. 止めるとき

| やりたいこと | やり方 |
|---|---|
| 一時停止(データは残す) | コンソール → EventBridge → ルール → 該当ルールを**無効化** |
| 更新間隔を変える | `sam deploy --parameter-overrides ScheduleExpression="rate(120 minutes)"` |
| 全部消す | `sam delete --stack-name market-sentiment`(バケットは空にしてから) |

`sam delete` はバケットが空でないと失敗します。先に `aws s3 rm s3://<バケット> --recursive` を。

---

## つまずきやすいところ

| 症状 | 原因 |
|---|---|
| ホームページに数字が出ない・コンソールにCORSエラー | `AllowedOrigin` が違う。`https://` から始まり、末尾スラッシュ無しで指定する |
| `sam deploy` が BucketAlreadyExists で落ちる | バケット名は世界で一意。別の名前にする |
| Lambdaは成功しているのに `latest.json` が無い | リージョン違いのバケットを見ている。コンソール右上を確認 |
| ログに `SSM` の AccessDenied | SSMのパラメータ名がテンプレートの `AlphaVantageKeyParam` と食い違っている |
| ログに `rate limit` / `Information` | その日の25回を使い切った。`state/last_run.json` の `requests_used_today` を見る |
| 実行はされるが `status: skipped` | UTC 5時・6時は意図的に間引いている(`SKIP_HOURS_UTC`)。またはクォータ切れ |
| チャートが右端だけ短い | 減衰ウィンドウ24時間ぶんの記事がまだ貯まっていない。丸1日待つ |
| 画面の「サーバ側の毎時集計と照合」の差が大きい | 再構成の実装かパラメータがずれている。`params` と `web/index.html` を確認 |

料金の内訳と、過去データをどう畳むかは `docs/cost.md` にあります。
