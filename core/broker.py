"""Core Event Broker — the asynchronous state machine that glues every component together.

State flow:
    IDLE ➜ COUNTDOWN ➜ CAPTURE ➜ PREVIEW ➜ PROCESS ➜ IDLE

The broker:
  - Manages state transitions via an asyncio event loop.
  - Broadcasts JSON messages to all connected WebSocket clients.
  - Dynamically loads the configured camera backend, input triggers,
    compositor, and storage pipeline.
  - Provides a ``trigger()`` coroutine that any component can call to
    start the photo sequence.
"""

import asyncio
import base64
import importlib
import logging
from typing import Any, Callable, Coroutine, Set

from core.state import State

logger = logging.getLogger(__name__)


class Broker:
    """Asynchronous event broker / state machine."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._state = State.IDLE
        self._ws_clients: Set[Any] = set()
        self._shutdown_event = asyncio.Event()
        self._trigger_event = asyncio.Event()

        # Component slots — populated lazily by _load_components()
        self._camera = None
        self._compositor = None
        self._storage_pipeline = None
        self._input_triggers: list = []

        # Captured frames for the current session
        self._frames: list[bytes] = []

        # Pending config to apply on next return to IDLE (set by reload_config)
        self._pending_config: dict | None = None

    # ------------------------------------------------------------------
    # Public API used by WebServer and Input triggers
    # ------------------------------------------------------------------

    def register_ws_client(self, ws: Any) -> None:
        self._ws_clients.add(ws)
        logger.debug("WebSocket client connected (total: %d)", len(self._ws_clients))

    def unregister_ws_client(self, ws: Any) -> None:
        self._ws_clients.discard(ws)
        logger.debug("WebSocket client disconnected (total: %d)", len(self._ws_clients))

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        """Return True if a graceful shutdown has been requested."""
        return self._shutdown_event.is_set()

    async def trigger(self) -> None:
        """Request a new photo sequence from any coroutine context."""
        if self._state is State.IDLE:
            self._trigger_event.set()
        else:
            logger.debug("trigger() ignored — current state is %s", self._state.name)

    async def confirm(self) -> None:
        """Advance from PREVIEW immediately (user pressed confirm)."""
        if self._state is State.PREVIEW:
            self._trigger_event.set()

    async def retake(self) -> None:
        """Cancel the current preview and return to IDLE."""
        if self._state is State.PREVIEW:
            await self._transition(State.IDLE)

    def reload_config(self, new_config: dict[str, Any]) -> None:
        """Apply a hot-reloaded config.

        Safe to call from any thread (e.g. SIGHUP handler or file-watcher).
        Component changes (camera, compositor, storage) take effect on the
        *next* session — never mid-capture.  ``app.host`` / ``app.port`` are
        ignored because they require a server restart.
        """
        # Reload only takes effect when IDLE to avoid disrupting a session
        if self._state is not State.IDLE:
            logger.info("Config reload queued — will apply after current session ends")
            self._pending_config = new_config
            return

        self._apply_config(new_config)
        # If an event loop is running, broadcast the notification asynchronously
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._broadcast({"event": "config_reloaded", "data": {}})
                )
            )
        except RuntimeError:
            pass  # No event loop — called from sync context (e.g. tests)

    def _apply_config(self, new_config: dict[str, Any]) -> None:
        """Synchronously swap components to match *new_config*.

        The ``config_reloaded`` WebSocket broadcast is done separately
        (from the async run loop) so this method stays thread-safe.
        """
        logger.info("Applying hot-reloaded configuration")
        self._config = new_config
        # Release the old camera before loading a new one to avoid device leaks
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None
        # Re-load all swappable components
        self._load_camera()
        self._load_compositor()
        self._load_storage()

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Bootstrap components and run the state machine until shutdown."""
        await self._load_components()

        try:
            while not self._shutdown_event.is_set():
                await self._run_idle()
                # Apply any pending config reload now that we're back in IDLE
                if self._pending_config is not None:
                    self._apply_config(self._pending_config)
                    self._pending_config = None
                    await self._broadcast({"event": "config_reloaded", "data": {}})
                if self._shutdown_event.is_set():
                    break
                await self._run_sequence()
        finally:
            await self._teardown()

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    async def _run_idle(self) -> None:
        self._trigger_event.clear()
        await self._transition(State.IDLE)

        try:
            await asyncio.wait_for(
                self._trigger_event.wait(),
                timeout=None,
            )
        except asyncio.TimeoutError:
            pass

    async def _run_sequence(self) -> None:
        """Execute COUNTDOWN ➜ CAPTURE (×shots) ➜ PREVIEW ➜ PROCESS."""
        seq_cfg = self._config.get("sequence", {})
        shots: int = seq_cfg.get("shots", 1)
        countdown: int = seq_cfg.get("countdown_seconds", 3)
        preview_duration: int = seq_cfg.get("preview_duration_seconds", 8)
        between_delay: int = seq_cfg.get("delay_between_shots_seconds", 2)

        self._frames = []

        try:
            for shot_number in range(1, shots + 1):
                # ---- COUNTDOWN ----
                await self._transition(State.COUNTDOWN, {"shot": shot_number, "total": shots})
                for remaining in range(countdown, 0, -1):
                    if self._shutdown_event.is_set():
                        return
                    await self._broadcast({"event": "countdown_tick", "data": {"remaining": remaining}})
                    await asyncio.sleep(1)

                # ---- CAPTURE ----
                await self._transition(State.CAPTURE, {"shot": shot_number, "total": shots})
                frame = await self._capture_frame()
                if frame is None:
                    logger.error("Capture returned no frame — aborting sequence")
                    await self._broadcast({"event": "error", "data": {"message": "Camera returned no frame."}})
                    return
                self._frames.append(frame)

                if shot_number < shots:
                    await asyncio.sleep(between_delay)

            # ---- PREVIEW ----
            all_frames_b64 = [
                f"data:image/jpeg;base64,{base64.b64encode(f).decode()}"
                for f in self._frames
            ]
            await self._transition(
                State.PREVIEW,
                {"frames": all_frames_b64},
            )

            self._trigger_event.clear()
            try:
                await asyncio.wait_for(self._trigger_event.wait(), timeout=preview_duration)
            except asyncio.TimeoutError:
                pass

            if self._state is not State.PREVIEW:
                return  # retake() moved us to IDLE already

            # ---- PROCESS ----
            await self._transition(State.PROCESS)
            await self._process_and_store()

        except asyncio.CancelledError:
            logger.info("Sequence cancelled")
        except Exception as exc:
            logger.exception("Unexpected error in sequence: %s", exc)
            await self._broadcast({"event": "error", "data": {"message": str(exc)}})

    # ------------------------------------------------------------------
    # Component actions
    # ------------------------------------------------------------------

    async def _capture_frame(self) -> bytes | None:
        if self._camera is None:
            logger.warning("No camera loaded — returning None")
            return None
        try:
            loop = asyncio.get_running_loop()
            frame: bytes = await loop.run_in_executor(None, self._camera.capture)
            return frame
        except Exception as exc:
            logger.exception("Camera capture error: %s", exc)
            return None

    async def _process_and_store(self) -> None:
        loop = asyncio.get_running_loop()

        # Compositor
        if self._compositor is not None:
            try:
                final_image: bytes = await loop.run_in_executor(
                    None,
                    self._compositor.compose,
                    self._frames,
                    self._config.get("compositor", {}),
                )
            except Exception as exc:
                logger.exception("Compositor error: %s", exc)
                final_image = self._frames[-1]
        else:
            final_image = self._frames[-1]

        # Storage pipeline
        if self._storage_pipeline is not None:
            all_paths: list[str] = []

            # Save each individual raw frame first
            for idx, raw_frame in enumerate(self._frames, start=1):
                meta = {"shots": len(self._frames), "frame": idx, "type": "raw"}
                try:
                    paths = await loop.run_in_executor(
                        None,
                        self._storage_pipeline.store,
                        raw_frame,
                        meta,
                    )
                    all_paths.extend(paths)
                except Exception as exc:
                    logger.exception("Storage error for raw frame %d: %s", idx, exc)

            # Save the final composite (or single processed image)
            composite_meta = {"shots": len(self._frames), "type": "composite"}
            try:
                paths = await loop.run_in_executor(
                    None,
                    self._storage_pipeline.store,
                    final_image,
                    composite_meta,
                )
                all_paths.extend(paths)
            except Exception as exc:
                logger.exception("Storage pipeline error for composite: %s", exc)
                await self._broadcast(
                    {"event": "error", "data": {"message": f"Storage error: {exc}"}}
                )
                return

            await self._broadcast(
                {"event": "storage_complete", "data": {"paths": all_paths}}
            )

    # ------------------------------------------------------------------
    # Broadcasting helpers
    # ------------------------------------------------------------------

    async def _transition(self, new_state: State, data: dict | None = None) -> None:
        self._state = new_state
        payload: dict = {"event": "state_change", "state": new_state.name, "data": data or {}}
        logger.info("State ➜ %s", new_state.name)
        await self._broadcast(payload)

    async def _broadcast(self, message: dict) -> None:
        import json
        text = json.dumps(message)
        dead: list = []
        for ws in list(self._ws_clients):
            try:
                await ws.send_str(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.discard(ws)

    # ------------------------------------------------------------------
    # Component lifecycle
    # ------------------------------------------------------------------

    async def _load_components(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_camera)
        await loop.run_in_executor(None, self._load_compositor)
        await loop.run_in_executor(None, self._load_storage)

    def _load_camera(self) -> None:
        backend_name: str = self._config["camera"]["backend"]
        backends = {
            "webcam": "cameras.webcam",
            "dslr_gphoto2": "cameras.dslr_gphoto2",
            "ip_cam": "cameras.ip_cam",
        }

        module_path = backends.get(backend_name)
        if module_path is None:
            logger.error("Unknown camera backend: %s", backend_name)
            return

        try:
            module = importlib.import_module(module_path)
            backend_cfg = self._config["camera"]["backends"].get(backend_name, {})
            cam = module.CameraBackend(backend_cfg)
            if not cam.is_available():
                logger.warning(
                    "Camera backend '%s' is not available — falling back to webcam",
                    backend_name,
                )
                if backend_name != "webcam":
                    from cameras import webcam as wm
                    fallback_cfg = self._config["camera"]["backends"].get("webcam", {})
                    cam = wm.CameraBackend(fallback_cfg)
                    if not cam.is_available():
                        logger.error("Webcam fallback also unavailable")
                        return
                else:
                    return

            cam.initialize()
            self._camera = cam
            logger.info("Camera loaded: %s", cam.__class__.__module__)
        except Exception as exc:
            logger.exception("Failed to load camera backend '%s': %s", backend_name, exc)

    def _load_compositor(self) -> None:
        comp_cfg = self._config.get("compositor", {})
        shots: int = self._config.get("sequence", {}).get("shots", 1)

        if not comp_cfg.get("enabled", False) or shots <= 1:
            from compositor.passthrough import Compositor
            self._compositor = Compositor()
            logger.info("Compositor: passthrough (single shot or disabled)")
        else:
            try:
                from compositor.grid_layout import Compositor
                self._compositor = Compositor()
                logger.info("Compositor: grid_layout")
            except Exception as exc:
                logger.exception("Failed to load grid compositor: %s", exc)
                from compositor.passthrough import Compositor
                self._compositor = Compositor()

    def _load_storage(self) -> None:
        from storage.pipeline import StoragePipeline
        self._storage_pipeline = StoragePipeline(self._config)
        logger.info("Storage pipeline loaded with targets: %s", self._config["storage"].get("targets"))

    async def _teardown(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
        logger.info("Broker shut down cleanly")

    # ------------------------------------------------------------------
    # Live preview helper (called by WebServer)
    # ------------------------------------------------------------------

    async def get_preview_frame(self) -> bytes | None:
        """Return a single low-res preview frame for the live view stream."""
        if self._camera is None or self._state not in (State.IDLE, State.COUNTDOWN):
            return None
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._camera.get_preview_frame)
        except Exception:
            return None
