"""Cloudflare R2 object store (S3-compatible API via boto3)."""
from __future__ import annotations

import logging

from .base import ObjectStore, StorageError

log = logging.getLogger(__name__)

MISSING_CODES = {"NoSuchKey", "404", "NotFound"}


def build_r2_client(endpoint_url: str, access_key_id: str, secret_access_key: str):
    import boto3
    from botocore.config import Config

    kwargs = dict(
        signature_version="s3v4",
        retries={"max_attempts": 5, "mode": "standard"},
        connect_timeout=15,
        read_timeout=60,
    )
    try:
        # boto3 >= 1.36 sends extra checksums by default; only send them when required for R2 compatibility.
        config = Config(request_checksum_calculation="when_required",
                        response_checksum_validation="when_required", **kwargs)
    except TypeError:
        config = Config(**kwargs)
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=config,
    )


def _error_code(exc) -> str:
    response = getattr(exc, "response", None) or {}
    return str(response.get("Error", {}).get("Code", "")) or str(
        response.get("ResponseMetadata", {}).get("HTTPStatusCode", "")
    )


class R2Store(ObjectStore):
    name = "r2"

    def __init__(self, bucket: str, client=None, *, endpoint_url=None, access_key_id=None, secret_access_key=None):
        self.bucket = bucket
        self.client = client or build_r2_client(endpoint_url, access_key_id, secret_access_key)

    def _wrap(self, action: str, exc: Exception) -> StorageError:
        return StorageError(f"R2 {action} failed: {type(exc).__name__} {_error_code(exc)}".strip())

    def get_bytes(self, key: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:  # botocore ClientError / connection errors
            if _error_code(exc) in MISSING_CODES:
                return None
            raise self._wrap(f"get {key}", exc) from exc

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str | None:
        try:
            response = self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except Exception as exc:
            raise self._wrap(f"put {key}", exc) from exc
        etag = response.get("ETag") if isinstance(response, dict) else None
        return etag.strip('"') if etag else None

    def head(self, key: str) -> dict | None:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _error_code(exc) in MISSING_CODES:
                return None
            raise self._wrap(f"head {key}", exc) from exc
        return {"etag": str(response.get("ETag", "")).strip('"'), "size": int(response.get("ContentLength", 0))}

    def copy(self, src: str, dst: str) -> None:
        try:
            self.client.copy_object(Bucket=self.bucket, Key=dst, CopySource={"Bucket": self.bucket, "Key": src})
        except Exception as exc:
            raise self._wrap(f"copy {src}", exc) from exc

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        try:
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
                if token:
                    kwargs["ContinuationToken"] = token
                response = self.client.list_objects_v2(**kwargs)
                keys.extend(obj["Key"] for obj in response.get("Contents", []) or [])
                if not response.get("IsTruncated"):
                    break
                token = response.get("NextContinuationToken")
                if not token:
                    break
        except Exception as exc:
            raise self._wrap(f"list {prefix}", exc) from exc
        return keys

    def delete_keys(self, keys: list[str]) -> int:
        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i:i + 1000]
            try:
                response = self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True}
                )
            except Exception as exc:
                raise self._wrap("delete", exc) from exc
            errors = response.get("Errors", []) if isinstance(response, dict) else []
            deleted += len(batch) - len(errors)
        return deleted
