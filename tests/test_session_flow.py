"""End-to-end session tests using the simulated (dummy) camera.

These run the real Broker run-loop, CameraManager, compositor and local
storage — no hardware needed.
"""

import asyncio
import json
import os

import pytest

from core.config_loader import validate
from core.broker import Broker
from core.state import State

LAYOUT = os.path.join(os.path.dirname(__file__), "fixtures", "4shot_strip.json")


def make_config(tmp_path, **seq) -> dict:
    sequence = {
        "shots": 2,
        "countdown_seconds": 0,
        "first_countdown_seconds": 0,
        "show_shot_seconds": 0,
        "preview_duration_seconds": 5,
        "max_session_seconds": 30,
    }
    sequence.update(seq)
    return validate({
        "app": {"log_file": None, "min_free_disk_mb": 0},
        "camera": {
            "backend": "dummy",
            "capture_retries": 1,
            "reconnect_interval_seconds": 0.5,
            "backends": {"dummy": {"resolution": [600, 400], "preview_resolution": [160, 100],
                                   "capture_delay_seconds": 0}},
        },
        "input": {"triggers": ["websocket_button"], "debounce_ms": 0},
        "sequence": sequence,
        "compositor": {"enabled": True, "template_map": LAYOUT},
        "storage": {"targets": ["local_disk"], "local_disk": {
            "raw_dir": str(tmp_path / "raw"), "composite_dir": str(tmp_path / "composites")}},
    })


class Recorder:
    """Fake WebSocket client that records every message."""

    def __init__(self):
        self.messages = []
        self.new = asyncio.Event()

    async def send_str(self, text):
        self.messages.append(json.loads(text))
        self.new.set()

    async def close(self):
        pass

    def states(self):
        return [m["state"] for m in self.messages if m["event"] == "state_change"]

    def events(self, name):
        return [m for m in self.messages if m["event"] == name]

    async def wait_for(self, pred, timeout=10.0):
        async def _loop():
            while not pred(self):
                self.new.clear()
                await self.new.wait()
        await asyncio.wait_for(_loop(), timeout)


async def start(config):
    broker = Broker(config)
    rec = Recorder()
    broker.register_ws_client(rec)
    task = asyncio.create_task(broker.run())
    # wait for camera to connect and IDLE
    for _ in range(100):
        if broker.state is State.IDLE and broker.camera and broker.camera.connected:
            break
        await asyncio.sleep(0.05)
    assert broker.camera.connected, broker.camera.status
    return broker, rec, task


async def stop(broker, task):
    broker.request_shutdown()
    await asyncio.wait_for(task, 10)


