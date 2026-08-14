"""ドメインモデル。

外部APIのレスポンス形式に依存しない中間表現を定義する。
データソースを差し替えても、以降のパイプライン(重複排除→スコア化→集計)は無変更で動く。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _parse_dt(value: Any) -> datetime:
    """ISO8601 文字列 / datetime を aware な UTC datetime に正規化する。"""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        # "2026-08-14T12:00:00Z" 形式を fromisoformat が扱える形に寄せる
        text = re.sub(r"Z$", "+00:00", text)
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    """UTC の ISO8601(秒精度・Zサフィックス)に整形する。"""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_id(url: str, title: str) -> str:
    """記事の安定ID。APIがIDを返さない場合のフォールバック。

    URLのクエリ(utm_* 等)は無視し、同一記事が別IDにならないようにする。
    """
    base = re.sub(r"[?#].*$", "", (url or "").strip().lower())
    key = base or re.sub(r"\s+", " ", (title or "").strip().lower())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class Article:
    """スコア化の対象となるニュース記事1件。"""

    id: str
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
    language: str = "en"
    #: -1.0(弱気)〜 +1.0(強気)。未スコアなら None
    score: float | None = None
    #: 指数との関連度 0.0〜1.0。加重平均の重みに乗る
    relevance: float = 1.0
    #: "apitube" / "claude" など、スコアの出所
    scored_by: str = ""
    #: フェーズ3(並走比較)用。{"apitube": 0.2, "claude": -0.1}
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_at"] = iso(self.published_at)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Article":
        d = dict(d)
        d["published_at"] = _parse_dt(d["published_at"])
        known = {f for f in cls.__dataclass_fields__}  # 未知キーは無視(前方互換)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SentimentPoint:
    """ある時刻における指数センチメントの1点。"""

    t: datetime
    score: float
    #: 集計に使った記事数
    n: int
    #: 重みの合計。信頼度の根拠として保持する
    weight: float
    #: 0.0〜1.0。記事が少ない/古いほど下がる
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": iso(self.t),
            "score": round(self.score, 4),
            "n": self.n,
            "weight": round(self.weight, 4),
            "confidence": round(self.confidence, 3),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SentimentPoint":
        return cls(
            t=_parse_dt(d["t"]),
            score=float(d["score"]),
            n=int(d["n"]),
            weight=float(d.get("weight", 0.0)),
            confidence=float(d.get("confidence", 0.0)),
        )
