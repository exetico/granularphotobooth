"""IP / Android camera backend.

Reads an MJPEG or RTSP stream using OpenCV.  Configure the stream URL in
``config.json`` under ``camera.backends.ip_cam.stream_url``.

Example stream apps that work with this backend:
* IP Webcam (Android) — http://192.168.1.x:8080/video
* DroidCam — http://192.168.1.x:4747/mjpegfeed
"""

import logging

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
        if not url:
            raise ValueError("ip_cam backend requires 'stream_url' in config")

        self._cap = cv2.VideoCapture(url)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open IP camera stream: {url!r}")

        logger.info("IP camera stream opened: %s", url)

    def capture(self) -> bytes:
        import cv2

        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("IP camera not initialised")

        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read frame from IP stream")

        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")

        return bytes(buf)

    def get_preview_frame(self) -> bytes:
        return self.capture()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("IP camera stream released")
