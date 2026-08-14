"""S3 の読み書き。JSONの入出力と Cache-Control 制御をここに閉じ込める。"""

from __future__ import annotations

import gzip
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class S3Store:
    def __init__(self, bucket: str, client=None) -> None:
        import boto3  # Lambda ランタイムに同梱

        self.bucket = bucket
        self.client = client or boto3.client("s3")

    def get_json(self, key: str, default: Any = None) -> Any:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            return default
        except Exception as e:  # 404 相当を例外クラス名で吸収(スタブ実装対策)
            if type(e).__name__ in ("NoSuchKey", "404", "ClientError") and "NoSuchKey" in str(e):
                return default
            raise
        body = resp["Body"].read()
        if resp.get("ContentEncoding") == "gzip" or key.endswith(".gz"):
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))

    def put_json(self, key: str, payload: Any, *, max_age: int = 60, public: bool = True) -> None:
        """SPAが読む公開データは短めの Cache-Control、内部状態は no-store。"""
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cache_control = f"public, max-age={max_age}" if public else "no-store"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl=cache_control,
        )
        log.info("s3://%s/%s を更新しました (%d bytes)", self.bucket, key, len(body))
