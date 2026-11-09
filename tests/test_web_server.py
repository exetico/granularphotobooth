"""HTTP / WebSocket tests for web/server.py using aiohttp's test utilities."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from core.broker import Broker
from core.state import State
from web.server import WebServer
from tests.test_session_flow import make_config


@pytest.fixture
async def client(tmp_path):
    cfg = make_config(tmp_path, shots=1)
    broker = Broker(cfg)
    task = asyncio.create_task(broker.run())
    for _ in range(100):
        if broker.camera and broker.camera.connected:
            break
        await asyncio.sleep(0.05)
    server = WebServer(cfg, broker)
    async with TestClient(TestServer(server.app)) as c:
        c.broker = broker
        yield c
    broker.request_shutdown()
    await asyncio.wait_for(task, 10)


async def test_index_injects_config(client):
    resp = await client.get("/")
    assert resp.status == 200
    html = await resp.text()
    assert "window.__cfg" in html and client.broker.boot_id in html
    assert resp.headers["Cache-Control"] == "no-store"


async def test_status_and_health(client):
    resp = await client.get("/api/status")
    data = await resp.json()
    assert data["state"] in ("IDLE", "BOOT") and data["camera"]["connected"] is True
    assert (await client.get("/healthz")).status == 200


async def test_websocket_hello_and_session(client):
    ws = await client.ws_connect("/ws")
    hello = json.loads((await ws.receive()).data)
    assert hello["event"] == "hello" and hello["data"]["boot_id"] == client.broker.boot_id
    await ws.send_str(json.dumps({"event": "trigger"}))
    composite = None
    for _ in range(50):
        msg = json.loads((await asyncio.wait_for(ws.receive(), 10)).data)
        if msg.get("state") == "PREVIEW":
            composite = msg["data"]["composite"]
            break
    assert composite
    img = await client.get(composite)
    assert img.status == 200 and (await img.read())[:2] == b"\xff\xd8"
    await ws.send_str(json.dumps({"event": "confirm"}))
    await ws.close()


async def test_bad_ws_messages_are_ignored(client):
    ws = await client.ws_connect("/ws")
    await ws.receive()
    for junk in ["not json", "[]", "123", json.dumps({"event": "nope"})]:
        await ws.send_str(junk)
    await ws.send_str(json.dumps({"event": "hello"}))
    msg = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
    assert msg["event"] == "hello"
    assert client.broker.state is State.IDLE
    await ws.close()


async def test_media_rejects_bad_names(client):
    for path in ["/media/..%2Fconfig.json", "/media/nothing.jpg", "/media/x.png"]:
        assert (await client.get(path)).status == 404


async def test_preview_still_and_stream(client):
    resp = await client.get("/preview.jpg")
    assert resp.status == 200 and (await resp.read())[:2] == b"\xff\xd8"
    resp = await client.get("/preview")
    assert resp.headers["Content-Type"].startswith("multipart/x-mixed-replace")
    chunk = await asyncio.wait_for(resp.content.read(2048), 5)
    assert b"--frame" in chunk
    resp.close()
