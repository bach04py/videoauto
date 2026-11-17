"""Single key presses for the watcher, without blocking its loop.

Windows (the target): msvcrt.kbhit()/getwch() read the console directly. Other platforms have no
non-blocking reader here yet: console_keys() returns None and the watcher says so and carries on.
"""

from __future__ import annotations

import sys
from typing import Protocol


class KeyReader(Protocol):
    def poll(self) -> str | None:
        """The next key pressed, or None at once when there is none."""


class MsvcrtKeys:
    def __init__(self) -> None:
        import msvcrt
        self._m = msvcrt

    def poll(self) -> str | None:
        if not self._m.kbhit():
            return None
        ch = self._m.getwch()
        if ch in ("\x00", "\xe0"):          # arrow / function keys come as two codes: drop both
            self._m.getwch()
            return None
        return ch


def console_keys() -> KeyReader | None:
    if sys.platform != "win32":
        return None
    try:
        return MsvcrtKeys()
    except ImportError:
        return None
