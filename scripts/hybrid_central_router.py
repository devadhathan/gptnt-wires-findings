"""Hybrid Central Router — real GPTNT integration.

Two modes:

  central  — Observation → sparse vector → reasoner → SoM click (no dialogue).
  dialogue — Same perception/act stack, but Defuser and Expert only cooperate
             over a chat transcript (structured collab bridge to GPTNT).

Encoders:
  oracle — mod BombState ground truth (proves the control loop)
  vlm    — per-wire VLM crops (default claude-sonnet-4-6); serial from widgets

Reasoners / Expert:
  rules — deterministic vanilla Wires manual (central mode only)
  haiku — Claude Haiku 4.5 as Expert (central: sparse→ACTION; dialogue: chat→ASK/CUT)

Usage:
    .venv/bin/python scripts/hybrid_central_router.py --encoder oracle --reasoner haiku
    .venv/bin/python scripts/hybrid_central_router.py --encoder vlm --vlm-model gpt-4.1 --seed 845
    .venv/bin/python scripts/hybrid_central_router.py --mode dialogue --vlm-model gpt-4.1 --seed 845
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import re
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import anyio
import numpy as np
import redis
import structlog
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from PIL import Image

from gptnt.ktane.actions import GameActionType, KtaneGameplayInput, RelativeCoordinate
from gptnt.ktane.client import KtaneClient
from gptnt.ktane.mission_spec import KtaneMissionConfig
from gptnt.ktane.process_manager import GameProcessManager
from gptnt.ktane.state.bomb import BombState
from gptnt.ktane.state.game import GameState
from gptnt.ktane.state.modules import WireSetModuleState
from gptnt.ktane.state.widget import SerialWidgetState
from gptnt.processors.labels.drawing import AnnotationBackgroundParams, AnnotationTextParams
from gptnt.processors.set_of_marks import MaskDrawingParams, SetOfMarksHandler

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from hybrid_dialogue_bridge import (  # noqa: E402
    append_live_chat,
    export_and_ingest_hybrid_dialogue,
    reset_live_chat,
)

logger = structlog.get_logger()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
VLM_MODEL = os.getenv("HYBRID_VLM_MODEL", "claude-sonnet-4-6")
EXPERT_MODEL = os.getenv("HYBRID_EXPERT_MODEL", "claude-haiku-4-5")

EncoderKind = Literal["oracle", "vlm"]
ReasonerKind = Literal["rules", "haiku"]
ModeKind = Literal["central", "dialogue"]

WIRES_MANUAL = """
WIRES (3–6 wires, top→bottom). Cut exactly one.
3: no red → 2nd; else last white → last; else >1 blue → last blue; else last.
4: >1 red & serial last digit odd → last red; else last yellow & no red → 1st;
   else exactly 1 blue → 1st; else >1 yellow → last; else 2nd.
5: last black & serial last digit odd → 4th; else exactly 1 red & >1 yellow → 1st;
   else no black → 2nd; else 1st.
6: no yellow & serial last digit odd → 3rd; else exactly 1 yellow & >1 white → 4th;
   else no red → last; else 4th.
""".strip()

VLM_SPARSE_PROMPT = """You are a perception encoder for Keep Talking and Nobody Explodes.
The image is a zoomed-in Wires module. Letter labels A,B,C… mark wires top→bottom.
Output ONE line only, no prose:

MODULE: wires | COUNT: <n> | STATE: [<color>,...] | CUT: []

Rules:
- Count EVERY labeled wire (A through last letter). COUNT must match that number (3–6).
- Colors top→bottom only from: red, blue, black, yellow, white
- STATE lists one color per label in order A,B,C… — comma-separated, no spaces
- Leave CUT as []
Example: MODULE: wires | COUNT: 6 | STATE: [black,yellow,white,yellow,red,black] | CUT: []
"""

WIRE_COLOR_PROMPT = """This crop shows ONE wire from Keep Talking and Nobody Explodes.
Reply with exactly one word — the wire color — from: red blue black yellow white
No punctuation, no explanation."""

VALID_WIRE_COLORS = frozenset({"red", "blue", "black", "yellow", "white"})
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "..", "runs", "hybrid_debug")

EXPERT_PROMPT = """You are the bomb Expert. You receive a sparse sensory vector from the Defuser/encoder
and the Wires manual. Reply with EXACTLY one line:
  ACTION: CUT <k>
or
  ACTION: NO_OP
where <k> is the 1-indexed wire number top→bottom (A=1, B=2, …).
Do not explain. Do not use dialogue. Apply the manual strictly to the sparse STATE and SERIAL_ODD.
"""

DIALOGUE_EXPERT_PROMPT = """You are the Expert in Keep Talking and Nobody Explodes.
You have the bomb manual. You cannot see the bomb. You only read Defuser messages.

Reply with EXACTLY one of:
  ASK: <short clarifying question>
  CUT: <k>
where <k> is the 1-indexed wire top→bottom (A=1, B=2, …).

Rules:
- Apply the Wires manual strictly to what the Defuser reported.
- If color order, wire count, or serial last-digit odd/even is missing, you MUST ASK.
- Prefer CUT only when you have enough info.
- No explanations. No other formats.
"""

DIALOGUE_EXPERT_REQUIRE_ASK_PROMPT = """You are the Expert in Keep Talking and Nobody Explodes.
You have the bomb manual. You cannot see the bomb. You only read Defuser messages.

Reply with EXACTLY one of:
  ASK: <short clarifying question>
  CUT: <k>
where <k> is the 1-indexed wire top→bottom (A=1, B=2, …).

Rules:
- Apply the Wires manual strictly to what the Defuser reported.
- You MUST ask at least one clarifying question before any CUT.
- After the Defuser answers, CUT when you have enough info.
- No explanations. No other formats.
"""

DIALOGUE_EXPERT_NATURAL_PROMPT = """You are the Expert in Keep Talking and Nobody Explodes, talking over radio.
You have the bomb manual. You cannot see the bomb — only the Defuser's messages.

