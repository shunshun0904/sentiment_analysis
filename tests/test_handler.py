"""handler.run() のエンドツーエンド。S3とニュースAPIはインメモリのスタブ。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import handler  # noqa: E402
from src.config import Config  # noqa: E402
from src.models import Article  # noqa: E402
from src.sources.base import NewsSource  # noqa: E402

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    """S3Store と同じ read/write インタフェースを持つインメモリ実装。"""

    def __init__(self) -> None:
        self.objects: dict[str, object] = {}
        self.cache_control: dict[str, bool] = {}

    def get_json(self, key: str, default=None):
        return self.objects.get(key, default)

    def put_json(self, key: str, payload, *, max_age: int = 60, public: bool = True) -> None:
        self.objects[key] = payload
        self.cache_control[key] = public


class FakeSource(NewsSource):
    name = "fake"

    def __init__(self, articles: list[Article]) -> None:
        self.articles = articles
        self.calls = 0

    def fetch(self, since, limit):  # noqa: ARG002
        self.calls += 1
        return list(self.articles)


def make(id_: str, score: float, hours_ago: float, title: str | None = None) -> Article:
    return Article(
        id=id_,
        title=title or f"headline {id_}",
        url=f"https://example.com/{id_}",
        source="example.com",
        published_at=NOW - timedelta(hours=hours_ago),
        score=score,
    )


@pytest.fixture
def cfg() -> Config:
    return Config(
        bucket="test-bucket",
        index_id="SPX",
        index_name="S&P 500",
        source="fake",
        scorer="passthrough",
        window_hours=24,
        half_life_hours=6,
        confidence_scale=8,
        top_articles=3,
    )


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


def run(monkeypatch, cfg, store, source, now=NOW):
    monkeypatch.setattr(handler, "get_source", lambda _cfg: source)
    return handler.run(cfg, now=now, store=store)


def test_writes_latest_history_and_cache(monkeypatch, cfg, store):
    source = FakeSource([make("a", 0.6, 0), make("b", -0.2, 2)])
    payload = run(monkeypatch, cfg, store, source)

    assert store.objects[cfg.key_latest()] is payload
    assert len(store.objects[cfg.key_history("2026-08-14")]["points"]) == 1
    assert set(store.objects[cfg.key_cache()]["articles"]) == {"a", "b"}
    assert payload["sentiment"]["article_count"] == 2
    assert payload["index"] == {"id": "SPX", "name": "S&P 500"}


def test_internal_state_is_not_publicly_cacheable(monkeypatch, cfg, store):
    run(monkeypatch, cfg, store, FakeSource([make("a", 0.5, 0)]))
    assert store.cache_control[cfg.key_cache()] is False
    assert store.cache_control[cfg.key_latest()] is True


def test_second_run_does_not_rescore_known_articles(monkeypatch, cfg, store):
    calls: list[int] = []

    class CountingScorer:
        name = "counting"

        def score(self, articles):
            calls.append(len(articles))
            return articles

    monkeypatch.setattr(handler, "get_scorer", lambda _cfg: CountingScorer())
    source = FakeSource([make("a", 0.5, 0), make("b", 0.1, 1)])

    run(monkeypatch, cfg, store, source, now=NOW)
    source.articles.append(make("c", -0.4, 0))
    run(monkeypatch, cfg, store, source, now=NOW + timedelta(minutes=5))

    assert calls == [2, 1]  # 2回目に渡るのは新着1件だけ


def test_series_grows_across_runs_and_reports_delta(monkeypatch, cfg, store):
    source = FakeSource([make("a", 1.0, 0)])
    run(monkeypatch, cfg, store, source, now=NOW)

    source.articles = [make("a", 1.0, 0), make("b", -1.0, 0)]
    payload = run(monkeypatch, cfg, store, source, now=NOW + timedelta(minutes=5))

    assert len(payload["series"]) >= 2
    assert payload["sentiment"]["delta"] is not None
    assert payload["sentiment"]["score"] < 0.99  # 弱気記事の追加で下がる


def test_series_is_rebuilt_when_history_is_missing(monkeypatch, cfg, store):
    payload = run(monkeypatch, cfg, store, FakeSource([make("a", 0.5, 3)]))
    # 24時間 / 5分 + 1 点を記事の発行時刻から再構築する
    assert len(payload["series"]) == 289


def test_top_articles_are_capped_and_sorted_by_impact(monkeypatch, cfg, store):
    source = FakeSource([make("a", 0.1, 0), make("b", -0.9, 0), make("c", 0.5, 0), make("d", 0.05, 0)])
    payload = run(monkeypatch, cfg, store, source)

    assert len(payload["articles"]) == 3
    assert payload["articles"][0]["id"] == "b"
    assert "weight" in payload["articles"][0]


def test_unscored_articles_stay_out_of_the_cache(monkeypatch, cfg, store):
    unscored = make("x", 0.0, 0)
    unscored.score = None
    run(monkeypatch, cfg, store, FakeSource([unscored, make("y", 0.3, 0)]))
    # 未スコアはキャッシュに入れない → 次回実行で再取得・再挑戦される
    assert set(store.objects[cfg.key_cache()]["articles"]) == {"y"}


def test_stale_articles_are_pruned_from_cache(monkeypatch, cfg, store):
    source = FakeSource([make("old", 0.5, 0)])
    run(monkeypatch, cfg, store, source, now=NOW)

    source.articles = [make("new", 0.5, 0)]
    later = NOW + timedelta(hours=72)
    source.articles[0].published_at = later
    run(monkeypatch, cfg, store, source, now=later)

    assert set(store.objects[cfg.key_cache()]["articles"]) == {"new"}


def test_meta_reports_scorer_and_counts(monkeypatch, cfg, store):
    payload = run(monkeypatch, cfg, store, FakeSource([make("a", 0.5, 0)]))
    assert payload["meta"]["scorer"] == "passthrough"
    assert payload["meta"]["source"] == "fake"
    assert payload["meta"]["fetched"] == 1
    assert payload["meta"]["new_scored"] == 1
