"""Abstract base class for storage targets."""

from abc import ABC, abstractmethod


class StorageTarget(ABC):
    """Contract for all storage target implementations."""

    def __init__(self, config: dict) -> None:
        self._config = config

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the target has all required configuration."""

    @abstractmethod
    def upload(self, image_bytes: bytes, metadata: dict) -> str:
        """Persist the image and return a URI / path string.

        Parameters
        ----------
        image_bytes:
            The final composited JPEG image.
        metadata:
            Arbitrary key/value pairs (e.g. ``{"shots": 4}``).

        Returns
        -------
        str
            A URI or path that identifies where the image was stored.
        """
