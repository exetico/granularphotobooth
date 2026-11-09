"""Local disk storage target.

Saves images to separate directories depending on whether they are raw
individual shots or the final composite.  Both directories are created
automatically.  Writes are *atomic* (temp file + fsync + rename), so a
power cut never leaves a half-written JPEG behind, and existing files are
never overwritten.

Configuration keys:
  raw_dir          — directory for individual shots (default ``output/raw/``)
  composite_dir    — directory for composites (default ``output/composites/``)
  filename_format  — pattern; placeholders:
                       {session}   session id, e.g. 20261108-201502_ab12cd
                       {kind}      ``shot1`` … ``shotN`` or ``composite``
                       {shot}      shot number (0 for the composite)
                       {timestamp} local time, e.g. 20261108-201502
                       {uuid}      8 random hex characters
                     Default: ``{session}_{kind}.jpg``.  Formats without
                     {kind}/{shot} get ``_shotN`` appended for raw shots.

The ``metadata`` dict passed by the pipeline may contain ``type``
(``raw`` / ``composite``), ``frame`` (shot number) and ``session_id``.
"""

import logging
import os
import uuid
from datetime import datetime

from storage.base import StorageTarget as _Base

logger = logging.getLogger(__name__)


def atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class StorageTarget(_Base):
    """Write the captured image to the local filesystem."""

    is_local = True

    def is_configured(self) -> bool:
        return bool(
            self._config.get("raw_dir")
            or self._config.get("composite_dir")
            or self._config.get("output_dir")  # backwards compat
        )

    def directory_for(self, image_type: str) -> str:
        if image_type == "composite":
            return self._config.get("composite_dir", self._config.get("output_dir", "output/composites/"))
        return self._config.get("raw_dir", self._config.get("output_dir", "output/raw/"))

    def upload(self, image_bytes: bytes, metadata: dict) -> str:
        image_type: str = metadata.get("type", "raw")
        fmt: str = self._config.get("filename_format", "{session}_{kind}.jpg")
        output_dir = self.directory_for(image_type)
        os.makedirs(output_dir, exist_ok=True)

        now = datetime.now()
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        session = metadata.get("session_id") or f"{timestamp}_{short_uuid[:6]}"
        shot = int(metadata.get("frame", 0) or 0)
        kind = "composite" if image_type == "composite" else (f"shot{shot}" if shot else "shot")

        try:
            filename = fmt.format(
                timestamp=timestamp, uuid=short_uuid, session=session, kind=kind, shot=shot
            )
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Bad filename_format %r (%s) — using default", fmt, exc)
            filename = f"{session}_{kind}.jpg"

        if image_type != "composite" and shot and "{kind}" not in fmt and "{shot}" not in fmt:
            stem, ext = os.path.splitext(filename)
            filename = f"{stem}_shot{shot}{ext}"

        filename = os.path.basename(filename)  # never allow path tricks
        path = os.path.join(output_dir, filename)
        # Never overwrite an existing photo
        stem, ext = os.path.splitext(path)
        n = 1
        while os.path.exists(path):
            path = f"{stem}-{n}{ext}"
            n += 1

        atomic_write(path, image_bytes)
        logger.info("Saved locally (%s): %s (%d bytes)", image_type, path, len(image_bytes))
        return path
