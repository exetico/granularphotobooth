"""Simulated camera — no hardware needed.

Generates colourful test images with a timestamp so you can try the whole
booth (countdown, capture, compositing, saving) on any computer, and so the
test-suite / CI can run end-to-end without a device.

Set ``"camera": {"backend": "dummy"}`` in config.json.
"""

import io
import logging
import math
import threading
import time
from datetime import datetime

from cameras.base import CameraBackend as _Base

logger = logging.getLogger(__name__)


class CameraBackend(_Base):
    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._open = False
        self._counter = 0
        self._lock = threading.Lock()
        # Test hooks: make the camera fail on purpose
        self.fail_next_captures = int(config.get("fail_next_captures", 0))
        self.capture_delay = float(config.get("capture_delay_seconds", 0.2))

    @classmethod
    def is_available(cls) -> bool:
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False

    def initialize(self) -> None:
        self._open = True
        logger.info("Dummy camera ready")

    def _render(self, size: tuple[int, int], label: str, quality: int) -> bytes:
        from PIL import Image, ImageDraw

        w, h = size
        t = time.time()
        hue = (t * 40) % 360
        r = int(127 + 127 * math.sin(math.radians(hue)))
        g = int(127 + 127 * math.sin(math.radians(hue + 120)))
        b = int(127 + 127 * math.sin(math.radians(hue + 240)))
        img = Image.new("RGB", (w, h), (r, g, b))
        draw = ImageDraw.Draw(img)
        # Moving circle so the live view visibly moves
        cx = int(w / 2 + (w / 3) * math.sin(t))
        rad = max(10, h // 6)
        draw.ellipse((cx - rad, h // 2 - rad, cx + rad, h // 2 + rad), fill=(255, 255, 255))
        text = f"{label}  {datetime.now().strftime('%H:%M:%S')}"
        draw.text((max(4, w // 40), max(4, h // 40)), text, fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def capture(self) -> bytes:
        if not self._open:
            raise RuntimeError("Dummy camera not initialised")
        if self.capture_delay:
            time.sleep(self.capture_delay)
        with self._lock:
            if self.fail_next_captures > 0:
                self.fail_next_captures -= 1
                raise RuntimeError("Simulated capture failure")
            self._counter += 1
            n = self._counter
        res = tuple(self._config.get("resolution", [1800, 1200]))
        return self._render(res, f"DUMMY SHOT #{n}", 90)

    def get_preview_frame(self) -> bytes:
        if not self._open:
            raise RuntimeError("Dummy camera not initialised")
        res = tuple(self._config.get("preview_resolution", [640, 426]))
        return self._render(res, "LIVE (dummy)", 70)

    def release(self) -> None:
        self._open = False
