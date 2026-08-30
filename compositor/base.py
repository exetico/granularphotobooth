"""Abstract base class for compositor backends."""

from abc import ABC, abstractmethod


class BaseCompositor(ABC):
    """Contract for all compositor implementations."""

    @abstractmethod
    def compose(self, frames: list[bytes], config: dict) -> bytes:
        """Combine one or more JPEG frames and return the final JPEG.

        Parameters
        ----------
        frames:
            Ordered list of raw JPEG images captured during the session.
        config:
            The ``compositor`` section of the loaded ``config.json``.

        Returns
        -------
        bytes
            The final composited image as JPEG bytes.
        """
