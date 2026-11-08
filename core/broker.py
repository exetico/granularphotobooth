"""Core Event Broker — the asynchronous state machine that glues every component together.

State flow::

    IDLE ➜ (COUNTDOWN ➜ CAPTURE) × shots ➜ PROCESS ➜ PREVIEW ➜ IDLE
                                                       │
                                            Retake ────┘ (new session)

Stability rules implemented here:

* Every shot is written to disk **immediately** after capture — a crash,
  power cut or "Retake" never loses an original photo.
* The camera is only touched through ``CameraManager`` (one thread,
  timeouts, retries, auto-reconnect).
* Every session has a hard time limit; whatever goes wrong, the booth ends
  up back in IDLE with a friendly error on screen.
* New browser windows get a full state snapshot on connect, so a reloaded
  or reconnected kiosk always shows the right screen.
"""

import asyncio
import collections
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from typing import Any, Set

from cameras.manager import CameraError, CameraManager
from core.state import State

logger = logging.getLogger(__name__)

_SCREEN_CACHE_SIZE = 60
_SCREEN_MAX_PX = 1600
_BROADCAST_TIMEOUT = 2.0


def _screen_version(jpeg: bytes, max_px: int = _SCREEN_MAX_PX) -> bytes:
    """Return a display-sized JPEG (fast; uses JPEG draft decoding)."""
    import io

    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(jpeg))
    if max(img.size) <= max_px and not img.getexif().get(0x0112):
        return jpeg
    img.draft("RGB", (max_px, max_px))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class Broker:
    """Asynchronous event broker / state machine."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._state = State.IDLE
        self._state_data: dict = {}
        self._ws_clients: Set[Any] = set()
        self._shutdown_event = asyncio.Event()
        self._trigger_event = asyncio.Event()
        self._review_action: str | None = None
        self._last_trigger = 0.0
        self._started = False
        self._boot_id = uuid.uuid4().hex[:12]

        self._camera: CameraManager | None = None
        self._compositor = None
        self._storage_pipeline = None
        self._input_triggers: list = []

        self._frames: list[bytes] = []
        self._session_id: str | None = None
        self._last_session: dict | None = None
        self._screen_cache: "collections.OrderedDict[str, bytes]" = collections.OrderedDict()
        self._disk_free_mb: float | None = None
        self._sessions_completed = 0
        self._last_error: str | None = None

        self._trigger_fired = False
        self._pending_config: dict | None = None
        self._bg_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Public API used by WebServer and Input triggers
    # ------------------------------------------------------------------

    @property
    def config(self) -> dict:
        return self._config

    @property
    def state(self) -> State:
        return self._state

    @property
    def camera(self) -> CameraManager | None:
        return self._camera

    @property
    def boot_id(self) -> str:
        return self._boot_id

    def register_ws_client(self, ws: Any) -> None:
        self._ws_clients.add(ws)
        logger.debug("WebSocket client connected (total: %d)", len(self._ws_clients))

    def unregister_ws_client(self, ws: Any) -> None:
        self._ws_clients.discard(ws)
        logger.debug("WebSocket client disconnected (total: %d)", len(self._ws_clients))

    def request_shutdown(self) -> None:
        self._shutdown_event.set()
        self._trigger_event.set()  # wake anything waiting

    def is_shutdown_requested(self) -> bool:
        """Return True if a graceful shutdown has been requested."""
        return self._shutdown_event.is_set()

    def _disk_full(self) -> bool:
        min_free = float(self._config.get("app", {}).get("min_free_disk_mb", 0) or 0)
        return self._disk_free_mb is not None and self._disk_free_mb < min_free

    async def trigger(self) -> None:
        """Request a new photo sequence (ignored unless IDLE)."""
        if self._state is not State.IDLE:
            logger.debug("trigger() ignored — current state is %s", self._state.name)
            return
        debounce = float(self._config.get("input", {}).get("debounce_ms", 0) or 0) / 1000
        now = time.monotonic()
        if now - self._last_trigger < debounce:
            logger.debug("trigger() debounced")
            return
        if self._started:
            if self._camera is not None and not self._camera.connected:
                await self._error("camera_offline", self._camera.status.get("message", "Camera not connected"))
                return
            if self._disk_full():
                await self._error("disk_full", f"Only {self._disk_free_mb:.0f} MB free on disk")
                return
        self._last_trigger = now
        self._trigger_event.set()

    async def confirm(self) -> None:
        """Finish the review screen now (guest pressed Done/Keep)."""
        if self._state is State.PREVIEW:
            self._review_action = "confirm"
            self._trigger_event.set()

    async def retake(self) -> None:
        """Reject the photos just taken and start again."""
        if self._state is State.PREVIEW:
            self._review_action = "retake"
            self._trigger_event.set()

    def snapshot(self) -> dict:
        """Everything a freshly connected UI needs to render the right screen."""
        return {
            "event": "hello",
            "data": {
                "boot_id": self._boot_id,
                "state": self._state.name,
                "state_data": self._state_data,
                "camera": self._camera.status if self._camera else None,
                "disk_free_mb": self._disk_free_mb,
                "disk_full": self._disk_full(),
            },
        }

    def status(self) -> dict:
        from core.version import __version__

        return {
            "version": __version__,
            "boot_id": self._boot_id,
            "state": self._state.name,
            "camera": self._camera.status if self._camera else None,
            "disk_free_mb": None if self._disk_free_mb is None else round(self._disk_free_mb),
            "disk_full": self._disk_full(),
            "sessions_completed": self._sessions_completed,
            "last_session": self._last_session,
            "last_error": self._last_error,
            "clients": len(self._ws_clients),
        }

    def get_screen_image(self, name: str) -> bytes | None:
        return self._screen_cache.get(name)

    # ------------------------------------------------------------------
    # Config hot-reload
    # ------------------------------------------------------------------

    def reload_config(self, new_config: dict[str, Any]) -> None:
        """Apply a hot-reloaded config.

        Changes take effect on the *next* session — never mid-capture.
        ``app.host`` / ``app.port`` require a restart.
        """
        if self._state is not State.IDLE:
            logger.info("Config reload queued — will apply after current session ends")
            self._pending_config = new_config
            return

        self._apply_config(new_config)
        try:
            asyncio.get_running_loop()
            self._spawn(self._broadcast({"event": "config_reloaded", "data": {}}))
        except RuntimeError:
            pass  # No event loop — called from sync context (e.g. tests)

    def _apply_config(self, new_config: dict[str, Any]) -> None:
        logger.info("Applying configuration")
        old = self._config
        self._config = new_config
        if old.get("app", {}).get("port") != new_config.get("app", {}).get("port") or \
                old.get("app", {}).get("host") != new_config.get("app", {}).get("host"):
            logger.warning("app.host / app.port changed — restart the app for this to take effect")
        self._load_compositor()
        old_pipeline = self._storage_pipeline
        self._load_storage()
        if old_pipeline is not None:
            old_pipeline.shutdown()  # running uploads still finish

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._camera is not None and old.get("camera") != new_config.get("camera"):
            self._spawn(self._camera.reconfigure(new_config["camera"]))
        if old.get("input") != new_config.get("input"):
            self._stop_inputs()
            self._start_inputs()

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Bootstrap components and run the state machine until shutdown."""
        await self._load_components()
        self._started = True
        self._spawn(self._health_loop())
        try:
            while not self._shutdown_event.is_set():
                await self._run_idle()
                if self._pending_config is not None:
                    self._apply_config(self._pending_config)
                    self._pending_config = None
                    await self._broadcast({"event": "config_reloaded", "data": {}})
                if self._shutdown_event.is_set():
                    break
                if not self._trigger_fired:
                    continue
                restart = True
                while restart and not self._shutdown_event.is_set():
                    restart = await self._run_session_guarded()
        finally:
            await self._teardown()

    async def _run_idle(self) -> None:
        self._trigger_fired = False
        self._trigger_event.clear()
        await self._transition(State.IDLE)
        await self._trigger_event.wait()
        self._trigger_fired = not self._shutdown_event.is_set()

    async def _run_session_guarded(self) -> bool:
        limit = float(self._config.get("sequence", {}).get("max_session_seconds", 300))
        try:
            return await asyncio.wait_for(self._run_sequence(), timeout=limit)
        except asyncio.TimeoutError:
            logger.error("Session exceeded %.0fs — aborting", limit)
            await self._error("timeout", "The session took too long and was stopped.")
        except CameraError as exc:
            await self._error("camera", str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Unexpected error in session: %s", exc)
            await self._error("internal", str(exc))
        return False

    async def _run_sequence(self) -> bool:
        """Run one session.  Returns True if the guest pressed Retake (restart)."""
        seq_cfg = self._config.get("sequence", {})
        shots = int(seq_cfg.get("shots", 1))
        countdown = int(seq_cfg.get("countdown_seconds", 3))
        first_countdown = seq_cfg.get("first_countdown_seconds")
        first_countdown = countdown if first_countdown is None else int(first_countdown)
        show_shot = float(seq_cfg.get("show_shot_seconds", 0) or 0)
        between_delay = float(seq_cfg.get("delay_between_shots_seconds", 0) or 0)
        review_seconds = float(seq_cfg.get("preview_duration_seconds", 8))

        loop = asyncio.get_running_loop()
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self._session_id = session_id
        self._frames = []
        shot_urls: list[str] = []
        raw_paths: list[str] = []
        logger.info("Session %s started (%d shots)", session_id, shots)

        for shot in range(1, shots + 1):
            # ---- COUNTDOWN ----
            seconds = first_countdown if shot == 1 else countdown
            await self._transition(State.COUNTDOWN, {"shot": shot, "total": shots, "seconds": seconds})
            for remaining in range(seconds, 0, -1):
                if self._shutdown_event.is_set():
                    return False
                await self._broadcast({"event": "countdown_tick", "data": {"remaining": remaining}})
                await asyncio.sleep(1)

            # ---- CAPTURE ----
            await self._transition(State.CAPTURE, {"shot": shot, "total": shots})
            frame = await self._capture_frame()
            if frame is None:
                raise CameraError("Camera returned no frame.")
            self._frames.append(frame)

            # Save the original right away — never lose a photo
            meta = {"shots": shots, "frame": shot, "type": "raw", "session_id": session_id}
            raw_paths.extend(await self._store(frame, meta))

            url = await self._cache_screen(f"{session_id}_shot{shot}.jpg", frame)
            shot_urls.append(url)
            await self._broadcast({"event": "shot_captured", "data": {"shot": shot, "total": shots, "url": url}})
            if show_shot:
                await asyncio.sleep(show_shot)
            if shot < shots and between_delay:
                await asyncio.sleep(between_delay)

        # ---- PROCESS ----
        await self._transition(State.PROCESS, {"total": shots})
        composite: bytes | None = None
        composite_path: str | None = None
        if self._compositor is not None:
            try:
                composite = await loop.run_in_executor(
                    None, self._compositor.compose, list(self._frames), self._config.get("compositor", {})
                )
            except Exception as exc:
                logger.exception("Compositor error (photos are saved): %s", exc)
                await self._broadcast({"event": "warning", "data": {"message": f"Layout error: {exc}"}})
        if composite is not None:
            paths = await self._store(composite, {"shots": shots, "type": "composite", "session_id": session_id})
            composite_path = paths[0] if paths else None
            composite_url = await self._cache_screen(f"{session_id}_composite.jpg", composite)
        else:
            composite_url = shot_urls[-1]

        self._last_session = {
            "session_id": session_id,
            "raw": raw_paths,
            "composite": composite_path,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        await self._broadcast({"event": "storage_complete", "data": {"paths": raw_paths + ([composite_path] if composite_path else [])}})

        # ---- PREVIEW / review ----
        self._review_action = None
        self._trigger_event.clear()
        await self._transition(State.PREVIEW, {
            "session_id": session_id,
            "composite": composite_url,
            "frames": shot_urls,
            "seconds": review_seconds,
        })
        try:
            await asyncio.wait_for(self._trigger_event.wait(), timeout=review_seconds)
        except asyncio.TimeoutError:
            pass
        action = self._review_action or "timeout"
        self._sessions_completed += 1

        if action == "retake":
            logger.info("Session %s rejected by guest (photos kept in raw folder)", session_id)
            if composite_path:
                await loop.run_in_executor(None, self._move_rejected, composite_path)
            self._last_session["rejected"] = True
            return self._config.get("sequence", {}).get("retake_mode", "restart") == "restart"
        logger.info("Session %s finished (%s)", session_id, action)
        return False

    @staticmethod
    def _move_rejected(path: str) -> None:
        try:
            rejected_dir = os.path.join(os.path.dirname(path), "rejected")
            os.makedirs(rejected_dir, exist_ok=True)
            shutil.move(path, os.path.join(rejected_dir, os.path.basename(path)))
        except OSError as exc:
            logger.warning("Could not move rejected composite: %s", exc)

    # ------------------------------------------------------------------
    # Component actions
    # ------------------------------------------------------------------

    async def _capture_frame(self) -> bytes | None:
        if self._camera is None:
            logger.warning("No camera loaded")
            return None
        return await self._camera.capture()

    async def _store(self, image: bytes, meta: dict) -> list[str]:
        if self._storage_pipeline is None:
            return []
        loop = asyncio.get_running_loop()
        paths = await loop.run_in_executor(None, self._storage_pipeline.store_local, image, meta)
        self._storage_pipeline.store_remote_background(image, meta)
        return paths

    async def _cache_screen(self, name: str, jpeg: bytes) -> str:
        loop = asyncio.get_running_loop()
        try:
            small = await loop.run_in_executor(None, _screen_version, jpeg)
        except Exception as exc:
            logger.warning("Could not create screen preview: %s", exc)
            small = jpeg
        self._screen_cache[name] = small
        self._screen_cache.move_to_end(name)
        while len(self._screen_cache) > _SCREEN_CACHE_SIZE:
            self._screen_cache.popitem(last=False)
        return f"/media/{name}"

    async def _process_and_store(self) -> None:
        """Compose + store the current frames (kept for API compatibility)."""
        loop = asyncio.get_running_loop()
        final = self._frames[-1]
        if self._compositor is not None:
            try:
                final = await loop.run_in_executor(
                    None, self._compositor.compose, self._frames, self._config.get("compositor", {})
                )
            except Exception as exc:
                logger.exception("Compositor error: %s", exc)
        paths = []
        for idx, frame in enumerate(self._frames, start=1):
            paths += await self._store(frame, {"shots": len(self._frames), "frame": idx, "type": "raw"})
        paths += await self._store(final, {"shots": len(self._frames), "type": "composite"})
        await self._broadcast({"event": "storage_complete", "data": {"paths": paths}})

    # ------------------------------------------------------------------
    # Broadcasting helpers
    # ------------------------------------------------------------------

    async def _transition(self, new_state: State, data: dict | None = None) -> None:
        self._state = new_state
        self._state_data = data or {}
        if self._camera is not None:
            self._camera.preview_wanted = new_state in (State.IDLE, State.COUNTDOWN)
        logger.info("State ➜ %s %s", new_state.name, {k: v for k, v in self._state_data.items() if k != "frames"} or "")
        await self._broadcast({"event": "state_change", "state": new_state.name, "data": self._state_data})

    async def _error(self, code: str, message: str) -> None:
        self._last_error = f"{datetime.now().isoformat(timespec='seconds')} {code}: {message}"
        logger.error("Error [%s]: %s", code, message)
        await self._broadcast({"event": "error", "data": {"code": code, "message": message}})

    async def _broadcast(self, message: dict) -> None:
        text = json.dumps(message)
        clients = list(self._ws_clients)
        if not clients:
            return

        async def _send(ws):
            try:
                await asyncio.wait_for(ws.send_str(text), timeout=_BROADCAST_TIMEOUT)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(_send(ws) for ws in clients))
        for ws in results:
            if ws is not None:
                self._ws_clients.discard(ws)

    async def _on_camera_status(self, status: dict) -> None:
        await self._broadcast({"event": "camera_status", "data": status})

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def _health_loop(self) -> None:
        """Periodically check disk space and tell the UI."""
        was_full = None
        while not self._shutdown_event.is_set():
            try:
                if self._storage_pipeline is not None:
                    loop = asyncio.get_running_loop()
                    self._disk_free_mb = await loop.run_in_executor(None, self._storage_pipeline.free_disk_mb)
                full = self._disk_full()
                if full != was_full:
                    if full:
                        logger.error("Low disk space: %.0f MB free", self._disk_free_mb or 0)
                    await self._broadcast({"event": "disk_status", "data": {
                        "disk_full": full, "disk_free_mb": self._disk_free_mb}})
                    was_full = full
            except Exception:
                logger.exception("Health check failed")
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Component lifecycle
    # ------------------------------------------------------------------

    async def _load_components(self) -> None:
        self._load_compositor()
        self._load_storage()
        if self._storage_pipeline is not None:
            loop = asyncio.get_running_loop()
            try:
                self._disk_free_mb = await loop.run_in_executor(None, self._storage_pipeline.free_disk_mb)
            except Exception:
                logger.exception("Could not check disk space")
        self._camera = CameraManager(self._config["camera"], on_status=self._on_camera_status)
        self._camera.preview_wanted = True
        await self._camera.start()
        self._start_inputs()

    def _start_inputs(self) -> None:
        names = self._config.get("input", {}).get("triggers", [])
        if "keyboard" in names:
            from inputs.keyboard import KeyboardTrigger

            trig = KeyboardTrigger(self._config.get("input", {}).get("keyboard", {}))
            trig.start(lambda: self._spawn(self.trigger()))
            self._input_triggers.append(trig)

    def _stop_inputs(self) -> None:
        for trig in self._input_triggers:
            try:
                trig.stop()
            except Exception:
                logger.exception("Error stopping input trigger")
        self._input_triggers = []

    def _load_compositor(self) -> None:
        comp_cfg = self._config.get("compositor", {})
        if not comp_cfg.get("enabled", False) or not comp_cfg.get("template_map"):
            from compositor.passthrough import Compositor
            self._compositor = Compositor()
            logger.info("Compositor: passthrough")
            return
        try:
            from compositor.grid_layout import Compositor, load_layout
            layout = load_layout(comp_cfg["template_map"])  # validate early
            shots = self._config.get("sequence", {}).get("shots", 1)
            if len(layout["slots"]) < shots:
                logger.warning("Layout %s has %d slots but sequence.shots is %d",
                               comp_cfg["template_map"], len(layout["slots"]), shots)
            self._compositor = Compositor()
            logger.info("Compositor: grid_layout (%s)", comp_cfg["template_map"])
        except Exception as exc:
            logger.error("Layout problem (%s) — falling back to single photo output", exc)
            from compositor.passthrough import Compositor
            self._compositor = Compositor()

    def _load_storage(self) -> None:
        from storage.pipeline import StoragePipeline
        self._storage_pipeline = StoragePipeline(self._config)
        if not self._storage_pipeline.local_targets:
            logger.warning("No local_disk storage enabled — photos are NOT saved on this computer")
        logger.info("Storage targets: %s", self._config["storage"].get("targets"))

    async def _teardown(self) -> None:
        self._stop_inputs()
        for task in list(self._bg_tasks):
            task.cancel()
        if self._camera is not None:
            try:
                await self._camera.stop()
            except Exception:
                logger.exception("Error while stopping camera")
        if self._storage_pipeline is not None:
            self._storage_pipeline.shutdown()
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        logger.info("Broker shut down cleanly")
