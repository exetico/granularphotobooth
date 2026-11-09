"""FTP / NAS storage target.

Uses the Python standard library ``ftplib`` — no extra dependencies.
Works with any FTP server, Synology NAS, or similar network storage.
"""

import ftplib
import logging
import uuid
from datetime import datetime, timezone
from io import BytesIO

from storage.base import StorageTarget as _Base

logger = logging.getLogger(__name__)


def _remote_name(metadata: dict, timestamp: str, short_uuid: str) -> str:
    """Same naming as local_disk when session info is available."""
    session = metadata.get("session_id")
    if not session:
        return f"photo_{timestamp}_{short_uuid}.jpg"
    if metadata.get("type") == "composite":
        return f"{session}_composite.jpg"
    return f"{session}_shot{int(metadata.get('frame', 0) or 0)}.jpg"


class StorageTarget(_Base):
    """Upload the captured image via FTP to a network-attached storage."""

    def is_configured(self) -> bool:
        return bool(self._config.get("host"))

    def upload(self, image_bytes: bytes, metadata: dict) -> str:
        host: str = self._config["host"]
        port: int = int(self._config.get("port", 21))
        username: str = self._config.get("username", "")
        password: str = self._config.get("password", "")
        remote_dir: str = self._config.get("remote_dir", "/")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_uuid = str(uuid.uuid4()).replace("-", "")[:8]
        filename = _remote_name(metadata, timestamp, short_uuid)

        remote_path = remote_dir.rstrip("/") + "/" + filename

        try:
            with ftplib.FTP() as ftp:
                ftp.connect(host=host, port=port, timeout=30)
                ftp.login(user=username, passwd=password)
                ftp.storbinary(f"STOR {remote_path}", BytesIO(image_bytes))
        except ftplib.all_errors as exc:
            raise RuntimeError(f"FTP upload to {host} failed: {exc}") from exc

        uri = f"ftp://{host}{remote_path}"
        logger.info("Uploaded via FTP: %s", uri)
        return uri
