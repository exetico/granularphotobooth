"""Application state enumeration."""

from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    COUNTDOWN = auto()
    CAPTURE = auto()
    PREVIEW = auto()
    PROCESS = auto()
