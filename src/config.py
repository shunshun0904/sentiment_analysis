"""設定値。環境変数で上書き可能なものは os.environ から読む。"""
import os

# ---- Alpha Vantage ----
AV_ENDPOINT = "https://www.alphavantage.co/query"
AV_API_KEY_SSM_PATH = os.environ.get("AV_KEY_SSM", "/sentiment/alphavantage/api_key")

# 市場全体のムードを対象にするため topics 軸で取得する。
#
# ⚠️ topics はカンマ区切りでも **AND** で、OR ではない（実測 2026-08-22）。
#    tickers が AND なのは既知だったが、topics も同じだった。
#    3トピックを並べると「3つすべてに該当する記事」しか返らず、実質空になる:
#
#      financial_markets のみ           最新 0.4時間前 / 約12件/時
#      3トピック指定（AND）              最新 8.4時間前 / 約0.7件/時
#
#    実際にこれで4回連続 0件になった。60分窓では必ず空になる。
#    複数トピックを OR で取るには1トピック1リクエストが要り、
#    25req/日 の枠では成立しない。よって1トピックに絞る。
#
# なお economy_macro / economy_monetary を捨てたわけではない。
# financial_markets で取った記事にもこれらの topic は付いてくるので、
# 下の RELEVANCE_TOPICS で重みとして拾っている。
AV_TOPICS = "financial_markets"
AV_LIMIT = 1000  # API上限

# ---- クォータ管理 ----
# 無料プラン 25 req/day。
#
# 当初は60分間隔（22回/日 + 予備3回）にしていたが、**予備3回では足りなかった**。
# プローブ1回と手動実行を1〜2回やるだけで枯れる。実際、運用初日に
# 上限に当たった（こちらのカウンタは4回、AV 側は上限到達 ── 両者が
# 食い違った理由は未解明。AV の日付境界が UTC でない可能性がある）。
#
# 120分間隔にして約12回/日にすると予備が13回残る。
# **チャートの細かさは落ちない** ── 記事に time_published が付いていて、
# 5分刻みの系列は画面側で再構成しているため。落ちるのは鮮度だけ（最大2時間）。
DAILY_QUOTA = 25
# 120分間隔だとこの間引きが効く回は多くて1回で、意味は薄い。
# 枠が逼迫しなくなったので外してもよいが、米系ニュースが薄い時間帯なのは変わらない。
SKIP_HOURS_UTC = {5, 6}
# 失敗時のリトライはしない。クォータを消費して翌日分を削るリスクのほうが大きい。
RETRY_ON_FAILURE = False

# ---- 取得ウィンドウ ----
LOOKBACK_BUFFER_MIN = 15          # 記事の遅延到着を拾うための遡り
# last_run が無いとき（初回・state を消したとき）。
# 減衰ウィンドウと同じ24時間ぶんを一気に取り、初回から曲線が引ける状態にする。
# これより古い記事は dedup 側で落ちるので、広げても無駄にはならない。
COLD_START_LOOKBACK_HOURS = 24

# ---- 重複排除 ----
SEEN_RETENTION_HOURS = 48

# ---- ソースフィルタ ----
# 空 set のときは「観測モード」: 全件を通しつつ source_domain の出現頻度を
# observe/sources.json に記録する。数日運用して実際の分布を見てから確定すること。
#
# 実測（2026-08-14）で確認できた source_domain の例:
#   "Stock Traders Daily" / "StocksToTrade" / "Timothy Sykes" /
#   "GuruFocus" / "Simply Wall Street" / "24/7 Wall St." / "TradingView"
# いずれもアルゴ生成またはリテール向けの低品質記事が多く、
# 市場センチメントに入れると希釈される。
SOURCE_WHITELIST: set[str] = set()

# ---- センチメント集計 ----
HALF_LIFE_HOURS = 6.0        # 時間減衰の半減期
DECAY_WINDOW_HOURS = 24      # これより古い記事は集計に含めない

# 記事の重みに topics の relevance_score を掛けるか。
# True にすると、市場と周辺的にしか関係しない記事の寄与が下がる。
USE_TOPIC_RELEVANCE = True
# relevance を取るトピック（この中の最大値を使う）
RELEVANCE_TOPICS = {"financial_markets", "economy_macro", "economy_monetary"}
MIN_TOPIC_RELEVANCE = 0.30   # これ未満の記事は捨てる

