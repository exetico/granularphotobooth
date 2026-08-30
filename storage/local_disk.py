"""Local disk storage target.

Saves images to separate directories depending on whether they are raw
individual shots or the final composite.  Both directories are created
automatically if they do not exist.

Configuration keys:
  raw_dir          — directory for individual raw frames (default: ``output/raw/``)
  composite_dir    — directory for composite/collage images (default: ``output/composites/``)
  filename_format  — filename pattern with ``{timestamp}`` and ``{uuid}`` placeholders

The ``metadata`` dict passed by the pipeline contains a ``type`` key:
  ``"raw"``       → image is written to ``raw_dir``
  ``"composite"`` → image is written to ``composite_dir``
  (anything else) → falls back to ``raw_dir``
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
        # Accept both old single-dir config and new split-dir config
        return bool(
            self._config.get("raw_dir")
            or self._config.get("composite_dir")
            or self._config.get("output_dir")  # backwards compat
        )

    def upload(self, image_bytes: bytes, metadata: dict) -> str:
        image_type: str = metadata.get("type", "raw")
        filename_fmt: str = self._config.get(
            "filename_format", "photo_{timestamp}_{uuid}.jpg"
        )

        if image_type == "composite":
            output_dir = self._config.get(
                "composite_dir",
                self._config.get("output_dir", "output/composites/"),
            )
        else:
            output_dir = self._config.get(
                "raw_dir",
                self._config.get("output_dir", "output/raw/"),
            )

        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_uuid = str(uuid.uuid4()).replace("-", "")[:8]
        filename = filename_fmt.format(timestamp=timestamp, uuid=short_uuid)

        path = os.path.join(output_dir, filename)
        with open(path, "wb") as fh:
            fh.write(image_bytes)

        logger.info("Saved locally (%s): %s (%d bytes)", image_type, path, len(image_bytes))
        return path
