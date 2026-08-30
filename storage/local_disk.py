"""Local disk storage target.

Saves the final image to a local directory with a configurable filename
pattern.  The output directory is created automatically if it does not
exist.

Filename placeholders:
  {timestamp}  — ISO-8601 UTC timestamp (``20260830T120000``)
  {uuid}       — Random UUID (first 8 characters)
"""

import logging
import os
import uuid
from datetime import datetime, timezone

from storage.base import StorageTarget as _Base

logger = logging.getLogger(__name__)


class StorageTarget(_Base):
    """Write the captured image to the local filesystem."""

    def is_configured(self) -> bool:
        return bool(self._config.get("output_dir"))

    def upload(self, image_bytes: bytes, metadata: dict) -> str:
        output_dir: str = self._config.get("output_dir", "output/")
        filename_fmt: str = self._config.get("filename_format", "photo_{timestamp}_{uuid}.jpg")

        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_uuid = str(uuid.uuid4()).replace("-", "")[:8]
        filename = filename_fmt.format(timestamp=timestamp, uuid=short_uuid)

        path = os.path.join(output_dir, filename)
        with open(path, "wb") as fh:
            fh.write(image_bytes)

        logger.info("Saved locally: %s (%d bytes)", path, len(image_bytes))
        return path
