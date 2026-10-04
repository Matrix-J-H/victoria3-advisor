"""Loads config.toml and .env from the project folder."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    save_dir: Path
    log_dir: Path
    game_dir: Path | None
    rakaly: Path
    data_dir: Path
    save_name: str = "autosave.v3"
    poll_seconds: float = 2.0
    log_files: list[str] = field(default_factory=lambda: ["debug.log", "game.log"])
    model: str = "claude-sonnet-5-5"
    max_spoken_words: int = 120
    max_output_tokens: int = 16000
    effort: str = ""
    voice_enabled: bool = True
    voice_rate: int = 1
    voice_name: str = ""
    speak_events: bool = True
    api_key: str = ""


def _load_env(path: Path) -> None:
    """Minimal .env reader: KEY=value lines, existing environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def _path(value: str, base: Path = ROOT) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(value)))
    return p if p.is_absolute() else base / p


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.toml"
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    _load_env(ROOT / ".env")
    paths, watch = raw.get("paths", {}), raw.get("watch", {})
    adv, voice = raw.get("advisor", {}), raw.get("voice", {})
    game_dir = paths.get("game_dir")
    return Config(
        save_dir=_path(paths["save_dir"]),
        log_dir=_path(paths["log_dir"]),
        game_dir=_path(game_dir) if game_dir else None,
        rakaly=_path(paths.get("rakaly", "bin/rakaly.exe")),
        data_dir=_path(paths.get("data_dir", "data")),
        save_name=watch.get("save_name", "autosave.v3"),
        poll_seconds=float(watch.get("poll_seconds", 2)),
        log_files=list(watch.get("log_files", ["debug.log", "game.log"])),
        model=adv.get("model", "claude-sonnet-5-5"),
        max_spoken_words=int(adv.get("max_spoken_words", 120)),
        max_output_tokens=int(adv.get("max_output_tokens", 16000)),
        effort=str(adv.get("effort", "")).strip(),
        voice_enabled=bool(voice.get("enabled", True)),
        voice_rate=int(voice.get("rate", 1)),
        voice_name=voice.get("voice_name", ""),
        speak_events=bool(voice.get("speak_events", True)),
        api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
    )
