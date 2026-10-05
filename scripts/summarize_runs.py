#!/usr/bin/env python3
"""Summarise a GPTNT recorder output folder into one CSV row per game.

Reads experiment-*.parquet (+ optional run_meta.json). Includes grounding checks:
whether the Defuser's last wire-colour description before the deciding cut matches
oracle bomb_state (count, colours, order). Unclear parses are marked unparsed.
Also tracks post-strike recovery and false-solve claims (Defuser reports green /
solved while Wires.isSolved is still False). Solved games get a solve_type;
failed games get a single failure_type. Label meanings (one line each):

  solve_type
    grounded          — Colour order matched oracle (and cut was correct when recorded).
    cancelled_errors  — Colour order was wrong, but the cut still hit the true wire.
    recovered_win     — Struck after a bad cut, then cut the right wire and solved.
    cut_slot_mismatch — Solved, but the first recorded cut ≠ seeded answer (rare).
    uncategorized_solve — Solved, but grounding/cut record incomplete (should stay rare).

  failure_type
    false_solve_claim — Defuser said solved/green while Wires.isSolved was still False.
    other_module_talk — Chat mentioned a different module type on a Wires-only bomb;
                        does not by itself mean the Expert applied the wrong rules.
    never_cut         — No wire was ever cut before timeout / end.
    struck_no_fix — Cut a wrong wire (strike) and never made the correct cut.

Failed / crashed / incomplete sessions are kept as rows with a reason.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

try:
    from gptnt.experiments.db.schema import AsBlob
except Exception:  # pragma: no cover - allow running without full env for meta-only
    AsBlob = None  # type: ignore

COLOUR_WORDS = (
    "red",
    "blue",
    "yellow",
    "white",
    "black",
    "orange",
    "green",
    "purple",
    "grey",
    "gray",
    "pink",
)
COLOUR_RE = re.compile(r"\b(" + "|".join(COLOUR_WORDS) + r")\b", re.I)
# Optional shade/qualifier before a colour word ("dark blue", "light gray/white", "dark/black").
_COLOUR_CORE = "|".join(COLOUR_WORDS)
COLOUR_TOKEN = (
    r"(?:(?:dark|light|pale|medium|grayish|greyish|grey|gray)[\s/\-]*)*"
    r"(" + _COLOUR_CORE + r")"
)
CUT_INSTR_RE = re.compile(r"\bcut\b.{0,80}\bwire|\bcut the\b|\bcut wire\b", re.I)
BEFORE_CUT_RE = re.compile(r"before I (tell|give)|which wire to cut", re.I)
# Prefer explicit top→bottom phrasing; bare "wires" alone is too greedy (see sync 234/1).
# Allow hyphenated "top-to-bottom" and "colors top-to-bottom are …".
TOP_TO_BOTTOM_RE = re.compile(
    r"(?:"
    r"(?:(?:from\s+)?top[\s\-]*(?:to|->|→)[\s\-]*bottom)|"
    r"(?:colors?|colours?|wires?)\s+(?:top[\s\-]*(?:to|->|→)[\s\-]*bottom\s+)?(?:are|colors?|colours?)?"
    r")"
    r"\s*[:\-]?\s*([^\n\.]{5,200})",
    re.I,
)
NUMBERED_RE = re.compile(
    r"(?:^|[^\d])([1-6])\s*[\):\-]?\s*(?:(?:dark|light|pale|medium)[\s/\-]*)?"
    r"(" + _COLOUR_CORE + r")\b",
    re.I,
)
LEFT_COL_RE = re.compile(
    r"left column\s*\((?:top\s*to\s*bottom)\)\s*:\s*([^\n.]+)",
    re.I,
)
RIGHT_COL_RE = re.compile(
    r"right column\s*\((?:top\s*to\s*bottom)\)\s*:\s*([^\n.]+)",
    re.I,
)
# "Top wire is yellow, middle is blue, bottom is yellow" / "Top wavy line is …"
TOP_MID_BOT_RE = re.compile(
    r"top(?:\s+\w+){0,3}\s+is\s+"
    + COLOUR_TOKEN
    + r".{0,100}?(?:middle|second)(?:\s+\w+){0,2}\s+is\s+"
    + COLOUR_TOKEN
    + r".{0,100}?(?:bottom|last|third)(?:\s+\w+){0,2}\s+is\s+"
    + COLOUR_TOKEN,
    re.I | re.S,
)
# Inline "(top yellow, middle blue, bottom yellow)" / "(top wire dark blue, …)"
INLINE_TMB_RE = re.compile(
    r"\(?\s*top(?:\s+(?:wire|line|pair))?\s+(?:is\s+)?"
    + COLOUR_TOKEN
    + r"\s*[,;/]?\s*(?:middle|second)(?:\s+(?:wire|line|pair))?\s+(?:is\s+)?"
    + COLOUR_TOKEN
    + r"\s*[,;/]?\s*(?:bottom|last|third)(?:\s+(?:wire|line|pair))?\s+(?:is\s+)?"
    + COLOUR_TOKEN,
    re.I | re.S,
)
# Two-wire shortcut: "top blue, bottom black"
TOP_BOT_RE = re.compile(
    r"\(?\s*top(?:\s+(?:wire|line))?\s+(?:is\s+)?"
    + COLOUR_TOKEN
    + r".{0,80}?bottom(?:\s+(?:wire|line))?\s+(?:is\s+)?"
    + COLOUR_TOKEN,
    re.I | re.S,
)
# "3 pairs (top pair black/black, middle pair yellow/yellow, bottom pair white/white)"
PAIR_SLASH_RE = re.compile(
    r"(" + _COLOUR_CORE + r")\s*/\s*(" + _COLOUR_CORE + r")",
    re.I,
)
WIRE_LIST_CUE_RE = re.compile(
    r"\b(?:top[\s\-]*(?:to|->|→)[\s\-]*bottom|wires?\s+(?:are|colors?|colours?)|"
    r"(?:colors?|colours?)\s+top|wire colors?|wavy\s+(?:lines?|wires?)|"
    r"pairs?|column\s*\(|\b[1-6]\s*[\)\-:]\s*(?:red|blue|yellow|white|black))\b",
    re.I,
)
# Expert cut ordinals as stated (not remapped to slots).
INSTRUCTED_POS_RE = re.compile(
    r"\bcut\s+(?:the\s+)?"
    r"(?:"
    r"(?P<ord>first|second|third|fourth|fifth|sixth|last|bottom|top)\b"
    r"|wire\s*(?P<num>[1-6])\b"
    r"|(?P<nth>[1-6])(?:st|nd|rd|th)?\s+wire\b"
    r")",
    re.I,
)
# Defuser claims the wires module is done (may be false vs bomb_state).
FALSE_SOLVE_CLAIM_RE = re.compile(
    r"(?:wire\s+(?:module|panel)\s+solved|module\s+solved|"
    r"(?:status\s+)?LED\s+(?:is\s+)?(?:now\s+)?bright\s+green|"
    r"bright\s+green(?:\s+status)?\s+LED|"
    r"panel\s+is\s+solved|successfully\s+solved\s+the\s+(?:\d+-)?wire)",
    re.I,
)
# Expert treats a (false) solve as done and moves on.
EXPERT_ACCEPTED_FALSE_RE = re.compile(
    r"(?:wire\s+(?:module|panel)\s+is\s+solved|module\s+is\s+solved|"
    r"good[,.]?\s+(?:the\s+)?wire|check\s+the\s+back|"
    r"other\s+modules|next\s+module|any\s+(?:other\s+)?(?:interactive\s+)?modules|"
    r"blank\s+covers|continue\s+(?:rotating|looking|checking))",
    re.I,
)
# Wrong-module talk on a Wires-only bomb (Simon, Complex Wires, etc.).
MODULE_MISID_RE = re.compile(
    r"\b(?:"
    r"simon(?:\s+says)?|"
    r"memory(?:\s+module)?|"
    r"(?:the\s+)?(?:big\s+)?button|"
    r"keypad|morse|maze|password|"
    r"who'?s\s+on\s+first|"
    r"wire\s+sequences?|"
    r"complicated\s+wires?|complex\s+wires?|"
    r"not\s+(?:in|matching)\s+(?:the\s+)?manual|"
    r"doesn'?t\s+match\s+(?:any|the)\s+(?:module|manual)|"
    r"unknown\s+module|can'?t\s+identify\s+(?:the\s+)?module"
    r")\b",
    re.I,
)
NAV_ACTIONS = frozenset({"left", "right", "up", "down", "flip", "out", "back"})

# Parse-quality ranks used when choosing the last pre-cut description.
_PARSE_RANK = {
    "parsed_two_column_left_then_right": 3,
    "parsed_numbered": 3,
    "parsed_top_mid_bot": 3,
    "parsed_pairs": 3,
    "parsed_list": 2,
    "parsed_list_tail": 1,
    "parsed_top_bot": 1,
    "unparsed": 0,
}


def _ok_wire_count(n: int) -> bool:
    """Wires modules are 3–6; allow 2 so missed wires still score as mismatch not unparsed."""
    return 2 <= n <= 6


# rule_seed 1764 Wires rules (as rendered in the Expert handbook / Expert thoughts):
# 6 wires: last_blue→last; >1_white→second; exactly_1_red→third; else→second
# 3 wires: last_black+empty_port_plate→second; >1_red+no_blue→last_red;
#          exactly_1_white→the_white; else→last


@dataclass
class PlayerBundle:
    path: Path
    role: str
    footer: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)


def _load_footer(path: Path) -> dict[str, Any]:
    meta = pq.read_metadata(path).metadata or {}
    return json.loads(meta[b"footer"])


def _table_rows(path: Path) -> list[dict[str, Any]]:
    table = pq.read_table(path)
    cols = table.to_pydict()
    n = table.num_rows
    rows = []
    for i in range(n):
        rows.append({k: cols[k][i] for k in cols})
    return rows


def _parse_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s
    return value


def _message_text(output: Any, raw_output: Any) -> str | None:
    for blob in (output, raw_output):
        parsed = _parse_jsonish(blob)
        if parsed is None:
            continue
        if isinstance(parsed, str):
            if "credit balance" in parsed or parsed in ("{}", "<error>"):
                continue
            # raw may embed JSON
            try:
                nested = json.loads(parsed)
                parsed = nested
            except Exception:
                if len(parsed) > 2:
                    return parsed
                continue
        if isinstance(parsed, dict):
            if isinstance(parsed.get("message"), str) and parsed["message"].strip():
                return parsed["message"]
            result = parsed.get("result") or {}
            if isinstance(result, dict):
                data = result.get("data") or {}
                if result.get("kind") == "send_message" and isinstance(data.get("message"), str):
                    return data["message"]
    return None


def _action_info(output: Any, raw_output: Any) -> dict[str, Any] | None:
    known = {"click", "hold", "left", "right", "back", "down", "up", "flip", "out"}
    for blob in (output, raw_output):
        parsed = _parse_jsonish(blob)
        if not isinstance(parsed, dict):
            continue
        if parsed.get("action") in known:
            return parsed
        result = parsed.get("result") or parsed
        if isinstance(result, dict) and result.get("kind") == "interact_game":
            return result.get("data") or result
        if isinstance(result, dict) and result.get("action") in known:
            return result
    return None


def _sum_usage(steps: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    for step in steps:
        usage = step.get("usage")
        if usage is None:
            continue
        decoded = None
        if AsBlob is not None and isinstance(usage, (bytes, bytearray)):
            try:
                decoded = AsBlob.from_blob(usage)
            except Exception:
                decoded = None
        if decoded is None:
            parsed = _parse_jsonish(usage)
            decoded = parsed if isinstance(parsed, dict) else None
        if not isinstance(decoded, dict):
            continue
        totals["input_tokens"] += int(decoded.get("input_tokens") or 0)
        totals["output_tokens"] += int(decoded.get("output_tokens") or 0)
        totals["requests"] += int(decoded.get("requests") or 0)
    return totals


def _bomb_dict(value: Any) -> dict[str, Any] | None:
    parsed = _parse_jsonish(value)
    return parsed if isinstance(parsed, dict) else None


def _wires_module(bomb: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bomb:
        return None
    for module in bomb.get("modules") or []:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name") or module.get("moduleType") or "")
        if name == "Wires":
            return module
    return None


def _oracle_wire_entries(bomb: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Present wires sorted by slot position ascending (top→bottom). Empties omitted."""
    module = _wires_module(bomb)
    if not module:
        return None
    wires = module.get("wires") or []
    if not wires:
        return None
    ordered = sorted(wires, key=lambda w: int(w.get("position", 0)))
    return [
        {
            "position": int(w.get("position", 0)),
            "color": str(w.get("color", "")).lower().replace("gray", "grey"),
            "is_cut": bool(w.get("isCut") if w.get("isCut") is not None else w.get("is_cut")),
        }
        for w in ordered
    ]


