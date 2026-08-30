"""Tests for the compositor components.

Uses only in-process Pillow calls — no camera, no network, no filesystem
writes except the /tmp directory.
"""

import io
import json
import os
import pytest

from PIL import Image


# ── Helpers ───────────────────────────────────────────────────────────

def _make_jpeg(width: int, height: int, color=(200, 50, 50)) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# ── Passthrough compositor ────────────────────────────────────────────

class TestPassthroughCompositor:
    def _compositor(self):
        from compositor.passthrough import Compositor
        return Compositor()

    def test_returns_last_frame(self):
        comp = self._compositor()
        frames = [_make_jpeg(100, 100, (r, 0, 0)) for r in (50, 100, 150)]
        result = comp.compose(frames, {})
        # Result must equal the last frame byte-for-byte
        assert result == frames[-1]

    def test_single_frame(self):
        comp = self._compositor()
        frame = _make_jpeg(200, 200)
        result = comp.compose([frame], {})
        assert result == frame

    def test_empty_frames_raises(self):
        comp = self._compositor()
        with pytest.raises(ValueError, match="empty"):
            comp.compose([], {})


# ── Grid layout compositor ────────────────────────────────────────────

class TestGridLayoutCompositor:
    LAYOUT_PATH = os.path.join(FIXTURES, "4shot_strip.json")

    def _compositor(self):
        from compositor.grid_layout import Compositor
        return Compositor()

    def _layout_config(self, tmp_path=None) -> dict:
        layout_path = self.LAYOUT_PATH
        return {
            "template_map": layout_path,
            "output_format": "JPEG",
            "output_quality": 85,
        }

    def test_compose_4_frames_correct_canvas_size(self, tmp_path):
        comp = self._compositor()
        frames = [_make_jpeg(100, 100, (i * 50, 0, 0)) for i in range(1, 5)]
        cfg = self._layout_config()
        result = comp.compose(frames, cfg)

        # Open result and verify canvas dimensions from the layout file
        with open(self.LAYOUT_PATH) as fh:
            layout = json.load(fh)
        expected_w, expected_h = layout["canvas_size"]

        img = Image.open(io.BytesIO(result))
        assert img.size == (expected_w, expected_h)

    def test_compose_fewer_frames_than_slots(self, tmp_path):
        """If fewer frames than slots are provided, no exception is raised."""
        comp = self._compositor()
        frames = [_make_jpeg(100, 100)]  # only 1 frame, 4 slots
        cfg = self._layout_config()
        result = comp.compose(frames, cfg)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_output_is_valid_jpeg(self, tmp_path):
        comp = self._compositor()
        frames = [_make_jpeg(100, 100) for _ in range(4)]
        cfg = self._layout_config()
        result = comp.compose(frames, cfg)
        assert result[:3] == b"\xff\xd8\xff"

    def test_missing_template_map_raises(self):
        comp = self._compositor()
        with pytest.raises(FileNotFoundError):
            comp.compose([_make_jpeg(100, 100)], {"template_map": "/nonexistent/path.json"})

    def test_empty_frames_passthrough_raises(self):
        from compositor.passthrough import Compositor
        comp = Compositor()
        with pytest.raises(ValueError):
            comp.compose([], {})
