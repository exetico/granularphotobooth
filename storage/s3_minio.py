"""S3 / MinIO object storage target.

Uses ``boto3`` with an optional custom ``endpoint_url`` so it works with
both AWS S3 and a self-hosted MinIO instance.

For local development/testing, spin up MinIO with:
    docker run -p 9000:9000 -p 9001:9001 \\
        -e MINIO_ROOT_USER=minioadmin \\
        -e MINIO_ROOT_PASSWORD=minioadmin \\
        quay.io/minio/minio server /data --console-address ":9001"

Then set in config.json:
    "endpoint_url": "http://localhost:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO

from storage.base import StorageTarget as _Base

logger = logging.getLogger(__name__)


class StorageTarget(_Base):
    """Upload the captured image to S3-compatible object storage."""

    def is_configured(self) -> bool:
        return bool(
            self._config.get("bucket")
            and self._config.get("access_key")
            and self._config.get("secret_key")
        )

    def upload(self, image_bytes: bytes, metadata: dict) -> str:
        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:
            raise RuntimeError("boto3 is not installed — S3/MinIO storage unavailable") from exc

        bucket: str = self._config["bucket"]
        prefix: str = self._config.get("prefix", "")
        endpoint_url: str | None = self._config.get("endpoint_url") or None

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_uuid = str(uuid.uuid4()).replace("-", "")[:8]
        key = f"{prefix}photo_{timestamp}_{short_uuid}.jpg"

        session = boto3.Session(
            aws_access_key_id=self._config["access_key"],
            aws_secret_access_key=self._config["secret_key"],
            region_name=self._config.get("region", "us-east-1"),
        )
        s3 = session.client("s3", endpoint_url=endpoint_url)

        try:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=BytesIO(image_bytes),
                ContentType="image/jpeg",
                Metadata={k: str(v) for k, v in metadata.items()},
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"S3 upload failed: {exc}") from exc

        if endpoint_url:
            uri = f"{endpoint_url.rstrip('/')}/{bucket}/{key}"
        else:
            uri = f"s3://{bucket}/{key}"

        logger.info("Uploaded to S3/MinIO: %s", uri)
        return uri
