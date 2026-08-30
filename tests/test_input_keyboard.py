"""Tests for the keyboard input trigger.

Uses unittest.mock to simulate pynput key events without requiring a real
keyboard device or display server.
"""

import asyncio
import threading
import time
import unittest.mock as mock
import pytest

from inputs.keyboard import KeyboardTrigger


# ── start() handles missing pynput gracefully ─────────────────────────

def test_start_without_pynput_does_not_raise(monkeypatch):
    """If pynput is unavailable, start() should log an error but not crash."""
    monkeypatch.setitem(__import__("sys").modules, "pynput", None)
    monkeypatch.setitem(__import__("sys").modules, "pynput.keyboard", None)

    trigger = KeyboardTrigger({"trigger_key": "space"})
    # Should not raise even without pynput
    trigger.start(lambda: None)
    trigger.stop()


# ── Callback fired when trigger key is pressed ────────────────────────

def test_callback_fired_on_trigger_key():
    """
    Simulate a pynput key-press event directly (without a real keyboard).
    Patches pynput.keyboard.Listener so it fires our on_press callback
    synchronously in the test.
    """
    try:
        import pynput  # noqa: F401
        from pynput import keyboard as kb
    except ImportError:
        pytest.skip("pynput not available or no display server")

    fired = threading.Event()

    trigger = KeyboardTrigger({"trigger_key": "space"})

    # Capture the on_press callback that KeyboardTrigger passes to Listener
    captured_on_press = {}

    class FakeListener:
        def __init__(self, on_press=None, **kwargs):
            captured_on_press["fn"] = on_press

        def start(self):
            pass

        def stop(self):
            pass

    with mock.patch("pynput.keyboard.Listener", FakeListener):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        trigger._loop = loop
        trigger.start(lambda: fired.set())

    # Simulate pressing the space key
    captured_on_press["fn"](kb.Key.space)
    assert fired.wait(timeout=1), "Callback was not fired after simulated keypress"

    loop.close()


def test_callback_not_fired_for_wrong_key():
    try:
        import pynput  # noqa: F401
        from pynput import keyboard as kb
    except ImportError:
        pytest.skip("pynput not available or no display server")

    fired = threading.Event()
    trigger = KeyboardTrigger({"trigger_key": "space"})

    captured_on_press = {}

    class FakeListener:
        def __init__(self, on_press=None, **kwargs):
            captured_on_press["fn"] = on_press
        def start(self): pass
        def stop(self): pass

    with mock.patch("pynput.keyboard.Listener", FakeListener):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        trigger._loop = loop
        trigger.start(lambda: fired.set())

    # Press a different key — should NOT fire
    captured_on_press["fn"](kb.Key.enter)
    time.sleep(0.05)
    assert not fired.is_set()
    loop.close()


# ── stop() is idempotent ──────────────────────────────────────────────

def test_stop_idempotent():
    trigger = KeyboardTrigger({"trigger_key": "space"})
    trigger.stop()  # No listener started — must not raise
    trigger.stop()  # Second call — must not raise
