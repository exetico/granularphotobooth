"""Admin panel backend — authentication and ``/api/admin/*`` routes.

Security model (deliberately simple, no user accounts):

* **Master token** — 256-bit random token stored in ``admin_token.txt``
  (created on first start, ``chmod 600``, git-ignored).  The full admin
  URL ``http://localhost:8080/admin#token=…`` is printed in the log at start.
* **PIN** (optional, ``admin.pin`` in config.json, at least 8 digits) — lets
  the operator open the admin panel from the kiosk itself (tap the top-left
  corner 5 times).  A correct PIN returns a short-lived session token.
  Wrong PINs are rate-limited: after 5 failures, PIN login is locked for
  5 minutes.
* Every admin request needs ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import hmac
import io
import json
import logging
import os
import re
import secrets
import urllib.parse
import time
from collections import deque
from typing import Any, Callable

from aiohttp import web

from core.config_loader import validate
from core.paths import output_dirs

logger = logging.getLogger(__name__)

SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
UPLOAD_DIR = "assets/uploads"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_IMAGE_EXT = (".png", ".jpg", ".jpeg")


def load_or_create_token(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
        if len(token) >= 32:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    logger.info("Created new admin token in %s", path)
    return token


class AdminAuth:
    def __init__(self, token: str, pin_getter: Callable[[], str | None]) -> None:
        self._token = token
        self._pin_getter = pin_getter
        self._sessions: dict[str, float] = {}
        self._failures: deque[float] = deque()
        self._locked_until = 0.0

    @property
    def token(self) -> str:
        return self._token

    def pin_enabled(self) -> bool:
        pin = self._pin_getter()
        return bool(pin) and str(pin).isdigit() and len(str(pin)) >= 8

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        if hmac.compare_digest(token.encode(), self._token.encode()):
            return True
        now = time.time()
        for t, expiry in list(self._sessions.items()):
            if expiry < now:
                del self._sessions[t]
        expiry = self._sessions.get(token)
        return expiry is not None and expiry >= now

    def check(self, request: web.Request) -> None:
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        if not self.is_valid(token):
            raise web.HTTPForbidden(text=json.dumps({"error": "not authorised"}), content_type="application/json")

    def login(self, pin: str) -> tuple[bool, str]:
        """Return (ok, token-or-error-message)."""
        now = time.time()
        if now < self._locked_until:
            return False, f"Too many wrong PINs — try again in {int(self._locked_until - now)} s"
        if not self.pin_enabled():
            return False, "PIN login is not enabled (set admin.pin, at least 8 digits)"
        expected = str(self._pin_getter())
        if hmac.compare_digest(str(pin).encode(), expected.encode()):
            self._failures.clear()
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + SESSION_SECONDS
            return True, token
        self._failures.append(now)
        while self._failures and self._failures[0] < now - LOCKOUT_SECONDS:
            self._failures.popleft()
        if len(self._failures) >= MAX_FAILURES:
            self._locked_until = now + LOCKOUT_SECONDS
            self._failures.clear()
            logger.warning("Admin PIN locked for %d s after %d wrong attempts", LOCKOUT_SECONDS, MAX_FAILURES)
            return False, f"Too many wrong PINs — locked for {LOCKOUT_SECONDS // 60} minutes"
        return False, "Wrong PIN"

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


def _atomic_write_text(path: str, text: str) -> None:
    tmp = f"{path}.tmp-{secrets.token_hex(3)}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _list_files(directory: str, exts: tuple[str, ...]) -> list[str]:
    if not os.path.isdir(directory):
        return []
    out = []
    for root, _dirs, files in os.walk(directory):
        for f in sorted(files):
            if f.lower().endswith(exts):
                out.append(os.path.relpath(os.path.join(root, f)).replace(os.sep, "/"))
    return sorted(out)


class AdminRoutes:
    """Registers the admin HTTP API on an aiohttp app."""

    def __init__(
        self,
        app: web.Application,
        broker,
        config_path: str | None,
        on_config: Callable[[dict], None],
        auth: AdminAuth,
        static_dir: str,
    ) -> None:
        self._broker = broker
        self._config_path = config_path
        self._on_config = on_config
        self._auth = auth
        self._static_dir = static_dir
        r = app.router
        r.add_get("/admin", self._page)
        r.add_get("/api/admin/info", self._info)
        r.add_post("/api/admin/login", self._login)
        r.add_post("/api/admin/logout", self._logout)
        r.add_get("/api/admin/status", self._status)
        r.add_get("/api/admin/config", self._get_config)
        r.add_post("/api/admin/config", self._post_config)
        r.add_get("/api/admin/assets", self._assets)
        r.add_post("/api/admin/upload", self._upload)
        r.add_get("/api/admin/photos", self._photos)
        r.add_get("/api/admin/logs", self._logs)
        r.add_post("/api/admin/camera/test", self._camera_test)
        r.add_post("/api/admin/camera/reconnect", self._camera_reconnect)
        r.add_post("/api/admin/restart", self._restart)

    # -- public -----------------------------------------------------------

    async def _page(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(os.path.join(self._static_dir, "admin.html"),
                                headers={"Cache-Control": "no-store"})

    async def _info(self, request: web.Request) -> web.Response:
        return web.json_response({"pin_enabled": self._auth.pin_enabled()})

    async def _login(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="expected JSON")
        ok, result = self._auth.login(str(body.get("pin", "")))
        if not ok:
            logger.warning("Admin PIN login failed from %s: %s", request.remote, result)
            return web.json_response({"error": result}, status=403)
        logger.info("Admin PIN login from %s", request.remote)
        return web.json_response({"token": result, "expires_in": SESSION_SECONDS})

    async def _logout(self, request: web.Request) -> web.Response:
        header = request.headers.get("Authorization", "")
        self._auth.logout(header[7:].strip() if header.lower().startswith("bearer ") else None)
        return web.json_response({"ok": True})

    # -- authenticated ----------------------------------------------------

    async def _status(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        return web.json_response(self._broker.status())

    def _read_user_config(self) -> dict:
        if not self._config_path or not os.path.isfile(self._config_path):
            return {}
        with open(self._config_path, encoding="utf-8") as fh:
            return json.load(fh)

    async def _get_config(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        try:
            user = self._read_user_config()
        except (OSError, json.JSONDecodeError) as exc:
            user = {}
            logger.warning("Admin: cannot read config file: %s", exc)
        return web.json_response({
            "path": self._config_path,
            "config": user,                 # what is in config.json
            "effective": self._broker.config,  # merged with defaults (+ CLI overrides)
        })

    async def _post_config(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        if not self._config_path:
            return web.json_response({"error": "No config file in use"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Body must be JSON"}, status=400)
        new_user_cfg = body.get("config") if isinstance(body, dict) else None
        if not isinstance(new_user_cfg, dict):
            return web.json_response({"error": "Missing 'config' object"}, status=400)
        try:
            merged = validate(new_user_cfg)
            pin = merged.get("admin", {}).get("pin")
            if pin and (not str(pin).isdigit() or len(str(pin)) < 8):
                raise ValueError("admin.pin must be at least 8 digits (or empty to disable)")
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        text = json.dumps(new_user_cfg, indent=2, ensure_ascii=False) + "\n"
        try:
            if os.path.isfile(self._config_path):
                with open(self._config_path, encoding="utf-8") as fh:
                    old = fh.read()
                _atomic_write_text(self._config_path + ".bak", old)
            _atomic_write_text(self._config_path, text)
        except OSError as exc:
            return web.json_response({"error": f"Could not save: {exc}"}, status=500)
        logger.info("Admin saved config.json from %s (backup: config.json.bak)", request.remote)
        self._on_config(merged)
        pending = self._broker.state.name != "IDLE"
        return web.json_response({"ok": True, "applied": not pending,
                                  "note": "Applies after the current session" if pending else "Applied"})

    async def _assets(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        layouts = []
        for path in _list_files("assets", (".json",)):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if "slots" in data and "canvas_size" in data:
                    layouts.append({"path": path, "slots": len(data["slots"]), "canvas_size": data["canvas_size"]})
            except (OSError, ValueError):
                continue
        images = _list_files("assets", _IMAGE_EXT)
        return web.json_response({"layouts": layouts, "images": images})

    async def _upload(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file" or not field.filename:
            return web.json_response({"error": "Send a 'file' field"}, status=400)
        name = os.path.basename(urllib.parse.unquote(field.filename).replace("\\", "/"))
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "image.png"
        if not name.lower().endswith(_IMAGE_EXT):
            return web.json_response({"error": "Only PNG or JPG images"}, status=400)
        data = bytearray()
        while True:
            chunk = await field.read_chunk(256 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                return web.json_response({"error": "File too large (max 25 MB)"}, status=400)
        try:
            from PIL import Image
            with Image.open(io.BytesIO(bytes(data))) as img:
                img.verify()
                size = img.size
        except Exception:
            return web.json_response({"error": "Not a valid image"}, status=400)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        stem, ext = os.path.splitext(name)
        path = os.path.join(UPLOAD_DIR, name)
        n = 1
        while os.path.exists(path):
            path = os.path.join(UPLOAD_DIR, f"{stem}-{n}{ext}")
            n += 1
        with open(path, "wb") as fh:
            fh.write(bytes(data))
        logger.info("Admin uploaded %s (%dx%d)", path, *size)
        return web.json_response({"path": path.replace(os.sep, "/"), "size": size})

    async def _photos(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        try:
            limit = max(1, min(200, int(request.query.get("limit", 24))))
        except ValueError:
            limit = 24
        _raw_dir, composite_dir = output_dirs(self._broker.config)
        root = os.path.commonpath(output_dirs(self._broker.config))
        items = []
        if os.path.isdir(composite_dir):
            for entry in os.scandir(composite_dir):
                if entry.is_file() and entry.name.lower().endswith((".jpg", ".jpeg", ".png")):
                    items.append((entry.stat().st_mtime, entry.path))
        items.sort(reverse=True)
        photos = [{
            "name": os.path.basename(p),
            "url": "/output/" + os.path.relpath(p, root).replace(os.sep, "/"),
            "time": int(mtime),
        } for mtime, p in items[:limit]]
        return web.json_response({"photos": photos, "total": len(items)})

    async def _logs(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        log_file = self._broker.config.get("app", {}).get("log_file")
        try:
            lines = max(10, min(2000, int(request.query.get("lines", 200))))
        except ValueError:
            lines = 200
        if not log_file or not os.path.isfile(log_file):
            return web.json_response({"lines": ["(no log file — set app.log_file)"]})
        with open(log_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 400_000))
            tail = fh.read().decode("utf-8", "replace").splitlines()[-lines:]
        return web.json_response({"lines": tail})

    async def _camera_test(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        try:
            url = await self._broker.test_capture()
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=409)
        return web.json_response({"url": url})

    async def _camera_reconnect(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        camera = self._broker.camera
        if camera is None:
            return web.json_response({"error": "No camera manager"}, status=409)
        await camera.reconfigure(self._broker.config["camera"])
        return web.json_response({"ok": True})

    async def _restart(self, request: web.Request) -> web.Response:
        self._auth.check(request)
        logger.warning("Restart requested from admin panel (%s)", request.remote)
        self._broker.request_restart()
        return web.json_response({"ok": True})