def _oracle_wires(bomb: dict[str, Any] | None) -> list[str] | None:
    entries = _oracle_wire_entries(bomb)
    if not entries:
        return None
    return [e["color"] for e in entries]


def _norm_colour(c: str) -> str:
    return c.lower().replace("gray", "grey")


def _extract_described_wires(text: str) -> tuple[list[str] | None, str]:
    """Return (colours top→bottom or None, parse_status)."""
    if not text:
        return None, "unparsed"

    # Two-column layout (left then right) — sync 234/1 style.
    lm = LEFT_COL_RE.search(text)
    rm = RIGHT_COL_RE.search(text)
    if lm and rm:
        left = [_norm_colour(c) for c in COLOUR_RE.findall(lm.group(1))]
        right = [_norm_colour(c) for c in COLOUR_RE.findall(rm.group(1))]
        if left and right:
            colours = left + right
            if _ok_wire_count(len(colours)):
                return colours, "parsed_two_column_left_then_right"

    numbered = NUMBERED_RE.findall(text)
    if numbered:
        by_idx: dict[int, str] = {}
        for idx_s, colour in numbered:
            by_idx[int(idx_s)] = _norm_colour(colour)
        if by_idx:
            colours = [by_idx[i] for i in sorted(by_idx)]
            if _ok_wire_count(len(colours)):
                return colours, "parsed_numbered"

    # Slash-pairs: "top pair black/black, middle pair yellow/yellow, …"
    if re.search(r"\bpairs?\b", text, re.I):
        slash = PAIR_SLASH_RE.findall(text)
        if len(slash) >= 2:
            colours = [_norm_colour(a) for pair in slash for a in pair]
            if _ok_wire_count(len(colours)):
                return colours, "parsed_pairs"

    tmb = TOP_MID_BOT_RE.search(text) or INLINE_TMB_RE.search(text)
    if tmb:
        colours = [_norm_colour(c) for c in tmb.groups()]
        if _ok_wire_count(len(colours)):
            return colours, "parsed_top_mid_bot"

    m = TOP_TO_BOTTOM_RE.search(text)
    if m:
        span = m.group(1)
        # Flatten any colour/colour pairs inside the span first.
        flat: list[str] = []
        pos = 0
        for sm in PAIR_SLASH_RE.finditer(span):
            flat.extend(_norm_colour(c) for c in COLOUR_RE.findall(span[pos : sm.start()]))
            flat.extend(_norm_colour(c) for c in sm.groups())
            pos = sm.end()
        flat.extend(_norm_colour(c) for c in COLOUR_RE.findall(span[pos:]))
        colours = flat if flat else [_norm_colour(c) for c in COLOUR_RE.findall(span)]
        if _ok_wire_count(len(colours)):
            return colours, "parsed_list"
        if len(colours) > 6:
            colours = colours[-6:]
            if _ok_wire_count(len(colours)):
                return colours, "parsed_list_tail"
        # Span matched but had no colours (false positive) — fall through.

    tb = TOP_BOT_RE.search(text)
    if tb:
        colours = [_norm_colour(c) for c in tb.groups()]
        if _ok_wire_count(len(colours)):
            return colours, "parsed_top_bot"

    # Only scavenge colours from the full message when it clearly lists wires.
    if WIRE_LIST_CUE_RE.search(text):
        # Prefer slash-pair expansion when present.
        slash = PAIR_SLASH_RE.findall(text)
        if len(slash) >= 2:
            colours = [_norm_colour(a) for pair in slash for a in pair]
            if _ok_wire_count(len(colours)):
                return colours, "parsed_pairs"
        colours = [_norm_colour(c) for c in COLOUR_RE.findall(text)]
        if _ok_wire_count(len(colours)):
            return colours, "parsed_list"
        if len(colours) > 6:
            colours = colours[-6:]
            if _ok_wire_count(len(colours)):
                return colours, "parsed_list_tail"
    return None, "unparsed"


