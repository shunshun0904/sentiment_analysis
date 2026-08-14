"""ニュース取得インタフェース。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..models import Article


class NewsSource(ABC):
    """データソース共通インタフェース。

    実装は「直近 since 以降の記事を Article のリストで返す」ことだけを保証する。
    スコアが付いていれば Article.score に入れる(付いていなければ None のまま)。
    """

    #: ログ・メタ情報に載る識別子
    name: str = "base"

    @abstractmethod
    def fetch(self, since: datetime, limit: int) -> list[Article]:
        raise NotImplementedError


def http_get_json(
    url: str,
    params: dict[str, Any],
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """依存ゼロのGETヘルパ。Lambdaに追加パッケージを持ち込まないため urllib を使う。

    鍵はクエリではなくヘッダで渡す(アクセスログやリファラに残さないため)。
    """
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    all_headers = {"Accept": "application/json", "User-Agent": "market-sentiment/1.0"}
    all_headers.update(headers or {})
    req = urllib.request.Request(f"{url}?{qs}", headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        # 鍵が本文に混じることは無いが、URLは鍵を含まない形で出す
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
