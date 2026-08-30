"""Global hotkey listener using pynput.

Reads the trigger key from ``config.json`` under
``input.keyboard.trigger_key``.  The listener runs in a background thread
managed by pynput and posts the trigger callback onto the asyncio event
loop so the broker can be called safely.

Supported key names: any character (``"a"``, ``" "``) or a special key
name from ``pynput.keyboard.Key`` (``"space"``, ``"enter"``, ``"f1"`` …).
"""

import asyncio
import logging

from inputs.base import InputTrigger

logger = logging.getLogger(__name__)


class KeyboardTrigger(InputTrigger):
    """Fires a trigger whenever the configured key is pressed."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._listener = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, callback) -> None:
        try:
            from pynput import keyboard as kb
        except ImportError:
            logger.error("pynput is not installed — keyboard trigger disabled")
            return

        self._callback = callback
        self._loop = asyncio.get_running_loop()

        raw_key: str = self._config.get("trigger_key", "space")

        # Resolve pynput Key constant or plain character
        try:
            trigger_key = kb.Key[raw_key]
        except KeyError:
            trigger_key = kb.KeyCode.from_char(raw_key)

        def on_press(key) -> None:
            if key == trigger_key:
                logger.debug("Keyboard trigger fired: %s", key)
                if self._loop and self._callback:
                    self._loop.call_soon_threadsafe(self._callback)

        self._listener = kb.Listener(on_press=on_press)
        self._listener.start()
        logger.info("Keyboard trigger listening for key: %r", raw_key)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("Keyboard trigger stopped")