def _instructed_position(expert_cut_msg: str | None) -> str:
    """Ordinal/position as the Expert stated it (e.g. 'second', 'last', '2')."""
    if not expert_cut_msg:
        return ""
    m = INSTRUCTED_POS_RE.search(expert_cut_msg)
    if not m:
        return ""
    if m.group("ord"):
        word = m.group("ord").lower()
        if word == "bottom":
            return "last"
        if word == "top":
            return "first"
        return word
    if m.group("num"):
        return m.group("num")
    if m.group("nth"):
        return m.group("nth")
    return ""


def _cut_slot_from_steps(steps: list[dict[str, Any]]) -> str:
    """Oracle slot index of the first wire that becomes cut."""
    seen_uncut: set[int] | None = None
    for step in steps:
        entries = _oracle_wire_entries(_bomb_dict(step.get("bomb_state")))
        if not entries:
            continue
        if seen_uncut is None:
            seen_uncut = {e["position"] for e in entries if not e["is_cut"]}
        for e in entries:
            if e["is_cut"] and e["position"] in (seen_uncut or set()):
                return str(e["position"])
    return ""


def _has_empty_port_plate(bomb: dict[str, Any] | None) -> bool:
    if not bomb:
        return False
    for key in ("widgets", "WidgetList", "edgework", "presentWidgets"):
        widgets = bomb.get(key)
        if isinstance(widgets, list):
            for w in widgets:
                if not isinstance(w, dict):
                    continue
                wtype = str(w.get("type") or w.get("name") or w.get("widgetType") or "").lower()
                if "port" in wtype:
                    ports = w.get("ports") or w.get("PortTypes") or w.get("portTypes") or []
                    if ports == [] or ports is None:
                        return True
                    if isinstance(ports, list) and len(ports) == 0:
                        return True
    # Some dumps flatten empty plates as a flag / portPlate with empty list
    for plate in bomb.get("portPlates") or bomb.get("PortPlates") or []:
        if isinstance(plate, list) and len(plate) == 0:
            return True
        if isinstance(plate, dict) and not (plate.get("ports") or plate.get("PortTypes")):
            return True
    return False