@pytest.mark.asyncio
async def test_full_session_saves_everything(tmp_path):
    broker, rec, task = await start(make_config(tmp_path))
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states())
        raw = sorted(os.listdir(tmp_path / "raw"))
        comps = os.listdir(tmp_path / "composites")
        assert len(raw) == 2 and raw[0].endswith("_shot1.jpg") and raw[1].endswith("_shot2.jpg")
        assert len(comps) == 1 and comps[0].endswith("_composite.jpg")
        # same session id for all files of one session
        assert raw[0].split("_shot")[0] == comps[0].split("_composite")[0]
        assert len(rec.events("shot_captured")) == 2
        preview = [m for m in rec.messages if m.get("state") == "PREVIEW"][-1]["data"]
        assert preview["composite"].startswith("/media/")
        assert broker.get_screen_image(preview["composite"].split("/")[-1])[:2] == b"\xff\xd8"
        assert "frames" in preview and all(not f.startswith("data:") for f in preview["frames"])

        await broker.confirm()
        await rec.wait_for(lambda r: r.states()[-1] == "IDLE")
        assert broker.status()["sessions_completed"] == 1
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_states_in_order(tmp_path):
    broker, rec, task = await start(make_config(tmp_path, shots=1))
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states())
        s = rec.states()
        i = s.index("COUNTDOWN")
        assert s[i:i + 4] == ["COUNTDOWN", "CAPTURE", "PROCESS", "PREVIEW"]
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_retake_restarts_session_and_keeps_raws(tmp_path):
    broker, rec, task = await start(make_config(tmp_path, shots=1))
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states())
        await broker.retake()
        await rec.wait_for(lambda r: r.states().count("PREVIEW") == 2)
        # The first composite was moved to rejected/, raws are all kept
        assert len(os.listdir(tmp_path / "raw")) == 2
        assert len(os.listdir(tmp_path / "composites" / "rejected")) == 1
        # No IDLE between the two sessions in restart mode
        s = rec.states()
        first, second = [i for i, x in enumerate(s) if x == "PREVIEW"]
        assert "IDLE" not in s[first:second]
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_retake_idle_mode_then_trigger_works(tmp_path):
    """Regression: the first trigger after a retake used to be swallowed."""
    broker, rec, task = await start(make_config(tmp_path, shots=1, retake_mode="idle"))
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states())
        await broker.retake()
        await rec.wait_for(lambda r: r.states()[-1] == "IDLE")
        await broker.trigger()
        await rec.wait_for(lambda r: r.states().count("PREVIEW") == 2)
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_capture_retry_recovers(tmp_path):
    cfg = make_config(tmp_path, shots=1)
    cfg["camera"]["backends"]["dummy"]["fail_next_captures"] = 1
    broker, rec, task = await start(cfg)
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states())
        assert not rec.events("error")
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_camera_failure_returns_to_idle_with_error(tmp_path):
    cfg = make_config(tmp_path, shots=1)
    cfg["camera"]["backends"]["dummy"]["fail_next_captures"] = 99
    broker, rec, task = await start(cfg)
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: r.events("error") and r.states()[-1] == "IDLE")
        assert rec.events("error")[0]["data"]["code"] == "camera"
        assert not task.done()  # app still running
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_capture_timeout_recovers(tmp_path):
    cfg = make_config(tmp_path, shots=1)
    cfg["camera"]["capture_timeout_seconds"] = 1
    cfg["camera"]["capture_retries"] = 0
    broker, rec, task = await start(cfg)
    try:
        broker.camera._backend.capture_delay = 3  # hang longer than the timeout
        await broker.trigger()
        await rec.wait_for(lambda r: r.events("error") and r.states()[-1] == "IDLE", timeout=15)
        # Camera reconnects on a fresh thread and the next session works
        for _ in range(100):
            if broker.camera.connected:
                break
            await asyncio.sleep(0.1)
        assert broker.camera.connected
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states(), timeout=15)
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_trigger_refused_when_camera_offline(tmp_path):
    cfg = make_config(tmp_path)
    cfg["camera"]["backend"] = "ip_cam"  # no stream_url → cannot connect
    broker = Broker(cfg)
    rec = Recorder()
    broker.register_ws_client(rec)
    task = asyncio.create_task(broker.run())
    try:
        await rec.wait_for(lambda r: r.events("camera_status"))
        await broker.trigger()
        await rec.wait_for(lambda r: r.events("error"))
        assert rec.events("error")[0]["data"]["code"] == "camera_offline"
        assert "COUNTDOWN" not in rec.states()
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_shutdown_while_idle_is_fast(tmp_path):
    broker, rec, task = await start(make_config(tmp_path))
    broker.request_shutdown()
    await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_disk_full_blocks_trigger(tmp_path):
    cfg = make_config(tmp_path)
    cfg["app"]["min_free_disk_mb"] = 10 ** 12
    broker, rec, task = await start(cfg)
    try:
        await broker.trigger()
        await rec.wait_for(lambda r: r.events("error"))
        assert rec.events("error")[0]["data"]["code"] == "disk_full"
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_preview_frames_are_shared(tmp_path):
    broker, rec, task = await start(make_config(tmp_path))
    try:
        fid, frame = await broker.camera.wait_frame(-1, timeout=5)
        assert frame and frame[:2] == b"\xff\xd8"
        fid2, _ = await broker.camera.wait_frame(fid, timeout=5)
        assert fid2 > fid
    finally:
        await stop(broker, task)


@pytest.mark.asyncio
async def test_hot_reload_changes_camera(tmp_path):
    broker, rec, task = await start(make_config(tmp_path, shots=1))
    try:
        new_cfg = make_config(tmp_path, shots=1)
        new_cfg["camera"]["preview_fps"] = 5
        broker.reload_config(new_cfg)
        await rec.wait_for(lambda r: r.events("config_reloaded"))
        for _ in range(100):
            if broker.camera.connected:
                break
            await asyncio.sleep(0.05)
        assert broker.camera.connected
        await broker.trigger()
        await rec.wait_for(lambda r: "PREVIEW" in r.states())
    finally:
        await stop(broker, task)
