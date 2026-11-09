"""IP / Android camera backend.

Reads an MJPEG or RTSP stream using OpenCV.  Configure the stream URL in
``config.json`` under ``camera.backends.ip_cam.stream_url``.

Example stream apps that work with this backend:
* IP Webcam (Android) — http://192.168.1.x:8080/video
* DroidCam — http://192.168.1.x:4747/mjpegfeed

If the app offers a full-resolution snapshot URL (IP Webcam: ``/photo.jpg``),
set ``snapshot_url`` and captures will use it instead of a stream frame.
"""

import logging
import re
import urllib.request

from cameras.base import CameraBackend as _Base

logger = logging.getLogger(__name__)


class CameraBackend(_Base):
    """Capture frames from a network-connected IP camera / MJPEG stream."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._cap = None

    @classmethod
    def is_available(cls) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            logger.warning("opencv-python is not installed — ip_cam backend unavailable")
            return False

    def initialize(self) -> None:
        import cv2

        url: str = self._config.get("stream_url", "")
        if not url or re.search(r"\.x[:/]", url):  # unedited placeholder 192.168.1.x
            raise ValueError("ip_cam backend requires a real 'stream_url' in config")

        self._cap = cv2.VideoCapture(url)
        if not self._cap.isOpened():
            self._cap = None
            raise RuntimeError(f"Cannot open IP camera stream: {url!r}")
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        logger.info("IP camera stream opened: %s", url)

    def _frame_jpeg(self, quality: int) -> bytes:
        import cv2

        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("IP camera not initialised")
        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read frame from IP stream")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buf.tobytes()

    def capture(self) -> bytes:
        snapshot = self._config.get("snapshot_url")
        if snapshot:
            with urllib.request.urlopen(snapshot, timeout=10) as resp:  # noqa: S310
                data = resp.read()
            if data[:2] != b"\xff\xd8":
                raise RuntimeError("Snapshot URL did not return a JPEG")
            return data
        return self._frame_jpeg(95)

    def get_preview_frame(self) -> bytes:
        return self._frame_jpeg(75)

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("IP camera stream released")