def _ordinal_to_slot(entries: list[dict[str, Any]], which: str) -> int | None:
    """Map first/second/.../last (among present wires) to oracle slot index."""
    if not entries:
        return None
    which = which.lower()
    ordinals = {
        "first": 0,
        "second": 1,
        "third": 2,
        "fourth": 3,
        "fifth": 4,
        "sixth": 5,
        "last": len(entries) - 1,
    }
    if which in ordinals:
        idx = ordinals[which]
        if 0 <= idx < len(entries):
            return entries[idx]["position"]
    if which.isdigit():
        # 1-based wire count among present wires
        i = int(which) - 1
        if 0 <= i < len(entries):
            return entries[i]["position"]
    return None


def _correct_slot_rule_seed_1764(bomb: dict[str, Any] | None) -> str:
    """Oracle slot the seeded manual says to cut for TRUE bomb_state (rule_seed 1764)."""
    entries = _oracle_wire_entries(bomb)
    if not entries:
        return ""
    colours = [e["color"] for e in entries]
    n = len(colours)

    def slot_for(which: str) -> str:
        s = _ordinal_to_slot(entries, which)
        return "" if s is None else str(s)

    if n == 6:
        # 1 last blue → last; 2 >1 white → second; 3 exactly 1 red → third; else second
        if colours[-1] == "blue":
            return slot_for("last")
        if colours.count("white") > 1:
            return slot_for("second")
        if colours.count("red") == 1:
            return slot_for("third")
        return slot_for("second")

    if n == 3:
        # 1 last black + empty port plate → second
        # 2 >1 red and no blue → last red
        # 3 exactly 1 white → the white
        # else last
        if colours[-1] == "black" and _has_empty_port_plate(bomb):
            return slot_for("second")
        if colours.count("red") > 1 and colours.count("blue") == 0:
            reds = [e["position"] for e in entries if e["color"] == "red"]
            return str(reds[-1]) if reds else ""
        if colours.count("white") == 1:
            for e in entries:
                if e["color"] == "white":
                    return str(e["position"])
        return slot_for("last")

    # 4/5-wire branches not needed for this pilot; leave blank rather than guess.
    return ""


def _solve_type(
    outcome: str,
    grounding_order_match: str,
    cut_slot: str,
    correct_slot: str,
    recovered_after_strike: str,
) -> str:
    """Classify every solved game into exactly one label; empty only if not solved."""
    if outcome != "solved":
        return ""
    # Mistake-then-fix wins: do not call these "wrong_cut".
    if recovered_after_strike == "True":
        return "recovered_win"
    if cut_slot and correct_slot and cut_slot != correct_slot:
        return "cut_slot_mismatch"
    # Prefer grounding when the cut slot was not recorded (e.g. defuser steps
    # missing from the parquet) but the colour order matched and the bomb solved.
    if grounding_order_match == "True":
        return "grounded"
    if grounding_order_match == "False":
        return "cancelled_errors"
    # Solved, but grounding parse / cut record both incomplete.
    return "uncategorized_solve"


def _wires_in_focus(bomb: dict[str, Any] | None) -> bool:
    module = _wires_module(bomb)
    if not module:
        return False
    focused = module.get("inFocus")
    if focused is None:
        focused = module.get("in_focus")
    return bool(focused)


def _wires_is_solved(bomb: dict[str, Any] | None) -> bool | None:
    module = _wires_module(bomb)
    if not module:
        return None
    if "isSolved" in module:
        return bool(module.get("isSolved"))
    if "is_solved" in module:
        return bool(module.get("is_solved"))
    return None


def _strike_count(bomb: dict[str, Any] | None) -> int:
    if not bomb:
        return 0
    strikes = bomb.get("strikes")
    if isinstance(strikes, list):
        return len(strikes)
    if isinstance(strikes, int):
        return strikes
    return 0


def _cut_positions(bomb: dict[str, Any] | None) -> set[int]:
    entries = _oracle_wire_entries(bomb)
    if not entries:
        return set()
    return {e["position"] for e in entries if e["is_cut"]}


