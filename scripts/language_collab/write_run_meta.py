#!/usr/bin/env python3
"""Write run_meta.json beside a GPTNT recorder output directory.

Captures experiment-condition fields that parquet provenance omits when runs use
`--force` (stock/overlay, git SHA, communication-prompt hashes). Does not modify
protected prompts or agent behaviour.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILES = [
    ROOT / "storage" / "prompts" / "requirements_communication_defuser.md",
    ROOT / "storage" / "prompts" / "requirements_communication_expert.md",
]
OVERLAY_MARKERS = {
    "overlay": "### Language discipline (experimental overlay)",
    "grounded_repair": "### Grounded repair (experimental overlay)",
}
# Back-compat for callers that import the old name.
OVERLAY_MARKER = OVERLAY_MARKERS["overlay"]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        )
        return out.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def prompt_condition() -> str:
    texts = [p.read_text(encoding="utf-8") for p in PROMPT_FILES]
    hits = [
        name
        for name, marker in OVERLAY_MARKERS.items()
        if all(marker in t for t in texts)
    ]
    if len(hits) == 1:
        return hits[0]
    if any(marker in t for t in texts for marker in OVERLAY_MARKERS.values()):
        return "mixed"
    return "stock"


def build_meta(
    *,
    condition: str | None,
    manifest: str | None,
    notes: str | None = None,
) -> dict:
    detected = prompt_condition()
    condition = condition or detected
    prompt_hashes = {str(p.relative_to(ROOT)): _sha256(p) for p in PROMPT_FILES}
    return {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "condition": condition,
        "condition_detected_from_prompts": detected,
        "manifest": manifest,
        "git_commit": _git_sha(),
        "prompt_hashes": prompt_hashes,
        "prompt_hash_combined": "sha256:"
        + hashlib.sha256(
            "".join(prompt_hashes[str(p.relative_to(ROOT))] for p in PROMPT_FILES).encode()
        ).hexdigest(),
        "overlay_marker": OVERLAY_MARKERS.get(condition, OVERLAY_MARKER),
        "notes": notes,
    }


def write_meta(out_dir: Path, meta: dict) -> Path:
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "run_meta.json"
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="EXPERIMENT_RECORDER_OUTPUTS directory")
    parser.add_argument(
        "--condition",
        choices=["stock", "overlay", "grounded_repair", "mixed", "unknown"],
        default=None,
        help="Override condition (default: detect from prompts on disk)",
    )
    parser.add_argument("--manifest", default=None, help="Run manifest path, if known")
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    meta = build_meta(condition=args.condition, manifest=args.manifest, notes=args.notes)
    path = write_meta(args.output_dir, meta)
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path
    print(f"wrote {shown}")
    print(f"  condition={meta['condition']} (detected={meta['condition_detected_from_prompts']})")
    print(f"  git_commit={meta['git_commit']}")
    print(f"  prompt_hash_combined={meta['prompt_hash_combined']}")


if __name__ == "__main__":
    main()
