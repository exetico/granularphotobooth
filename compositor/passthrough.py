"""Passthrough compositor — returns the last (or only) frame unchanged.

Used when ``compositor.enabled`` is ``false`` or ``sequence.shots`` is 1.
"""

import logging

from compositor.base import BaseCompositor

logger = logging.getLogger(__name__)


class Compositor(BaseCompositor):
    """No-op compositor — passes the final frame through as-is."""

    def compose(self, frames: list[bytes], config: dict) -> bytes:
        if not frames:
            raise ValueError("compose() called with empty frames list")
        logger.debug("Passthrough compositor: returning last of %d frame(s)", len(frames))
        return frames[-1]
