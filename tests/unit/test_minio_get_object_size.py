"""MinIO get_object size verification and non-retryable error handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from minio.error import S3Error

from ragent.storage.minio_registry import MinioSiteRegistry


def _site(name: str = "__default__") -> dict:
    return {
        "name": name,
        "endpoint": "minio:9000",
        "access_key": "ak",
        "secret_key": "example_minio_secret_not_real",  # pragma: allowlist secret
        "bucket": "b",
        "read_only": False,
    }


def _registry(stub: MagicMock) -> MinioSiteRegistry:
    return MinioSiteRegistry.from_json(json.dumps([_site()]), minio_factory=lambda **_: stub)


def _resp(data: bytes) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = data
    return resp


def test_get_object_passes_when_size_matches() -> None:
    stub = MagicMock()
    stub.get_object.return_value = _resp(b"hello world")
    out = _registry(stub).get_object("__default__", "k", expected_size=11)
    assert out == b"hello world"


def test_get_object_raises_on_size_mismatch() -> None:
    stub = MagicMock()
    stub.get_object.return_value = _resp(b"hello")  # 5 bytes
    reg = _registry(stub)
    with pytest.raises(OSError, match="size mismatch"):
        reg.get_object("__default__", "k", expected_size=11)


def test_get_object_without_expected_size_skips_check() -> None:
    """Backward compat: callers that didn't supply expected_size keep working."""
    stub = MagicMock()
    stub.get_object.return_value = _resp(b"hello")
    out = _registry(stub).get_object("__default__", "k")
    assert out == b"hello"


def _s3err(code: str) -> S3Error:
    return S3Error(
        code=code,
        message="error",
        resource="/bucket/key",
        request_id="r",
        host_id="h",
        response=MagicMock(status=403, headers={}, text=""),
    )


@pytest.mark.parametrize(
    "code",
    [
        "NoSuchKey",
        "NoSuchBucket",
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidBucketName",
        "MethodNotAllowed",
    ],
)
def test_get_object_non_retryable_s3error_raises_immediately(code: str) -> None:
    """Non-retryable S3 errors must surface immediately without retry."""
    stub = MagicMock()
    stub.get_object.side_effect = _s3err(code)
    reg = _registry(stub)
    with pytest.raises(S3Error) as exc_info:
        reg.get_object("__default__", "k")
    assert exc_info.value.code == code
    # Exactly one call — no retry
    assert stub.get_object.call_count == 1
