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


# ── Overlay / repeat / EXIF ───────────────────────────────────────────

def _jpeg(color, size=(400, 300), exif_orientation=None):
    import io
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _layout(tmp_path, **extra):
    import json
    layout = {"canvas_size": [200, 200], "slots": [
        {"x": 0, "y": 0, "width": 100, "height": 100},
        {"x": 100, "y": 100, "width": 100, "height": 100}]}
    layout.update(extra)
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(layout))
    return str(p)


def test_overlay_is_drawn_on_top(tmp_path):
    import io
    from PIL import Image
    from compositor.grid_layout import Compositor
    ov = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ov.putpixel((10, 10), (0, 0, 255, 255))
    for x in range(5, 15):
        for y in range(5, 15):
            ov.putpixel((x, y), (0, 0, 255, 255))
    ov.save(tmp_path / "ov.png")
    out = Compositor().compose([_jpeg((255, 0, 0))], {
        "template_map": _layout(tmp_path, overlay_image="ov.png")})
    img = Image.open(io.BytesIO(out)).convert("RGB")
    r, g, b = img.getpixel((10, 10))
    assert b > 200 and r < 60          # overlay on top of the photo
    r, g, b = img.getpixel((50, 50))
    assert r > 200 and b < 60          # photo visible where overlay is transparent


def test_repeat_frames_fills_all_slots(tmp_path):
    import io
    from PIL import Image
    from compositor.grid_layout import Compositor
    out = Compositor().compose([_jpeg((0, 255, 0))], {
        "template_map": _layout(tmp_path, repeat_frames=True), "background_color": "#000000"})
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.getpixel((150, 150))[1] > 200


def test_bad_layout_gives_clear_error(tmp_path):
    import json
    from compositor.grid_layout import load_layout
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"canvas_size": [10, 10], "slots": [{"x": 0}]}))
    with pytest.raises(ValueError, match="slot 1"):
        load_layout(str(p))


def test_exif_rotation_is_applied(tmp_path):
    import io
    from PIL import Image
    from compositor.grid_layout import Compositor
    import json
    p = tmp_path / "tall.json"
    p.write_text(json.dumps({"canvas_size": [300, 400], "slots": [{"x": 0, "y": 0, "width": 300, "height": 400}]}))
    # landscape image with "rotate 90" orientation → should become portrait without heavy cropping
    out = Compositor().compose([_jpeg((255, 255, 0), size=(400, 300), exif_orientation=6)], {"template_map": str(p)})
    assert Image.open(io.BytesIO(out)).size == (300, 400)
