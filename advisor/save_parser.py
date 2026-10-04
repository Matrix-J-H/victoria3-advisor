"""Reader for decoded (plain-text) Victoria 3 saves.

A decoded save is 200 MB+ of nested `key=value` text. Parsing all of it in
Python would take minutes, so this module works in two layers:

* `SaveFile` memory-maps the file, indexes the top-level blocks, and can hand
  out the raw text of single database entries, filtered by a cheap regex.
* `parse()` turns one such small piece of text into dicts and lists.
"""
from __future__ import annotations

import mmap
import re
from pathlib import Path
from typing import Any, Callable, Iterator

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}=]|[^\s{}=]+')
_COLOR_PREFIXES = {"rgb", "hsv", "hsv360", "hex"}
_INT = re.compile(r"-?\d+\Z")
_FLOAT = re.compile(r"-?\d+\.\d+\Z")

ITEMS_KEY = "_items"  # holds bare values when a block mixes `a=b` pairs and bare values


def _atom(tok: str) -> Any:
    if tok.startswith('"'):
        return tok[1:-1]
    if _INT.match(tok):
        return int(tok)
    if _FLOAT.match(tok):
        return float(tok)
    if tok == "yes":
        return True
    if tok == "no":
        return False
    return tok  # identifiers, dates such as 1877.1.1


def _build(pairs: list[tuple[str, Any]], items: list[Any]) -> Any:
    if not pairs:
        return items if items else {}
    out: dict[str, Any] = {}
    seen_twice: set[str] = set()
    for key, value in pairs:
        if key in out:
            if key in seen_twice:
                out[key].append(value)
            else:
                out[key] = [out[key], value]
                seen_twice.add(key)
        else:
            out[key] = value
    if items:
        out[ITEMS_KEY] = items
    return out


def _container(toks: list[str], i: int) -> tuple[Any, int]:
    """Parse from just after a `{` up to and including its matching `}`."""
    pairs: list[tuple[str, Any]] = []
    items: list[Any] = []
    n = len(toks)
    while i < n:
        tok = toks[i]
        if tok == "}":
            return _build(pairs, items), i + 1
        if tok == "{":
            value, i = _container(toks, i + 1)
            items.append(value)
            continue
        nxt = toks[i + 1] if i + 1 < n else ""
        if nxt == "=":
            value, i = _value(toks, i + 2)
            pairs.append((str(_atom(tok)), value))
        elif tok in _COLOR_PREFIXES and nxt == "{":
            value, i = _container(toks, i + 2)
            items.append(value)
        else:
            items.append(_atom(tok))
            i += 1
    return _build(pairs, items), i  # ran off the end: tolerate truncated input


def _value(toks: list[str], i: int) -> tuple[Any, int]:
    if i >= len(toks):
        return None, i
    tok = toks[i]
    if tok == "{":
        return _container(toks, i + 1)
    if tok in _COLOR_PREFIXES and i + 1 < len(toks) and toks[i + 1] == "{":
        return _container(toks, i + 2)
    return _atom(tok), i + 1


def parse(text: str | bytes) -> Any:
    """Parse a fragment of save text (the inside of a block) into Python objects."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    toks = _TOKEN.findall(text)
    toks.append("}")
    value, _ = _container(toks, 0)
    return value


def as_list(value: Any) -> list[Any]:
    """Blocks with one entry parse as a single value; normalise to a list."""
    if value is None or value == {}:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and set(value) == {ITEMS_KEY}:
        return value[ITEMS_KEY]
    return [value]


class SaveFile:
    """Random access to a decoded save without loading it into Python objects."""

    _TOP = re.compile(rb"^([a-z_0-9]+)=", re.M)
    _ENTRY = re.compile(rb"^\t\t(\d+)=\{[ \t]*\r?\n", re.M)

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = open(self.path, "rb")
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        self.blocks: dict[str, tuple[int, int]] = {}
        self._index()

    def _index(self) -> None:
        starts = [(m.group(1).decode(), m.start()) for m in self._TOP.finditer(self._mm)]
        for (name, start), (_, end) in zip(starts, starts[1:] + [("", len(self._mm))]):
            self.blocks.setdefault(name, (start, end))

    def close(self) -> None:
        self._mm.close()
        self._fh.close()

    def __enter__(self) -> "SaveFile":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def raw(self, name: str) -> bytes:
        if name not in self.blocks:
            return b""
        start, end = self.blocks[name]
        return self._mm[start:end]

    def top(self, name: str) -> Any:
        """Parse a whole top-level block or scalar. Use only for small blocks."""
        raw = self.raw(name)
        if not raw:
            return None
        return parse(raw).get(name)

    def entries(
        self,
        block: str,
        match: re.Pattern[bytes] | None = None,
        keep: Callable[[int, bytes], bool] | None = None,
    ) -> Iterator[tuple[int, bytes]]:
        """Yield (id, raw text) for entries of `block`'s database.

        `match` is searched in the raw entry text; `keep` is an extra predicate.
        Only entries passing both are yielded, so callers parse very little.
        """
        if block not in self.blocks:
            return
        start, end = self.blocks[block]
        mm = self._mm
        pos = start
        while True:
            m = self._ENTRY.search(mm, pos, end)
            if not m:
                return
            close = mm.find(b"\n\t\t}", m.end() - 1, end)
            if close == -1:
                return
            entry_id = int(m.group(1))
            body = mm[m.end():close + 1]
            pos = close + 4
            if match is not None and not match.search(body):
                continue
            if keep is not None and not keep(entry_id, body):
                continue
            yield entry_id, body

    def entry(self, block: str, entry_id: int) -> bytes | None:
        if block not in self.blocks:
            return None
        start, end = self.blocks[block]
        m = re.compile(rb"^\t\t%d=\{[ \t]*\r?\n" % entry_id, re.M).search(self._mm, start, end)
        if not m:
            return None
        close = self._mm.find(b"\n\t\t}", m.end() - 1, end)
        if close == -1:
            return None
        return self._mm[m.end():close + 1]


def field(body: bytes, key: str, depth: int = 3) -> str | None:
    """Fast lookup of one scalar `key=value` line at a given tab depth, without parsing."""
    m = re.search(rb"^" + b"\t" * depth + key.encode() + rb"=(.*?)\r?$", body, re.M)
    if not m:
        return None
    return m.group(1).decode("utf-8", "replace").strip().strip('"')
