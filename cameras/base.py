"""Abstract base class for all camera backends."""

from abc import ABC, abstractmethod


class CameraBackend(ABC):
    """Contract that every camera backend must satisfy.

    Concrete backends are loaded dynamically by the broker based on the
    ``camera.backend`` key in ``config.json``.
    """

    def __init__(self, config: dict) -> None:
        self._config = config

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Return True if this backend can be used on the current system.

        This is called *before* ``initialize()`` so the broker can fall back
        gracefully if hardware is absent.
        """

    @abstractmethod
    def initialize(self) -> None:
        """Open the device / camera session.  Called once at startup."""

    @abstractmethod
    def capture(self) -> bytes:
        """Trigger the shutter and return a JPEG-encoded image as bytes."""

    @abstractmethod
    def get_preview_frame(self) -> bytes:
        """Return a low-resolution JPEG preview frame for the live view."""

    @abstractmethod
    def release(self) -> None:
        """Close the device and free all resources."""
