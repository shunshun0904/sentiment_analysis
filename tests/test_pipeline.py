"""パイプライン各段のテスト。ネットワークもAWSも要らない。

保存先は既定の fs バックエンド（tmp_path）で回す。s3 バックエンドが同じ
振る舞いをするかは test_both_backends_behave_the_same で1本だけ確かめる。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import dedup  # noqa: E402
import fetch  # noqa: E402
import score  # noqa: E402
import store  # noqa: E402

NOW = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)


def av(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def article(hours_ago: float, overall: float, *, rel: float = 1.0, url: str = "", title: str = "") -> dict:
    t = NOW - timedelta(hours=hours_ago)
    return {
        "url": url or f"https://example.com/{hours_ago}-{overall}",
        "t": av(t),
        "source": "Reuters",
        "title": title or f"headline {hours_ago} {overall}",
        "overall": overall,
        "rel": rel,
        "tickers": [],
    }


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """1本ごとに空の置き場を渡す。テスト同士が状態を共有しないように。"""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))


# --- score ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.9, "Bearish"),
        (-0.35, "Somewhat-Bearish"),   # 境界は上側のラベルに入る
        (-0.2, "Somewhat-Bearish"),
        (0.0, "Neutral"),
        (0.15, "Somewhat-Bullish"),
        (0.34, "Somewhat-Bullish"),
        (0.35, "Bullish"),
        (0.4341, "Bullish"),           # 実測値。±0.35 は上下限ではない
    ],
)
def test_labels_follow_alpha_vantage_thresholds(value, expected):
    assert score.label_of(value) == expected


def test_decay_halves_at_half_life():
    published = NOW - timedelta(hours=config.HALF_LIFE_HOURS)
    assert score.decay(published, NOW) == pytest.approx(0.5)


def test_recent_article_outweighs_older_one():
    result = score.aggregate([article(0, 1.0), article(12, -1.0)], NOW)
    # 12時間前 = 重み0.25 → (1.0 - 0.25) / 1.25 = 0.6
    assert result["sentiment"] == pytest.approx(0.6, abs=1e-4)
    assert result["n_articles"] == 2


def test_raw_mean_is_the_unweighted_control():
    result = score.aggregate([article(0, 1.0), article(12, -1.0)], NOW)
    # 対照系列は減衰も relevance も掛けない。主系列と乖離してよい
    assert result["raw_mean"] == pytest.approx(0.0)
    assert result["raw_mean"] != result["sentiment"]


def test_relevance_scales_the_weight():
    weighted = score.aggregate([article(0, 1.0, rel=1.0), article(0, -1.0, rel=0.2)], NOW)
    assert weighted["sentiment"] == pytest.approx((1.0 - 0.2) / 1.2, abs=1e-4)


def test_empty_input_gives_null_not_zero():
    result = score.aggregate([], NOW)
    assert result["sentiment"] is None
    assert result["label"] is None
    assert result["raw_mean"] is None
    assert result["n_articles"] == 0


def test_ticker_counts_are_ranked():
    a = article(0, 0.5) | {"tickers": ["NVDA", "AAPL"]}
    b = article(1, 0.5) | {"tickers": ["NVDA"]}
    result = score.aggregate([a, b], NOW)
    assert result["top_tickers"][0] == {"s": "NVDA", "n": 2}


# --- dedup ---------------------------------------------------------------


def test_title_key_ignores_case_and_punctuation():
    assert dedup.title_key("Fed holds rates steady!") == dedup.title_key("fed holds rates steady")


def test_same_story_from_two_domains_counts_once():
    """実測例: stockstotrade.com と timothysykes.com が同一タイトル・同一時刻で配信。
    URLだけの排除では二重計上になる。"""
    feed = [
        {"url": "https://stockstotrade.com/a", "time_published": av(NOW),
         "title": "Nvidia rallies", "source_domain": "StocksToTrade",
         "overall_sentiment_score": 0.4,
         "topics": [{"topic": "financial_markets", "relevance_score": "0.9"}]},
        {"url": "https://timothysykes.com/b", "time_published": av(NOW),
         "title": "Nvidia Rallies!", "source_domain": "Timothy Sykes",
         "overall_sentiment_score": 0.4,
         "topics": [{"topic": "financial_markets", "relevance_score": "0.9"}]},
    ]
    kept, counts = dedup.filter_articles(feed, {}, NOW)
    assert len(kept) == 1
    # 観測用のカウントは、捨てた側も含めて数える
    assert counts == {"StocksToTrade": 1, "Timothy Sykes": 1}


def test_low_relevance_articles_are_dropped():
    feed = [{
        "url": "https://example.com/x", "time_published": av(NOW), "title": "side note",
        "source_domain": "Reuters", "overall_sentiment_score": 0.9,
        "topics": [{"topic": "financial_markets", "relevance_score": "0.10"}],
    }]
    kept, _ = dedup.filter_articles(feed, {}, NOW)
    assert kept == []


def test_articles_older_than_the_window_are_dropped():
    old = av(NOW - timedelta(hours=config.DECAY_WINDOW_HOURS + 1))
    feed = [{
        "url": "https://example.com/old", "time_published": old, "title": "stale",
        "source_domain": "Reuters", "overall_sentiment_score": 0.5,
        "topics": [{"topic": "financial_markets", "relevance_score": "0.9"}],
    }]
    kept, _ = dedup.filter_articles(feed, {}, NOW)
    assert kept == []


def test_trim_keeps_max_relevance_across_market_topics():
    rec = dedup.trim({
        "url": "u", "time_published": av(NOW), "title": "t", "source_domain": "Reuters",
        "overall_sentiment_score": "0.42",
        "topics": [
            {"topic": "earnings", "relevance_score": "0.99"},        # 対象外
            {"topic": "financial_markets", "relevance_score": "0.55"},
            {"topic": "economy_macro", "relevance_score": "0.71"},   # これが採られる
        ],
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.2"},
            {"ticker": "NVDA", "relevance_score": "0.8"},
        ],
        "summary": "捨てられるはず", "banner_image": "https://img", "authors": ["x"],
    })
    assert rec["rel"] == pytest.approx(0.71)
    assert rec["overall"] == pytest.approx(0.42)
    assert rec["tickers"][0] == "NVDA"          # relevance の高い順
    assert set(rec) == {"url", "t", "source", "title", "overall", "rel", "tickers"}


def test_whitelist_filters_but_observation_mode_passes_all(monkeypatch):
    feed = [{
        "url": "https://example.com/x", "time_published": av(NOW), "title": "noise",
        "source_domain": "StocksToTrade", "overall_sentiment_score": 0.5,
        "topics": [{"topic": "financial_markets", "relevance_score": "0.9"}],
    }]
    # 観測モード（空 set）は全件通す
    assert len(dedup.filter_articles(feed, {}, NOW)[0]) == 1

    monkeypatch.setattr(config, "SOURCE_WHITELIST", {"Reuters"})
    assert dedup.filter_articles(feed, {}, NOW)[0] == []


def test_prune_seen_drops_entries_past_retention():
    old = (NOW - timedelta(hours=config.SEEN_RETENTION_HOURS + 1)).strftime("%Y%m%dT%H%M")
    fresh = NOW.strftime("%Y%m%dT%H%M")
    pruned = dedup.prune_seen({"urls": {"a": old, "b": fresh}, "titles": {}}, NOW)
    assert set(pruned["urls"]) == {"b"}


# --- fetch ---------------------------------------------------------------


def test_cold_start_window_looks_back_a_fixed_span():
    start, end = fetch.build_window(None, NOW)
    assert start == fetch.av_time(NOW - timedelta(hours=config.COLD_START_LOOKBACK_HOURS))
    assert end == fetch.av_time(NOW)


def test_warm_window_overlaps_the_previous_run():
    last = NOW - timedelta(hours=1)
    start, _ = fetch.build_window(last, NOW)
    # 遅れて届く記事を拾うため、前回実行より少し手前から取る
    assert start == fetch.av_time(last - timedelta(minutes=config.LOOKBACK_BUFFER_MIN))


def test_quota_resets_on_a_new_day():
    state = {"quota_date": "2026-08-14", "requests_used_today": 25}
    assert fetch.check_quota(state, "2026-08-15") == 0


def test_quota_blocks_at_the_daily_limit():
    state = {"quota_date": "2026-08-15", "requests_used_today": config.DAILY_QUOTA}
    with pytest.raises(fetch.QuotaExceeded):
        fetch.check_quota(state, "2026-08-15")


@pytest.mark.parametrize("key", ["Error Message", "Note", "Information"])
def test_alpha_vantage_errors_arrive_as_http_200(monkeypatch, key):
    """レート超過もパラメータ不正も 200 で返ってくる。本文で判定するしかない。"""
    class Resp:
        def read(self):
            return json.dumps({key: "something went wrong"}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *a, **kw: Resp())
    with pytest.raises(fetch.FetchError, match=key):
        fetch.fetch_news("k", "20260815T1300", "20260815T1400")


def test_unexpected_shape_is_rejected(monkeypatch):
    class Resp:
        def read(self):
            return b'{"something": "else"}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *a, **kw: Resp())
    with pytest.raises(fetch.FetchError, match="unexpected response shape"):
        fetch.fetch_news("k", "20260815T1300", "20260815T1400")


# --- store ---------------------------------------------------------------


def test_window_articles_spans_two_days_and_cuts_at_the_window():
    yesterday = NOW - timedelta(hours=20)
    stale = NOW - timedelta(hours=30)
    store.append_jsonl(f"{config.PREFIX_ARTICLES}{yesterday.date().isoformat()}.jsonl",
                       [article(20, 0.3), article(30, 0.9)])
    store.append_jsonl(f"{config.PREFIX_ARTICLES}{NOW.date().isoformat()}.jsonl",
                       [article(1, -0.4)])

    got = store.window_articles(NOW)
    assert [round(a["overall"], 2) for a in got] == [0.3, -0.4]   # 古い順、30時間前は落ちる
    assert all(a["t"] >= av(stale) for a in got)


def test_build_public_carries_everything_the_spa_needs():
    store.append_jsonl(f"{config.PREFIX_ARTICLES}{NOW.date().isoformat()}.jsonl",
                       [article(1, 0.5), article(2, -0.2)])
    store.append_jsonl(config.KEY_SERIES, [score.aggregate([article(1, 0.5)], NOW)])

    store.build_public(NOW, [article(1, 0.5)])
    payload = store.get_json(config.KEY_LATEST)

    assert payload["schema_version"] == 2
    assert payload["params"]["half_life_hours"] == config.HALF_LIFE_HOURS
    assert payload["params"]["step_min"] == config.DISPLAY_STEP_MIN
    # 5分刻みの再構成には、偏りのない窓の全記事が要る
    assert [a["s"] for a in payload["window"]] == [-0.2, 0.5]
    assert set(payload["window"][0]) == {"t", "s", "r"}
    assert payload["top_articles"][0]["title"]


def test_build_public_caps_the_window(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_WINDOW_ARTICLES", 2)
    store.append_jsonl(f"{config.PREFIX_ARTICLES}{NOW.date().isoformat()}.jsonl",
                       [article(3, 0.1), article(2, 0.2), article(1, 0.3)])
    store.build_public(NOW, [])
    payload = store.get_json(config.KEY_LATEST)
    # 上限を超えたら新しいほうを残す
    assert [a["s"] for a in payload["window"]] == [0.2, 0.3]


def test_append_jsonl_is_read_back_line_by_line():
    store.append_jsonl("k.jsonl", [{"a": 1}])
    store.append_jsonl("k.jsonl", [{"a": 2}, {"a": 3}])
    assert store.read_jsonl("k.jsonl") == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_missing_key_reads_as_empty():
    assert store.read_jsonl("nope.jsonl") == []
    assert store.get_json("nope.json", default={"x": 1}) == {"x": 1}


def test_both_backends_behave_the_same(monkeypatch):
    """S3 とローカルファイルで、store の外から見た振る舞いが変わらないこと。

    AWS に配備しなおす道は残してあるので、fs だけ通っていても意味が無い。
    無い鍵は default、追記は積み上がる ── 両方で同じであることを押さえる。
    """
    import fakes

    s3 = fakes.install_boto3()
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    import backend_s3
    monkeypatch.setattr(config, "S3_BUCKET", "test-bucket")
    monkeypatch.setattr(store, "_be", backend_s3)

    assert store.get_json("nope.json", default={"x": 1}) == {"x": 1}
    assert store.read_jsonl("nope.jsonl") == []

    store.append_jsonl("k.jsonl", [{"a": 1}])
    store.append_jsonl("k.jsonl", [{"a": 2}, {"a": 3}])
    assert store.read_jsonl("k.jsonl") == [{"a": 1}, {"a": 2}, {"a": 3}]

    store.put_json("j.json", {"hello": "世界"})
    assert store.get_json("j.json") == {"hello": "世界"}

    # S3 でだけ意味を持つもの ── 60秒キャッシュと Content-Type
    last = [p for p in s3.put_args if p["Key"] == "j.json"][-1]
    assert last["CacheControl"] == "max-age=60"
    assert last["ContentType"] == "application/json"


# --- handler（GitHub Actions が通す道そのもの） ---------------------------


def _feed_item(minutes_ago: int, overall: float, *, url: str, title: str) -> dict:
    """Alpha Vantage の生レスポンス1件ぶん。trim される前の形。"""
    t = NOW - timedelta(minutes=minutes_ago)
    return {
        "url": url,
        "time_published": av(t),
        "source_domain": "reuters.com",
        "title": title,
        "overall_sentiment_score": overall,
        "overall_sentiment_label": "Neutral",
        "summary": "落とされるはずの本文。" * 50,
        "banner_image": "https://example.com/x.png",
        "topics": [{"topic": "financial_markets", "relevance_score": "0.85"}],
        "ticker_sentiment": [{"ticker": "NVDA", "relevance_score": "0.6"}],
    }


def test_handler_run_writes_everything_the_next_run_and_the_spa_need(monkeypatch):
    """`python3 -m handler` が1回走ったあとの状態を、外から見える形で確かめる。

    ワークフローはこの関数を呼ぶだけなので、ここが通れば
    あとは data ブランチへコミットするだけになる。
    """
    import handler

    feed = [
        _feed_item(10, 0.42, url="https://example.com/a", title="Rally into the close"),
        _feed_item(20, -0.30, url="https://example.com/b", title="Claims rise"),
        # 同じ記事の別ドメイン配信。URLが違ってもタイトルで落ちる
        _feed_item(20, -0.30, url="https://other.example/b2", title="Claims rise!"),
    ]
    monkeypatch.setattr(store, "utcnow", lambda: NOW)
    monkeypatch.setattr(store, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(fetch, "fetch_news", lambda *a, **kw: {"feed": feed, "items": "3"})

    result = handler.lambda_handler({}, None)
    assert result["status"] == "ok"
    assert result["n_articles"] == 2          # 3件目は重複として落ちる

    # 1. 次の回が続きから取れる
    state = store.get_json(config.KEY_LAST_RUN)
    assert state["last_success_utc"] == NOW.strftime("%Y%m%dT%H%M")
    assert state["requests_used_today"] == 1

    # 2. 画面が読むもの
    payload = store.get_json(config.KEY_LATEST)
    assert payload["schema_version"] == 2
    assert payload["n_articles"] == 2
    assert len(payload["window"]) == 2
    assert payload["params"]["half_life_hours"] == config.HALF_LIFE_HOURS

    # 3. 監査ログ。summary / banner_image は落ちている
    day = NOW.date().isoformat()
    logged = store.read_jsonl(f"{config.PREFIX_ARTICLES}{day}.jsonl")
    assert len(logged) == 2
    assert set(logged[0]) == {"url", "t", "source", "title", "overall", "rel", "tickers"}

    # 4. 観測モードでソース分布が貯まっている（ホワイトリスト確定用）
    assert store.get_json(config.KEY_SOURCES) == {"reuters.com": 3}


def test_handler_skips_the_quiet_hours_without_spending_quota(monkeypatch):
    quiet = NOW.replace(hour=sorted(config.SKIP_HOURS_UTC)[0])
    monkeypatch.setattr(store, "utcnow", lambda: quiet)

    import handler

    def boom(*a, **kw):
        raise AssertionError("間引く時間帯なのに取得しようとした")

    monkeypatch.setattr(fetch, "fetch_news", boom)
    assert handler.lambda_handler({}, None)["reason"] == "quiet_hour"


def test_the_number_covers_the_whole_window_not_just_this_run(monkeypatch):
    """新着が0件の回でも、24時間ぶんの値が出続けること。

    集計を「今回取った記事」に対して行うと、重複排除で新着が0になった回に
    current が null に落ちて画面から数字が消える。S(t) の定義（24時間の
    加重平均）からも外れる。実際にそうなっていたので、ここで固定する。
    """
    import handler

    fresh = [_feed_item(10, 0.42, url="https://example.com/a", title="Rally")]
    monkeypatch.setattr(store, "utcnow", lambda: NOW)
    monkeypatch.setattr(store, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(fetch, "fetch_news", lambda *a, **kw: {"feed": fresh, "items": "1"})

    # 1回目 ── 記事を取り込む
    first = handler.lambda_handler({}, None)
    assert first["n_articles"] == 1

    # 2回目 ── 同じ記事しか返ってこない（＝新着0件）
    second = handler.lambda_handler({}, None)
    assert second["n_articles"] == 1, "新着が0でも窓の中身で測り続ける"
    assert second["sentiment"] is not None

    payload = store.get_json(config.KEY_LATEST)
    assert payload["current"] is not None
    assert payload["n_articles"] == 1
    # 今回の取得ぶんは0件なので、記事一覧は空になる（これは正しい）
    assert payload["top_articles"] == []


def test_series_rows_are_the_window_aggregate(monkeypatch):
    """history/sentiment.jsonl の各行が、その時刻の窓全体の集計であること。

    SPA はこの系列と自分の5分刻み再構成を突き合わせて自己点検する。
    両者の定義がずれていると、その照合が意味を失う。
    """
    import handler

    older = _feed_item(600, -0.60, url="https://example.com/old", title="Old news")
    newer = _feed_item(10, 0.40, url="https://example.com/new", title="New news")
    monkeypatch.setattr(store, "utcnow", lambda: NOW)
    monkeypatch.setattr(store, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(fetch, "fetch_news",
                        lambda *a, **kw: {"feed": [older, newer], "items": "2"})
    handler.lambda_handler({}, None)

    row = store.read_jsonl(config.KEY_SERIES)[-1]
    expected = score.aggregate(store.window_articles(NOW), NOW)
    assert row["sentiment"] == expected["sentiment"]
    assert row["n_articles"] == 2
    # 10時間前の記事は減衰で軽くなるので、単純平均より新しい側に寄る
    assert row["sentiment"] > row["raw_mean"]


def test_topics_is_a_single_topic():
    """topics はカンマ区切りでも AND。複数並べると実質空になる。

    実測（2026-08-22）:
      financial_markets のみ    最新 0.4時間前 / 約12件/時
      3トピック（AND）           最新 8.4時間前 / 約0.7件/時

    60分窓では後者は必ず0件になり、実際に4回連続で0件を記録した。
    OR で取るには1トピック1リクエストが必要で、25req/日 では成立しない。
    """
    assert "," not in config.AV_TOPICS, (
        f"AV_TOPICS={config.AV_TOPICS!r} — カンマは AND。1トピックに絞ること"
    )


def test_relevance_still_covers_the_macro_topics():
    """取得は1トピックでも、重みは市場系3トピックの最大で取る。

    financial_markets で取った記事にも economy_macro / economy_monetary は
    付いてくるので、金融政策の記事が軽くならないようにここで拾う。
    """
    assert config.AV_TOPICS in config.RELEVANCE_TOPICS
    assert {"economy_macro", "economy_monetary"} <= config.RELEVANCE_TOPICS

    fed = {
        "url": "https://example.com/fed", "time_published": av(NOW),
        "source_domain": "reuters.com", "title": "Fed holds rates",
        "overall_sentiment_score": 0.2,
        "topics": [{"topic": "financial_markets", "relevance_score": "0.35"},
                   {"topic": "economy_monetary", "relevance_score": "0.94"}],
        "ticker_sentiment": [],
    }
    assert dedup.trim(fed)["rel"] == 0.94


def test_cold_start_fills_the_whole_decay_window():
    """初回は減衰ウィンドウと同じ幅を取る。1〜2時間だと曲線が引けない。"""
    assert config.COLD_START_LOOKBACK_HOURS == config.DECAY_WINDOW_HOURS
    time_from, _ = fetch.build_window(None, NOW)
    assert time_from == (NOW - timedelta(hours=24)).strftime("%Y%m%dT%H%M")


def test_rate_limit_is_told_apart_from_other_errors():
    """AV は HTTP 200 の本文で上限を伝えてくる。実際に返った文面で固定する。"""
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"Information":
                "We have detected your API key as XXXX and our standard API rate "
                "limit is 25 requests per day. Please subscribe to any of the "
                "premium plans at https://www.alphavantage.co/premium/ to "
                "instantly remove all daily rate limits."}).encode()

    import unittest.mock as m
    with m.patch.object(fetch.urllib.request, "urlopen", lambda *a, **kw: Resp()):
        with pytest.raises(fetch.RateLimited):
            fetch.fetch_news("k", "20260822T0100", "20260822T0300")


def test_hitting_the_limit_stands_down_for_the_rest_of_the_day(monkeypatch):
    """上限に当たったら、その日の残りは叩きに行かない。

    こちらの計数と AV の計数はずれうる（実際にずれた）。ずれたまま
    残りの回を叩き続けると、翌日ぶんまで削りかねない。
    """
    import handler

    def limited(*a, **kw):
        raise fetch.RateLimited("Information: ...rate limit is 25 requests per day...")

    monkeypatch.setattr(store, "utcnow", lambda: NOW)
    monkeypatch.setattr(store, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(fetch, "fetch_news", limited)

    assert handler.lambda_handler({}, None)["reason"] == "rate_limited"
    state = store.get_json(config.KEY_LAST_RUN)
    assert state["requests_used_today"] == config.DAILY_QUOTA
    assert state["rate_limited_at"] == NOW.strftime("%Y%m%dT%H%M")

    # 次の回は API に触らずスキップする
    def boom(*a, **kw):
        raise AssertionError("打ち止めのはずなのに叩きに行った")

    monkeypatch.setattr(fetch, "fetch_news", boom)
    assert handler.lambda_handler({}, None)["reason"] == "quota"


def scheduled_runs_per_day() -> float:
    """間引きを考慮した1日の実行回数。

    間引き時間に当たる回数は、間隔によって変わる（60分なら SKIP_HOURS の数だけ、
    120分なら約半分）。時間あたりの実行回数 × 間引き時間数 で数える。
    """
    per_hour = 60 / config.SCHEDULE_MINUTES
    return 24 * per_hour - len(config.SKIP_HOURS_UTC) * per_hour


def test_schedule_fits_the_daily_quota_with_room_to_touch_it():
    """スケジュールがクォータを食い切らず、手で触る余地を残していること。

    以前はここで「予備 ≧ 1日の実行回数」を要求していた。**その規則は外した** ──
    無料枠25回/日では、その条件は間隔120分以上でしか満たせず、
    「取得は60分おき」という運用方針と両立しないため。

    代わりに残したのは、① 枠を食い切らないこと ② 手動実行のぶんが最低2回残ること。
    薄さを補うのは仕掛けのほう:
      - 上限に当たった日は打ち止めにする（test_hitting_the_limit_stands_down…）
      - 失敗してもリトライしない（RETRY_ON_FAILURE=False）
    余裕が要るときは SKIP_HOURS_UTC を広げるのがいちばん軽い。
    """
    runs = scheduled_runs_per_day()
    spare = config.DAILY_QUOTA - runs
    assert runs < config.DAILY_QUOTA, f"1日{runs:.0f}回で枠{config.DAILY_QUOTA}回を超えている"
    assert spare >= 2, f"1日{runs:.0f}回で予備{spare:.0f}回 — 手で1回も試せない"


def test_display_step_matches_the_collection_interval():
    """表示の刻みが、新しい情報が入る速さを上回っていないこと。

    5分刻みでも計算は正しい（各点で窓の全記事を実際に計算し直している）が、
    情報が60分に1回しか入らないのに5分刻みで描くと、
    細かさが情報量を上回って見える。揃えるという判断。
    """
    assert config.DISPLAY_STEP_MIN == config.SCHEDULE_MINUTES


def test_polling_interval_cannot_drift_from_the_schedule():
    """実行間隔と画面のポーリング間隔は、別々に渡せないこと。

    以前は別々の環境変数で、スケジュールだけ更新されて片方がずれた
    （60分間隔なのに画面には「120分ごと」と渡っていた）。
    """
    assert config.UPDATE_INTERVAL_SECONDS == config.SCHEDULE_MINUTES * 60
