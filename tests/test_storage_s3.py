"""Tests for the S3/MinIO storage target.

Requires a local MinIO instance.  Start one with:

    docker run -p 9000:9000 -p 9001:9001 \\
        -e MINIO_ROOT_USER=minioadmin \\
        -e MINIO_ROOT_PASSWORD=minioadmin \\
        quay.io/minio/minio server /data --console-address ":9001"

These tests are skipped automatically when the MINIO_ENDPOINT environment
variable is not set or the MinIO server is unreachable, so the CI suite
still passes without Docker.

Usage:
    MINIO_ENDPOINT=http://localhost:9000 pytest tests/test_storage_s3.py
"""

import os
import pytest


_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "")
_BUCKET   = os.environ.get("MINIO_BUCKET", "test-photobooth")
_ACCESS   = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
_SECRET   = os.environ.get("MINIO_SECRET_KEY", "minioadmin")


def _is_minio_available() -> bool:
    if not _ENDPOINT:
        return False
    try:
        import urllib.request
        urllib.request.urlopen(_ENDPOINT + "/minio/health/live", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_minio_available(),
    reason="MinIO not available (set MINIO_ENDPOINT env var to enable)",
)


def _ensure_bucket(s3_client, bucket: str) -> None:
    try:
        s3_client.create_bucket(Bucket=bucket)
    except Exception:
        pass  # bucket may already exist


def _make_jpeg_bytes() -> bytes:
    from PIL import Image
    import io
    img = Image.new("RGB", (10, 10), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def s3_client():
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        endpoint_url=_ENDPOINT,
        aws_access_key_id=_ACCESS,
        aws_secret_access_key=_SECRET,
        region_name="us-east-1",
    )
    _ensure_bucket(client, _BUCKET)
    return client


def test_upload_stores_object(s3_client):
    from storage.s3_minio import StorageTarget

    target = StorageTarget({
        "endpoint_url": _ENDPOINT,
        "bucket": _BUCKET,
        "access_key": _ACCESS,
        "secret_key": _SECRET,
        "prefix": "tests/",
    })
    uri = target.upload(_make_jpeg_bytes(), {"shots": 1})
    assert uri.startswith(_ENDPOINT)
    assert _BUCKET in uri


def test_uploaded_object_is_retrievable(s3_client):
    from storage.s3_minio import StorageTarget

    target = StorageTarget({
        "endpoint_url": _ENDPOINT,
        "bucket": _BUCKET,
        "access_key": _ACCESS,
        "secret_key": _SECRET,
        "prefix": "tests/",
    })
    image_bytes = _make_jpeg_bytes()
    uri = target.upload(image_bytes, {})

    # Extract key from URI: http://host/bucket/key
    key = "/".join(uri.split("/")[4:])
    response = s3_client.get_object(Bucket=_BUCKET, Key=key)
    downloaded = response["Body"].read()
    assert downloaded == image_bytes


def test_is_configured_requires_bucket_and_keys():
    from storage.s3_minio import StorageTarget

    assert StorageTarget({"bucket": "b", "access_key": "k", "secret_key": "s"}).is_configured() is True
    assert StorageTarget({"bucket": "", "access_key": "k", "secret_key": "s"}).is_configured() is False
    assert StorageTarget({"bucket": "b", "access_key": "",  "secret_key": "s"}).is_configured() is False