def _post_strike_and_false_claim_metrics(
    defuser_steps: list[dict[str, Any]] | None,
    expert_steps: list[dict[str, Any]] | None,
    correct_slot: str,
) -> dict[str, str]:
    """False-solve claims + post-first-strike recovery / navigation stats."""
    empty = {
        "false_solve_claim": "False",
        "expert_accepted_false_claim": "",
        "steps_after_first_strike": "",
        "nav_steps_after_first_strike": "",
        "redescribed_after_strike": "",
        "recovered_after_strike": "",
    }
    if not defuser_steps:
        return empty

    false_claim_ts: float | None = None
    for step in defuser_steps:
        msg = _message_text(step.get("output"), step.get("raw_output"))
        if not msg or not FALSE_SOLVE_CLAIM_RE.search(msg):
            continue
        bomb = _bomb_dict(step.get("bomb_state"))
        solved = _wires_is_solved(bomb)
        # Claim while oracle says unsolved (or wires missing from state).
        if solved is False or solved is None:
            false_claim_ts = float(step.get("timestamp") or 0.0)
            break

    false_solve_claim = false_claim_ts is not None
    expert_accepted = ""
    if false_solve_claim:
        expert_accepted = "False"
        for step in expert_steps or []:
            ts = float(step.get("timestamp") or 0.0)
            if ts + 1e-9 < (false_claim_ts or 0.0):
                continue
            msg = _message_text(step.get("output"), step.get("raw_output"))
            if not msg:
                continue
            # Ignore model scratchpads; only player-facing chat counts.
            stripped = msg.strip()
            if stripped.startswith("<thought>") and "</thought>" in stripped:
                after = stripped.split("</thought>", 1)[-1].strip()
                if not after or after.startswith("<action>"):
                    continue
                msg = after
            if EXPERT_ACCEPTED_FALSE_RE.search(msg):
                expert_accepted = "True"
                break
            # Fresh cut order after the false claim → expert did not accept.
            if INSTRUCTED_POS_RE.search(msg) or (
                CUT_INSTR_RE.search(msg) and not BEFORE_CUT_RE.search(msg)
                and re.search(r"\bcut\s+(?:the\s+)?(?:\d|first|second|third|fourth|fifth|sixth|last|wire)", msg, re.I)
            ):
                break

    first_strike_idx: int | None = None
    first_strike_ts: float | None = None
    cuts_at_strike: set[int] = set()
    for idx, step in enumerate(defuser_steps):
        bomb = _bomb_dict(step.get("bomb_state"))
        if _strike_count(bomb) >= 1:
            first_strike_idx = idx
            first_strike_ts = float(step.get("timestamp") or 0.0)
            cuts_at_strike = _cut_positions(bomb)
            break

    if first_strike_idx is None or first_strike_ts is None:
        return {
            "false_solve_claim": str(false_solve_claim),
            "expert_accepted_false_claim": expert_accepted,
            "steps_after_first_strike": "",
            "nav_steps_after_first_strike": "",
            "redescribed_after_strike": "",
            "recovered_after_strike": "",
        }

    steps_after = len(defuser_steps) - first_strike_idx
    nav_after = 0
    redescribed = False
    recovered = False
    correct_pos: int | None = int(correct_slot) if correct_slot.isdigit() else None

    for step in defuser_steps[first_strike_idx:]:
        ts = float(step.get("timestamp") or 0.0)
        act = _action_info(step.get("output"), step.get("raw_output"))
        if isinstance(act, dict) and act.get("action") in NAV_ACTIONS:
            nav_after += 1

        msg = _message_text(step.get("output"), step.get("raw_output"))
        if msg and ts > first_strike_ts + 1e-9:
            colours, status = _extract_described_wires(msg)
            if colours is not None and _PARSE_RANK.get(status, 0) > 0:
                redescribed = True

        bomb = _bomb_dict(step.get("bomb_state"))
        if _wires_is_solved(bomb) is True:
            recovered = True
        if correct_pos is not None:
            new_cuts = _cut_positions(bomb) - cuts_at_strike
            if correct_pos in new_cuts:
                recovered = True

    return {
        "false_solve_claim": str(false_solve_claim),
        "expert_accepted_false_claim": expert_accepted,
        "steps_after_first_strike": str(steps_after),
        "nav_steps_after_first_strike": str(nav_after),
        "redescribed_after_strike": str(redescribed),
        "recovered_after_strike": str(recovered),
    }


def _dialogue_blob(steps: list[dict[str, Any]] | None) -> str:
    if not steps:
        return ""
    parts: list[str] = []
    for step in steps:
        msg = _message_text(step.get("output"), step.get("raw_output"))
        if msg:
            parts.append(msg)
    return "\n".join(parts)


def _any_wire_cut(steps: list[dict[str, Any]] | None) -> bool:
    if not steps:
        return False
    for step in steps:
        if _cut_positions(_bomb_dict(step.get("bomb_state"))):
            return True
    return False


def _failure_type(
    outcome: str,
    *,
    false_solve_claim: str,
    recovered_after_strike: str,
    steps_after_first_strike: str,
    defuser_steps: list[dict[str, Any]] | None,
    expert_steps: list[dict[str, Any]] | None,
    expert_cut_msg: str | None,
) -> str:
    """One label per failed game; empty when solved / non-failure."""
    if outcome == "solved":
        return ""

    # Priority order matches the failure taxonomy (see module docstring).
    if false_solve_claim == "True":
        return "false_solve_claim"

    blob = _dialogue_blob(expert_steps) + "\n" + _dialogue_blob(defuser_steps)
    if MODULE_MISID_RE.search(blob):
        # Not "description was wrong" — only that other-module talk appeared.
        return "other_module_talk"

    cut_happened = _any_wire_cut(defuser_steps)
    if not cut_happened:
        # Expert gave a cut, or game just stalled with no cut at all.
        return "never_cut"

    # Struck then never recovered — includes exploratory top-wire probes.
    if steps_after_first_strike and recovered_after_strike != "True":
        return "struck_no_fix"

    # Timed out after cutting something but no strike recorded / odd edge cases.
    if expert_cut_msg and recovered_after_strike != "True":
        return "struck_no_fix"

    return "never_cut"


def _instruction_has_colour(expert_cut_msg: str | None) -> str:
    if not expert_cut_msg:
        return ""
    return str(bool(COLOUR_RE.search(expert_cut_msg)))


