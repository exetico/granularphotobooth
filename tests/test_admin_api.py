"""Admin panel API: authentication, PIN lockout, config editing, tools."""

import asyncio
import json
import os

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from core.broker import Broker
from web import admin as admin_mod
from web.admin import load_or_create_token
from web.server import WebServer
from tests.test_session_flow import make_config

TOKEN = "t" * 43
PIN = "12345678"


@pytest.fixture
async def ctx(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # uploads / asset listing relative to cwd
    cfg_path = tmp_path / "config.json"
    user_cfg = {"admin": {"pin": PIN}, "camera": {"backend": "dummy"}}
    cfg_path.write_text(json.dumps(user_cfg))
    cfg = make_config(tmp_path, shots=1)
    cfg["admin"]["pin"] = PIN
    broker = Broker(cfg)
    task = asyncio.create_task(broker.run())
    for _ in range(100):
        if broker.camera and broker.camera.connected:
            break
        await asyncio.sleep(0.05)
    reloaded = []

    def on_config(new):
        reloaded.append(new)
        broker.reload_config(new)

    server = WebServer(cfg, broker, config_path=str(cfg_path), on_config=on_config, admin_token=TOKEN)
    async with TestClient(TestServer(server.app)) as client:
        client.broker, client.cfg_path, client.reloaded = broker, cfg_path, reloaded
        client.auth = {"Authorization": f"Bearer {TOKEN}"}
        yield client
    broker.request_shutdown()
    await asyncio.wait_for(task, 10)


async def test_requires_token(ctx):
    for path in ["/api/admin/status", "/api/admin/config", "/api/admin/photos", "/api/admin/logs"]:
        assert (await ctx.get(path)).status == 403
        assert (await ctx.get(path, headers={"Authorization": "Bearer wrong"})).status == 403
    assert (await ctx.post("/api/admin/restart")).status == 403
    assert not ctx.broker.restart_requested


async def test_token_grants_access(ctx):
    r = await ctx.get("/api/admin/status", headers=ctx.auth)
    assert r.status == 200 and (await r.json())["camera"]["connected"]


async def test_admin_page_is_public_but_api_is_not(ctx):
    r = await ctx.get("/admin")
    assert r.status == 200 and "Booth Admin" in await r.text()
    info = await (await ctx.get("/api/admin/info")).json()
    assert info == {"pin_enabled": True}


async def test_pin_login_gives_session_token(ctx):
    r = await ctx.post("/api/admin/login", json={"pin": PIN})
    token = (await r.json())["token"]
    r = await ctx.get("/api/admin/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status == 200
    await ctx.post("/api/admin/logout", headers={"Authorization": f"Bearer {token}"})
    r = await ctx.get("/api/admin/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status == 403


async def test_pin_lockout(ctx):
    for _ in range(admin_mod.MAX_FAILURES):
        r = await ctx.post("/api/admin/login", json={"pin": "00000000"})
        assert r.status == 403
    r = await ctx.post("/api/admin/login", json={"pin": PIN})  # correct, but locked
    assert r.status == 403 and "Too many" in (await r.json())["error"]


async def test_pin_not_leaked_to_kiosk(ctx):
    html = await (await ctx.get("/")).text()
    assert PIN not in html
    assert '"admin_corner_taps": 5' in html


async def test_get_config_returns_raw_and_effective(ctx):
    data = await (await ctx.get("/api/admin/config", headers=ctx.auth)).json()
    assert data["config"]["camera"] == {"backend": "dummy"}
    assert data["effective"]["sequence"]["shots"] == 1


async def test_save_valid_config_writes_backup_and_reloads(ctx):
    new = {"admin": {"pin": PIN}, "camera": {"backend": "dummy"}, "sequence": {"shots": 3}}
    r = await ctx.post("/api/admin/config", headers=ctx.auth, json={"config": new})
    assert r.status == 200, await r.text()
    assert json.loads(ctx.cfg_path.read_text()) == new
    assert json.loads((ctx.cfg_path.parent / "config.json.bak").read_text())["camera"] == {"backend": "dummy"}
    assert ctx.reloaded and ctx.reloaded[-1]["sequence"]["shots"] == 3
    assert ctx.broker.config["sequence"]["shots"] == 3


async def test_save_invalid_config_is_rejected_and_file_untouched(ctx):
    before = ctx.cfg_path.read_text()
    for bad in [{"sequence": {"shots": 99}}, {"camera": {"backend": "nope"}}, {"admin": {"pin": "123"}}]:
        r = await ctx.post("/api/admin/config", headers=ctx.auth, json={"config": bad})
        assert r.status == 400
        assert "error" in await r.json()
    r = await ctx.post("/api/admin/config", headers=ctx.auth, data="not json")
    assert r.status == 400
    assert ctx.cfg_path.read_text() == before
    assert not ctx.reloaded


async def test_camera_test_photo(ctx):
    r = await ctx.post("/api/admin/camera/test", headers=ctx.auth)
    url = (await r.json())["url"]
    img = await ctx.get(url)
    assert img.status == 200 and (await img.read())[:2] == b"\xff\xd8"


async def test_photos_lists_latest_composites(ctx):
    await ctx.broker.trigger()
    for _ in range(100):
        if ctx.broker.state.name == "PREVIEW":
            break
        await asyncio.sleep(0.05)
    data = await (await ctx.get("/api/admin/photos", headers=ctx.auth)).json()
    assert data["total"] == 1
    r = await ctx.get(data["photos"][0]["url"])
    assert r.status == 200


async def test_upload_image(ctx):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (40, 60), (0, 0, 0, 0)).save(buf, format="PNG")
    form = FormData()
    form.add_field("file", buf.getvalue(), filename="my overlay.png", content_type="image/png")
    r = await ctx.post("/api/admin/upload", headers=ctx.auth, data=form)
    data = await r.json()
    assert r.status == 200 and data["path"] == "assets/uploads/my_overlay.png" and data["size"] == [40, 60]
    assets = await (await ctx.get("/api/admin/assets", headers=ctx.auth)).json()
    assert "assets/uploads/my_overlay.png" in assets["images"]

    form = FormData()
    form.add_field("file", b"<script>", filename="x.png", content_type="image/png")
    assert (await ctx.post("/api/admin/upload", headers=ctx.auth, data=form)).status == 400
    form = FormData()
    form.add_field("file", b"abc", filename="../../evil.sh")
    assert (await ctx.post("/api/admin/upload", headers=ctx.auth, data=form)).status == 400


async def test_restart_sets_flag(ctx):
    r = await ctx.post("/api/admin/restart", headers=ctx.auth)
    assert r.status == 200
    assert ctx.broker.restart_requested and ctx.broker.is_shutdown_requested()


def test_token_file_created_private_and_reused(tmp_path):
    p = tmp_path / "admin_token.txt"
    t1 = load_or_create_token(str(p))
    assert len(t1) >= 40 and oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert load_or_create_token(str(p)) == t1
