#!/usr/bin/env python3
"""Apply / restore experimental language overlays on GPTNT communication prompts.

WARNING: `apply` modifies protected files under storage/prompts/.
That sets protected_content_modified for submissions. Use `restore` before any
official submission workflow.

Overlays (append-only to stock communication prompts):
  language         — existing language-discipline overlay (default)
  grounded_repair  — grounding / strike-repair overlay
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

PROMPT_TARGETS = {
    "defuser": ROOT / "storage" / "prompts" / "requirements_communication_defuser.md",
    "expert": ROOT / "storage" / "prompts" / "requirements_communication_expert.md",
}

# Named overlay packs: role -> overlay file, plus detection marker.
OVERLAYS: dict[str, dict[str, object]] = {
    "language": {
        "marker": "### Language discipline (experimental overlay)",
        "files": {
            "defuser": HERE / "overlay_defuser.md",
            "expert": HERE / "overlay_expert.md",
        },
    },
    "grounded_repair": {
        "marker": "### Grounded repair (experimental overlay)",
        "files": {
            "defuser": HERE / "overlays" / "grounded_repair" / "defuser.md",
            "expert": HERE / "overlays" / "grounded_repair" / "expert.md",
        },
    },
}

ALL_MARKERS = [str(cfg["marker"]) for cfg in OVERLAYS.values()]


def _which_applied(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    hits = [name for name, cfg in OVERLAYS.items() if str(cfg["marker"]) in text]
    if not hits:
        return None
    if len(hits) > 1:
        return "mixed:" + ",".join(hits)
    return hits[0]


def apply(overlay_name: str) -> None:
    if overlay_name not in OVERLAYS:
        raise SystemExit(f"unknown overlay {overlay_name!r}; choose from {sorted(OVERLAYS)}")
    cfg = OVERLAYS[overlay_name]
    marker = str(cfg["marker"])
    files: dict[str, Path] = cfg["files"]  # type: ignore[assignment]

    # Ensure stock baseline before appending (no stacking overlays).
    for role, target in PROMPT_TARGETS.items():
        text = target.read_text(encoding="utf-8")
        if any(m in text for m in ALL_MARKERS):
            print("restoring stock prompts before apply…")
            restore()
            break

    for role, target in PROMPT_TARGETS.items():
        text = target.read_text(encoding="utf-8")
        if marker in text:
            print(f"skip (already applied): {target.relative_to(ROOT)}")
            continue
        overlay_path = files[role]
        addition = overlay_path.read_text(encoding="utf-8").strip() + "\n"
        target.write_text(text.rstrip() + "\n\n" + addition + "\n", encoding="utf-8")
        print(f"applied [{overlay_name}] → {target.relative_to(ROOT)}")
    print(
        "\n⚠ Protected prompts modified. Local exploration only.\n"
        "   Restore before submission: python scripts/language_collab/apply_overlays.py restore"
    )


def restore() -> None:
    rels = [str(t.relative_to(ROOT)) for t in PROMPT_TARGETS.values()]
    cmd = ["git", "checkout", "--", *rels]
    subprocess.run(cmd, cwd=ROOT, check=True)
    print("restored:", ", ".join(rels))


def status() -> None:
    for role, target in PROMPT_TARGETS.items():
        state = _which_applied(target) or "stock"
        print(f"{state:16} {target.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["apply", "restore", "status"])
    parser.add_argument(
        "overlay",
        nargs="?",
        default="language",
        choices=sorted(OVERLAYS),
        help="Overlay pack to apply (default: language). Ignored for restore/status.",
    )
    args = parser.parse_args()
    if args.command == "apply":
        apply(args.overlay)
    elif args.command == "restore":
        restore()
    else:
        status()


if __name__ == "__main__":
    main()