def _wire_description_zoom_metrics(
    defuser_steps: list[dict[str, Any]], cut_action_ts: float | None
) -> dict[str, str]:
    """Track pre/post-zoom wire lists relative to the grounding description."""
    cutoff = cut_action_ts if cut_action_ts is not None else float("inf")
    first_zoom_ts: float | None = None

    grounding_rank = -1
    grounding_in_focus = ""
    pre_rank = -1
    pre_colours: list[str] | None = None
    post_rank = -1
    post_colours: list[str] | None = None

    for step in defuser_steps:
        ts = float(step.get("timestamp") or 0.0)
        if ts > cutoff:
            break
        bomb = _bomb_dict(step.get("bomb_state"))
        in_focus = _wires_in_focus(bomb)
        if in_focus and first_zoom_ts is None:
            first_zoom_ts = ts

        msg = _message_text(step.get("output"), step.get("raw_output"))
        if not msg:
            continue
        colours, status = _extract_described_wires(msg)
        rank = _PARSE_RANK.get(status, 0)
        if rank == 0 or colours is None:
            continue

        if rank > grounding_rank or (rank == grounding_rank and rank > 0):
            grounding_rank = rank
            grounding_in_focus = str(in_focus)

        if first_zoom_ts is None or ts < first_zoom_ts:
            if rank > pre_rank or (rank == pre_rank and rank > 0):
                pre_rank = rank
                pre_colours = colours
        else:
            if rank > post_rank or (rank == post_rank and rank > 0):
                post_rank = rank
                post_colours = colours

    described_before_zoom = ""
    if grounding_in_focus:
        described_before_zoom = str(grounding_in_focus == "False")

    if first_zoom_ts is None:
        redescribed_after_zoom = "False"
        redescription_changed = "False"
    else:
        redescribed_after_zoom = str(post_colours is not None)
        if pre_colours is not None and post_colours is not None:
            redescription_changed = str(pre_colours != post_colours)
        elif post_colours is not None and pre_colours is None:
            redescription_changed = "True"
        else:
            redescription_changed = "False"

    return {
        "described_before_zoom": described_before_zoom,
        "redescribed_after_zoom": redescribed_after_zoom,
        "redescription_changed": redescription_changed,
    }


def _grounding(
    described: list[str] | None, parse_status: str, oracle: list[str] | None
) -> dict[str, Any]:
    if parse_status == "unparsed" or described is None:
        return {
            "grounding_status": "unparsed",
            "grounding_count_match": "",
            "grounding_colours_match": "",
            "grounding_order_match": "",
            "described_wires": "",
            "oracle_wires": ",".join(oracle) if oracle else "",
        }
    if oracle is None:
        return {
            "grounding_status": "no_oracle",
            "grounding_count_match": "",
            "grounding_colours_match": "",
            "grounding_order_match": "",
            "described_wires": ",".join(described),
            "oracle_wires": "",
        }
    count_ok = len(described) == len(oracle)
    order_ok = described == oracle
    colours_ok = sorted(described) == sorted(oracle)
    status = "match" if order_ok else ("partial" if count_ok or colours_ok else "mismatch")
    return {
        "grounding_status": status,
        "grounding_count_match": str(count_ok),
        "grounding_colours_match": str(colours_ok),
        "grounding_order_match": str(order_ok),
        "described_wires": ",".join(described),
        "oracle_wires": ",".join(oracle),
    }


def _outcome_from_bomb(bomb: dict[str, Any] | None, is_hard_crash: bool) -> tuple[str, str]:
    if is_hard_crash:
        return "crash", "hard_crash"
    if not bomb:
        return "incomplete", "missing_final_bomb_state"
    # support both alias styles
    is_solved = bomb.get("is_solved")
    if is_solved is None:
        is_solved = bomb.get("isSolved")
    is_detonated = bomb.get("is_detonated")
    if is_detonated is None:
        is_detonated = bomb.get("isDetonated")
    strikes = bomb.get("strikes") or []
    timer = bomb.get("timer_module") or bomb.get("timerModule") or {}
    secs = timer.get("seconds_remaining")
    if secs is None:
        secs = timer.get("secondsRemaining")
    max_strikes = bomb.get("max_strikes") or bomb.get("maxStrikes") or 3
    n_strikes = len(strikes) if isinstance(strikes, list) else int(strikes or 0)

    if is_solved:
        return "solved", ""
    # Prefer timeout when the clock hit zero (GPTNT labels these timeout even with strikes).
    if secs is not None and float(secs) <= 0 and n_strikes < int(max_strikes):
        return "timeout", f"seconds_remaining=0;strikes={n_strikes}"
    if n_strikes >= int(max_strikes) or is_detonated:
        return "strikeout", f"strikes={n_strikes}"
    if secs is not None and float(secs) <= 0:
        return "timeout", "seconds_remaining=0"
    return "incomplete", "no_terminal_outcome"


def _group_parquets(run_dir: Path) -> dict[str, dict[str, PlayerBundle]]:
    """attempt_name -> role -> bundle."""
    groups: dict[str, dict[str, PlayerBundle]] = defaultdict(dict)
    for path in sorted(run_dir.glob("experiment-*.parquet")):
        footer = _load_footer(path)
        instance = footer["instance"]
        # attempt_name not always in footer; derive from filename prefix
        # experiment-<attempt_name>-<uuid>.parquet
        name = path.name
        body = name[len("experiment-") : -len(".parquet")]
        # uuid is last 36 chars after final '-' of uuid format — split from right on known uuid
        # safer: use session_id grouping
        session_id = str(instance["session_id"])
        role = footer["role"]
        steps = _table_rows(path)
        groups[session_id][role] = PlayerBundle(path=path, role=role, footer=footer, steps=steps)
        # stash attempt label on footer for later
        footer["_attempt_label"] = body.rsplit("-", 5)[0] if "-" in body else body
    return groups


