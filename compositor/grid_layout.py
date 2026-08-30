"""Grid layout compositor using Pillow.

Reads a background PNG template and a JSON coordinate map, then pastes
each captured JPEG frame into the corresponding slot on the canvas.

Template map JSON format (``assets/layouts/4shot_strip.json``):
    {
      "canvas_size": [1200, 1800],
      "slots": [
        {"id": 1, "x": 60,  "y": 60,  "width": 1080, "height": 560},
        ...
      ]
    }
"""

import io
import json
import logging
import os

from compositor.base import BaseCompositor

logger = logging.getLogger(__name__)


class Compositor(BaseCompositor):
    """Paste captured frames onto a background template using a slot map."""

    def compose(self, frames: list[bytes], config: dict) -> bytes:
        from PIL import Image, ImageOps

        if not frames:
            raise ValueError("compose() called with empty frames list")

        template_image_path: str = config.get("template_image", "")
        template_map_path: str = config.get("template_map", "")
        output_quality: int = config.get("output_quality", 90)
        output_format: str = config.get("output_format", "JPEG").upper()

        # Load slot map
        if not template_map_path or not os.path.isfile(template_map_path):
            raise FileNotFoundError(f"Template map not found: {template_map_path!r}")

        with open(template_map_path, encoding="utf-8") as fh:
            layout: dict = json.load(fh)

        canvas_size: list[int] = layout["canvas_size"]
        slots: list[dict] = layout["slots"]

        # Build canvas — use template image if provided, otherwise blank white
        if template_image_path and os.path.isfile(template_image_path):
            canvas = Image.open(template_image_path).convert("RGBA")
            canvas = canvas.resize((canvas_size[0], canvas_size[1]), Image.LANCZOS)
        else:
            logger.debug("No template image found — using white canvas")
            canvas = Image.new("RGBA", (canvas_size[0], canvas_size[1]), (255, 255, 255, 255))

        # Paste each frame into its slot
        for i, slot in enumerate(slots):
            if i >= len(frames):
                logger.debug("Not enough frames for slot %d — skipping", slot.get("id", i + 1))
                break

            slot_w: int = slot["width"]
            slot_h: int = slot["height"]

            raw_frame = Image.open(io.BytesIO(frames[i])).convert("RGBA")
            # crop-to-fill: preserve aspect ratio, no stretching
            fitted = ImageOps.fit(raw_frame, (slot_w, slot_h), method=Image.LANCZOS)

            canvas.paste(fitted, (slot["x"], slot["y"]))
            logger.debug("Pasted frame %d into slot %d at (%d, %d)", i + 1, slot.get("id", i + 1), slot["x"], slot["y"])

        # Encode result
        result = canvas.convert("RGB")
        buf = io.BytesIO()
        result.save(buf, format=output_format, quality=output_quality)
        return buf.getvalue()
