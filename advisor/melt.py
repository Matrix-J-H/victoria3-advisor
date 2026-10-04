"""Turns an autosave into plain text, using rakaly for binary saves."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

TEXT_HEADER = b"SAV0100"  # uncompressed plain text, readable as-is


class MeltError(RuntimeError):
    pass


def to_text(save: Path, work_dir: Path, rakaly: Path) -> tuple[Path, str | None]:
    """Return (path to a plain-text copy of `save`, warning or None).

    The save is copied first so the game is free to overwrite the original
    while we read.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    copy = work_dir / "current.v3"
    text = work_dir / "current_text.v3"
    shutil.copyfile(save, copy)
    with open(copy, "rb") as fh:
        header = fh.read(len(TEXT_HEADER))
    if header == TEXT_HEADER:
        # A text save written by the game itself (debug mode). Its indentation is
        # irregular, so tidy it into the layout the reader expects.
        reindent(copy, text)
        return text, None
    if not rakaly.exists():
        raise MeltError(f"Save is binary and rakaly was not found at {rakaly}.")
    strict = _run(rakaly, copy, text, "error")
    if strict.returncode == 0 and text.exists():
        return text, None
    lenient = _run(rakaly, copy, text, "stringify")
    if lenient.returncode == 0 and text.exists():
        return text, ("rakaly did not recognise some fields in this save, probably because the game "
                      "was patched. The reading may be incomplete; check for a newer rakaly release.")
    detail = (lenient.stderr or strict.stderr or "").strip()[:500]
    raise MeltError(f"rakaly could not decode the save: {detail}")


def _run(rakaly: Path, src: Path, dst: Path, unknown: str) -> subprocess.CompletedProcess[str]:
    dst.unlink(missing_ok=True)
    return subprocess.run(
        [str(rakaly), "melt", "--format", "vic3", "--unknown-key", unknown, "--out", str(dst), str(src)],
        capture_output=True, text=True, timeout=300,
    )


_QUOTED = re.compile(rb'"(?:[^"\\]|\\.)*"')


def reindent(src: Path, dst: Path) -> None:
    """Rewrite a text save with one tab per nesting level, as rakaly produces."""
    depth = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        out = []
        for line in fin:
            body = line.strip()
            if not body:
                continue
            bare = _QUOTED.sub(b"", body) if b'"' in body else body
            opens, closes = bare.count(b"{"), bare.count(b"}")
            leading = 0
            if closes:
                for ch in bare:
                    if ch == 125:      # }
                        leading += 1
                    elif ch != 32 and ch != 9:
                        break
            out.append(b"\t" * max(depth - leading, 0) + body + b"\n")
            depth = max(depth + opens - closes, 0)
            if len(out) >= 50000:
                fout.writelines(out)
                out = []
        fout.writelines(out)
