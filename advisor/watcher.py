"""Notices new autosaves and new lines in the game logs, by polling."""
from __future__ import annotations

from pathlib import Path


class SaveWatcher:
    """Reports a save once per write, after the file has stopped changing."""

    def __init__(self, path: Path, skip_existing: bool = True):
        self.path = path
        self._done = self._stamp() if skip_existing else None
        self._pending: tuple[int, int] | None = None

    def _stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def poll(self) -> Path | None:
        stamp = self._stamp()
        if stamp is None or stamp == self._done or stamp[1] == 0:
            self._pending = None
            return None
        if stamp != self._pending:  # still being written: wait for two identical readings
            self._pending = stamp
            return None
        self._done, self._pending = stamp, None
        return self.path


class LogTailer:
    """Returns complete new lines appended to a log file since the last poll."""

    def __init__(self, path: Path, from_start: bool = False):
        self.path = path
        self._pos = 0 if from_start else self._size()
        self._partial = b""

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def poll(self) -> list[str]:
        size = self._size()
        if size < self._pos:  # the game truncates its logs when it starts
            self._pos, self._partial = 0, b""
        if size == self._pos:
            return []
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._pos)
                chunk = fh.read(size - self._pos)
        except OSError:
            return []
        self._pos += len(chunk)
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()
        return [ln.decode("utf-8", "replace").rstrip("\r") for ln in lines]
