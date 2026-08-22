# 株式市場センチメント可視化

金融ニュースのセンチメントを時間減衰付き加重平均で集計し、指数(まずS&P500)の
「今の空気」を可視化する。

```
120分に1回 取得 → 重複排除 → ソースフィルタ → 集計 → 保存
      └→  public/latest.json                ← 画面が120分ごとにfetch
          history/sentiment.jsonl           ← 実行ごとの集計(残す)
          history/articles/YYYY-MM-DD.jsonl ← 記事の監査ログ
          state/last_run.json / seen.json   ← 実行状態
          observe/sources.json              ← ソース分布

見せ方は2つ。どちらも同じ latest.json を読む:
  ホームページ  shunshun0904/playbench の「市場センチメント」タブ ← こちらが本番
  単体のSPA     web/index.html
```

## 動かし方は2つある

**保存先だけが違い、パイプラインは同じコードです**(`STORE_BACKEND` で切り替え)。

| | GitHub Actions（待機中） | AWS ← **いまこちらで運用** |
|---|---|---|
| 実行 | `schedule` cron | EventBridge |
| 保存 | `data` ブランチ(git) | S3 |
| 配信 | GitHub Pages | S3 静的ホスティング |
| 鍵 | Actions secrets | SSM SecureString |
| CORS | **不要**(Pages が `*` を返す) | バケットに設定が要る |
| 料金 | **0円** | 月2〜4円 |
| 手続き | 鍵を1つ登録 → ボタン2回 | IAM・SSM・SAM・予算アラート |
| 実行時刻 | ベストエフォート(遅延・欠落あり) | 確実 |
| 手順書 | **`docs/github-actions.md`** | `docs/aws-setup.md` |

Actions の遅延・欠落は設計が吸収する。`fetch.build_window()` が
「前回**成功**時刻 − 15分」を起点にするので、次の回が空白ぶんを取り直す
(`AV_LIMIT` は1000。実測レートは時間帯で開きがあり、UTC深夜で約12件/時、
市場が動く時間帯では桁が上がる ── 最悪でも数時間ぶんは1リクエストに収まる)。

## 更新は120分、表示は5分刻み

**ポーリング頻度は遅延であって、チャートの時間解像度ではない。**
記事には `time_published` が付くので、120分に1回まとめて取得しても、
5分刻みの系列はブラウザ側で再構築できる。失うのは最大120分の鮮度だけ。

$$S(t) = \frac{\sum_j d_j \cdot r_j \cdot s_j}{\sum_j d_j \cdot r_j},\qquad
  d_j = 0.5^{\Delta t / T_{1/2}}$$

再構築は `web/index.html` の `reconstruct()` / `pointAt()`。
サーバ側の集計(`series`)と突き合わせる自己点検を画面に出しており、
両者の差が大きければ実装がずれている(現状の残差は5分の格子ぶんのみ)。

## 構成

| パス | 役割 |
|---|---|
| `src/handler.py` | パイプラインの結線。入口は2つ(`lambda_handler` / `python3 -m handler`) |
| `src/fetch.py` | Alpha Vantage クライアント。ウィンドウ計算、クォータ確認 |
| `src/dedup.py` | 重複排除(URL＋正規化タイトル)、ソースフィルタ、レコード削減 |
| `src/score.py` | **センチメント集計。差し替え対象はここだけ** |
| `src/store.py` | 保存の読み書き、`public/latest.json` 生成。置き場に依存しない |
| `src/backend_fs.py` | 置き場: ローカルのファイル(GitHub Actions) |
| `src/backend_s3.py` | 置き場: S3(AWS Lambda)。boto3 の import はここだけ |
| `src/config.py` | 全パラメータ。**暫定値はここにコメントで明記してある** |
| `web/index.html` | SPA(単一HTML、依存ライブラリなし)。5分刻みの再構築を持つ |
| `.github/workflows/collect.yml` | 120分ごとの集計(GitHub Actions。schedule は停止中) |
| `.github/workflows/probe.yml` | 手動。ランナーから叩けるか・無料枠か・時刻系を実測する |
| `infra/template.yaml` | SAMテンプレート(バケット/Lambda/スケジュール/アラーム) |
| `docs/SCHEMA.md` | **JSONスキーマの正**。画面と集計側の契約 |
| `docs/handoff-v2.md` | 設計の経緯と確定事項。実測値はこちらが正 |
| `docs/aws-setup.md` | **AWS で動かす手順**(本番はこちら) |
| `docs/github-actions.md` | GitHub Actions で動かす手順。いつでも切り替えられる |
| `docs/cost.md` | 料金の内訳と、過去データを畳む方針 |
| `tools/make_sample.py` | 鍵不要のサンプルデータ生成(ローカル確認用) |
| `tools/probe_alphavantage.py` | APIを1回叩いて実際に何が返るか見る |

