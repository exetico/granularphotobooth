"""gphoto2 DSLR / mirrorless camera backend (Linux + macOS).

Requires ``libgphoto2`` and the ``gphoto2`` Python bindings:

    Linux:  sudo apt install libgphoto2-dev && pip install gphoto2
    macOS:  brew install libgphoto2 pkg-config && pip install gphoto2

Notes for stability
-------------------
* All calls are made from the CameraManager's single camera thread —
  libgphoto2 is not thread-safe.
* On macOS the system daemons ``PTPCamera`` / ``mscamerad`` grab the USB
  camera as soon as it is plugged in.  With ``kill_ptpcamera_on_macos``
  (default ``true``) we stop them before opening the camera.
* If the camera is set to RAW+JPEG we pick the JPEG.  RAW-only is rejected
  with a clear error message (the booth needs a JPEG to show and compose).
* Photos stay on the camera's SD card as a backup unless
  ``delete_after_download`` is ``true``.
"""

import logging
import subprocess
import sys
import time

from cameras.base import CameraBackend as _Base

logger = logging.getLogger(__name__)

# config key -> list of candidate widget names (they differ per brand)
_SETTING_WIDGETS: dict[str, list[str]] = {
    "iso": ["iso", "isospeed"],
    "aperture": ["aperture", "f-number", "fnumber"],
    "shutter_speed": ["shutterspeed", "shutterspeed2", "exposuretime"],
    "image_format": ["imageformat", "imagequality"],
    "capture_target": ["capturetarget"],
    "white_balance": ["whitebalance"],
}

_JPEG_EXT = (".jpg", ".jpeg")


def _kill_macos_ptp_daemons() -> None:
    if sys.platform != "darwin":
        return
    for proc in ("PTPCamera", "mscamerad", "ptpcamerad"):
        try:
            subprocess.run(["killall", "-9", proc], capture_output=True, timeout=5, check=False)
        except Exception:  # pragma: no cover - best effort
            pass


class CameraBackend(_Base):
    """DSLR backend using the gphoto2 Python bindings."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._camera = None
        self._model = "unknown"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import gphoto2 as gp
        except ImportError:
            logger.warning("gphoto2 Python bindings not installed (pip install gphoto2)")
            return False
        try:
            cameras = list(gp.Camera.autodetect())
            if not cameras:
                logger.warning("gphoto2: no cameras detected — is it on, in PC/PTP mode, cable OK?")
                return False
            logger.info("gphoto2 detected: %s", ", ".join(name for name, _ in cameras))
            return True
        except Exception as exc:
            logger.warning("gphoto2 availability check failed: %s", exc)
            return False

    # ------------------------------------------------------------------

    def initialize(self) -> None:
        import gphoto2 as gp

        if self._config.get("kill_ptpcamera_on_macos", True):
            _kill_macos_ptp_daemons()

        camera = gp.Camera()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                camera.init()
                break
            except gp.GPhoto2Error as exc:
                last_exc = exc
                logger.warning("gphoto2 init attempt %d failed: %s", attempt + 1, exc)
                if self._config.get("kill_ptpcamera_on_macos", True):
                    _kill_macos_ptp_daemons()
                time.sleep(1.0)
        else:
            raise RuntimeError(f"Could not open DSLR: {last_exc}")

        self._camera = camera
        try:
            summary = str(camera.get_summary())
            for line in summary.splitlines():
                if line.lower().startswith("model:"):
                    self._model = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass

        self._apply_settings()
        logger.info("DSLR initialised via gphoto2: %s", self._model)

    def _apply_settings(self) -> None:
        import gphoto2 as gp

        wanted = {k: self._config.get(k) for k in _SETTING_WIDGETS if self._config.get(k) not in (None, "")}
        if not wanted:
            return
        try:
            cfg = self._camera.get_config()
        except gp.GPhoto2Error as exc:
            logger.warning("Could not read camera config: %s", exc)
            return

        changed = False
        for key, value in wanted.items():
            widget = None
            for name in _SETTING_WIDGETS[key]:
                try:
                    widget = cfg.get_child_by_name(name)
                    break
                except gp.GPhoto2Error:
                    continue
            if widget is None:
                logger.warning("Camera has no setting for %s — ignored", key)
                continue
            value = str(value)
            try:
                choices = [widget.get_choice(i) for i in range(widget.count_choices())]
            except Exception:
                choices = []
            if choices and value not in choices:
                logger.warning("Camera does not accept %s=%r. Valid values: %s", key, value, ", ".join(choices))
                continue
            try:
                widget.set_value(value)
                changed = True
                logger.info("Camera setting %s = %s", key, value)
            except gp.GPhoto2Error as exc:
                logger.warning("Could not set %s=%s: %s", key, value, exc)
        if changed:
            try:
                self._camera.set_config(cfg)
            except gp.GPhoto2Error as exc:
                logger.warning("Could not apply camera settings: %s", exc)

    # ------------------------------------------------------------------

    def _drain_events(self, timeout_ms: int = 100, max_seconds: float = 3.0) -> list:
        """Read pending camera events; return CameraFilePaths of added files."""
        import gphoto2 as gp

        added = []
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            event_type, data = self._camera.wait_for_event(timeout_ms)
            if event_type == gp.GP_EVENT_TIMEOUT:
                break
            if event_type == gp.GP_EVENT_FILE_ADDED:
                added.append(data)
        return added

    def capture(self) -> bytes:
        import gphoto2 as gp

        if self._camera is None:
            raise RuntimeError("DSLR camera not initialised")

        try:
            path = self._camera.capture(gp.GP_CAPTURE_IMAGE)
        except gp.GPhoto2Error as exc:
            # -1 / "Unspecified error" on Canon is usually autofocus failing
            raise RuntimeError(f"DSLR capture failed ({exc}). If it is dark, try manual focus.") from exc

        candidates = [path] + self._drain_events()
        jpeg = next((p for p in candidates if p.name.lower().endswith(_JPEG_EXT)), None)
        if jpeg is None:
            raise RuntimeError(
                "Camera produced no JPEG (only %s). Set image quality to JPEG or RAW+JPEG."
                % ", ".join(p.name for p in candidates)
            )

        camera_file = self._camera.file_get(jpeg.folder, jpeg.name, gp.GP_FILE_TYPE_NORMAL)
        data = bytes(memoryview(camera_file.get_data_and_size()))

        if self._config.get("delete_after_download", False):
            for p in candidates:
                try:
                    self._camera.file_delete(p.folder, p.name)
                except gp.GPhoto2Error:
                    pass

        if data[:2] != b"\xff\xd8":
            raise RuntimeError("Downloaded file is not a JPEG")
        return data

    def has_preview(self) -> bool:
        return bool(self._config.get("liveview", True))

    def get_preview_frame(self) -> bytes:
        if self._camera is None:
            raise RuntimeError("DSLR camera not initialised")
        if not self._config.get("liveview", True):
            raise RuntimeError("Live view disabled")
        camera_file = self._camera.capture_preview()
        return bytes(memoryview(camera_file.get_data_and_size()))

    def release(self) -> None:
        if self._camera is not None:
            try:
                self._camera.exit()
            except Exception:
                pass
            self._camera = None
            logger.info("DSLR camera released")
