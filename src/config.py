"""設定値。環境変数で上書き可能なものは os.environ から読む。"""
import os

# ---- Alpha Vantage ----
AV_ENDPOINT = "https://www.alphavantage.co/query"
AV_API_KEY_SSM_PATH = os.environ.get("AV_KEY_SSM", "/sentiment/alphavantage/api_key")

# 市場全体のムードを対象にするため topics 軸で取得する。
# tickers パラメータは AND 条件のためバスケット取得には使えない（実測確認済み 2026-08-15）。
AV_TOPICS = "financial_markets,economy_macro,economy_monetary"
AV_LIMIT = 1000  # API上限

# ---- クォータ管理 ----
# 無料プラン 25 req/day。毎時実行すると24回で余裕が1回しかないため、
# 米系ニュースが薄い時間帯を間引いて予備を3回確保する。
DAILY_QUOTA = 25
SKIP_HOURS_UTC = {5, 6}  # 22回/日 + 予備3回
# 失敗時のリトライはしない。クォータを消費して翌日分を削るリスクのほうが大きい。
RETRY_ON_FAILURE = False

# ---- 取得ウィンドウ ----
LOOKBACK_BUFFER_MIN = 15          # 記事の遅延到着を拾うための遡り
COLD_START_LOOKBACK_HOURS = 2     # last_run が無いとき

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

# ---- S3 ----
# 未設定でも import 時に落とさない（テストとローカル道具のため）。
# 実行時に空なら store 側で気づく。
S3_BUCKET = os.environ.get("S3_BUCKET", "")
KEY_LAST_RUN = "state/last_run.json"
KEY_SEEN = "state/seen.json"
KEY_SERIES = "history/sentiment.jsonl"
KEY_LATEST = "public/latest.json"
KEY_SOURCES = "observe/sources.json"
PREFIX_ARTICLES = "history/articles/"

PUBLIC_SERIES_HOURS = 72
PUBLIC_TOP_ARTICLES = 20

# ---- SPA へ渡す再構成用データ ----
# 「取得は60分間隔でも表示は5分刻みで再構築する」ためには、SPA 側に
# 減衰ウィンドウ内の**全記事**の (時刻, スコア, relevance) が要る。
# top_articles は |score| 上位20件の偏った標本なので、これで再構成すると値が歪む。
#
# 概算: 採用60〜100件/時 × 24時間 = 1,400〜2,400件、1件約40バイトで 60〜100KB。
# gzip が効くので実転送は 1/4 程度。60分に1回の取得なので許容範囲。
# 重すぎるなら、この上限を下げるか、5分刻みの再構成を Lambda 側に移す。
PUBLIC_WINDOW_ARTICLES = 3000
# 表示の刻み。SPA はこの間隔で系列を再構成する
DISPLAY_STEP_MIN = 5
# SPA のポーリング間隔（秒）。EventBridge のスケジュールと揃える
UPDATE_INTERVAL_SECONDS = int(os.environ.get("UPDATE_INTERVAL_SECONDS", 3600))
