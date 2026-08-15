"""Object storage.

One thin wrapper over any S3-compatible endpoint, so MinIO (local), Cloudflare R2
(cloud v2) and AWS S3 are interchangeable via config alone — this is what makes the
"local v1 then lift to cloud" path a config change rather than a rewrite.

Uploads go **browser -> object store via presigned URL**, never through the API
(CLAUDE.md §8), so the presign helpers here are the API's whole involvement in a
10 GB upload.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from sp_core.config import get_settings


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    key: str
    method: str = "PUT"


def _client(endpoint: str) -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        # SigV4 + path-style: MinIO and R2 both require it, and virtual-host style
        # would need DNS we don't control locally.
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def internal_client() -> Any:
    """Client for server-side reads/writes (API and workers)."""
    return _client(get_settings().s3_endpoint)


@lru_cache
def public_client() -> Any:
    """Client used only to mint URLs the browser will follow."""
    return _client(get_settings().s3_public_endpoint)


def ensure_bucket() -> None:
    """Create the bucket if missing. Safe to call on every boot."""
    settings = get_settings()
    client = internal_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        # Racing with another process that just created it is fine.
        with contextlib.suppress(ClientError):
            client.create_bucket(Bucket=settings.s3_bucket)


def raw_upload_key(user_id: UUID, upload_id: UUID, filename: str = "export.zip") -> str:
    safe = filename.replace("/", "_").replace("\\", "_")[:120]
    return f"raw/{user_id}/{upload_id}/{safe}"


def stream_key(user_id: UUID, activity_id: UUID) -> str:
    return f"streams/{user_id}/{activity_id}.parquet"


def presign_put(
    key: str, content_type: str = "application/zip", expires_s: int = 3600
) -> PresignedUpload:
    url = public_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires_s,
    )
    return PresignedUpload(url=url, key=key)


def presign_get(key: str, expires_s: int = 900) -> str:
    """Short-lived, scoped read URL. Buckets are private (CLAUDE.md §8)."""
    url: str = public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=expires_s,
    )
    return url


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    internal_client().put_object(
        Bucket=get_settings().s3_bucket, Key=key, Body=data, ContentType=content_type
    )


def get_bytes(key: str) -> bytes:
    response = internal_client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return bytes(response["Body"].read())


def object_exists(key: str) -> bool:
    try:
        internal_client().head_object(Bucket=get_settings().s3_bucket, Key=key)
    except ClientError:
        return False
    return True


def object_size(key: str) -> int | None:
    try:
        response = internal_client().head_object(Bucket=get_settings().s3_bucket, Key=key)
    except ClientError:
        return None
    return int(response["ContentLength"])


def delete_prefix(prefix: str) -> int:
    """Delete every object under a prefix. Used by account/Strava deletion."""
    client = internal_client()
    bucket = get_settings().s3_bucket
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        client.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": item["Key"]} for item in contents]}
        )
        deleted += len(contents)
    return deleted
