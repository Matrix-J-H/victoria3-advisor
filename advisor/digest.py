"""Boils a decoded save down to a one-page summary of the player's country.

The digest is a plain dict (saved as JSON) so it can be diffed against the
previous one and handed to the model as-is.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .localization import Localization
from .save_parser import SaveFile, as_list, field, parse

NO_ID = 4294967295
SHORTAGE_THRESHOLD = 0.05      # share of input goods a building could not buy
UNDERSTAFFED_THRESHOLD = 0.8   # staffing / levels


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _r(value: Any, digits: int = 0) -> float | int:
    v = round(_num(value), digits)
    return int(v) if digits == 0 else v


def _trend(block: Any, back: int = 0) -> float | None:
    """Latest value of a trend block (`back` samples earlier if given). Samples are 28 days apart."""
    try:
        values = block["channels"]["0"]["values"]
    except (KeyError, TypeError):
        return None
    if not isinstance(values, list) or not values:
        return None
    idx = max(0, len(values) - 1 - back)
    return _num(values[idx])


def _sum(block: Any) -> float:
    if isinstance(block, dict):
        return sum(_num(v) for v in block.values() if not isinstance(v, (dict, list))) + _sum(block.get("_items"))
    return sum(_num(v) for v in as_list(block))


class _Names:
    """Resolves country ids to readable names, looking each one up only once."""

    def __init__(self, save: SaveFile, loc: Localization):
        self.save, self.loc, self._cache = save, loc, {}

    def tag(self, cid: Any) -> str:
        if not isinstance(cid, int) or cid == NO_ID:
            return "?"
        if cid not in self._cache:
            body = self.save.entry("country_manager", cid)
            self._cache[cid] = (field(body, "definition") if body else None) or f"#{cid}"
        return self._cache[cid]

    def country(self, cid: Any) -> str:
        return self.loc.name(self.tag(cid))


def find_player(save: SaveFile) -> int | None:
    """The country the player controls now.

    player_manager keeps one entry per country the player has controlled, so
    after switching country there are several. The save's title names the
    current one; fall back to the most recent entry.
    """
    players = save.top("player_manager") or {}
    ids = [e["country"] for e in (players.get("database") or {}).values()
           if isinstance(e, dict) and isinstance(e.get("country"), int)]
    if len(ids) <= 1:
        return ids[0] if ids else None
    title = str((save.top("meta_data") or {}).get("name", "")).strip().lower()
    for cid in ids:
        body = save.entry("country_manager", cid)
        if body is None:
            continue
        m = re.search(rb'^\t{3,6}(?:name|custom_name|localized_name)="([^"]+)"', body, re.M)
        if m and title and m.group(1).decode("utf-8", "replace").strip().lower() == title:
            return cid
    for cid in reversed(ids):  # entries are added in order, so the last is the latest
        if save.entry("country_manager", cid) is not None:
            return cid
    return None


def build_digest(save: SaveFile, loc: Localization, country_id: int | None = None) -> dict[str, Any]:
    cid = country_id if country_id is not None else find_player(save)
    if cid is None:
        raise ValueError("No player country found in the save (player_manager is empty).")
    raw = save.entry("country_manager", cid)
    if raw is None:
        raise ValueError(f"Country {cid} not found in the save.")
    country = parse(raw)
    names = _Names(save, loc)
    meta = save.top("meta_data") or {}
    own = re.compile(rb"^\t\t\tcountry=%d\r?$" % cid, re.M)

    digest: dict[str, Any] = {
        "game_date": str(save.top("date") or meta.get("game_date")),
        "game_version": meta.get("version"),
        "country": {
            "id": cid,
            "tag": country.get("definition"),
            "name": loc.name(country.get("definition")),
            "government": loc.name(country.get("government")),
            "type": country.get("country_type"),
        },
    }
    digest["standing"] = _standing(save, cid, country)
    digest["budget"] = _budget(country)
    digest["economy"] = _economy(country)
    digest["population"] = _population(country)
    states = _states(save, loc, own)
    digest["construction"] = _construction(country, loc, states["region_of"])
    digest["states"] = states["list"]
    digest["buildings"] = _buildings(save, loc, states["ids"], states["region_of"])
    digest["politics"] = _politics(save, loc, own, country)
    digest["technology"] = _technology(save, loc, own)
    digest["diplomacy"] = _diplomacy(save, loc, names, cid)
    digest["military"] = _military(save, loc, own)
    return digest


def _standing(save: SaveFile, cid: int, country: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"prestige": _trend(country.get("prestige"))}
    rankings = as_list((save.top("country_rankings") or {}).get("country_rankings"))
    for row in rankings:
        if isinstance(row, dict) and row.get("country") == cid:
            out["rank"] = row.get("rank")
            out["rank_position"] = row.get("score")
            out["countries_ranked"] = len(rankings)
            break
    return out


def _budget(country: dict) -> dict[str, Any]:
    b = country.get("budget") or {}
    income, expenses = _sum(b.get("weekly_income")), _sum(b.get("weekly_expenses"))
    money, principal = _num(b.get("money")), _num(b.get("principal"))
    out = {
        "treasury": _r(money),
        "debt": _r(principal),
        "credit_limit": _r(b.get("credit")),
        "weekly_income": _r(income),
        "weekly_expenses": _r(expenses),
        "weekly_balance": _r(income - expenses),
        "investment_pool": _r(b.get("investment_pool")),
    }
    credit = _num(b.get("credit"))
    if principal > 0 and credit > 0:
        out["debt_share_of_credit"] = _r(principal / credit, 2)
    if income - expenses < 0 and money > 0 and principal == 0:
        out["weeks_until_treasury_empty"] = _r(money / (expenses - income))
    return out


def _economy(country: dict) -> dict[str, Any]:
    gdp_now, gdp_year = _trend(country.get("gdp")), _trend(country.get("gdp"), back=13)
    out: dict[str, Any] = {"gdp": _r(gdp_now)}
    if gdp_now and gdp_year:
        out["gdp_growth_1y_pct"] = _r((gdp_now / gdp_year - 1) * 100, 1)
    out["literacy"] = _r(_trend(country.get("literacy")), 3)
    out["avg_standard_of_living"] = _r(_trend(country.get("avgsoltrend")), 1)
    out["taxed_goods"] = as_list(country.get("taxed_goods"))
    return out


def _population(country: dict) -> dict[str, Any]:
    p = country.get("pop_statistics") or {}
    lower, middle, upper = (_num(p.get(f"population_{s}_strata")) for s in ("lower", "middle", "upper"))
    total = lower + middle + upper
    radicals, loyalists = _num(p.get("population_radicals")), _num(p.get("population_loyalists"))
    out = {
        "total": _r(total), "lower": _r(lower), "middle": _r(middle), "upper": _r(upper),
        "radicals": _r(radicals), "loyalists": _r(loyalists),
        "unemployed_workforce": _r(p.get("population_unemployed_workforce")),
        "salaried_workforce": _r(p.get("population_salaried_workforce")),
    }
    if total:
        out["radicals_pct"] = _r(radicals / total * 100, 1)
        out["loyalists_pct"] = _r(loyalists / total * 100, 1)
    return out


def _construction(country: dict, loc: Localization, region_of: dict[int, str]) -> dict[str, Any]:
    def queue(name: str) -> list[dict[str, Any]]:
        out = []
        for el in as_list((country.get(name) or {}).get("construction_elements")):
            if isinstance(el, dict):
                out.append({
                    "building": loc.name(el.get("type")),
                    "state": region_of.get(el.get("state"), "?"),
                    "points_left": _r(el.get("construction_left")),
                    "in_progress": "construction_speed" in el,
                })
        return out

    return {"government_queue": queue("government_queue"), "private_queue": queue("private_queue")}


def _states(save: SaveFile, loc: Localization, own: re.Pattern[bytes]) -> dict[str, Any]:
    rows, ids, region_of = [], [], {}
    for sid, body in save.entries("states", own):
        s = parse(body)
        stats = s.get("pop_statistics") or {}
        region = loc.name(s.get("region"))
        ids.append(sid)
        region_of[sid] = region
        infra, usage = _num(s.get("infrastructure")), _num(s.get("infrastructure_usage"))
        row = {
            "id": sid,
            "name": region,
            "population": _r(sum(_num(stats.get(f"population_{k}_strata")) for k in ("lower", "middle", "upper"))),
            "unemployed": _r(stats.get("population_unemployed_workforce")),
            "radicals": _r(stats.get("population_radicals")),
            "infrastructure": _r(infra, 1),
            "infrastructure_usage": _r(usage, 1),
            "incorporated": _num(s.get("incorporation")) >= 1,
            "arable_land": s.get("arable_land"),
        }
        if usage > infra:
            row["infrastructure_overloaded"] = True
        rows.append(row)
    rows.sort(key=lambda r: -r["population"])
    return {"list": rows, "ids": ids, "region_of": region_of}


def _buildings(save: SaveFile, loc: Localization, state_ids: list[int], region_of: dict[int, str]) -> dict[str, Any]:
    if not state_ids:
        return {"by_type": [], "problems": []}
    in_states = re.compile(rb"^\t\t\tstate=(?:%s)\r?$" % b"|".join(str(i).encode() for i in state_ids), re.M)
    by_type: dict[str, dict[str, float]] = defaultdict(lambda: {"levels": 0, "weekly_profit": 0.0})
    problems: list[dict[str, Any]] = []
    for _bid, body in save.entries("building_manager", in_states):
        b = parse(body)
        levels = _num(b.get("levels"))
        if levels <= 0:
            continue
        kind = b.get("building", "?")
        if kind.startswith("building_subsistence") or kind == "building_urban_center":
            continue
        name, where = loc.name(kind), region_of.get(b.get("state"), "?")
        profit = _num(b.get("profit_after_reserves"))
        agg = by_type[name]
        agg["levels"] += levels
        agg["weekly_profit"] += profit
        is_government = "government_dividends" in b
        issues = []
        shortage = _num(b.get("input_goods_shortage"))
        if shortage >= SHORTAGE_THRESHOLD:
            issues.append(f"input shortage {round(shortage * 100)}%")
        if "staffing" in b and _num(b.get("staffing")) < levels * UNDERSTAFFED_THRESHOLD:
            issues.append(f"staffed {b.get('staffing'):.1f} of {int(levels)} levels")
        if profit < 0 and not is_government:
            issues.append(f"losing {round(-profit)} a week")
        if b.get("subsidized"):
            issues.append("subsidised")
        if issues:
            problems.append({"building": name, "state": where, "levels": int(levels), "issues": issues})
    rows = [{"building": n, "levels": int(v["levels"]), "weekly_profit": _r(v["weekly_profit"])}
            for n, v in sorted(by_type.items(), key=lambda kv: -kv[1]["levels"])]
    return {"by_type": rows, "problems": problems}


def _politics(save: SaveFile, loc: Localization, own: re.Pattern[bytes], country: dict) -> dict[str, Any]:
    laws = []
    for _lid, body in save.entries("laws", own):
        if field(body, "active") == "yes":
            laws.append(loc.name(field(body, "law")))
    groups = []
    for _gid, body in save.entries("interest_groups", own):
        g = parse(re.sub(rb"^\t\t\tpops=\{.*?^\t\t\t\}\r?$", b"", body, flags=re.M | re.S))
        if g.get("is_marginal"):
            continue
        groups.append({
            "name": loc.name(g.get("definition")),
            "clout_pct": _r(_num(g.get("clout")) * 100, 1),
            "approval": g.get("approval"),
            "mood": g.get("approval_state"),
            "in_government": bool(g.get("in_government")),
        })
    groups.sort(key=lambda g: -g["clout_pct"])
    movements = []
    for _mid, body in save.entries("political_movement_manager", own):
        radicalism = _num(_float(field(body, "radicalism")))
        movements.append({
            "name": loc.name(field(body, "ideology")),
            "radicalism_pct": _r(radicalism * 100),
            "status": field(body, "managed_status"),
        })
    movements.sort(key=lambda m: -m["radicalism_pct"])
    election = None
    for _eid, body in save.entries("election_manager", own):
        election = field(body, "next_election")
    modifiers = [m.get("modifier") for m in as_list((country.get("timed_modifiers") or {}).get("modifiers"))
                 if isinstance(m, dict)]
    return {
        "laws": sorted(laws),
        "interest_groups": groups,
        "government_clout_pct": _r(sum(g["clout_pct"] for g in groups if g["in_government"]), 1),
        "movements": movements,
        "next_election": election,
        "active_modifiers": modifiers,
    }


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _technology(save: SaveFile, loc: Localization, own: re.Pattern[bytes]) -> dict[str, Any]:
    for _tid, body in save.entries("technology", own):
        t = parse(body)
        return {
            "researching": loc.name(t.get("research_technology")),
            "queue": [loc.name(x) for x in as_list(t.get("research_queue"))],
            "acquired_count": len(as_list(t.get("acquired_technologies"))),
        }
    return {}


_SUBJECT_PACTS = {"colony", "puppet", "dominion", "protectorate", "tributary", "personal_union",
                  "vassal", "chartered_company", "crown_colony"}


def _diplomacy(save: SaveFile, loc: Localization, names: _Names, cid: int) -> dict[str, Any]:
    mine = re.compile(rb"=%d\r?$" % cid, re.M)
    pacts, overlord, subjects = [], None, []
    for _pid, body in save.entries("pacts", mine):
        p = parse(body)
        targets = p.get("targets") or {}
        first, second = targets.get("first"), targets.get("second")
        if cid not in (first, second):
            continue
        action = p.get("action")
        other = names.country(second if first == cid else first)
        if action in _SUBJECT_PACTS:
            if second == cid:
                overlord = {"country": other, "type": action, "liberty_desire": _r(p.get("liberty_desire"), 1)}
            else:
                subjects.append({"country": other, "type": action, "liberty_desire": _r(p.get("liberty_desire"), 1)})
        else:
            pacts.append({"type": action, "with": other, "we_initiated": first == cid})
    plays, play_ids = [], set()
    for pid, body in save.entries("diplomatic_plays"):
        p = parse(body)
        involved = as_list(p.get("involved"))
        if cid not in involved and cid not in (p.get("initiator"), p.get("target")):
            continue
        play_ids.add(pid)
        side = next((r.get("side") for r in as_list(p.get("country_records"))
                     if isinstance(r, dict) and r.get("country") == cid), None)
        plays.append({
            "type": loc.name(p.get("type")),
            "initiator": names.country(p.get("initiator")),
            "target": names.country(p.get("target")),
            "our_side": side,
            "escalation": p.get("escalation"),
            "is_war": isinstance(p.get("war"), int) and p.get("war") != NO_ID,
        })
    wars = []
    for _wid, body in save.entries("war_manager", mine):
        if field(body, "dead") == "yes":
            continue
        w = parse(body)
        parts = [x for x in as_list(w.get("war_participants")) if isinstance(x, dict)]
        me = next((x for x in parts if x.get("country") == cid), None)
        if me is None:
            continue
        wars.append({
            "since": w.get("start_date"),
            "our_war_support": _r(me.get("war_support")),
            "participants": [{"country": names.country(x.get("country")), "war_support": _r(x.get("war_support"))}
                             for x in parts if x.get("country") != cid],
        })
    return {"overlord": overlord, "subjects": subjects, "pacts": pacts, "diplomatic_plays": plays, "wars": wars}


def _military(save: SaveFile, loc: Localization, own: re.Pattern[bytes]) -> dict[str, Any]:
    units: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _uid, body in save.entries("new_combat_unit_manager", own):
        formation = _float(field(body, "formation"))
        kind = field(body, "type")
        if formation is not None and kind:
            units[int(formation)][loc.name(kind)] += 1
    formations = []
    for fid, body in save.entries("military_formation_manager", own):
        f = parse(body)
        formations.append({
            "type": f.get("type"),
            "number": f.get("ordinal_number"),
            "units": dict(units.get(fid, {})),
            "organization": f.get("organization"),
            "supply": f.get("supply"),
        })
    armies = sum(sum(f["units"].values()) for f in formations if f["type"] == "army")
    fleets = sum(sum(f["units"].values()) for f in formations if f["type"] == "fleet")
    ships = sum(1 for _ in save.entries("ship_manager", own))
    return {"army_units": armies, "navy_units": fleets or ships, "formations": formations}


# Keys worth reporting when they move between two digests.
_WATCHED = [
    ("budget", "treasury"), ("budget", "debt"), ("budget", "weekly_balance"),
    ("economy", "gdp"), ("economy", "avg_standard_of_living"),
    ("population", "total"), ("population", "radicals"), ("population", "loyalists"),
    ("population", "unemployed_workforce"), ("standing", "prestige"), ("standing", "rank_position"),
    ("military", "army_units"), ("military", "navy_units"),
]


def diff_digests(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """What changed since the previous autosave, in a form the model can read quickly."""
    if not old or (old.get("country") or {}).get("tag") != (new.get("country") or {}).get("tag"):
        return {}
    out: dict[str, Any] = {"since": old.get("game_date")}
    for section, key in _WATCHED:
        a, b = (old.get(section) or {}).get(key), (new.get(section) or {}).get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a != b:
            out[f"{section}.{key}"] = {"was": a, "now": b}
    old_laws, new_laws = set((old.get("politics") or {}).get("laws", [])), set((new.get("politics") or {}).get("laws", []))
    if new_laws - old_laws:
        out["laws_enacted"] = sorted(new_laws - old_laws)
    old_b = {r["building"]: r["levels"] for r in (old.get("buildings") or {}).get("by_type", [])}
    built = {r["building"]: r["levels"] - old_b.get(r["building"], 0)
             for r in (new.get("buildings") or {}).get("by_type", []) if r["levels"] != old_b.get(r["building"], 0)}
    if built:
        out["building_level_changes"] = built
    return out
