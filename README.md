# 株式市場センチメント可視化

金融ニュースのセンチメントを時間減衰付き加重平均で集計し、指数(まずS&P500)の
「今の空気」を可視化する。Lambda + EventBridge + S3 のサーバレス構成。

```
EventBridge (60分)
  └→ Lambda  取得 → 重複排除 → ソースフィルタ → 集計
        └→ S3  public/latest.json             ← 画面が60分ごとにfetch
               history/sentiment.jsonl        ← 毎時の集計(残す)
               history/articles/YYYY-MM-DD.jsonl ← 記事の監査ログ(400日で自動削除)
               state/last_run.json / seen.json   ← 実行状態(非公開・7日)
               observe/sources.json              ← ソース分布(非公開)

見せ方は2つ。どちらも同じ latest.json を読む:
  ホームページ  shunshun0904/playbench の「市場センチメント」タブ ← こちらが本番
  単体のSPA     web/index.html(バケットに置けば単体でも見られる)
```

## 更新は60分、表示は5分刻み

**ポーリング頻度は遅延であって、チャートの時間解像度ではない。**
記事には `time_published` が付くので、60分に1回まとめて取得しても、
5分刻みの系列はブラウザ側で再構築できる。失うのは最大60分の鮮度だけ。

$$S(t) = \frac{\sum_j d_j \cdot r_j \cdot s_j}{\sum_j d_j \cdot r_j},\qquad
  d_j = 0.5^{\Delta t / T_{1/2}}$$

再構築は `web/index.html` の `reconstruct()` / `pointAt()`。
サーバ側の毎時集計(`series`)と突き合わせる自己点検を画面に出しており、
両者の差が大きければ実装がずれている(現状の残差は5分の格子ぶんのみ)。

## 構成

| パス | 役割 |
|---|---|
| `src/handler.py` | Lambda本体。パイプラインの各段を順に呼ぶだけ |
| `src/fetch.py` | Alpha Vantage クライアント。ウィンドウ計算、クォータ確認 |
| `src/dedup.py` | 重複排除(URL＋正規化タイトル)、ソースフィルタ、レコード削減 |
| `src/score.py` | **センチメント集計。差し替え対象はここだけ** |
| `src/store.py` | S3 I/O、SSMからの鍵取得、`public/latest.json` 生成 |
| `src/config.py` | 全パラメータ。**暫定値はここにコメントで明記してある** |
| `web/index.html` | SPA(単一HTML、依存ライブラリなし)。5分刻みの再構築を持つ |
| `infra/template.yaml` | SAMテンプレート(バケット/Lambda/スケジュール/アラーム) |
| `docs/SCHEMA.md` | **JSONスキーマの正**。SPAとLambdaの契約 |
| `docs/handoff-v2.md` | 設計の経緯と確定事項。実測値はこちらが正 |
| `docs/aws-setup.md` | **AWS側でやる手続き**。鍵の登録から配備・停止まで |
| `docs/cost.md` | 料金の内訳と、過去データを畳む方針 |
| `tools/make_sample.py` | AWS不要のサンプルデータ生成(ローカル確認用) |

## ローカルで動かす

```bash
python3 -m pytest -q                 # 34件、AWS・ネットワーク不要
python3 tools/make_sample.py         # web/public/latest.json を生成
python3 -m http.server -d web 8000   # → http://localhost:8000/
```

`tools/make_sample.py` は S3 だけインメモリに差し替えて、集計と `latest.json` の
組み立ては**本番と同じコード**(`score` / `store`)を通す。

## デプロイ

手順の全文は **`docs/aws-setup.md`**(鍵の登録・予算アラート・停止の仕方まで)。
短く書くと:

