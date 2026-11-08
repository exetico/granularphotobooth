"""OpenCV webcam backend — the default, cross-platform camera.

Works with any built-in webcam, USB camera, capture card or virtual camera
on Linux (V4L2) and macOS (AVFoundation).  ``device_index`` may also be a
path to a video file, which is handy for headless testing.
"""

import logging
import sys
from typing import Union

from cameras.base import CameraBackend as _Base

logger = logging.getLogger(__name__)


class CameraBackend(_Base):
    """Capture frames from a V4L2 / AVFoundation webcam."""

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

    def _open(self, device):
        import cv2

        api = cv2.CAP_ANY
        if isinstance(device, int):
            if sys.platform == "darwin":
                api = cv2.CAP_AVFOUNDATION
            elif sys.platform.startswith("linux"):
                api = cv2.CAP_V4L2
        cap = cv2.VideoCapture(device, api)
        if not cap.isOpened() and api != cv2.CAP_ANY:
            cap.release()
            cap = cv2.VideoCapture(device)
        return cap

    def initialize(self) -> None:
        import cv2

        device: Union[int, str] = self._config.get("device_index", 0)
        resolution = self._config.get("resolution", [1280, 720])

        self._cap = self._open(device)
        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            raise RuntimeError(f"Cannot open video device: {device!r}")

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        # Keep the driver buffer small so capture() returns a *current* frame
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Warm up: the first frames are often black / badly exposed
        ok = False
        for _ in range(int(self._config.get("warmup_frames", 5))):
            ok, _frame = self._cap.read()
        if not ok:
            self._cap.release()
            self._cap = None
            raise RuntimeError(f"Video device {device!r} opened but delivers no frames")

        actual = (
            int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        logger.info("Webcam opened: device=%r, requested=%s, actual=%s", device, resolution, actual)

    def _read(self):
        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("Webcam is not initialised")
        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read frame from webcam")
        return frame

    def capture(self) -> bytes:
        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("Webcam is not initialised")

        import cv2

        # Drop a buffered (possibly stale) frame so the photo is "now"
        self._cap.grab()
        frame = self._read()
        quality = int(self._config.get("jpeg_quality", 95))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buf.tobytes()

    def get_preview_frame(self) -> bytes:
        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError("Webcam is not initialised")

        import cv2

        frame = self._read()
        preview_res = self._config.get("preview_resolution", [640, 360])
        h, w = frame.shape[:2]
        # Keep the aspect ratio of the camera — never stretch the live view
        target_w = int(preview_res[0])
        target_h = max(1, int(h * target_w / max(1, w)))
        small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise RuntimeError("Failed to encode preview frame")
        return buf.tobytes()

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            finally:
                self._cap = None
            logger.info("Webcam released")