def summarise_run(run_dir: Path) -> list[dict[str, Any]]:
    meta_path = run_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    condition = meta.get("condition", "unknown")
    git_commit = meta.get("git_commit")
    prompt_hash = meta.get("prompt_hash_combined")

    groups = _group_parquets(run_dir)
    rows: list[dict[str, Any]] = []

    for session_id, roles in sorted(groups.items(), key=lambda kv: kv[0]):
        defuser = roles.get("defuser")
        expert = roles.get("expert")
        primary = defuser or expert
        if primary is None:
            continue
        inst = primary.footer["instance"]
        mission = inst.get("mission_spec") or {}
        seed = mission.get("seed")
        rule_seed = mission.get("ruleSeed") or mission.get("rule_seed")
        attempt = inst.get("attempt")
        mode = (inst.get("defuser_protocol") or {}).get("communication_style")
        defuser_name = inst.get("defuser_name")
        expert_name = inst.get("expert_name")

        # Prefer provenance from footer; fall back to run_meta / force-nulls
        prov_commit = primary.footer.get("release_commit") or git_commit
        prov_prompt = primary.footer.get("protected_content_digest") or prompt_hash

        final_bomb = None
        if defuser and defuser.footer.get("final_bomb_state"):
            final_bomb = defuser.footer["final_bomb_state"]
        # Prefer last non-null defuser step bomb_state (richer / consistent aliases)
        if defuser:
            for step in reversed(defuser.steps):
                b = _bomb_dict(step.get("bomb_state"))
                if b:
                    final_bomb = b
                    break

        is_hard_crash = bool(primary.footer.get("is_hard_crash"))
        outcome, reason = _outcome_from_bomb(final_bomb, is_hard_crash)

        # Expert API failures
        expert_errors = 0
        if expert:
            for step in expert.steps:
                raw = str(step.get("raw_output") or "")
                if "credit balance" in raw or "invalid_request_error" in raw:
                    expert_errors += 1
        if expert_errors and outcome in {"solved", "timeout", "strikeout"}:
            reason = (reason + "; " if reason else "") + f"expert_api_errors={expert_errors}"

        # Timeline: messages + actions
        timeline: list[tuple[float, str, str, Any]] = []
        for role_name, bundle in roles.items():
            for step in bundle.steps:
                ts = float(step.get("timestamp") or 0.0)
                msg = _message_text(step.get("output"), step.get("raw_output"))
                act = _action_info(step.get("output"), step.get("raw_output"))
                if msg:
                    timeline.append((ts, role_name, "message", msg))
                if act:
                    timeline.append((ts, role_name, "action", act))
        timeline.sort(key=lambda x: x[0])

        expert_cut_ts = None
        expert_cut_msg = None
        for ts, role_name, kind, payload in timeline:
            if role_name == "expert" and kind == "message" and isinstance(payload, str):
                if CUT_INSTR_RE.search(payload) and not BEFORE_CUT_RE.search(payload):
                    expert_cut_ts = ts
                    expert_cut_msg = payload
                    break

        cut_action_ts = None
        for ts, role_name, kind, payload in timeline:
            if role_name != "defuser" or kind != "action":
                continue
            if not isinstance(payload, dict):
                continue
            action = payload.get("action")
            # deciding cut is a click (SoM), not rotate
            if action == "click" and (expert_cut_ts is None or ts >= expert_cut_ts - 1e-6):
                # if no expert cut, still take first click after descriptions as candidate
                if expert_cut_ts is not None or cut_action_ts is None:
                    cut_action_ts = ts
                    if expert_cut_ts is not None:
                        break

        # Best defuser wire description before cut click (or before end).
        # Prefer higher-quality parses; among equals, take the latest.
        desc_text = None
        desc_rank = -1
        cutoff = cut_action_ts if cut_action_ts is not None else float("inf")
        for ts, role_name, kind, payload in timeline:
            if ts > cutoff:
                break
            if role_name == "defuser" and kind == "message" and isinstance(payload, str):
                _colours, status = _extract_described_wires(payload)
                rank = _PARSE_RANK.get(status, 0)
                if rank > desc_rank or (rank == desc_rank and rank > 0):
                    desc_text = payload
                    desc_rank = rank

        described, parse_status = _extract_described_wires(desc_text or "")
        # Oracle wires: prefer bomb_state just before cut; else earliest / final
        oracle = None
        if defuser:
            for step in defuser.steps:
                ts = float(step.get("timestamp") or 0.0)
                if cut_action_ts is not None and ts > cut_action_ts:
                    break
                b = _bomb_dict(step.get("bomb_state"))
                ow = _oracle_wires(b)
                if ow:
                    oracle = ow
        if oracle is None:
            oracle = _oracle_wires(final_bomb)

        ground = _grounding(described, parse_status, oracle)

        # Prefer pre-cut bomb for correct_slot (uncut wires); fall back to final.
        bomb_for_rules = None
        if defuser:
            for step in defuser.steps:
                ts = float(step.get("timestamp") or 0.0)
                if cut_action_ts is not None and ts > cut_action_ts:
                    break
                b = _bomb_dict(step.get("bomb_state"))
                if _oracle_wire_entries(b):
                    bomb_for_rules = b
        if bomb_for_rules is None:
            bomb_for_rules = final_bomb

        instructed_position = _instructed_position(expert_cut_msg)
        cut_slot = _cut_slot_from_steps(defuser.steps) if defuser else ""
        correct_slot = (
            _correct_slot_rule_seed_1764(bomb_for_rules)
            if str(rule_seed) == "1764"
            else ""
        )
        zoom_metrics = (
            _wire_description_zoom_metrics(defuser.steps, cut_action_ts)
            if defuser
            else {
                "described_before_zoom": "",
                "redescribed_after_zoom": "",
                "redescription_changed": "",
            }
        )
        strike_metrics = _post_strike_and_false_claim_metrics(
            defuser.steps if defuser else None,
            expert.steps if expert else None,
            correct_slot,
        )
        solve_type = _solve_type(
            outcome,
            ground.get("grounding_order_match", ""),
            cut_slot,
            correct_slot,
            strike_metrics["recovered_after_strike"],
        )
        failure_type = _failure_type(
            outcome,
            false_solve_claim=strike_metrics["false_solve_claim"],
            recovered_after_strike=strike_metrics["recovered_after_strike"],
            steps_after_first_strike=strike_metrics["steps_after_first_strike"],
            defuser_steps=defuser.steps if defuser else None,
            expert_steps=expert.steps if expert else None,
            expert_cut_msg=expert_cut_msg,
        )

        def_usage = _sum_usage(defuser.steps) if defuser else {"input_tokens": 0, "output_tokens": 0, "requests": 0}
        exp_usage = _sum_usage(expert.steps) if expert else {"input_tokens": 0, "output_tokens": 0, "requests": 0}

        strikes = final_bomb.get("strikes") if final_bomb else None
        if isinstance(strikes, list):
            strike_count = len(strikes)
        else:
            strike_count = ""

        timer = (final_bomb or {}).get("timer_module") or (final_bomb or {}).get("timerModule") or {}
        secs = timer.get("seconds_remaining")
        if secs is None:
            secs = timer.get("secondsRemaining")

        cut_after_expert = ""
        if expert_cut_ts is not None and cut_action_ts is not None:
            cut_after_expert = str(cut_action_ts >= expert_cut_ts - 1e-9)
        elif expert_cut_ts is None:
            cut_after_expert = "no_expert_cut"
        else:
            cut_after_expert = "no_defuser_click"

        rows.append(
            {
                "run_dir": str(run_dir),
                "session_id": session_id,
                "seed": seed,
                "attempt": attempt,
                "mode": mode,
                "condition": condition,
                "defuser_model": defuser_name,
                "expert_model": expert_name,
                "rule_seed": rule_seed,
                "git_commit": prov_commit or "",
                "prompt_hash": prov_prompt or "",
                "suite_name": inst.get("suite_name"),
                "suite_revision": inst.get("suite_revision"),
                "outcome": outcome,
                "reason": reason,
                "strike_count": strike_count,
                "seconds_remaining": secs if secs is not None else "",
                "expert_cut_before_click": cut_after_expert,
                "expert_cut_excerpt": (expert_cut_msg or "")[:160].replace("\n", " "),
                "instructed_position": instructed_position,
                "cut_slot": cut_slot,
                "correct_slot": correct_slot,
                "solve_type": solve_type,
                "described_before_zoom": zoom_metrics["described_before_zoom"],
                "instruction_has_colour": _instruction_has_colour(expert_cut_msg),
                "redescribed_after_zoom": zoom_metrics["redescribed_after_zoom"],
                "redescription_changed": zoom_metrics["redescription_changed"],
                "false_solve_claim": strike_metrics["false_solve_claim"],
                "expert_accepted_false_claim": strike_metrics["expert_accepted_false_claim"],
                "steps_after_first_strike": strike_metrics["steps_after_first_strike"],
                "nav_steps_after_first_strike": strike_metrics["nav_steps_after_first_strike"],
                "redescribed_after_strike": strike_metrics["redescribed_after_strike"],
                "recovered_after_strike": strike_metrics["recovered_after_strike"],
                "failure_type": failure_type,
                "defuser_input_tokens": def_usage["input_tokens"],
                "defuser_output_tokens": def_usage["output_tokens"],
                "expert_input_tokens": exp_usage["input_tokens"],
                "expert_output_tokens": exp_usage["output_tokens"],
                "total_input_tokens": def_usage["input_tokens"] + exp_usage["input_tokens"],
                "total_output_tokens": def_usage["output_tokens"] + exp_usage["output_tokens"],
                "n_defuser_steps": len(defuser.steps) if defuser else 0,
                "n_expert_steps": len(expert.steps) if expert else 0,
                "expert_api_errors": expert_errors,
                "is_hard_crash": is_hard_crash,
                **ground,
            }
        )

    rows.sort(key=lambda r: (str(r["mode"]), int(r["seed"] or 0), int(r["attempt"] or 0)))
    return _dedupe_attempt_rows(rows)


