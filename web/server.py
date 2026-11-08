"""Lightweight aiohttp web server.

Routes:
  - ``GET /``              → kiosk UI (``web/static/index.html``)
  - ``GET /static/…``      → JS/CSS assets
  - ``GET /ws``            → WebSocket (bidirectional JSON protocol)
  - ``GET /preview``       → MJPEG live view (shared, any number of viewers)
  - ``GET /preview.jpg``   → latest single live-view frame
  - ``GET /media/{name}``  → screen-sized versions of the photos just taken
  - ``GET /output/…``      → saved photos (raw + composites)
  - ``GET /api/status``    → JSON health/status for operators & monitoring
  - ``GET /healthz``       → 200 when the app is up (for watchdogs)
"""

import asyncio
import json
import logging
import os
import re

from aiohttp import web

from core.paths import output_dirs, output_root
from core.version import __version__

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_MEDIA_NAME = re.compile(r"^[\w\-.]+\.jpg$")


@web.middleware
async def _headers_middleware(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    if isinstance(response, web.StreamResponse) and not response.prepared:
        path = request.path
        if path == "/" or path.startswith("/static/") or path.startswith("/api/"):
            # The kiosk must always run the code that matches the backend
            response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    if isinstance(response, web.HTTPException):
        raise response
    return response


class WebServer:
    """aiohttp-based HTTP + WebSocket server."""

    def __init__(self, config: dict, broker) -> None:
        self._config = config
        self._broker = broker
        self._app = web.Application(middlewares=[_headers_middleware])
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    @property
    def app(self) -> web.Application:
        return self._app

    def _setup_routes(self) -> None:
        r = self._app.router
        r.add_get("/", self._index)
        r.add_get("/ws", self._websocket_handler)
        r.add_get("/preview", self._preview_handler)
        r.add_get("/preview.jpg", self._preview_still)
        r.add_get("/media/{name}", self._media)
        r.add_get("/api/status", self._status)
        r.add_get("/healthz", self._healthz)
        r.add_static("/static", _STATIC_DIR)
        for d in output_dirs(self._config):
            os.makedirs(d, exist_ok=True)
        r.add_static("/output", output_root(self._config))

    def _ui_config(self) -> dict:
        cfg = self._broker.config if hasattr(self._broker, "config") else self._config
        ui = dict(cfg.get("ui", {}))
        ui["kiosk_mode"] = bool(cfg.get("app", {}).get("kiosk_mode", False))
        ui["boot_id"] = getattr(self._broker, "boot_id", "")
        ui["version"] = __version__
        return ui

    async def _index(self, request: web.Request) -> web.Response:
        index_path = os.path.join(_STATIC_DIR, "index.html")
        with open(index_path, encoding="utf-8") as fh:
            html = fh.read()
        payload = json.dumps(self._ui_config()).replace("</", "<\\/")
        html = html.replace("<!-- RUNTIME_CONFIG -->", f"<script>window.__cfg = {payload};</script>")
        return web.Response(text=html, content_type="text/html")

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(self._broker.status())

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _media(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if not _MEDIA_NAME.match(name):
            raise web.HTTPNotFound()
        data = self._broker.get_screen_image(name)
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=64 * 1024)
        await ws.prepare(request)

        self._broker.register_ws_client(ws)
        logger.debug("WebSocket connection established from %s", request.remote)
        try:
            await ws.send_str(json.dumps(self._broker.snapshot()))
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_ws_message(ws, msg.data)
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        except Exception as exc:
            logger.debug("WebSocket closed with error: %s", exc)
        finally:
            self._broker.unregister_ws_client(ws)
        return ws

    async def _handle_ws_message(self, ws, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Received invalid JSON over WebSocket: %r", raw[:200])
            return
        if not isinstance(message, dict):
            return

        event = message.get("event")
        if event == "trigger":
            await self._broker.trigger()
        elif event == "confirm":
            await self._broker.confirm()
        elif event == "retake":
            await self._broker.retake()
        elif event == "hello":
            await ws.send_str(json.dumps(self._broker.snapshot()))
        elif event == "client_error":
            logger.warning("Browser error: %s", str(message.get("message"))[:500])
        else:
            logger.debug("Unknown WebSocket event: %s", event)

    async def _preview_handler(self, request: web.Request) -> web.StreamResponse:
        """MJPEG live-view endpoint.  All viewers share one camera reader."""
        response = web.StreamResponse(
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store",
                "Connection": "close",
            }
        )
        await response.prepare(request)
        last_id = -1
        try:
            while not self._broker.is_shutdown_requested():
                camera = self._broker.camera
                if camera is None:
                    await asyncio.sleep(0.25)
                    continue
                last_id, frame = await camera.wait_frame(last_id, timeout=2.0)
                if frame is None:
                    continue
                await response.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
                )
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:
            logger.debug("Preview stream ended: %s", exc)
        return response

    async def _preview_still(self, request: web.Request) -> web.Response:
        camera = self._broker.camera
        frame = camera.latest_frame() if camera else None
        if frame is None:
            raise web.HTTPServiceUnavailable(text="no frame")
        return web.Response(body=frame, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def run(self) -> None:
        host: str = self._config["app"]["host"]
        port: int = self._config["app"]["port"]

        self._runner = web.AppRunner(self._app, handle_signals=False, shutdown_timeout=2)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port, reuse_address=True)
        try:
            await site.start()
        except OSError as exc:
            logger.critical("Cannot listen on %s:%s — %s (is the booth already running?)", host, port, exc)
            self._broker.request_shutdown()
            await self._runner.cleanup()
            raise
        shown = "localhost" if host in ("0.0.0.0", "127.0.0.1", "::") else host
        logger.info("Web server running — open http://%s:%d", shown, port)

        try:
            while not self._broker.is_shutdown_requested():
                await asyncio.sleep(0.5)
        finally:
            await self._runner.cleanup()
            logger.info("Web server stopped")
