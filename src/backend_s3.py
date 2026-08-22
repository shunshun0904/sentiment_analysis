"""保存先バックエンド: S3。

AWS Lambda で使う。`infra/template.yaml` が STORE_BACKEND=s3 を渡す。
boto3 の import はこのファイルに閉じてあるので、fs バックエンドで動かすときは
boto3 が入っていなくてよい。
"""

# 注釈を文字列のまま扱う。`X | None` は Python 3.10 以降の書き方で、
# 3.9 で import すると TypeError になる（AWS CloudShell の python3 が 3.9）。
from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

import config

_s3 = boto3.client("s3")


def read_bytes(key: str) -> bytes | None:
    try:
        obj = _s3.get_object(Bucket=config.S3_BUCKET, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return obj["Body"].read()


def write_bytes(key: str, data: bytes, content_type: str) -> None:
    _s3.put_object(
        Bucket=config.S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl="max-age=60",
    )


def get_api_key() -> str:
    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=config.AV_API_KEY_SSM_PATH, WithDecryption=True)
    return resp["Parameter"]["Value"]
