"""パイプライン各段のテスト。ネットワークとAWSはスタブで置き換える。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import aggregate, dedupe  # noqa: E402
from src.config import Config  # noqa: E402
from src.models import Article, canonical_id  # noqa: E402
from src.scoring.passthrough import PassthroughScorer  # noqa: E402
from src.sources.apitube import to_article  # noqa: E402

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def make(id_: str, score: float, hours_ago: float, title: str = "", relevance: float = 1.0) -> Article:
    return Article(
        id=id_,
        title=title or f"headline {id_}",
        url=f"https://example.com/{id_}",
        source="example.com",
        published_at=NOW - timedelta(hours=hours_ago),
        score=score,
        relevance=relevance,
    )


# --- 集計 -----------------------------------------------------------------


def test_decay_halves_at_half_life():
    article = make("a", 1.0, hours_ago=6)
    assert aggregate.decay_weight(article, NOW, half_life_hours=6.0) == pytest.approx(0.5)


def test_recent_article_dominates_older_one():
    point, _ = aggregate.compute(
        [make("new", 1.0, 0), make("old", -1.0, 12)],
        NOW,
        window_hours=24,
        half_life_hours=6,
        confidence_scale=8,
    )
    # 12時間前 = 重み0.25 なので (1.0 - 0.25) / 1.25 = 0.6
    assert point.score == pytest.approx(0.6)
    assert point.n == 2


def test_articles_outside_window_are_excluded():
    point, _ = aggregate.compute(
        [make("stale", 1.0, 48)], NOW, window_hours=24, half_life_hours=6, confidence_scale=8
    )
    assert point.n == 0
    assert point.score == 0.0
    assert point.confidence == 0.0


def test_relevance_scales_weight():
    article = make("a", 1.0, hours_ago=0, relevance=0.25)
    assert aggregate.decay_weight(article, NOW, half_life_hours=6.0) == pytest.approx(0.25)


def test_unscored_articles_are_ignored():
    unscored = make("x", 0.0, 0)
    unscored.score = None
    point, _ = aggregate.compute(
        [unscored, make("y", 0.8, 0)], NOW, window_hours=24, half_life_hours=6, confidence_scale=8
    )
    assert point.n == 1
    assert point.score == pytest.approx(0.8)


def test_contributors_sorted_by_absolute_impact():
    _, contributors = aggregate.compute(
        [make("weak", 0.1, 0), make("strong", -0.9, 0)],
        NOW,
        window_hours=24,
        half_life_hours=6,
        confidence_scale=8,
    )
    assert contributors[0][0].id == "strong"


def test_confidence_grows_with_article_count():
    few, _ = aggregate.compute([make("a", 0.5, 0)], NOW, window_hours=24, half_life_hours=6, confidence_scale=8)
    many, _ = aggregate.compute(
        [make(str(i), 0.5, 0) for i in range(20)],
        NOW,
        window_hours=24,
        half_life_hours=6,
        confidence_scale=8,
    )
    assert few.confidence < many.confidence < 1.0


def test_future_articles_do_not_leak_into_past_points():
    # 1時間前の時点を計算するとき、その後に出た記事は含めない
    articles = [make("past", 1.0, 2), make("future", -1.0, 0)]
    point, _ = aggregate.compute(
        articles,
        NOW - timedelta(hours=1),
        window_hours=24,
        half_life_hours=6,
        confidence_scale=8,
    )
    assert point.n == 1
    assert point.score == pytest.approx(1.0)


def test_rebuild_series_covers_window():
    series = aggregate.rebuild_series(
        [make("a", 0.5, 1)], NOW, window_hours=2, half_life_hours=6, confidence_scale=8
    )
    assert len(series) == 25  # 2時間 / 5分 + 1
    assert series[0].t == NOW - timedelta(hours=2)
    assert series[-1].t == NOW


@pytest.mark.parametrize(
    ("score", "expected"),
    [(-0.9, "弱気"), (-0.3, "やや弱気"), (0.0, "中立"), (0.3, "やや強気"), (0.8, "強気")],
)
def test_labels(score, expected):
    assert aggregate.label(score) == expected


# --- 重複排除 -------------------------------------------------------------


def test_split_new_separates_known_ids():
    fresh, known = dedupe.split_new([make("a", 0.1, 0), make("b", 0.1, 0)], {"a"})
    assert [a.id for a in fresh] == ["b"]
    assert [a.id for a in known] == ["a"]


def test_split_new_collapses_duplicate_ids_in_one_batch():
    fresh, _ = dedupe.split_new([make("a", 0.1, 0), make("a", 0.1, 0)], set())
    assert len(fresh) == 1


def test_near_duplicate_titles_keep_earliest():
    articles = [
        make("late", 0.5, 1, title="Fed holds rates steady!"),
        make("first", 0.5, 2, title="fed holds rates steady"),
    ]
    kept = dedupe.drop_near_duplicates(articles)
    assert [a.id for a in kept] == ["first"]


def test_near_duplicates_outside_window_are_both_kept():
    articles = [
        make("a", 0.5, 0, title="Fed holds rates steady"),
        make("b", 0.5, 12, title="Fed holds rates steady"),
    ]
    assert len(dedupe.drop_near_duplicates(articles, window=timedelta(hours=6))) == 2


def test_prune_cache_drops_old_entries():
    cache = {"old": make("old", 0.1, 72).to_dict(), "new": make("new", 0.1, 1).to_dict()}
    assert set(dedupe.prune_cache(cache, NOW, retention_hours=48)) == {"new"}


def test_canonical_id_ignores_tracking_params():
    assert canonical_id("https://a.com/x?utm_source=t", "T") == canonical_id("https://a.com/x", "T")


# --- スコア化 -------------------------------------------------------------


def test_passthrough_clamps_and_tags():
    article = make("a", 1.8, 0)
    PassthroughScorer().score([article])
    assert article.score == 1.0
    assert article.scored_by == "source"


def test_passthrough_leaves_unscored_alone():
    article = make("a", 0.0, 0)
    article.score = None
    PassthroughScorer().score([article])
    assert article.score is None


# --- APITube マッピング ---------------------------------------------------


def test_to_article_maps_nested_sentiment():
    article = to_article(
        {
            "id": "abc",
            "title": "Stocks rally",
            "href": "https://news.example/1",
            "description": "Markets closed higher.",
            "published_at": "2026-08-14T11:30:00Z",
            "source": {"domain": "news.example"},
            "sentiment": {"overall": {"score": 0.42, "polarity": "positive"}},
        }
    )
    assert article is not None
    assert (article.id, article.score, article.source) == ("abc", 0.42, "news.example")
    assert article.published_at == datetime(2026, 8, 14, 11, 30, tzinfo=timezone.utc)


def test_to_article_falls_back_to_polarity_only():
    article = to_article(
        {
            "title": "Selloff deepens",
            "url": "https://news.example/2",
            "published_at": "2026-08-14T11:00:00Z",
            "sentiment": {"polarity": "negative"},
        }
    )
    assert article is not None and article.score == -0.5
    assert article.id  # URLから生成される


def test_to_article_rejects_incomplete_items():
    assert to_article({"title": "no date"}) is None
    assert to_article({"published_at": "2026-08-14T11:00:00Z"}) is None


# --- モデルの往復 ---------------------------------------------------------


def test_article_round_trip_through_json():
    original = make("a", 0.25, 3)
    restored = Article.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.published_at == original.published_at
    assert restored.score == original.score


def test_article_from_dict_ignores_unknown_keys():
    payload = make("a", 0.1, 0).to_dict() | {"future_field": 1}
    assert Article.from_dict(payload).id == "a"


# --- 設定 -----------------------------------------------------------------


def test_s3_keys_are_namespaced_by_index(monkeypatch):
    monkeypatch.setenv("INDEX_ID", "N225")
    monkeypatch.setenv("DATA_PREFIX", "v1/")
    cfg = Config()
    assert cfg.key_latest() == "v1/data/N225/latest.json"
    assert cfg.key_history("2026-08-14") == "v1/data/N225/history/2026-08-14.json"
    assert cfg.key_cache() == "v1/state/N225/articles.json"
