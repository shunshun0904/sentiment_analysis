"""スコア算出インタフェース。

フェーズ1(API付属スコア)→ フェーズ2(Claude Haiku 4.5)の差し替えは
このクラスの実装を1つ足すだけで済むようにしてある。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Article


class Scorer(ABC):
    """新着記事にセンチメントスコアを付ける。

    実装は `articles` を破壊的に更新してよい(呼び出し側は戻り値を使う)。
    スコアを付けられなかった記事は score=None のまま返し、集計側で除外する。
    """

    name: str = "base"

    @abstractmethod
    def score(self, articles: list[Article]) -> list[Article]:
        raise NotImplementedError


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
