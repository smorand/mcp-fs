"""MinIO / S3 blob backend: content-addressed byte storage, one bucket per volume.

Objects are keyed by their sha256, so identical content is stored once. Partial
reads use S3 ``Range`` requests, which is the "fast read" mechanism (no manual
block chunking). All calls are async via aioboto3.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aioboto3
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mcp_fs.models import BlobConfig

logger = logging.getLogger(__name__)

_ALREADY_OWNED = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})
_NOT_FOUND = frozenset({"404", "NoSuchKey", "NoSuchBucket", "NotFound"})


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


class MinioBlobStore:
    """Content-addressed blob store over an S3-compatible bucket."""

    __slots__ = ("_bucket", "_config", "_session")

    def __init__(self, config: BlobConfig, bucket: str) -> None:
        self._config = config
        self._bucket = bucket
        self._session = aioboto3.Session()

    def _client(self) -> Any:
        return self._session.client(
            "s3",
            endpoint_url=self._config.endpoint,
            aws_access_key_id=self._config.access_key,
            aws_secret_access_key=self._config.secret_key,
            region_name=self._config.region,
        )

    async def put(self, sha256: str, data: bytes) -> None:
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=sha256, Body=data)

    async def get(self, sha256: str, offset: int = 0, length: int | None = None) -> bytes:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": sha256}
        if offset or length is not None:
            end = "" if length is None else str(offset + length - 1)
            kwargs["Range"] = f"bytes={offset}-{end}"
        async with self._client() as s3:
            response = await s3.get_object(**kwargs)
            async with response["Body"] as stream:
                data: bytes = await stream.read()
                return data

    async def exists(self, sha256: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=sha256)
            except ClientError as exc:
                if _error_code(exc) in _NOT_FOUND:
                    return False
                raise
            return True

    async def delete(self, sha256: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=sha256)

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.create_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if _error_code(exc) not in _ALREADY_OWNED:
                    raise

    async def remove_bucket(self) -> None:
        async with self._client() as s3:
            try:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self._bucket):
                    objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                    if objects:
                        await s3.delete_objects(Bucket=self._bucket, Delete={"Objects": objects})
                await s3.delete_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if _error_code(exc) not in _NOT_FOUND:
                    raise
