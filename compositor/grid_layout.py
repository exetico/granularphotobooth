"""Grid layout compositor using Pillow.

Builds the final picture in three layers:

1. **Background** — ``template_image`` PNG/JPG (or a solid ``background_color``)
2. **Photos**     — each captured frame, crop-to-fill into its slot
3. **Overlay**    — ``overlay_image`` PNG with transparency (logos, frames,
                    text, event name …) drawn *on top* of the photos

Layout JSON format (``assets/layouts/4shot_strip.json``)::

    {
      "canvas_size": [1200, 1800],
      "template_image": "4shot_strip_bg.png",   # optional, relative to JSON
      "overlay_image": "4shot_strip_overlay.png",  # optional, relative to JSON
      "repeat_frames": false,                    # true = reuse shots to fill
                                                 # extra slots (2x strips)
      "slots": [
        {"id": 1, "x": 60, "y": 60, "width": 1080, "height": 560, "rotate": 0},
        ...
      ]
    }

Paths in ``config.json`` win over paths in the layout JSON.
"""

import io
import json
import logging
import os

from compositor.base import BaseCompositor

logger = logging.getLogger(__name__)


def _resolve(path: str, base_dir: str) -> str:
    if not path:
        return ""
    if os.path.isabs(path) or os.path.isfile(path):
        return path
    candidate = os.path.join(base_dir, path)
    return candidate if os.path.isfile(candidate) else path


def _parse_color(value: str):
    from PIL import ImageColor
    try:
        return ImageColor.getrgb(value or "#ffffff")
    except ValueError:
        return (255, 255, 255)


def load_layout(template_map_path: str) -> dict:
    if not template_map_path or not os.path.isfile(template_map_path):
        raise FileNotFoundError(f"Template map not found: {template_map_path!r}")
    with open(template_map_path, encoding="utf-8") as fh:
        layout: dict = json.load(fh)
    size = layout.get("canvas_size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v > 0 for v in size)):
        raise ValueError(f"{template_map_path}: 'canvas_size' must be [width, height]")
    slots = layout.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError(f"{template_map_path}: 'slots' must be a non-empty list")
    for i, slot in enumerate(slots):
        for key in ("x", "y", "width", "height"):
            if not isinstance(slot.get(key), int):
                raise ValueError(f"{template_map_path}: slot {i + 1} needs integer '{key}'")
        if slot["width"] <= 0 or slot["height"] <= 0:
            raise ValueError(f"{template_map_path}: slot {i + 1} has zero size")
    return layout


class Compositor(BaseCompositor):
    """Paste captured frames onto a background template using a slot map."""

    def compose(self, frames: list[bytes], config: dict) -> bytes:
        from PIL import Image, ImageOps

        if not frames:
            raise ValueError("compose() called with empty frames list")

        template_map_path: str = config.get("template_map", "")
        layout = load_layout(template_map_path)
        base_dir = os.path.dirname(os.path.abspath(template_map_path))

        output_quality: int = int(config.get("output_quality", 92))
        output_format: str = str(config.get("output_format", "JPEG")).upper()
        template_image_path = _resolve(config.get("template_image") or layout.get("template_image", ""), base_dir)
        overlay_image_path = _resolve(config.get("overlay_image") or layout.get("overlay_image", ""), base_dir)

        width, height = layout["canvas_size"]
        slots: list[dict] = layout["slots"]
        repeat = bool(layout.get("repeat_frames", False))

        if len(frames) > len(slots):
            logger.warning("Layout has %d slots but %d shots were taken — extra shots not shown in composite",
                           len(slots), len(frames))

        # 1. Background
        bg_color = _parse_color(config.get("background_color") or layout.get("background_color", "#ffffff"))
        canvas = Image.new("RGBA", (width, height), bg_color + (255,))
        if template_image_path:
            if os.path.isfile(template_image_path):
                with Image.open(template_image_path) as tpl:
                    tpl = tpl.convert("RGBA")
                    if tpl.size != (width, height):
                        tpl = tpl.resize((width, height), Image.LANCZOS)
                    canvas.alpha_composite(tpl)
            else:
                logger.warning("Template image not found: %s — using plain background", template_image_path)

        # 2. Photos
        decoded: dict[int, "Image.Image"] = {}
        for i, slot in enumerate(slots):
            if i < len(frames):
                idx = i
            elif repeat:
                idx = i % len(frames)
            else:
                break
            slot_w, slot_h = slot["width"], slot["height"]
            rotate = int(slot.get("rotate", 0)) % 360
            if idx not in decoded:
                img = Image.open(io.BytesIO(frames[idx]))
                # Fast JPEG decode at reduced size (big win for DSLR files)
                biggest = max(s["width"] for s in slots), max(s["height"] for s in slots)
                img.draft("RGB", (biggest[0] * 2, biggest[1] * 2))
                img = ImageOps.exif_transpose(img).convert("RGB")
                decoded[idx] = img
            img = decoded[idx]
            if rotate in (90, 270):
                fitted = ImageOps.fit(img, (slot_h, slot_w), method=Image.LANCZOS).rotate(rotate, expand=True)
            else:
                fitted = ImageOps.fit(img, (slot_w, slot_h), method=Image.LANCZOS)
                if rotate:
                    fitted = fitted.rotate(rotate)
            canvas.paste(fitted, (slot["x"], slot["y"]))

        # 3. Overlay
        if overlay_image_path:
            if os.path.isfile(overlay_image_path):
                with Image.open(overlay_image_path) as ov:
                    ov = ov.convert("RGBA")
                    if ov.size != (width, height):
                        ov = ov.resize((width, height), Image.LANCZOS)
                    canvas.alpha_composite(ov)
            else:
                logger.warning("Overlay image not found: %s", overlay_image_path)

        buf = io.BytesIO()
        if output_format == "PNG":
            canvas.save(buf, format="PNG")
        else:
            canvas.convert("RGB").save(buf, format="JPEG", quality=output_quality, optimize=True)
        return buf.getvalue()
