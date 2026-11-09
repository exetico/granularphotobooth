"""Global hotkey listener using pynput.

Reads the trigger key from ``config.json`` under
``input.keyboard.trigger_key``.  The listener runs in a background thread
managed by pynput and posts the trigger callback onto the asyncio event
loop so the broker can be called safely.

Supported key names: any character (``"a"``, ``" "``) or a special key
name from ``pynput.keyboard.Key`` (``"space"``, ``"enter"``, ``"f1"`` …).

This is a *global* hotkey (works even when the browser is not focused).
macOS: grant the terminal / Python "Accessibility" + "Input Monitoring"
permission.  Linux: needs an X11 session (Wayland blocks global hooks).
Most USB buzzer buttons and presenter clickers send a key press, and the
browser UI already listens for ``ui.trigger_keys`` — so in a normal kiosk
setup this global listener is optional.
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
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not called from the event loop: invoke the callback directly
            # (the caller is then responsible for thread safety).
            self._loop = None

        raw_key: str = self._config.get("trigger_key", "space")

        # Resolve pynput Key constant or plain character
        try:
            trigger_key = kb.Key[raw_key]
        except KeyError:
            trigger_key = kb.KeyCode.from_char(raw_key)

        def on_press(key) -> None:
            if key == trigger_key:
                logger.debug("Keyboard trigger fired: %s", key)
                if not self._callback:
                    return
                if self._loop is not None:
                    try:
                        self._loop.call_soon_threadsafe(self._callback)
                    except RuntimeError:
                        pass  # loop closed during shutdown
                else:
                    self._callback()

        try:
            self._listener = kb.Listener(on_press=on_press)
            self._listener.start()
        except Exception as exc:
            # No X server / Wayland / missing macOS Accessibility permission
            logger.error("Global keyboard trigger unavailable (%s). The browser UI still "
                         "accepts keys while it has focus.", exc)
            self._listener = None
            return
        logger.info("Keyboard trigger listening for key: %r", raw_key)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("Keyboard trigger stopped")
