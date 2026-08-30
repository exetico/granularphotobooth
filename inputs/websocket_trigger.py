"""WebSocket-based trigger.

The browser UI sends a JSON message with ``{"event": "trigger"}`` over the
WebSocket connection.  ``web/server.py`` calls ``broker.trigger()``
directly, so this module is a thin shim that documents the contract and
can be used for testing without a real browser.
"""

import asyncio
import logging

from inputs.base import InputTrigger

logger = logging.getLogger(__name__)


class WebSocketTrigger(InputTrigger):
    """Accepts trigger calls forwarded by the WebSocket server."""

    def start(self, callback) -> None:
        self._callback = callback
        logger.info("WebSocket trigger ready")

    def stop(self) -> None:
        self._callback = None

    def fire(self) -> None:
        """Called by the WebSocket server when a 'trigger' message arrives."""
        if self._callback:
            self._callback()
