"""Abstract base class for input trigger components."""

from abc import ABC, abstractmethod
from typing import Callable


class InputTrigger(ABC):
    """Contract that every input trigger backend must satisfy."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._callback: Callable | None = None

    @abstractmethod
    def start(self, callback: Callable) -> None:
        """Begin listening for trigger events.

        ``callback`` is a zero-argument callable that will be invoked
        (from whatever thread the listener uses) whenever the trigger
        fires.  The implementation is responsible for ensuring the
        callback is called safely even from a background thread.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release any resources."""
