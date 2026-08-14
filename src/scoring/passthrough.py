"""フェーズ1: データソース付属のスコアをそのまま採用する(追加コスト0円)。"""

from __future__ import annotations

from ..models import Article
from .base import Scorer, clamp


class PassthroughScorer(Scorer):
    name = "passthrough"

    def score(self, articles: list[Article]) -> list[Article]:
        for article in articles:
            if article.score is None:
                continue
            article.score = clamp(float(article.score))
            article.scored_by = article.scored_by or "source"
            article.scores.setdefault(article.scored_by, article.score)
        return articles