```bash
aws ssm put-parameter --name /sentiment/alphavantage/api_key \
  --type SecureString --value "$AV_KEY"

sam build -t infra/template.yaml
sam deploy --guided --parameter-overrides \
  BucketName=<一意な名前> AllowedOrigin=https://shunshun0904.github.io
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
| `S3_BUCKET` | — | データとSPAを置くバケット |
| `AV_KEY_SSM` | `/sentiment/alphavantage/api_key` | 鍵を入れた SSM SecureString |
| `UPDATE_INTERVAL_SECONDS` | `3600` | 画面側のポーリング間隔。スケジュールと揃える |

| `config.py` の値 | 既定 | 備考 |
|---|---|---|
| `DAILY_QUOTA` / `SKIP_HOURS_UTC` | `25` / `{5, 6}` | 22回/日 + 予備3回 |
| `HALF_LIFE_HOURS` | `6.0` | **暫定値**。運用後に実際の値動きと比較して調整 |
| `DECAY_WINDOW_HOURS` | `24` | これより古い記事は集計に含めない |
| `MIN_TOPIC_RELEVANCE` | `0.30` | **暫定値** |
| `SOURCE_WHITELIST` | 空 = **観測モード** | 全件通しつつ分布を `observe/sources.json` に蓄積 |
| `PUBLIC_WINDOW_ARTICLES` | `3000` | SPAに渡す再構成用データの上限 |

## クォータ管理(無料枠 25 req/日)

- 毎時実行だと24回で予備が1回しかないため、**UTC 5時・6時を間引いて22回/日**
- **失敗時のリトライはしない**。クォータを消費して翌日分を削るリスクのほうが大きい
- `state/last_run.json` で使用回数を自己管理し、上限に達したらスキップする
- Alpha Vantage は **HTTP 200 のままエラーを本文で返す**。レート超過は
  `Note` / `Information`、パラメータ不正は `Error Message` キー。3つとも `fetch.py` で
  例外にしている

## 貯まるデータと料金

月2〜4円。60分間隔なので v1(5分間隔)の想定より1桁小さい。
**主役はストレージではなくPUTリクエスト**で、1年ぶんのストレージは月0.04円。
内訳と根拠は `docs/cost.md`。

| 置き場 | 粒度 | 保持 |
|---|---|---|
| `history/sentiment.jsonl` | 毎時1点 | 消さない。10年で約15MB |
| `history/articles/YYYY-MM-DD.jsonl` | 記事1件1行 | 400日で自動削除(`ArticleRetentionDays`) |
| `state/` | 実行状態 | 7日で自動削除 |

集計済みの系列は記事ログと prefix が違うので、**記事が消えても長期の推移は残る**。
削除はS3のライフサイクルがやる。手で消す作業は無い。

## 未確定・要検証(コード中のコメントにも同じことが書いてある)

| 項目 | 状態 | 確かめ方 |
|---|---|---|
| 無料キーで `NEWS_SENTIMENT` が叩けるか | **未確認**。検証はMCP経由の別キーで実施した | 自分の鍵で1回叩く |
| `time_published` のタイムゾーン | **未確認**。当面 UTC として扱っている | 実記事の配信時刻と突き合わせる |
| `SOURCE_WHITELIST` | 空(観測モード) | 数日運用して `observe/sources.json` を見る |
| `HALF_LIFE_HOURS = 6.0` | 暫定値 | S&P500の値動きと目視比較。鈍ければ3〜4h、ノイズが多ければ8〜12h |
| `MIN_TOPIC_RELEVANCE = 0.30` | 暫定値 | 同上 |
| スコアの理論上下限 | APIに明示なし。実測で `0.434062` を確認 | 運用しながら観察 |

## 設計メモ

- **緑赤は使わない**。色覚多様性(P型・D型)で判別が難しく、金融ダッシュボードで
  最も起きやすい可読性の失敗のため。単体SPAは青(強気)/赤(弱気)、ホームページ側は
  そちらの版面に合わせて朱(強気)/藍(弱気)。どちらも中立はグレーで、
  色だけに意味を持たせず、数値とラベル(「やや弱気」等)を必ず併記する
- **対照系列 `raw_mean`(単純平均)を必ず併記する。** 主系列と乖離したときに、
  減衰や relevance の設定を疑うための基準。チャートでは無彩色の破線で描く
- 同時実行数は1に固定。`state/` への並行書き込みとクォータの二重消費を防ぐため
- `latest.json` は60秒キャッシュ
