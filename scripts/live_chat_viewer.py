#!/usr/bin/env python3
"""Live chat viewer for GPTNT player logs and hybrid Defuser↔Expert dialogue."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import streamlit as st

TS_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?Published message.*?"
    r"message=(?P<q>['\"])(?P<msg>.*?)(?P=q).*?to_role=(?P<to>\w+)",
    re.DOTALL,
)

LABELS = {
    "gpt-4-1-nano": ("defuser", "Defuser · gpt-4.1-nano"),
    "claude-haiku-4-5": ("expert", "Expert · claude-haiku-4-5"),
    "gpt-5-2": ("openai", "GPT-5.2"),
    "claude-sonnet-4-6": ("claude", "Claude Sonnet"),
}


def latest_run_dir(root: Path) -> Path | None:
    runs = sorted((root / "output" / "logs").glob("run_*"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def extract_messages(log_dir: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for path in sorted(log_dir.glob("*__0.log")):
        stem = path.name.replace("__0.log", "")
        kind, label = LABELS.get(stem, ("other", stem))
        text = re.sub(r"\x1b\[[0-9;]*m", "", path.read_text(errors="replace"))
        for match in TS_RE.finditer(text):
            rows.append((match.group("ts"), kind, label, match.group("msg")))
    rows.sort(key=lambda row: row[0])
    return rows


def extract_hybrid_messages(path: Path) -> list[tuple[str, str, str, str]]:
    if not path.exists():
        return []
    rows: list[tuple[str, str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = row.get("role", "other")
        label = "Defuser · hybrid" if role == "defuser" else "Expert · hybrid"
        rows.append((row.get("ts", ""), role, label, row.get("content", "")))
    return rows


st.set_page_config(page_title="GPTNT Live Chat", layout="centered")
st.title("GPTNT Live Chat")
root = Path(__file__).resolve().parents[1]
hybrid_path = root / "output" / "hybrid_live" / "chat.jsonl"
source = st.radio(
    "Source",
    ["Hybrid dialogue (live)", "Official run logs"],
    horizontal=True,
    index=0 if hybrid_path.exists() and hybrid_path.stat().st_size > 0 else 1,
)

if source.startswith("Hybrid"):
    st.caption(f"Watching `{hybrid_path}` · auto-refresh 1.5s")
    messages = extract_hybrid_messages(hybrid_path)
    if not messages:
        st.info("Waiting for hybrid Defuser↔Expert messages… run hybrid_central_router.py --mode dialogue")
else:
    log_dir = latest_run_dir(root)
    if log_dir is None:
        st.warning("No run logs found under output/logs/")
        st.stop()
    st.caption(f"Watching `{log_dir.name}` · auto-refresh 1.5s")
    messages = extract_messages(log_dir)
    if not messages:
        st.info("Waiting for chat messages…")

if messages:
    for ts, kind, label, msg in messages[-100:]:
        avatar = "🧑‍🔧" if kind == "defuser" else "📘" if kind == "expert" else "🤖"
        bubble = "user" if kind == "defuser" else "assistant"
        with st.chat_message(bubble, avatar=avatar):
            st.markdown(f"**{label}** · `{ts}`")
            st.write(msg)

time.sleep(1.5)
st.rerun()