Speak in short natural sentences (no ASK:/CUT: prefixes, no JSON).
- If you need missing facts (wire colors top→bottom, count, serial last digit odd/even), ask a normal question.
- When you know which wire to cut, say it clearly, e.g. "Cut the 4th wire" or "Cut wire 4."
- Apply the Wires manual strictly. Do not invent colors the Defuser did not report.
- Keep replies to 1–2 sentences.
"""

DIALOGUE_EXPERT_NATURAL_REQUIRE_ASK_PROMPT = """You are the Expert in Keep Talking and Nobody Explodes, talking over radio.
You have the bomb manual. You cannot see the bomb — only the Defuser's messages.

Speak in short natural sentences (no ASK:/CUT: prefixes).
You MUST ask at least one clarifying question before telling them to cut any wire.
After they answer and you have enough info, say clearly which wire to cut, e.g. "Cut the 4th wire."
Apply the Wires manual strictly. Keep replies to 1–2 sentences.
"""

DIALOGUE_DEFUSER_PROMPT = """You are the Defuser. Answer the Expert's question using ONLY the perception notes.
Reply with one short factual sentence. Do not suggest which wire to cut.
"""

DIALOGUE_DEFUSER_NATURAL_PROMPT = """You are the Defuser on a bomb, talking over radio to your Expert.
Using ONLY the perception notes, answer in 1–2 short spoken sentences.
Be factual. Do not suggest which wire to cut. No KEY=value tokens. No bullet lists.
"""

DIALOGUE_DEFUSER_OPENING_NATURAL_PROMPT = """You are the Defuser who just focused a Wires module. Radio the Expert.
Using ONLY the perception notes, say 1–2 short stressed sentences.
Mention that it's Wires and how many wires you see, and the serial if present.
Do NOT list every wire color yet — wait for the Expert to ask.
Do not suggest which wire to cut. No KEY=value tokens.
"""

_ORDINAL_TO_N = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "sixth": 6,
    "6th": 6,
}


# ---------------------------------------------------------------------------
# Sparse encoder
# ---------------------------------------------------------------------------


def _serial_meta(bomb: BombState) -> tuple[str, int]:
    serial = next((w for w in bomb.widgets if isinstance(w, SerialWidgetState)), None)
    serial_text = serial.serial_number if serial else "?"
    serial_odd = int(serial_text[-1].isdigit() and int(serial_text[-1]) % 2 == 1)
    return serial_text, serial_odd


def encode_bomb_state(bomb: BombState) -> str:
    """Oracle encoder: compress BombState into a sparse token string."""
    serial_text, serial_odd = _serial_meta(bomb)
    wires_mod = next((m for m in bomb.modules if isinstance(m, WireSetModuleState)), None)
    if wires_mod is None:
        return (
            f"MODULE: none | SERIAL: {serial_text} | SERIAL_ODD: {serial_odd} | "
            f"STRIKES: {bomb.strike_count} | SOLVED: {int(bomb.is_solved)}"
        )

    colors = ",".join(w.color for w in sorted(wires_mod.wires, key=lambda w: w.position))
    cuts = ",".join(
        str(w.position) for w in sorted(wires_mod.wires, key=lambda w: w.position) if w.is_cut
    )
    return (
        f"MODULE: wires | COUNT: {len(wires_mod.wires)} | STATE: [{colors}] | "
        f"CUT: [{cuts}] | SERIAL: {serial_text} | SERIAL_ODD: {serial_odd} | "
        f"FOCUS: {int(wires_mod.in_focus)} | MOD_SOLVED: {int(wires_mod.is_solved)} | "
        f"STRIKES: {bomb.strike_count} | BOMB_SOLVED: {int(bomb.is_solved)}"
    )


def _merge_vlm_sparse(vlm_line: str, bomb: BombState) -> str:
    """Attach serial / focus / solved metadata from the live bomb to a VLM wire line."""
    serial_text, serial_odd = _serial_meta(bomb)
    wires_mod = next((m for m in bomb.modules if isinstance(m, WireSetModuleState)), None)
    focus = int(wires_mod.in_focus) if wires_mod else 0
    mod_solved = int(wires_mod.is_solved) if wires_mod else 0
    match = re.search(
        r"MODULE:\s*wires\s*\|\s*COUNT:\s*\d+\s*\|\s*STATE:\s*\[[^\]]*\]\s*\|\s*CUT:\s*\[[^\]]*\]",
        vlm_line,
        flags=re.IGNORECASE,
    )
    core = match.group(0) if match else vlm_line.strip().splitlines()[0]
    return (
        f"{core} | SERIAL: {serial_text} | SERIAL_ODD: {serial_odd} | "
        f"FOCUS: {focus} | MOD_SOLVED: {mod_solved} | "
        f"STRIKES: {bomb.strike_count} | BOMB_SOLVED: {int(bomb.is_solved)}"
    )


def _pil_to_png_b64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-") or model.startswith("anthropic/")


def _normalize_wire_color(raw: str) -> str | None:
    token = re.sub(r"[^a-z]", "", raw.strip().lower().split()[0] if raw.strip() else "")
    aliases = {"grey": "white", "gray": "white", "silver": "white", "gold": "yellow"}
    token = aliases.get(token, token)
    return token if token in VALID_WIRE_COLORS else None


def _crop_wire_band(
    image: Image.Image,
    coord: RelativeCoordinate,
    *,
    x_frac: float = 0.42,
    y_frac: float = 0.055,
) -> Image.Image:
    """Wide short crop around a SoM mark — one horizontal wire band."""
    w, h = image.size
    cx, cy = int(coord.x_pos * w), int(coord.y_pos * h)
    half_w = max(24, int(w * x_frac / 2))
    half_h = max(10, int(h * y_frac / 2))
    box = (
        max(0, cx - half_w),
        max(0, cy - half_h),
        min(w, cx + half_w),
        min(h, cy + half_h),
    )
    return image.crop(box)


async def _vlm_image_text(
    *,
    image: Image.Image,
    prompt: str,
    model: str,
    max_tokens: int = 40,
) -> str:
    b64 = _pil_to_png_b64(image)
    if _is_anthropic_model(model):
        client = AsyncAnthropic()
        response = await client.messages.create(
            model=model.removeprefix("anthropic/"),
            max_tokens=max_tokens,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                    ],
                }
            ],
        )
        return "".join(block.text for block in response.content if hasattr(block, "text")).strip()

    client = AsyncOpenAI()
    data_url = f"data:image/png;base64,{b64}"
    response = await client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )
    return (response.choices[0].message.content or "").strip()


async def encode_with_vlm(
    *,
    image: Image.Image,
    bomb: BombState,
    model: str = VLM_MODEL,
) -> str:
    """Full-frame vision encoder (legacy fallback)."""
    raw = await _vlm_image_text(image=image, prompt=VLM_SPARSE_PROMPT, model=model, max_tokens=120)
    print(f"👁  VLM ({model}) raw: {raw!r}")
    return _merge_vlm_sparse(raw, bomb)


async def encode_with_vlm_per_wire(
    *,
    clean_image: Image.Image,
    marks: dict[str, RelativeCoordinate],
    bomb: BombState,
    model: str = VLM_MODEL,
    save_debug: bool = True,
) -> str:
    """Classify each SoM-marked wire crop independently, then assemble sparse STATE."""
    ordered = sorted(marks.items(), key=lambda kv: kv[0])
    os.makedirs(DEBUG_DIR, exist_ok=True)

    async def one(letter: str, coord: RelativeCoordinate) -> str:
        crop = _crop_wire_band(clean_image, coord)
        if save_debug:
            crop.save(os.path.join(DEBUG_DIR, f"wire_{letter}.png"))
        raw = await _vlm_image_text(
            image=crop, prompt=WIRE_COLOR_PROMPT, model=model, max_tokens=8
        )
        color = _normalize_wire_color(raw)
        print(f"👁  VLM wire {letter}: {raw!r} → {color}")
        if color is None:
            raise RuntimeError(f"VLM returned invalid color for wire {letter}: {raw!r}")
        return color

    colors = list(await asyncio.gather(*(one(letter, coord) for letter, coord in ordered)))
    core = (
        f"MODULE: wires | COUNT: {len(colors)} | STATE: [{','.join(colors)}] | CUT: []"
    )
    print(f"👁  VLM ({model}) per-wire assembled: {core}")
    if save_debug:
        clean_image.save(os.path.join(DEBUG_DIR, "clean_frame.png"))
    return _merge_vlm_sparse(core, bomb)


# ---------------------------------------------------------------------------
# Reasoner — deterministic Wires solver (replaces dialogue expert)
# ---------------------------------------------------------------------------


def _parse_sparse(sparse: str) -> dict[str, Any]:
    parts = {k.strip(): v.strip() for k, _, v in (p.partition(":") for p in sparse.split("|"))}
    state = parts.get("STATE", "[]").strip()
    colors = [c.strip() for c in state.strip("[]").split(",") if c.strip()]
    return {
        "module": parts.get("MODULE", ""),
        "colors": colors,
        "serial_odd": parts.get("SERIAL_ODD", "0") == "1",
        "mod_solved": parts.get("MOD_SOLVED", "0") == "1",
        "bomb_solved": parts.get("BOMB_SOLVED", "0") == "1",
    }


def reason_wires(sparse_state: str, _manual: str = WIRES_MANUAL) -> str:
    """Return ACTION: CUT <1-indexed> or ACTION: NO_OP using vanilla Wires rules."""
    parsed = _parse_sparse(sparse_state)
    if parsed["module"] != "wires" or parsed["mod_solved"] or parsed["bomb_solved"]:
        return "ACTION: NO_OP"

    colors: list[str] = parsed["colors"]
    n = len(colors)
    odd = parsed["serial_odd"]

    def count(color: str) -> int:
        return sum(c == color for c in colors)

    def last_index(color: str) -> int:
        return max(i for i, c in enumerate(colors) if c == color)

    cut: int
    if n == 3:
        if count("red") == 0:
            cut = 2
        elif colors[-1] == "white":
            cut = n
        elif count("blue") > 1:
            cut = last_index("blue") + 1
        else:
            cut = n
    elif n == 4:
        if count("red") > 1 and odd:
            cut = last_index("red") + 1
        elif colors[-1] == "yellow" and count("red") == 0:
            cut = 1
        elif count("blue") == 1:
            cut = 1
        elif count("yellow") > 1:
            cut = n
        else:
            cut = 2
    elif n == 5:
        if colors[-1] == "black" and odd:
            cut = 4
        elif count("red") == 1 and count("yellow") > 1:
            cut = 1
        elif count("black") == 0:
            cut = 2
        else:
            cut = 1
    elif n == 6:
        if count("yellow") == 0 and odd:
            cut = 3
        elif count("yellow") == 1 and count("white") > 1:
            cut = 4
        elif count("red") == 0:
            cut = n
        else:
            cut = 4
    else:
        return "ACTION: NO_OP"

    return f"ACTION: CUT {cut}"


def _normalize_action(text: str) -> str:
    """Extract ACTION: CUT N / NO_OP from free-form model output."""
    upper = text.upper()
    cut = re.search(r"ACTION:\s*CUT\s+(\d+)", upper)
    if cut:
        return f"CUT {cut.group(1)}"
    if "NO_OP" in upper or "NOOP" in upper:
        return "NO_OP"
    # bare "CUT 4"
    bare = re.search(r"\bCUT\s+(\d+)\b", upper)
    if bare:
        return f"CUT {bare.group(1)}"
    return "NO_OP"


async def reason_with_haiku(
    sparse_state: str,
    manual: str = WIRES_MANUAL,
    *,
    model: str = EXPERT_MODEL,
) -> str:
    """Haiku expert: sparse vector + manual → ACTION command."""
    client = AsyncAnthropic()
    response = await client.messages.create(
        model=model,
        max_tokens=40,
        temperature=0,
        system=EXPERT_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"MANUAL:\n{manual}\n\nSPARSE_STATE:\n{sparse_state}",
            }
        ],
    )
    raw = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
    print(f"🧠 Haiku raw: {raw!r}")
    return _normalize_action(raw)


def sparse_to_defuser_utterance(sparse: str, *, style: str = "full") -> str:
    """Turn encoder sparse into a Defuser chat line (Expert never sees raw sparse)."""
    parsed = _parse_sparse(sparse)
    parts = {k.strip(): v.strip() for k, _, v in (p.partition(":") for p in sparse.split("|"))}
    colors = parsed["colors"]
    labeled = ", ".join(
        f"{string.ascii_uppercase[i]}={c}" for i, c in enumerate(colors)
    )
    serial = parts.get("SERIAL", "?")
    odd = "odd" if parsed["serial_odd"] else "even"
    if style == "partial":
        # Incomplete on purpose — Expert must ASK for colors / parity.
        return (
            f"I'm looking at a Wires module with {len(colors)} wires. "
            f"Serial reads {serial}. Ask me what you need."
        )
    return (
        f"Wires module in focus. COUNT={len(colors)}. "
        f"Top to bottom: {labeled}. "
        f"Serial {serial} (last digit {odd}). None cut yet."
    )


async def defuser_natural_utterance(
    perception_notes: str,
    *,
    model: str = EXPERT_MODEL,
    opening: bool = False,
) -> str:
    system = (
        DIALOGUE_DEFUSER_OPENING_NATURAL_PROMPT
        if opening
        else DIALOGUE_DEFUSER_NATURAL_PROMPT
    )
    client = AsyncAnthropic()
    response = await client.messages.create(
        model=model,
        max_tokens=120,
        temperature=0.4,
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"PERCEPTION NOTES (private):\n{perception_notes}",
            }
        ],
    )
    raw = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
    return raw


def _parse_expert_dialogue(text: str) -> tuple[str, str | int | None]:
    """Return ('ask', question) | ('cut', n) | ('noop', None). Supports structured + natural."""
    stripped = text.strip()
    ask = re.match(r"^ASK:\s*(.+)$", stripped, flags=re.IGNORECASE | re.DOTALL)
    if ask:
        return "ask", ask.group(1).strip()

    structured_cut = re.search(
        r"(?:^|\b)(?:CUT|ACTION:\s*CUT)\s*:?\s*(\d+)\b", stripped, flags=re.IGNORECASE
    )
    if structured_cut:
        return "cut", int(structured_cut.group(1))

    lower = stripped.lower()
    # Natural: "cut the 4th wire", "cut wire 4", "cut the fourth wire"
    natural_num = re.search(
        r"\bcut(?:ting)?\s+(?:the\s+)?(?:wire\s+)?(?:number\s+|#\s*)?(\d+)\b",
        lower,
    )
    if natural_num:
        return "cut", int(natural_num.group(1))
    for word, n in _ORDINAL_TO_N.items():
        if re.search(rf"\bcut(?:ting)?\s+(?:the\s+)?{word}(?:\s+wire)?\b", lower):
            return "cut", n
        if re.search(rf"\bcut(?:ting)?\s+(?:the\s+)?wire\s+{word}\b", lower):
            return "cut", n

    # Natural question / structured-less ask
    ask_starters = (
        "what",
        "which",
        "how",
        "is ",
        "are ",
        "can you",
        "could you",
        "tell me",
        "do you",
        "what's",
        "whats",
    )
    if "?" in stripped or lower.startswith(ask_starters):
        return "ask", stripped

    return "noop", None


def _format_transcript(transcript: list[dict[str, str]]) -> str:
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in transcript)


async def expert_dialogue_turn(
    transcript: list[dict[str, str]],
    manual: str = WIRES_MANUAL,
    *,
    model: str = EXPERT_MODEL,
    require_ask: bool = False,
    asks_so_far: int = 0,
    natural: bool = False,
) -> str:
    """Expert sees only chat + manual — never BombState / sparse."""
    if natural:
        system = (
            DIALOGUE_EXPERT_NATURAL_REQUIRE_ASK_PROMPT
            if require_ask and asks_so_far < 1
            else DIALOGUE_EXPERT_NATURAL_PROMPT
        )
        reply_hint = "Your radio reply:"
        extra = ""
        if require_ask and asks_so_far < 1:
            extra = (
                "\n\nIMPORTANT: You have not asked yet. "
                "Ask a clarifying question this turn — do not tell them to cut yet."
            )
    else:
        system = (
            DIALOGUE_EXPERT_REQUIRE_ASK_PROMPT
            if require_ask and asks_so_far < 1
            else DIALOGUE_EXPERT_PROMPT
        )
        reply_hint = "Your reply (ASK: … or CUT: <k>):"
        extra = ""
        if require_ask and asks_so_far < 1:
            extra = (
                "\n\nIMPORTANT: You have not asked a question yet. "
                "Reply with ASK: … this turn (CUT is not allowed yet)."
            )
    client = AsyncAnthropic()
    response = await client.messages.create(
        model=model,
        max_tokens=120 if natural else 80,
        temperature=0.3 if natural else 0,
        system=system,
        messages=[
            {
                "role": "user",
                "content": (
                    f"MANUAL:\n{manual}\n\n"
                    f"DIALOGUE SO FAR:\n{_format_transcript(transcript)}\n\n"
                    f"{reply_hint}{extra}"
                ),
            }
        ],
    )
    raw = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
    print(f"🗣️  Expert: {raw!r}")
    return raw


async def defuser_answer_ask(
    question: str,
    perception_notes: str,
    *,
    model: str = EXPERT_MODEL,
    natural: bool = False,
) -> str:
    """Defuser answers from private perception notes (no cutting advice)."""
    system = DIALOGUE_DEFUSER_NATURAL_PROMPT if natural else DIALOGUE_DEFUSER_PROMPT
    client = AsyncAnthropic()
    response = await client.messages.create(
        model=model,
        max_tokens=100 if natural else 80,
        temperature=0.3 if natural else 0,
        system=system,
        messages=[
            {
                "role": "user",
                "content": (
                    f"PERCEPTION NOTES (private):\n{perception_notes}\n\n"
                    f"EXPERT SAID:\n{question}"
                ),
            }
        ],
    )
    raw = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
    print(f"🗣️  Defuser (answer): {raw!r}")
    return raw


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


@dataclass
class ExecutionPacket:
    sparse_stream: str
    action_command: str
    latency_ms: float
    bandwidth_bytes: int
    status: str = "ok"
    encoder: str = "oracle"
    reasoner: str = "rules"
    mode: str = "central"
    oracle_sparse: str | None = None
    dialogue: list[dict[str, str]] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sparse_stream": self.sparse_stream,
            "action_command": self.action_command,
            "latency_ms": self.latency_ms,
            "bandwidth_bytes": self.bandwidth_bytes,
            "status": self.status,
            "encoder": self.encoder,
            "reasoner": self.reasoner,
            "mode": self.mode,
            "oracle_sparse": self.oracle_sparse,
            "dialogue": self.dialogue,
        }


class HybridCentralRouter:
    """Perception→reason→act over a live KtaneClient (central or structured dialogue)."""

    def __init__(
        self,
        client: KtaneClient,
        *,
        encoder: EncoderKind = "oracle",
        reasoner: ReasonerKind = "rules",
        mode: ModeKind = "central",
        use_redis: bool = True,
        vlm_model: str = VLM_MODEL,
        expert_model: str = EXPERT_MODEL,
        max_dialogue_turns: int = 6,
        require_ask: bool = False,
        defuser_style: str = "full",
    ) -> None:
        self.client = client
        self.encoder = encoder
        self.reasoner = reasoner
        self.mode = mode
        self.vlm_model = vlm_model
        self.expert_model = expert_model
        self.max_dialogue_turns = max_dialogue_turns
        self.require_ask = require_ask
        self.defuser_style = defuser_style
        self.manual_context = WIRES_MANUAL
        self._last_strike_count = 0
        self._acted = False
        self.transcript: list[dict[str, str]] = []
        self.chat_session_id = str(uuid4())
        if mode == "dialogue":
            reset_live_chat(session_id=self.chat_session_id)
            print(f"📡 Live chat → output/hybrid_live/chat.jsonl (session {self.chat_session_id[:8]}…)")
            print("   streamlit run scripts/live_chat_viewer.py")

        self.som = SetOfMarksHandler(
            annotation_text_params=AnnotationTextParams(
                font=2, font_scale=0.7, thickness=1, space_between_boxes=2
            ),
            annotation_background_params=AnnotationBackgroundParams(padding=3, alpha=1),
            mask_drawing_params=MaskDrawingParams(
                mask_thickness=1, soft_mask_alpha=0.1, bw_outside_mask=False
            ),
            add_labels=True,
            add_mask_outline=True,
            mark_type="alphabet",
        )

        self.r: redis.Redis | None = None
        if use_redis:
            try:
                self.r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
                _ = self.r.ping()
                self.r.delete("bomb_state:error_lock")
            except redis.RedisError as exc:
                logger.warning("Redis unavailable; continuing without shared memory", error=str(exc))
                self.r = None

        print(
            f"⚡ [HYBRID CIR] mode={mode} | encoder={encoder}"
            + (f"/{vlm_model}" if encoder == "vlm" else "")
            + f" | expert={reasoner}"
            + (f"/{expert_model}" if reasoner == "haiku" or mode == "dialogue" else "")
            + (
                f" | defuser={defuser_style} require_ask={require_ask}"
                if mode == "dialogue"
                else ""
            )
        )

    def _set_error_lock(self, locked: bool) -> None:
        if self.r is not None:
            self.r.set("bomb_state:error_lock", "1" if locked else "0")

    def _error_locked(self) -> bool:
        if self.r is None:
            return False
        return self.r.get("bomb_state:error_lock") == b"1"

    def _publish_mental_model(self, sparse: str) -> None:
        if self.r is not None:
            self.r.set("global_mental_model", sparse)
            self.r.publish("bomb_events", sparse)

    async def _latest_frame(self) -> Image.Image:
        frames = await self.client.get_observation_frames()
        rgb_frames = frames.extract_frames(output="pil_image", last_n_frames=1)
        if not rgb_frames:
            raise RuntimeError("No observation frames")
        return rgb_frames[-1]

    async def _refresh_som(self, bomb: BombState) -> dict[str, RelativeCoordinate]:
        frames = await self.client.get_observation_frames()
        rgb_frames = frames.extract_frames(output="pil_image", last_n_frames=1)
        seg = frames.extract_segmentation(output="pil_image")
        if not rgb_frames or seg is None:
            raise RuntimeError("No observation / segmentation available for SoM")
        self.som.reset()
        _ = self.som.run(
            observation=np.asarray(rgb_frames[-1]),
            colorful_image=np.asarray(seg),
            zoomed_in_component=bomb.zoomed_in_component,
        )
        return dict(self.som.mark_to_coordinate)

    async def _click(self, coord: RelativeCoordinate) -> None:
        action = KtaneGameplayInput(action=GameActionType.click_release, location=coord)
        _ = await self.client.send_action(action)

    async def _ensure_wires_focused(self, bomb: BombState) -> BombState:
        wires = next((m for m in bomb.modules if isinstance(m, WireSetModuleState)), None)
        if wires is None:
            raise RuntimeError("No Wires module on bomb")
        if wires.in_focus:
            return bomb

        _ = await self.client.resume_time()
        for attempt in range(5):
            marks = await self._refresh_som(bomb)
            if not marks:
                raise RuntimeError("No SoM marks to focus Wires module")
            # Already zoomed into wires: many marks are individual wires — don't click them to "focus".
            if len(marks) >= 3:
                bomb = await self.client.get_bomb_state()
                wires = next(m for m in bomb.modules if isinstance(m, WireSetModuleState))
                if wires.in_focus:
                    _ = await self.client.stop_time()
                    return bomb
            first_mark = sorted(marks.keys(), key=lambda m: (len(str(m)), str(m)))[0]
            print(
                f"🔎 Focus attempt {attempt + 1}: mark {first_mark} @ {marks[first_mark]} "
                f"(marks={sorted(marks)})"
            )
            await self._click(marks[first_mark])
            await anyio.sleep(0.8)
            bomb = await self.client.get_bomb_state()
            wires = next(m for m in bomb.modules if isinstance(m, WireSetModuleState))
            if wires.in_focus:
                _ = await self.client.stop_time()
                return bomb
        raise RuntimeError("Failed to focus Wires module after retries")

    async def _cut_wire(self, bomb: BombState, wire_1indexed: int) -> BombState:
        bomb = await self._ensure_wires_focused(bomb)
        _ = await self.client.resume_time()
        marks = await self._refresh_som(bomb)
        letter = string.ascii_uppercase[wire_1indexed - 1]
        if letter not in marks:
            raise RuntimeError(
                f"Wire mark {letter} missing; available={sorted(marks.keys())} "
                f"zoomed_in={bomb.zoomed_in_component}"
            )
        print(f"✂️  Cutting wire {wire_1indexed} ({letter}) @ {marks[letter]}")
        await self._click(marks[letter])
        await anyio.sleep(0.8)
        _ = await self.client.stop_time()
        return await self.client.get_bomb_state()

    async def _capture_clean_and_marks(
        self, bomb: BombState
    ) -> tuple[Image.Image, Image.Image, dict[str, RelativeCoordinate]]:
        """Return (clean RGB, SoM-annotated RGB, mark→coord)."""
        frames = await self.client.get_observation_frames()
        rgb_frames = frames.extract_frames(output="pil_image", last_n_frames=1)
        seg = frames.extract_segmentation(output="pil_image")
        if not rgb_frames or seg is None:
            raise RuntimeError("No observation / segmentation available for SoM")
        clean = rgb_frames[-1]
        if not isinstance(clean, Image.Image):
            clean = Image.fromarray(np.asarray(clean).astype(np.uint8))
        self.som.reset()
        annotated = self.som.run(
            observation=np.asarray(clean),
            colorful_image=np.asarray(seg),
            zoomed_in_component=bomb.zoomed_in_component,
        )
        return clean, Image.fromarray(annotated.astype(np.uint8)), dict(self.som.mark_to_coordinate)

    async def _encode(self, bomb: BombState) -> tuple[str, str, BombState]:
        """Return (sparse_for_reasoner, oracle_sparse, maybe-updated bomb)."""
        oracle = encode_bomb_state(bomb)
        if self.encoder == "oracle":
            return oracle, oracle, bomb

        bomb = await self._ensure_wires_focused(bomb)
        oracle = encode_bomb_state(bomb)
        # Per-wire crops on the clean frame (SoM overlays confuse color VLMs).
        clean, annotated, marks = await self._capture_clean_and_marks(bomb)
        print(f"🏷  SoM marks for VLM: {sorted(marks.keys())}")
        os.makedirs(DEBUG_DIR, exist_ok=True)
        annotated.save(os.path.join(DEBUG_DIR, "som_annotated.png"))
        if len(marks) < 3:
            sparse = await encode_with_vlm(image=annotated, bomb=bomb, model=self.vlm_model)
        else:
            sparse = await encode_with_vlm_per_wire(
                clean_image=clean,
                marks=marks,
                bomb=bomb,
                model=self.vlm_model,
            )
        print(f"📐 Oracle: {oracle}")
        print(f"📐 VLM:    {sparse}")
        return sparse, oracle, bomb

    async def _reason(self, sparse: str) -> str:
        if self.reasoner == "haiku":
            return await reason_with_haiku(
                sparse, self.manual_context, model=self.expert_model
            )
        return reason_wires(sparse, self.manual_context).replace("ACTION: ", "")

    async def _dialogue_decide(self, bomb: BombState) -> tuple[BombState, ExecutionPacket]:
        """Defuser reports via chat; Expert replies; cut only from Expert chat."""
        start = time.time()
        sparse, oracle, bomb = await self._encode(bomb)
        # Full private notes for Defuser answers — Expert never receives this object.
        private_notes = sparse_to_defuser_utterance(sparse, style="full")
        natural = self.defuser_style == "natural"
        if natural:
            opening = await defuser_natural_utterance(
                private_notes, model=self.expert_model, opening=True
            )
        else:
            opening = sparse_to_defuser_utterance(sparse, style=self.defuser_style)
        self.transcript = [{"role": "defuser", "content": opening}]
        append_live_chat(role="defuser", content=opening, session_id=self.chat_session_id)
        print(f"🗣️  Defuser: {opening}")
        self._publish_mental_model(sparse)

        action = "NO_OP"
        status = "ok"
        turns = 0
        asks_so_far = 0
        while turns < self.max_dialogue_turns:
            turns += 1
            expert_raw = await expert_dialogue_turn(
                self.transcript,
                self.manual_context,
                model=self.expert_model,
                require_ask=self.require_ask,
                asks_so_far=asks_so_far,
                natural=natural,
            )
            self.transcript.append({"role": "expert", "content": expert_raw})
            append_live_chat(role="expert", content=expert_raw, session_id=self.chat_session_id)
            kind, payload = _parse_expert_dialogue(expert_raw)
            if kind == "ask":
                asks_so_far += 1
                answer = await defuser_answer_ask(
                    str(payload),
                    private_notes,
                    model=self.expert_model,
                    natural=natural,
                )
                self.transcript.append({"role": "defuser", "content": answer})
                append_live_chat(role="defuser", content=answer, session_id=self.chat_session_id)
                continue
            if kind == "cut":
                if self.require_ask and asks_so_far < 1:
                    nudge = (
                        "I still need you to ask me something first — "
                        "what do you need to know before we cut?"
                        if natural
                        else "System: CUT ignored — you must ASK at least once before cutting."
                    )
                    self.transcript.append({"role": "defuser", "content": nudge})
                    append_live_chat(role="defuser", content=nudge, session_id=self.chat_session_id)
                    print(f"⛔ {nudge}")
                    continue
                action = f"CUT {payload}"
                break
            status = "noop_expert"
            break

        bandwidth = sum(len(m["content"].encode()) for m in self.transcript)
        packet = ExecutionPacket(
            sparse_stream=sparse,
            action_command=action,
            latency_ms=(time.time() - start) * 1000,
            bandwidth_bytes=bandwidth,
            status=status,
            encoder=self.encoder,
            reasoner=self.reasoner,
            mode="dialogue",
            oracle_sparse=oracle,
            dialogue=list(self.transcript),
        )
        print(
            f"📊 [DIALOGUE] turns={len(self.transcript)} asks={asks_so_far} "
            f"→ '{packet.action_command}' | {packet.latency_ms:.2f}ms "
            f"| natural={natural} cut_from_expert_chat={action.startswith('CUT ')}"
        )
        return bomb, packet

    async def decide(self, bomb: BombState) -> tuple[BombState, ExecutionPacket]:
        if self.mode == "dialogue":
            return await self._dialogue_decide(bomb)

        start = time.time()
        if self._error_locked():
            self._set_error_lock(False)
            return bomb, ExecutionPacket(
                sparse_stream="",
                action_command="NO_OP",
                latency_ms=0.0,
                bandwidth_bytes=0,
                status="recovering",
                encoder=self.encoder,
                reasoner=self.reasoner,
                mode=self.mode,
            )

        sparse, oracle, bomb = await self._encode(bomb)
        self._publish_mental_model(sparse)

        if bomb.strike_count > self._last_strike_count:
            print(
                f"🚨 [CIR INTERRUPT] Strike registered "
                f"({self._last_strike_count} → {bomb.strike_count})"
            )
            self._last_strike_count = bomb.strike_count
            self._set_error_lock(True)
            self._acted = True
            return bomb, ExecutionPacket(
                sparse_stream=sparse,
                action_command="NO_OP",
                latency_ms=(time.time() - start) * 1000,
                bandwidth_bytes=len(sparse.encode()),
                status="strike",
                encoder=self.encoder,
                reasoner=self.reasoner,
                mode=self.mode,
                oracle_sparse=oracle,
            )
        self._last_strike_count = bomb.strike_count

        if self._acted:
            action = "NO_OP"
        else:
            action = await self._reason(sparse)

        packet = ExecutionPacket(
            sparse_stream=sparse,
            action_command=action,
            latency_ms=(time.time() - start) * 1000,
            bandwidth_bytes=len(sparse.encode()),
            encoder=self.encoder,
            reasoner=self.reasoner,
            mode=self.mode,
            oracle_sparse=oracle,
        )
        print(
            f"📊 [METRICS] '{packet.sparse_stream}' → '{packet.action_command}' "
            f"| {packet.latency_ms:.2f}ms | encoder={self.encoder} reasoner={self.reasoner}"
        )
        return bomb, packet

    async def step(self, bomb: BombState) -> tuple[BombState, ExecutionPacket]:
        bomb, packet = await self.decide(bomb)
        if packet.status in {"recovering", "strike"}:
            return bomb, packet
        if packet.action_command.startswith("CUT "):
            wire_n = int(packet.action_command.split()[1])
            bomb = await self._cut_wire(bomb, wire_n)
            self._acted = True
        return bomb, packet


# ---------------------------------------------------------------------------
# Mission lifecycle
# ---------------------------------------------------------------------------


async def wait_for_game_state(
    client: KtaneClient, *targets: GameState, timeout: float = 90
) -> GameState:
    with anyio.move_on_after(timeout) as scope:
        while True:
            try:
                state = await client.get_game_state()
            except Exception:  # noqa: BLE001 — game still booting
                await anyio.sleep(0.5)
                continue
            if state in targets:
                return state
            await anyio.sleep(0.25)
    if scope.cancel_called:
        raise TimeoutError(f"Timed out waiting for {[t.value for t in targets]}")
    raise RuntimeError("unreachable")


async def run_hybrid_mission(
    *,
    seed: int = 845,
    time_limit: int = 81,
    rule_seed: int = 1,
    encoder: EncoderKind = "oracle",
    reasoner: ReasonerKind = "rules",
    mode: ModeKind = "central",
    use_redis: bool = True,
    vlm_model: str = VLM_MODEL,
    expert_model: str = EXPERT_MODEL,
    require_ask: bool = False,
    defuser_style: str = "full",
) -> dict[str, Any]:
    if mode == "dialogue" and reasoner == "rules":
        # Dialogue requires a chat Expert; rules bypass the transcript.
        reasoner = "haiku"

    process = GameProcessManager()
    client = KtaneClient(url="")
    try:
        port = await process.start()
        await client.update_url(f"http://localhost:{port}")
        print(f"🎮 KTANE up on :{port} (waiting for mod HTTP…)")
        _ = await client.wait_for_valid_healthcheck()
        print("✓ Mod HTTP ready")

        _ = await wait_for_game_state(client, GameState.main_menu, timeout=120)
        mission = KtaneMissionConfig(
            seed=seed,
            rule_seed=rule_seed,
            time_limit=time_limit,
            num_strikes_allowed=3,
            components=["Wires"],
            optional_widgets=3,
            needy_time=60,
            force_modules_to_front=True,
            time_scale=1.0,
            time_step_size=3000,
            session_id=uuid4(),
        )
        print(
            f"🚀 Starting mission seed={seed} rule_seed={rule_seed} t={time_limit}s "
            f"mode={mode}"
        )
        _ = await client.start_mission(mission)
        _ = await wait_for_game_state(
            client, GameState.lights_on, GameState.lights_off, timeout=60
        )
        if await client.get_game_state() == GameState.lights_off:
            _ = await wait_for_game_state(client, GameState.lights_on, timeout=30)

        _ = await client.stop_time()
        router = HybridCentralRouter(
            client,
            encoder=encoder,
            reasoner=reasoner,
            mode=mode,
            use_redis=use_redis,
            vlm_model=vlm_model,
            expert_model=expert_model,
            require_ask=require_ask,
            defuser_style=defuser_style,
            max_dialogue_turns=8 if require_ask or defuser_style in {"partial", "natural"} else 6,
        )

        bomb = await client.get_bomb_state()
        history: list[dict[str, Any]] = []
        steps = 0
        while bomb.outcome.value == "incomplete" and steps < 20:
            steps += 1
            bomb, packet = await router.step(bomb)
            history.append(packet.as_dict())
            bomb = await client.get_bomb_state()
            if packet.action_command.startswith("CUT ") or packet.status == "strike":
                break

        if bomb.outcome.value == "incomplete":
            _ = await client.resume_time()
            deadline = time.time() + max(5, bomb.seconds_remaining + 2)
            while time.time() < deadline and bomb.outcome.value == "incomplete":
                await anyio.sleep(0.5)
                bomb = await client.get_bomb_state()

        dialogue = history[-1].get("dialogue") if history else None
        ask_count = sum(
            1
            for m in (dialogue or [])
            if m.get("role") == "expert"
            and _parse_expert_dialogue(m.get("content", ""))[0] == "ask"
        )
        export_meta = None
        if mode == "dialogue" and dialogue:
            try:
                export_meta = export_and_ingest_hybrid_dialogue(
                    dialogue=dialogue,
                    bomb=bomb,
                    vlm_model=vlm_model,
                    expert_model=expert_model,
                    seed=seed,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️  Dialogue Viewer export failed: {exc}")

        result = {
            "outcome": bomb.outcome.value,
            "is_solved": bomb.is_solved,
            "is_detonated": bomb.is_detonated,
            "strikes": bomb.strike_count,
            "seconds_remaining": bomb.seconds_remaining,
            "mode": mode,
            "encoder": encoder,
            "reasoner": reasoner,
            "defuser_style": defuser_style,
            "require_ask": require_ask,
            "ask_count": ask_count,
            "cut_from_expert_chat": bool(
                dialogue and any(
                    m.get("role") == "expert"
                    and _parse_expert_dialogue(m.get("content", ""))[0] == "cut"
                    for m in dialogue
                )
            ),
            "viewer_export": export_meta,
            "steps": history,
            "final_sparse": encode_bomb_state(bomb),
        }
        print(
            f"\n🏁 Outcome: {result['outcome']} | strikes={result['strikes']} "
            f"| mode={mode} encoder={encoder} reasoner={reasoner} "
            f"| asks={ask_count} cut_from_expert_chat={result['cut_from_expert_chat']}"
        )
        print(json.dumps(result, indent=2))
        return result
    finally:
        await process.terminate()
        await client.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid Central Router over live KTANE")
    parser.add_argument("--seed", type=int, default=845)
    parser.add_argument("--time-limit", type=int, default=81)
    parser.add_argument("--rule-seed", type=int, default=1)
    parser.add_argument("--mode", choices=["central", "dialogue"], default="dialogue")
    parser.add_argument("--encoder", choices=["oracle", "vlm"], default="vlm")
    parser.add_argument("--reasoner", choices=["rules", "haiku"], default="haiku")
    parser.add_argument("--vlm-model", default=VLM_MODEL)
    parser.add_argument("--expert-model", default=EXPERT_MODEL)
    parser.add_argument(
        "--defuser-style",
        choices=["full", "partial", "natural"],
        default="natural",
        help="full=dump facts; partial=structured withhold; natural=spoken radio dialogue",
    )
    parser.add_argument(
        "--require-ask",
        action="store_true",
        default=True,
        help="Reject CUT until Expert has asked at least once (default on)",
    )
    parser.add_argument(
        "--no-require-ask",
        action="store_false",
        dest="require_ask",
        help="Allow Expert to CUT immediately",
    )
    parser.add_argument("--no-redis", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        run_hybrid_mission(
            seed=args.seed,
            time_limit=args.time_limit,
            rule_seed=args.rule_seed,
            encoder=args.encoder,
            reasoner=args.reasoner,
            mode=args.mode,
            use_redis=not args.no_redis,
            vlm_model=args.vlm_model,
            expert_model=args.expert_model,
            require_ask=args.require_ask,
            defuser_style=args.defuser_style,
        )
    )


if __name__ == "__main__":
    main()
