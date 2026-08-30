"""Lightweight aiohttp web server.

Provides:
  - ``GET /``             → serves ``web/static/index.html``
  - ``GET /static/…``     → serves JS/CSS assets
  - ``GET /output/…``     → serves captured images from the output directory
  - ``GET /ws``           → WebSocket endpoint (bidirectional JSON protocol)
  - ``GET /preview``      → MJPEG live-view stream
"""

import asyncio
import base64
import json
import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_WEB_DIR = os.path.dirname(__file__)


class WebServer:
    """aiohttp-based HTTP + WebSocket server."""

    def __init__(self, config: dict, broker) -> None:
        self._config = config
        self._broker = broker
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/ws", self._websocket_handler)
        self._app.router.add_get("/preview", self._preview_handler)
        self._app.router.add_static("/static", _STATIC_DIR)
        output_dir = self._config["storage"]["local_disk"].get("output_dir", "output/")
        abs_output = os.path.abspath(output_dir)
        os.makedirs(abs_output, exist_ok=True)
        self._app.router.add_static("/output", abs_output)

    async def _index(self, request: web.Request) -> web.Response:
        index_path = os.path.join(_STATIC_DIR, "index.html")
        with open(index_path, encoding="utf-8") as fh:
            html = fh.read()
        return web.Response(text=html, content_type="text/html")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._broker.register_ws_client(ws)
        logger.debug("WebSocket connection established from %s", request.remote)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_ws_message(ws, msg.data)
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        finally:
            self._broker.unregister_ws_client(ws)

        return ws

    async def _handle_ws_message(self, ws, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Received invalid JSON over WebSocket: %r", raw)
            return

        event = message.get("event")
        if event == "trigger":
            await self._broker.trigger()
        elif event == "confirm":
            await self._broker.confirm()
        elif event == "retake":
            await self._broker.retake()
        else:
            logger.debug("Unknown WebSocket event: %s", event)

    async def _preview_handler(self, request: web.Request) -> web.StreamResponse:
        """MJPEG live-view endpoint.  Streams preview frames to the browser."""
        response = web.StreamResponse(
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache",
            }
        )
        await response.prepare(request)

        try:
            while not response.task.done():
                frame = await self._broker.get_preview_frame()
                if frame is None:
                    await asyncio.sleep(0.1)
                    continue
                boundary = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                )
                await response.write(boundary + frame + b"\r\n")
                await asyncio.sleep(0.05)  # ~20 fps
        except (ConnectionResetError, asyncio.CancelledError):
            pass

        return response

    async def run(self) -> None:
        host: str = self._config["app"]["host"]
        port: int = self._config["app"]["port"]

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        logger.info("Web server running at http://%s:%d", host, port)

        # Keep alive until the broker requests shutdown
        while not self._broker.is_shutdown_requested():
            await asyncio.sleep(0.5)

        await self._runner.cleanup()
        logger.info("Web server stopped")
