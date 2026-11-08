"""CameraManager — makes any camera backend safe to use from the broker.

Responsibilities
----------------
* **One camera thread.**  Every backend call (open, preview, capture,
  release) runs on a single dedicated worker thread.  libgphoto2 and many
  OpenCV drivers crash or hang if used from two threads at once.
* **Shared live view.**  One preview loop reads frames at ``preview_fps``;
  any number of browser MJPEG streams just read the latest frame.
* **Timeouts.**  A hung driver call is abandoned after a timeout; the
  manager swaps in a fresh worker thread and reconnects.
* **Retries + reconnect.**  Failed captures are retried; repeated preview
  failures (camera unplugged / switched off) trigger an automatic
  reconnect loop.  The app keeps running and shows "camera offline".
* **Status.**  Connection changes are reported through ``on_status`` so the
  UI can show what is going on.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

BACKEND_MODULES: dict[str, str] = {
    "webcam": "cameras.webcam",
    "dslr_gphoto2": "cameras.dslr_gphoto2",
    "ip_cam": "cameras.ip_cam",
    "dummy": "cameras.dummy",
}

_PREVIEW_TIMEOUT = 5.0
_OPEN_TIMEOUT = 20.0
_RELEASE_TIMEOUT = 5.0
_PREVIEW_FAILURES_BEFORE_RECONNECT = 5


class CameraError(RuntimeError):
    """Raised when a photo could not be taken after all retries."""


class CameraManager:
    def __init__(
        self,
        camera_config: dict[str, Any],
        on_status: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> None:
        self._cfg = camera_config
        self._on_status = on_status
        self._executor = self._new_executor()
        self._backend = None
        self._backend_name: str | None = None
        self._io_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._running = False
        self._wake = asyncio.Event()

        self.preview_wanted = True
        self._capturing = False
        self._frame: bytes | None = None
        self._frame_id = 0
        self._frame_cond = asyncio.Condition()
        self._preview_failures = 0

        self.status: dict[str, Any] = {
            "connected": False,
            "backend": None,
            "fallback": False,
            "message": "Starting camera…",
            "preview": False,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _new_executor() -> concurrent.futures.ThreadPoolExecutor:
        return concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera")

    async def start(self) -> None:
        if self._task is None:
            self._running = True
            self._task = asyncio.create_task(self._supervisor(), name="camera-supervisor")

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._disconnect("Camera stopped")
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def reconfigure(self, camera_config: dict[str, Any]) -> None:
        """Switch to a new camera configuration (closes and re-opens)."""
        self._cfg = camera_config
        await self._disconnect("Camera settings changed — reconnecting…")
        self._wake.set()

    @property
    def connected(self) -> bool:
        return self._backend is not None

    @property
    def backend_name(self) -> str | None:
        return self._backend_name

    # ------------------------------------------------------------------
    # Thread helpers
    # ------------------------------------------------------------------

    async def _call(self, fn: Callable, timeout: float, *args):
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._executor, fn, *args)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error("Camera call %s timed out after %.0fs — replacing camera thread",
                         getattr(fn, "__name__", fn), timeout)
            # The stuck thread cannot be killed; abandon it and use a new one.
            old = self._executor
            self._executor = self._new_executor()
            old.shutdown(wait=False, cancel_futures=True)
            self._backend = None
            raise

    async def _set_status(self, **changes) -> None:
        new = {**self.status, **changes}
        if new == self.status:
            return
        self.status = new
        level = logging.INFO if new.get("connected") else logging.WARNING
        logger.log(level, "Camera status: %s", new.get("message"))
        if self._on_status is not None:
            try:
                res = self._on_status(dict(new))
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.exception("Camera status callback failed")

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def _open_blocking(self, name: str):
        module_path = BACKEND_MODULES.get(name)
        if module_path is None:
            raise RuntimeError(f"Unknown camera backend: {name!r}")
        module = importlib.import_module(module_path)
        backend_cfg = self._cfg.get("backends", {}).get(name, {})
        cls = module.CameraBackend
        if not cls.is_available():
            raise RuntimeError(f"Camera '{name}' not found")
        cam = cls(backend_cfg)
        cam.initialize()
        return cam

    async def _connect(self) -> bool:
        primary = self._cfg.get("backend", "webcam")
        names = [primary]
        if self._cfg.get("fallback_to_webcam") and primary != "webcam":
            names.append("webcam")

        errors = []
        for name in names:
            try:
                cam = await self._call(self._open_blocking, _OPEN_TIMEOUT, name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            self._backend = cam
            self._backend_name = name
            self._preview_failures = 0
            fallback = name != primary
            await self._set_status(
                connected=True,
                backend=name,
                fallback=fallback,
                preview=bool(cam.has_preview()),
                message=f"Camera ready ({name}{', fallback' if fallback else ''})",
            )
            return True

        await self._set_status(
            connected=False, backend=None, fallback=False, preview=False,
            message="Camera not connected — " + "; ".join(errors),
        )
        return False

    async def _disconnect(self, message: str) -> None:
        backend, self._backend = self._backend, None
        self._backend_name = None
        if backend is not None:
            try:
                await self._call(backend.release, _RELEASE_TIMEOUT)
            except Exception as exc:
                logger.debug("Error while releasing camera: %s", exc)
        await self._set_status(connected=False, backend=None, preview=False, message=message)

    # ------------------------------------------------------------------
    # Supervisor: connect, reconnect and live view
    # ------------------------------------------------------------------

    async def _supervisor(self) -> None:
        while self._running:
            try:
                if self._backend is None:
                    async with self._io_lock:
                        ok = await self._connect()
                    if not ok:
                        await self._sleep(float(self._cfg.get("reconnect_interval_seconds", 3)))
                    continue

                if self.preview_wanted and not self._capturing and self._backend.has_preview():
                    started = time.monotonic()
                    await self._preview_once()
                    fps = float(self._cfg.get("preview_fps", 15))
                    delay = max(0.0, 1.0 / fps - (time.monotonic() - started))
                    await asyncio.sleep(delay)
                else:
                    await self._sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the supervisor die
                logger.exception("Camera supervisor error")
                await asyncio.sleep(1)

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _preview_once(self) -> None:
        async with self._io_lock:
            backend = self._backend
            if backend is None or self._capturing:
                return
            try:
                frame = await self._call(backend.get_preview_frame, _PREVIEW_TIMEOUT)
            except Exception as exc:
                self._preview_failures += 1
                logger.debug("Preview frame failed (%d): %s", self._preview_failures, exc)
                if self._preview_failures >= _PREVIEW_FAILURES_BEFORE_RECONNECT:
                    logger.warning("Live view keeps failing — reconnecting camera")
                    await self._disconnect("Camera lost — reconnecting…")
                return
        self._preview_failures = 0
        await self._publish(frame)

    async def _publish(self, frame: bytes) -> None:
        async with self._frame_cond:
            self._frame = frame
            self._frame_id += 1
            self._frame_cond.notify_all()

    async def wait_frame(self, last_id: int, timeout: float = 2.0) -> tuple[int, bytes | None]:
        """Wait for a preview frame newer than *last_id*."""
        async with self._frame_cond:
            try:
                await asyncio.wait_for(
                    self._frame_cond.wait_for(lambda: self._frame_id != last_id), timeout
                )
            except asyncio.TimeoutError:
                return last_id, None
            return self._frame_id, self._frame

    def latest_frame(self) -> bytes | None:
        return self._frame

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    async def capture(self) -> bytes:
        """Take one photo; retries and reconnects as needed.

        Raises ``CameraError`` if no photo could be taken.
        """
        retries = int(self._cfg.get("capture_retries", 2))
        timeout = float(self._cfg.get("capture_timeout_seconds", 30))
        last_error: Exception | None = None
        self._capturing = True
        try:
            for attempt in range(retries + 1):
                async with self._io_lock:
                    if self._backend is None:
                        await self._connect()
                    backend = self._backend
                    if backend is None:
                        last_error = CameraError(self.status.get("message") or "Camera not connected")
                    else:
                        try:
                            data = await self._call(backend.capture, timeout)
                            if not data or data[:2] != b"\xff\xd8":
                                raise CameraError("Camera returned an invalid image")
                            return data
                        except Exception as exc:
                            last_error = exc
                            logger.warning("Capture attempt %d/%d failed: %s", attempt + 1, retries + 1, exc)
                            if attempt >= 1 or isinstance(exc, asyncio.TimeoutError):
                                # Second failure in a row: fully re-open the camera
                                await self._disconnect("Camera error — reconnecting…")
                if attempt < retries:
                    await asyncio.sleep(0.5 + attempt)
        finally:
            self._capturing = False
            self._wake.set()
        raise CameraError(f"Could not take photo: {last_error}")