## ローカルで動かす

```bash
python3 -m pytest -q                 # 46件、AWS・ネットワーク不要
python3 tools/make_sample.py         # web/public/latest.json を生成
python3 -m http.server -d web 8000   # → http://localhost:8000/
```

`tools/make_sample.py` は置き場を一時ディレクトリに向けるだけで、集計と
`latest.json` の組み立ては**本番と同じコード**(`score` / `store`)を通す。

## 配備 — GitHub Actions(いまは止めてある)

手順の全文は **`docs/github-actions.md`**。短く書くと:

```
1. Secrets に ALPHAVANTAGE_KEY を登録
2. Actions → "Probe Alpha Vantage" を手で1回押す   ← ここが分岐点
3. Actions → "Collect market sentiment" を1回押す（data ブランチができる）
4. Settings → Pages → Source を data ブランチ / (root) に
5. collect.yml の schedule のコメントを外す
```

⚠️ **AWS 側と同じ API キーの 25req/日 を共有している。** 両方を定期実行すると
枠を食い合うので、切り替えるときは **先に AWS の EventBridge ルールを無効化する**。

手順2で `premium` と言われたら無料枠では使えない。ランナーから到達できなければ
IP で弾かれている(BGG が同じ経路を401で弾いていた前例がある)。
どちらも Actions か AWS かに関係しない問題なので、**先に確かめる**。

## 配備 — AWS(本番)

手順の全文は **`docs/aws-setup.md`**(鍵の登録・予算アラート・停止の仕方まで)。
短く書くと:

```bash
aws ssm put-parameter --name /sentiment/alphavantage/api_key \
  --type SecureString --value "$AV_KEY"

sam build -t infra/template.yaml
sam deploy --guided --parameter-overrides \
  BucketName=<一意な名前> AllowedOrigin=https://shunshun0904.github.io ScheduleMinutes=120
```

出力の `LatestJsonURL` を、ホームページ側(`playbench`)の `data/sentiment.js` に書く。
これでホームページの「市場センチメント」タブに数字が入る。

```bash
aws s3 cp web/index.html s3://<バケット>/index.html --content-type "text/html; charset=utf-8"
```

## 主なパラメータ

コードの既定値は `src/config.py`。環境変数で上書きできるのは次の3つだけで、
残りはコードを直すこと(暫定値の根拠をコメントと一緒に残すため)。

| 変数 | 既定 | 意味 |
|---|---|---|
| `STORE_BACKEND` | `fs` | `fs` = ローカルのファイル / `s3` = S3 |
| `DATA_DIR` | `data` | `fs` のときの置き場の根 |
| `AV_API_KEY` | — | `fs` のときの鍵(Actions secrets から渡す) |
| `S3_BUCKET` | — | `s3` のときのバケット |
| `AV_KEY_SSM` | `/sentiment/alphavantage/api_key` | `s3` のときの SSM パス |
| `SCHEDULE_MINUTES` | `120` | 実行間隔(分)。**画面のポーリング間隔もここから決まる** |

| `config.py` の値 | 既定 | 備考 |
|---|---|---|
| `DAILY_QUOTA` / `SKIP_HOURS_UTC` | `25` / `{5, 6}` | 120分間隔で約12回/日 + 予備13回 |
| `SCHEDULE_MINUTES` | `120` | 実行間隔。SAM では `ScheduleMinutes` パラメータから渡る |
| `HALF_LIFE_HOURS` | `6.0` | **暫定値**。運用後に実際の値動きと比較して調整 |
| `DECAY_WINDOW_HOURS` | `24` | これより古い記事は集計に含めない |
| `MIN_TOPIC_RELEVANCE` | `0.30` | **暫定値** |
| `SOURCE_WHITELIST` | 空 = **観測モード** | 全件通しつつ分布を `observe/sources.json` に蓄積 |
| `PUBLIC_WINDOW_ARTICLES` | `3000` | SPAに渡す再構成用データの上限 |

