# AWS まわりで、こちらでやる手続き

コード側は済んでいます。ここに書いてあるのは**アカウントと鍵の手続き**だけです。
上から順にやれば動きます。所要は初回で1時間ほど、うち待ち時間が半分。

---

## 0. 用意するもの

| もの | 取り方 | 費用 |
|---|---|---|
| AWSアカウント | <https://aws.amazon.com/jp/> | 無料(登録にクレジットカードは要る) |
| APITube の鍵 | <https://apitube.io/> で登録 → ダッシュボードの API key | 無料枠あり（ベンダー各ページの記載は**1,000クレジット/日**・00:00 UTC リセット。ダッシュボードで要確認） |
| Anthropic の鍵 | フェーズ2に進むときだけ。<https://console.anthropic.com/> | 従量(月150〜300円想定) |

APITubeの鍵は先に取ってください。**取ったら、AWSに触る前に実測します**
(5分、AWSは不要):

```bash
APITUBE_KEY=xxxx python3 tools/probe_apitube.py
```

4つを測って判定まで出します。**特に「遅延」を見てください。**

| 出るもの | 見かた |
|---|---|
| 認証 | ヘッダ `X-API-Key` で通るか |
| **遅延** | 最新記事が何分前か。**12時間遅れなら5分更新は無意味** → 間隔を延ばすか、プラン/ソースを変える |
| 件数 | 1リクエストで何件返るか(無料は10件との記載あり) |
| スコア | `sentiment` が付くか。付かなければフェーズ2(Claude)へ前倒し |

ここで方針が決まってから、下の配備に進むほうが手戻りがありません。
遅延が大きければ、手順4の `ScheduleExpression` を変えて配備します。

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
# AWS CLI と SAM CLI を入れる(macOS)
brew install awscli aws-sam-cli

aws configure     # アクセスキー、シークレット、リージョン(ap-northeast-1)を入れる
aws sts get-caller-identity   # 自分が誰として繋がっているか確認
```

アクセスキーはコンソールの IAM → ユーザー → セキュリティ認証情報 で作ります。
**ルートユーザーのキーは作らないこと。** IAMユーザーを1つ作り、
`AdministratorAccess` を付けて、そのキーを使います。

**(b) ブラウザだけで済ませる(AWS CloudShell)**

コンソール右上のターミナルアイコンを押すと、鍵の設定なしで CLI が使える環境が開きます。
`git clone` してそこから `sam deploy` すれば、PCに何も入れずに済みます。
SAM CLI は CloudShell に最初から入っています。

## 3. APIキーを SSM に預ける

キーをコードや環境変数に直書きしないための置き場です。標準パラメータは**無料**。

```bash
aws ssm put-parameter \
  --name /market-sentiment/apitube-key \
  --type SecureString \
  --value "取得したAPITubeの鍵" \
  --region ap-northeast-1
```

フェーズ2に進むときは、同じ要領で Anthropic の鍵も:

```bash
aws ssm put-parameter --name /market-sentiment/anthropic-key --type SecureString --value "sk-ant-..."
```

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

## 5. ホームページ側に URL を書く

`playbench` リポジトリの `data/sentiment.js` の `endpoint` に、控えた URL を書きます。

```js
endpoint: 'https://shun-market-sentiment.s3.ap-northeast-1.amazonaws.com/data/SPX/latest.json',
```

書いて push すれば、GitHub Pages 側に反映されます。書くまでのあいだ、
ページは「まだ配備していません」とだけ出ます。

**5分待ってから開いてください。** 最初のLambdaが走るまで数字は入りません。

## 6. 予算アラートを置く(これは必ず)

S3とLambdaは無料枠に収まりますが、**気づかない増え方への蓋**として置きます。
月1ドルで通知が来れば、何かおかしいと分かります。

コンソール → 請求とコスト管理 → Budgets → 予算を作成
→ コスト予算 → 月次 1 USD → メール通知(実績が80%超で1通、100%で1通)

Budgets は2つまで無料です。

## 7. 動いているか確かめる

```bash
# 直近のログ(sam から見るのがいちばん早い)
sam logs -n CollectorFunction --stack-name market-sentiment --tail

# 置かれた JSON を直接見る
aws s3 cp s3://shun-market-sentiment/data/SPX/latest.json - | head -40

# 何が置いてあるか
aws s3 ls s3://shun-market-sentiment/data/SPX/ --recursive --human-readable --summarize
```

失敗が続くと CloudWatch アラーム(`ErrorAlarm`)が鳴ります。
通知先は自分で足してください(SNSトピックを作ってアラームに紐づける)。

## 8. 止めるとき

| やりたいこと | やり方 |
|---|---|
| 一時停止(データは残す) | コンソール → EventBridge → ルール → 該当ルールを**無効化** |
| 更新間隔を変える | `sam deploy --parameter-overrides ScheduleExpression="rate(15 minutes)"` |
| 全部消す | `sam delete --stack-name market-sentiment`(バケットは空にしてから) |

`sam delete` はバケットが空でないと失敗します。先に `aws s3 rm s3://<バケット> --recursive` を。

---

## つまずきやすいところ

| 症状 | 原因 |
|---|---|
| ホームページに数字が出ない・コンソールにCORSエラー | `AllowedOrigin` が違う。`https://` から始まり、末尾スラッシュ無しで指定する |
| `sam deploy` が BucketAlreadyExists で落ちる | バケット名は世界で一意。別の名前にする |
| Lambdaは成功しているのに `latest.json` が無い | リージョン違いのバケットを見ている。コンソール右上を確認 |
| ログに `APITUBE_KEY を設定してください` | SSMのパラメータ名がテンプレートの `ApiTubeKeyParam` と食い違っている |
| ログに `HTTP 429` | APITubeの日次上限を使い切った。`MAX_PAGES` を減らすか `ScheduleExpression` を延ばす（15分間隔なら96回/日） |
| 枠の減りが読めない | `latest.json` の `meta.api_quota` に毎回の残枠が記録されている。数日ぶん見れば実際の上限が分かる |

料金の内訳と、過去データをどう畳むかは `docs/cost.md` にあります。