_OUTCOME_RANK = {
    "solved": 5,
    "timeout": 4,
    "strikeout": 4,
    "crash": 2,
    "incomplete": 1,
}


def _dedupe_attempt_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per (mode, seed, attempt) when resumes left duplicate sessions."""
    best: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for r in rows:
        key = (r.get("mode"), r.get("seed"), r.get("attempt"))
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue
        r_rank = _OUTCOME_RANK.get(str(r.get("outcome")), 0)
        c_rank = _OUTCOME_RANK.get(str(cur.get("outcome")), 0)
        r_steps = int(r.get("n_defuser_steps") or 0)
        c_steps = int(cur.get("n_defuser_steps") or 0)
        r_ground = 0 if r.get("grounding_status") == "unparsed" else 1
        c_ground = 0 if cur.get("grounding_status") == "unparsed" else 1
        if (r_rank, r_steps, r_ground) > (c_rank, c_steps, c_ground):
            best[key] = r
    out = list(best.values())
    out.sort(key=lambda r: (str(r["mode"]), int(r["seed"] or 0), int(r["attempt"] or 0)))
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Folder of experiment-*.parquet files")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="CSV path (default: <run_dir>/summary.csv)",
    )
    args = parser.parse_args()
    run_dir = args.run_dir
    if not run_dir.is_dir():
        print(f"not a directory: {run_dir}", file=sys.stderr)
        sys.exit(1)
    rows = summarise_run(run_dir)
    out = args.output or (run_dir / "summary.csv")
    write_csv(rows, out)
    print(f"wrote {out} ({len(rows)} games)")
    # compact preview
    for r in rows:
        print(
            f"{r['mode']:5} seed={r['seed']} att={r['attempt']} {r['outcome']:10} "
            f"ground={r['grounding_status']:10} solve={r['solve_type'] or '-':16} "
            f"instr={r['instructed_position'] or '-'} cut={r['cut_slot'] or '-'} "
            f"correct={r['correct_slot'] or '-'} "
            f"false_claim={r['false_solve_claim']} "
            f"fail={r['failure_type'] or '-'} "
            f"post_strike={r['steps_after_first_strike'] or '-'} "
            f"nav_after={r['nav_steps_after_first_strike'] or '-'} "
            f"recovered={r['recovered_after_strike'] or '-'} "
            f"desc={r['described_wires'] or '-'} oracle={r['oracle_wires'] or '-'}"
        )


if __name__ == "__main__":
    main()
