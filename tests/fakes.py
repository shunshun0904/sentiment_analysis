"""テスト用の足場。

`store` は import 時に boto3 クライアントを作るので、boto3 が無い環境
（この開発コンテナ・CI）では素直に import できない。偽の boto3 を
sys.modules に差し込んでから import する。

同時に、S3 をインメモリの辞書に置き換える。ネットワークもAWSも要らない。
"""
from __future__ import annotations

import sys
import types


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _NoSuchKey(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    """put/get だけの S3。中身は self.objects に文字列で持つ。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_args: list[dict] = []

    def get_object(self, Bucket: str, Key: str):  # noqa: N803
        if Key not in self.objects:
            raise _NoSuchKey()
        return {"Body": _FakeBody(self.objects[Key])}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs["Body"]
        self.put_args.append(kwargs)
        return {}


class FakeSSM:
    def __init__(self, value: str = "test-key") -> None:
        self.value = value

    def get_parameter(self, Name: str, WithDecryption: bool = False):  # noqa: N803
        return {"Parameter": {"Value": self.value}}


def install(bucket: str = "test-bucket") -> FakeS3:
    """偽 boto3 を仕込んで `store` を import できる状態にし、FakeS3 を返す。"""
    s3 = FakeS3()

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name, *a, **kw: s3 if name == "s3" else FakeSSM()

    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _NoSuchKey
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions

    sys.modules["boto3"] = fake_boto3
    sys.modules["botocore"] = botocore
    sys.modules["botocore.exceptions"] = exceptions

    import os

    os.environ.setdefault("S3_BUCKET", bucket)
    return s3
