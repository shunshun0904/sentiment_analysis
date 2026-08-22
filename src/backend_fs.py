"""保存先バックエンド: ローカルのファイル。

GitHub Actions で使う。キーはそのまま `config.DATA_DIR` 配下の相対パスになる。

    state/last_run.json  →  data/state/last_run.json

永続化は git が担う。ワークフローが実行後に data ブランチへコミットする。
"""

# 注釈を文字列のまま扱う。`X | None` は Python 3.10 以降の書き方で、
# 3.9 で import すると TypeError になる（AWS CloudShell の python3 が 3.9）。
from __future__ import annotations

import os

import config


def _path(key: str) -> str:
    return os.path.join(config.DATA_DIR, key)


def read_bytes(key: str) -> bytes | None:
    """無ければ None。S3 の NoSuchKey と同じ扱いにするため例外にしない。"""
    try:
        with open(_path(key), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_bytes(key: str, data: bytes, content_type: str) -> None:
    """content_type は S3 と署名を揃えるためだけに受ける。ここでは使わない
    ── 配信するのは GitHub Pages で、拡張子から自分で決めるため。"""
    path = _path(key)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def get_api_key() -> str:
    key = os.environ.get("AV_API_KEY", "")
    if not key:
        raise RuntimeError(
            "AV_API_KEY が空です。GitHub Actions では secrets.ALPHAVANTAGE_KEY を "
            "env に渡してください"
        )
    return key
