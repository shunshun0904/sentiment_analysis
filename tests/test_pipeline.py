"""パイプライン各段のテスト。ネットワークもAWSも要らない。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import fakes  # noqa: E402

S3 = fakes.install()

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
def clean_s3():
    S3.objects.clear()
    S3.put_args.clear()
    yield


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
    payload = json.loads(S3.objects[config.KEY_LATEST].decode("utf-8"))

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
    payload = json.loads(S3.objects[config.KEY_LATEST].decode("utf-8"))
    # 上限を超えたら新しいほうを残す
    assert [a["s"] for a in payload["window"]] == [0.2, 0.3]


def test_append_jsonl_is_read_back_line_by_line():
    store.append_jsonl("k.jsonl", [{"a": 1}])
    store.append_jsonl("k.jsonl", [{"a": 2}, {"a": 3}])
    assert store.read_jsonl("k.jsonl") == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_missing_key_reads_as_empty():
    assert store.read_jsonl("nope.jsonl") == []
    assert store.get_json("nope.json", default={"x": 1}) == {"x": 1}