# 記事一覧パネル用に残す言及銘柄の数（スコアには使わない）
TICKERS_PER_ARTICLE = 3

# ---- 保存先 ----
# 同じキー空間を2つの置き場で使う。どちらを使うかはここだけで決まる。
#
#   "fs" — ローカルのファイル。GitHub Actions 用。DATA_DIR 配下に書き、
#          ワークフローが data ブランチへコミットして永続化する
#   "s3" — S3。AWS Lambda 用（infra/template.yaml が STORE_BACKEND=s3 を渡す）
#
# 既定を "fs" にしてあるのは、テストもローカル道具も AWS 無しで動かせるため。
STORE_BACKEND = os.environ.get("STORE_BACKEND", "fs")

# fs バックエンドの根。下の KEY_* はこの下の相対パスになる。
DATA_DIR = os.environ.get("DATA_DIR", "data")

# s3 バックエンドのバケット。未設定でも import 時に落とさない。
S3_BUCKET = os.environ.get("S3_BUCKET", "")

# 置き場によらない共通のキー。fs ではそのままパス名として使う。
KEY_LAST_RUN = "state/last_run.json"
KEY_SEEN = "state/seen.json"
KEY_SERIES = "history/sentiment.jsonl"
KEY_LATEST = "public/latest.json"
KEY_SOURCES = "observe/sources.json"
PREFIX_ARTICLES = "history/articles/"

PUBLIC_SERIES_HOURS = 72
# 記事一覧に載せる件数。**公開の新しい順**に選ぶ。
# 以前は |score| の大きい順だったが、両端に偏った標本になり
# 「何が効いているか」を読み違えやすいのでやめた。
PUBLIC_RECENT_ARTICLES = 20

# ---- SPA へ渡す再構成用データ ----
# 「取得は60分間隔でも表示は5分刻みで再構築する」ためには、SPA 側に
# 減衰ウィンドウ内の**全記事**の (時刻, スコア, relevance) が要る。
# top_articles は |score| 上位20件の偏った標本なので、これで再構成すると値が歪む。
#
# 概算: 採用60〜100件/時 × 24時間 = 1,400〜2,400件、1件約40バイトで 60〜100KB。
# gzip が効くので実転送は 1/4 程度。60分に1回の取得なので許容範囲。
# 重すぎるなら、この上限を下げるか、5分刻みの再構成を Lambda 側に移す。
PUBLIC_WINDOW_ARTICLES = 3000
# 表示の刻み。SPA はこの間隔で系列を再構成する。
# 取得間隔と同じ60分にしてある ── 新しい情報が入るのが60分に1回なので、
# 表示もその粒度に揃えるという判断（本人の指定）。
#
# ここを小さくしても嘘にはならない（記事の発行時刻は分単位で持っているので、
# 5分刻みでも各点で窓の全記事を実際に計算し直している）が、
# 見た目の細かさが情報の入る速さを上回るのを避けている。
DISPLAY_STEP_MIN = 60
# 実行間隔（分）。**画面のポーリング間隔もここ1つから決まる。**
# 別々の環境変数にしていたら、スケジュールだけ更新されて片方がずれた。
# 揃えるのを人間の注意力に任せない。
#
# ⚠️ 60分 = 22回/日（間引き2時間を除く）で、無料枠25回に対し**予備は3回**しかない。
#    プローブ1回と手動実行1回で枯れる。実際に運用初日に上限到達した設定。
#    それでも60分にしているのは、鮮度を優先する判断（本人の指定）。
#    枯れても壊れないよう、上限に当たった日は打ち止めにする仕掛けが handler にある。
#    余裕が欲しくなったら SKIP_HOURS_UTC を広げるのがいちばん軽い
#    （{4,5,6,7} にすれば 20回/日 で予備5回。UTC 4-7 は米国の深夜で記事が薄い）。
SCHEDULE_MINUTES = int(os.environ.get("SCHEDULE_MINUTES", 60))
UPDATE_INTERVAL_SECONDS = SCHEDULE_MINUTES * 60
