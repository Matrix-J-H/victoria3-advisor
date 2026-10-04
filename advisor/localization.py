"""Turns script keys (building_steel_mill, GBR, law_wealth_voting) into English names.

Names come from the game's own localization files. The result is cached in
data/loc_cache.json because reading 160+ files takes a few seconds.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_LINE = re.compile(r'^\s+([A-Za-z0-9_.\-]+):\d*\s+"(.*)"\s*(?:#.*)?$')
_PREFIXES = ("building_", "law_", "ig_", "STATE_", "pm_", "ideology_", "je_", "dp_",
             "combat_unit_type_", "institution_", "company_", "region_", "gov_")
_MARKUP = re.compile(r"#[A-Za-z_!]+\s?|\[[^\]]*\]|\$[^$]*\$|@[A-Za-z_]+!")


class Localization:
    def __init__(self, names: dict[str, str] | None = None):
        self.names = names or {}

    @classmethod
    def load(cls, game_dir: str | Path | None, cache_file: str | Path | None = None) -> "Localization":
        cache = Path(cache_file) if cache_file else None
        loc_dir = Path(game_dir) / "localization" / "english" if game_dir else None
        if cache and cache.exists():
            try:
                return cls(json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        names: dict[str, str] = {}
        if loc_dir and loc_dir.is_dir():
            for path in sorted(loc_dir.rglob("*_l_english.yml")):
                try:
                    text = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    m = _LINE.match(line)
                    if m and len(m.group(2)) <= 80:  # names only, skip descriptions
                        names.setdefault(m.group(1), m.group(2))
            if cache and names:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(names), encoding="utf-8")
                except OSError:
                    pass
        return cls(names)

    def name(self, key: object) -> str:
        if not isinstance(key, str) or not key:
            return str(key)
        hit = self.names.get(key)
        if hit:
            cleaned = _MARKUP.sub("", hit).strip()
            if cleaned:
                return cleaned
        stripped = key
        for prefix in _PREFIXES:
            if key.startswith(prefix):
                stripped = key[len(prefix):]
                break
        if stripped != key or "_" in key:
            return stripped.replace("_", " ").strip().title()
        return key  # bare country tags and the like
