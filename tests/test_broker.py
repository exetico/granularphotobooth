"""Tests for the core state machine (Broker).

These tests exercise all state transitions without requiring a camera,
a WebSocket client, or any real hardware.
"""

import asyncio
import pytest

from core.state import State
from core.broker import Broker

# ── Minimal config ────────────────────────────────────────────────────

def _cfg(**overrides) -> dict:
    base = {
        "app": {"host": "127.0.0.1", "port": 8080, "debug": True, "kiosk_mode": False},
        "camera": {
            "backend": "webcam",
            "backends": {
                "webcam": {"device_index": 0, "resolution": [640, 480], "preview_resolution": [320, 240]}
            },
        },
        "input": {"triggers": [], "keyboard": {"trigger_key": "space"}},
        "sequence": {
            "shots": 1,
            "countdown_seconds": 0,
            "preview_duration_seconds": 1,
            "delay_between_shots_seconds": 0,
        },
        "compositor": {"enabled": False},
        "storage": {
            "targets": [],
            "local_disk": {"enabled": False, "output_dir": "/tmp/test_output/", "filename_format": "photo_{timestamp}_{uuid}.jpg"},
        },
    }
    base.update(overrides)
    return base


# ── State enum ────────────────────────────────────────────────────────

def test_state_enum_values():
    assert State.IDLE.name == "IDLE"
    assert State.COUNTDOWN.name == "COUNTDOWN"
    assert State.CAPTURE.name == "CAPTURE"
    assert State.PREVIEW.name == "PREVIEW"
    assert State.PROCESS.name == "PROCESS"


# ── Broker initial state ──────────────────────────────────────────────

def test_broker_initial_state():
    broker = Broker(_cfg())
    assert broker._state is State.IDLE


# ── Broadcast collects clients ────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_sends_to_registered_clients():
    broker = Broker(_cfg())

    received = []

    class FakeWS:
        async def send_str(self, text):
            received.append(text)

    ws = FakeWS()
    broker.register_ws_client(ws)
    await broker._broadcast({"event": "test", "data": {}})

    assert len(received) == 1
    import json
    msg = json.loads(received[0])
    assert msg["event"] == "test"


# ── Register / unregister clients ─────────────────────────────────────

def test_register_unregister_ws_client():
    broker = Broker(_cfg())

    class FakeWS:
        async def send_str(self, text): ...

    ws = FakeWS()
    broker.register_ws_client(ws)
    assert ws in broker._ws_clients

    broker.unregister_ws_client(ws)
    assert ws not in broker._ws_clients


# ── trigger() ignored when not IDLE ──────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_ignored_when_not_idle():
    broker = Broker(_cfg())
    broker._state = State.COUNTDOWN
    await broker.trigger()
    assert not broker._trigger_event.is_set()


# ── trigger() fires when IDLE ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_sets_event_when_idle():
    broker = Broker(_cfg())
    assert broker._state is State.IDLE
    await broker.trigger()
    assert broker._trigger_event.is_set()


# ── confirm() only fires from PREVIEW ────────────────────────────────

@pytest.mark.asyncio
async def test_confirm_only_in_preview():
    broker = Broker(_cfg())
    broker._state = State.IDLE
    await broker.confirm()
    assert not broker._trigger_event.is_set()

    broker._state = State.PREVIEW
    await broker.confirm()
    assert broker._trigger_event.is_set()


# ── retake() moves to IDLE from PREVIEW ──────────────────────────────

@pytest.mark.asyncio
async def test_retake_from_preview():
    broker = Broker(_cfg())

    received_states = []

    class FakeWS:
        async def send_str(self, text):
            import json
            msg = json.loads(text)
            if msg.get("event") == "state_change":
                received_states.append(msg["state"])

    broker.register_ws_client(FakeWS())
    broker._state = State.PREVIEW
    await broker.retake()

    assert broker._state is State.IDLE
    assert "IDLE" in received_states


# ── Shutdown event ────────────────────────────────────────────────────

def test_request_shutdown():
    broker = Broker(_cfg())
    assert not broker._shutdown_event.is_set()
    broker.request_shutdown()
    assert broker._shutdown_event.is_set()


# ── Broken WS client is removed after failed send ────────────────────

@pytest.mark.asyncio
async def test_dead_client_removed_after_failed_broadcast():
    broker = Broker(_cfg())

    class BrokenWS:
        async def send_str(self, text):
            raise ConnectionResetError("gone")

    ws = BrokenWS()
    broker.register_ws_client(ws)
    await broker._broadcast({"event": "ping"})

    assert ws not in broker._ws_clients
