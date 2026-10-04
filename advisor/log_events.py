"""Parses the lines the telemetry mod writes to the game log.

A line looks like:
    [12:30:01][...]: V3ADV;monthly_economy;date=1 January 1877;country=Sierra Leone;gdp=4219500.5
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MARKER = "V3ADV;"
_NUMBER = re.compile(r"^-?[\d,]*\.?\d+([KMB])?$")
_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9}


@dataclass
class LogEvent:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def date(self) -> str:
        return str(self.fields.get("date", ""))


def _value(text: str) -> Any:
    text = text.strip()
    m = _NUMBER.match(text)
    if m:
        scale = _SCALE.get(m.group(1) or "", 1)
        digits = text.rstrip("KMB").replace(",", "")
        try:
            return float(digits) * scale
        except ValueError:
            return text
    if text.lower() in ("yes", "true"):
        return True
    if text.lower() in ("no", "false"):
        return False
    return text


def parse_line(line: str) -> LogEvent | None:
    at = line.find(MARKER)
    if at == -1:
        return None
    parts = line[at + len(MARKER):].strip().split(";")
    if not parts or not parts[0]:
        return None
    fields: dict[str, Any] = {}
    for part in parts[1:]:
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip()] = _value(value)
    return LogEvent(parts[0].strip(), fields)


def describe(event: LogEvent) -> str | None:
    """A short sentence to speak for events that matter right now, else None.

    The mod marks the player's part in a diplomatic play or war with `role`
    (target, initiator or none); battle and default lines are only written for
    the player's own country.
    """
    f = event.fields
    role = f.get("role")
    if event.kind == "play_started":
        if role == "target":
            return f"Warning. {f.get('initiator')} has started a diplomatic play against you."
        return None  # the player's own plays and other countries' plays need no alert
    if event.kind == "war_started":
        if role == "target":
            return f"You are now at war with {f.get('initiator')}."
        if role == "initiator":
            return f"You are now at war with {f.get('target')}."
        return None
    if event.kind == "battle_won":
        return f"Battle won against {f.get('enemy')} in {f.get('state')}."
    if event.kind == "battle_lost":
        return f"Battle lost to {f.get('enemy')} in {f.get('state')}."
    if event.kind == "default":
        return "Your country has defaulted on its debt."
    return None
