"""OpenCV webcam backend — the default, cross-platform camera.

Works with any built-in webcam, USB camera, or virtual camera.  Also
accepts a path to a video file as ``device_index`` for headless testing
in CI environments.
"""

import logging
from typing import Union

from cameras.base import CameraBackend as _Base

logger = logging.getLogger(__name__)


class CameraBackend(_Base):
    """Capture frames from a V4L2 / DirectShow / AVFoundation webcam."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._cap = None

    @classmethod
    def is_available(cls) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            logger.warning("opencv-python is not installed — webcam backend unavailable")
            return False

    def initialize(self) -> None:
        import cv2

        device: Union[int, str] = self._config.get("device_index", 0)
        resolution = self._config.get("resolution", [1280, 720])

        self._cap = cv2.VideoCapture(device)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video device: {device!r}")

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        logger.info("Webcam opened: device=%r, resolution=%s", device, resolution)

    def capture(self) -> bytes:
        import cv2

        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("Webcam is not initialised")

        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read frame from webcam")

        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")

        return bytes(buf)

    def get_preview_frame(self) -> bytes:
        import cv2

        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("Webcam is not initialised")

        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read preview frame")

        preview_res = self._config.get("preview_resolution", [640, 360])
        small = cv2.resize(frame, (preview_res[0], preview_res[1]))

        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            raise RuntimeError("Failed to encode preview frame")

        return bytes(buf)

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Webcam released")