## クォータ管理(無料枠 25 req/日)

**間隔は120分**。60分間隔(22回/日)にしていたが、予備3回では足りず運用初日に
上限に当たった ── プローブ1回と手動実行を1〜2回やるだけで枯れる。
120分なら約12回/日で予備が13回残る。

**チャートの細かさは落ちない。** 5分刻みは `time_published` から画面側で
再構成しているため。落ちるのは鮮度だけ(最大2時間)。

- **失敗時のリトライはしない**。クォータを消費して翌日分を削るリスクのほうが大きい
- `state/last_run.json` で使用回数を自己管理し、上限に達したらスキップする
- **上限に当たった日はそこで打ち止め**にする(`rate_limited_at` を記録)。
  こちらの計数と AV の計数はずれうる ── 実際にずれた(こちら4回 / AV は上限到達、
  理由は未解明)。ずれたまま残りの回を叩き続けると翌日ぶんまで削る
- Alpha Vantage は **HTTP 200 のままエラーを本文で返す**。レート超過は
  `Note` / `Information`、パラメータ不正は `Error Message` キー。3つとも `fetch.py` で
  例外にしている

## 貯まるデータと料金

**GitHub Actions なら0円**(public リポジトリは分数無制限)。ただしリポジトリは
`latest.json` の毎時書き換えで年32〜128MB 太る ── 畳み方は `docs/github-actions.md`。

AWS の場合は月1〜2円。120分間隔なので v1(5分間隔)の想定より2桁小さい。
**主役はストレージではなくPUTリクエスト**で、1年ぶんのストレージは月0.04円。
内訳と根拠は `docs/cost.md`。

| 置き場 | 粒度 | 保持 |
|---|---|---|
| `history/sentiment.jsonl` | 実行ごと1点 | 消さない。10年で約8MB |
| `history/articles/YYYY-MM-DD.jsonl` | 記事1件1行 | 400日で自動削除(`ArticleRetentionDays`) |
| `state/` | 実行状態 | 7日で自動削除 |

集計済みの系列は記事ログと prefix が違うので、**記事が消えても長期の推移は残る**。
上の「保持」はAWSの話で、削除はS3のライフサイクルがやる(手で消す作業は無い)。
GitHub Actions では自動失効の仕掛けが無いので、太ってきたら `data` ブランチを
畳む(`docs/github-actions.md`)。

## 未確定・要検証(コード中のコメントにも同じことが書いてある)

| 項目 | 状態 | 確かめ方 |
|---|---|---|
| 無料キーで `NEWS_SENTIMENT` が叩けるか | **確認済(2026-08-22)**。無料枠で使える | — |
| `time_published` のタイムゾーン | **確認済(2026-08-22)**。UTC。最新記事は26分前だった | — |
| `SOURCE_WHITELIST` | 空(観測モード) | 数日運用して `observe/sources.json` を見る |
| `HALF_LIFE_HOURS = 6.0` | 暫定値 | S&P500の値動きと目視比較。鈍ければ3〜4h、ノイズが多ければ8〜12h |
| `MIN_TOPIC_RELEVANCE = 0.30` | 暫定値 | 同上 |
| スコアの理論上下限 | APIに明示なし。実測で `0.434062` を確認 | 運用しながら観察 |
| 記事の流量 | 時間帯差が大きい。UTC深夜 約12件/時 | 数日ぶんの `n_articles` を見る |

## 設計メモ

- **緑赤は使わない**。色覚多様性(P型・D型)で判別が難しく、金融ダッシュボードで
  最も起きやすい可読性の失敗のため。単体SPAは青(強気)/赤(弱気)、ホームページ側は
  そちらの版面に合わせて朱(強気)/藍(弱気)。どちらも中立はグレーで、
  色だけに意味を持たせず、数値とラベル(「やや弱気」等)を必ず併記する
- **対照系列 `raw_mean`(単純平均)を必ず併記する。** 主系列と乖離したときに、
  減衰や relevance の設定を疑うための基準。チャートでは無彩色の破線で描く
- 同時実行数は1に固定。`state/` への並行書き込みとクォータの二重消費を防ぐため
- `latest.json` は60秒キャッシュ
