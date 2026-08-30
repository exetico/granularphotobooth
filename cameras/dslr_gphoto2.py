"""gphoto2 DSLR camera backend.

Loaded dynamically only when ``camera.backend`` is set to
``"dslr_gphoto2"`` in ``config.json``.  Requires the ``gphoto2`` Python
bindings to be installed (``pip install gphoto2``), which in turn require
``libgphoto2`` — available on Linux and macOS.
"""

import logging

from cameras.base import CameraBackend as _Base

logger = logging.getLogger(__name__)


class CameraBackend(_Base):
    """DSLR backend using the gphoto2 Python bindings."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._camera = None
        self._context = None

    @classmethod
    def is_available(cls) -> bool:
        try:
            import gphoto2 as gp  # noqa: F401
            # Attempt to list connected cameras; returns empty list if none.
            context = gp.Context()
            cameras = gp.Camera.autodetect(context)
            available = len(cameras) > 0
            if not available:
                logger.warning("gphoto2: no cameras detected")
            return available
        except ImportError:
            logger.warning("gphoto2 Python bindings not installed")
            return False
        except Exception as exc:
            logger.warning("gphoto2 availability check failed: %s", exc)
            return False

    def initialize(self) -> None:
        import gphoto2 as gp

        self._context = gp.Context()
        self._camera = gp.Camera()
        self._camera.init(self._context)

        # Apply settings from config
        cfg_map = {
            "iso": "/main/imgsettings/iso",
            "aperture": "/main/capturesettings/aperture",
            "shutter_speed": "/main/capturesettings/shutterspeed",
        }
        cam_cfg = self._camera.get_config(self._context)
        for key, widget_path in cfg_map.items():
            value = self._config.get(key)
            if value is not None:
                try:
                    _, widget = cam_cfg.get_child_by_name(widget_path.split("/")[-1])
                    widget.set_value(str(value))
                except Exception as exc:
                    logger.debug("Could not set %s=%s: %s", key, value, exc)
        try:
            self._camera.set_config(cam_cfg, self._context)
        except Exception as exc:
            logger.warning("Could not apply camera config: %s", exc)

        logger.info("DSLR initialised via gphoto2 (hint: %s)", self._config.get("model_hint", "unknown"))

    def capture(self) -> bytes:
        import gphoto2 as gp
        import io

        if self._camera is None:
            raise RuntimeError("DSLR camera not initialised")

        file_path = self._camera.capture(gp.GP_CAPTURE_IMAGE, self._context)
        camera_file = self._camera.file_get(
            file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL, self._context
        )
        buf = io.BytesIO()
        camera_file.save_fd(buf)
        return buf.getvalue()

    def get_preview_frame(self) -> bytes:
        import gphoto2 as gp

        if self._camera is None:
            raise RuntimeError("DSLR camera not initialised")

        camera_file, _file_path = self._camera.capture_preview(self._context)
        return camera_file.get_data_and_size()

    def release(self) -> None:
        if self._camera is not None:
            try:
                self._camera.exit(self._context)
            except Exception:
                pass
            self._camera = None
            self._context = None
            logger.info("DSLR camera released")
