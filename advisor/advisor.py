"""Asks Claude for advice based on the digest and recent events."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SYSTEM = """You are a strategy advisor to someone playing Victoria 3 (game version {version}). \
They are playing {country}. Your advice is read aloud to them while they play, so they cannot \
look at it.

You receive a JSON summary of their country taken from the latest autosave, what changed since \
the previous autosave, and recent events from the game log. Work only from that data and your \
knowledge of the game's mechanics. Things the summary does not contain, such as market prices, \
must not be invented; if a recommendation depends on something you cannot see, say what to check.

Cover economy, politics, diplomacy and military, but only where there is something worth doing. \
Rank by urgency: an imminent threat (bankruptcy, a diplomatic play against them, a losing war, \
a radical movement close to revolt) comes before growth advice. Take the country's situation into \
account, for example whether it is a subject of another country.

Answer in exactly this form:

SPOKEN:
At most {max_words} words of plain spoken English, no lists, symbols, abbreviations or markup. \
Give the two or three most important actions for the next six months, most urgent first, each \
with a brief reason. Name specific buildings, states, laws or interest groups. Say numbers the \
way a person would say them. Do not repeat advice from last time unless it is still the top \
priority and has not been acted on.

DETAIL:
Short written notes for the log: the reasoning and numbers behind each point, and anything \
lower-priority that did not make the spoken part."""


@dataclass
class Advice:
    spoken: str
    detail: str
    raw: str
    truncated: bool = False   # the reply hit the output limit before finishing


def build_user_message(
    digest: dict[str, Any],
    changes: dict[str, Any],
    events: list[dict[str, Any]],
    telemetry: dict[str, Any] | None,
    previous_spoken: str | None,
    warning: str | None,
) -> str:
    parts = ["COUNTRY SUMMARY (from autosave dated %s):" % digest.get("game_date"),
             json.dumps(digest, separators=(",", ":"))]
    if changes:
        parts += ["", "CHANGES SINCE PREVIOUS AUTOSAVE:", json.dumps(changes, separators=(",", ":"))]
    if telemetry:
        parts += ["", "LATEST MONTHLY FIGURES FROM THE GAME LOG:", json.dumps(telemetry, separators=(",", ":"))]
    if events:
        parts += ["", "EVENTS SINCE PREVIOUS ADVICE:", json.dumps(events[-40:], separators=(",", ":"))]
    if previous_spoken:
        parts += ["", "WHAT YOU SAID LAST TIME:", previous_spoken]
    if warning:
        parts += ["", "DATA WARNING: " + warning]
    parts += ["", "Notes on the data: budget figures are weekly; weekly_balance is approximate. "
              "buildings.problems lists buildings that are short of inputs, understaffed, losing money "
              "or subsidised. Dates are year.month.day."]
    return "\n".join(parts)


def split_reply(text: str) -> Advice:
    spoken, detail = text, ""
    upper = text.upper()
    s, d = upper.find("SPOKEN:"), upper.find("DETAIL:")
    if s != -1:
        end = d if d > s else len(text)
        spoken = text[s + len("SPOKEN:"):end]
    if d != -1:
        detail = text[d + len("DETAIL:"):]
    return Advice(" ".join(spoken.split()), detail.strip(), text)


def get_advice(
    api_key: str,
    model: str,
    max_words: int,
    digest: dict[str, Any],
    changes: dict[str, Any],
    events: list[dict[str, Any]],
    telemetry: dict[str, Any] | None = None,
    previous_spoken: str | None = None,
    warning: str | None = None,
    max_output_tokens: int = 16000,
    effort: str = "",
) -> Advice:
    import anthropic  # imported here so the rest of the program runs without the package

    client = anthropic.Anthropic(api_key=api_key)
    system = SYSTEM.format(
        version=digest.get("game_version", "unknown"),
        country=(digest.get("country") or {}).get("name", "an unknown country"),
        max_words=max_words,
    )
    # Current Claude models think before answering, and that thinking counts
    # towards max_tokens. The limit must leave room for both, or the reply is
    # cut off part-way through.
    request: dict[str, Any] = dict(
        model=model,
        max_tokens=max_output_tokens,
        system=system,
        messages=[{"role": "user", "content": build_user_message(
            digest, changes, events, telemetry, previous_spoken, warning)}],
    )
    if effort:
        try:
            message = client.messages.create(**request, extra_body={"output_config": {"effort": effort}})
        except anthropic.BadRequestError:
            message = client.messages.create(**request)  # model does not take an effort setting
    else:
        message = client.messages.create(**request)
    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    advice = split_reply(text)
    advice.truncated = getattr(message, "stop_reason", "end_turn") not in ("end_turn", "stop_sequence", None)
    if advice.truncated:
        advice.spoken = _whole_sentences(advice.spoken)
    return advice


def _whole_sentences(text: str) -> str:
    """Drop a trailing half-sentence so a cut-off reply is not read out mid-word."""
    end = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
    return text[:end + 1] if end != -1 else text
