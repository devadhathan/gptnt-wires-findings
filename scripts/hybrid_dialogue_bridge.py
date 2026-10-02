"""Bridge hybrid Defuser↔Expert chat into GPTNT viewers.

1. Live: append JSONL to output/hybrid_live/chat.jsonl (scripts/live_chat_viewer.py)
2. Dialogue Viewer: write experiment-*.parquet + ingest into output/experiments.duckdb
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic_ai.usage import RunUsage

from gptnt.common.paths import Paths
from gptnt.experiments.db.ingest import ingest_player_records
from gptnt.experiments.records import ExperimentPlayerRecord, ExperimentStep
from gptnt.experiments.recorder.parquet import (
    blob_step,
    footer_from_player_record,
    load_player_record_from_parquet,
    write_player_record_parquet,
)
from gptnt.ktane.state.bomb import BombState
from gptnt.players.actions import SendMessageAction
from gptnt.provenance import Provenance

ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = ROOT / "output" / "hybrid_live"
LIVE_CHAT = LIVE_DIR / "chat.jsonl"
HYBRID_RECORDER_ROOT = ROOT / "output" / "experiment_recorder_outputs"


def reset_live_chat(*, session_id: str) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_CHAT.write_text("")
    (LIVE_DIR / "session.txt").write_text(session_id)


def append_live_chat(*, role: str, content: str, session_id: str) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "role": role,
        "content": content,
        "session_id": session_id,
    }
    with LIVE_CHAT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _find_template_parquet() -> Path:
    """Reuse a Wires-845 instance footer so suite/protocol fields stay valid."""
    roots = sorted(HYBRID_RECORDER_ROOT.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in roots:
        for path in folder.glob("experiment-*Wires_845*.parquet"):
            return path
    for path in HYBRID_RECORDER_ROOT.rglob("experiment-*.parquet"):
        return path
    raise FileNotFoundError(
        "No template experiment-*.parquet under output/experiment_recorder_outputs/. "
        "Run any official GPTNT wires mission once, then retry."
    )


def export_and_ingest_hybrid_dialogue(
    *,
    dialogue: list[dict[str, str]],
    bomb: BombState | None,
    vlm_model: str,
    expert_model: str,
    seed: int = 845,
) -> dict[str, Any]:
    """Write two player parquets (defuser/expert) and ingest into experiments.duckdb."""
    if not dialogue:
        raise ValueError("No dialogue turns to export")

    template_path = _find_template_parquet()
    template = load_player_record_from_parquet(template_path)
    provenance = Provenance.capture(force=True)

    session_id = uuid4()
    defuser_uuid = uuid4()
    expert_uuid = uuid4()
    game_uuid = uuid4()
    defuser_name = f"hybrid-vlm-{vlm_model}"
    expert_name = f"hybrid-expert-{expert_model}"

    instance = template.experiment_instance.model_copy(
        update={
            "session_id": session_id,
            "defuser_uuid": defuser_uuid,
            "expert_uuid": expert_uuid,
            "game_uuid": game_uuid,
            "defuser_name": defuser_name,
            "expert_name": expert_name,
            "attempt": int(time.time()) % 100000,
        }
    )

    t0 = time.time()
    defuser_steps: list[ExperimentStep] = []
    expert_steps: list[ExperimentStep] = []
    d_i = 0
    e_i = 0
    for turn_i, msg in enumerate(dialogue):
        role = msg["role"]
        if role not in {"defuser", "expert"}:
            continue
        is_last = turn_i == len(dialogue) - 1
        if role == "defuser":
            d_i += 1
            step_n = d_i
            player_uuid = defuser_uuid
            player_name = defuser_name
        else:
            e_i += 1
            step_n = e_i
            player_uuid = expert_uuid
            player_name = expert_name
        step = ExperimentStep(
            step=step_n,
            timestamp=float(turn_i) + 0.01,
            role=role,  # type: ignore[arg-type]
            session_id=session_id,
            player_uuid=player_uuid,
            player_name=player_name,
            output=SendMessageAction(message=msg["content"]),
            raw_output=msg["content"],
            thoughts=None,
            input_messages=[],
            new_messages=[],
            bomb_state=bomb if is_last else None,
            observation=None,
            usage=RunUsage(),
            num_prompt_truncations=0,
            error_type=None,
            is_reflection=False,
        )
        if role == "defuser":
            defuser_steps.append(step)
        else:
            expert_steps.append(step)

    if not defuser_steps or not expert_steps:
        raise ValueError("Dialogue must include both defuser and expert turns")

    # Ensure each player record has at least one bomb_state for summary extraction.
    if bomb is not None:
        if defuser_steps[-1].bomb_state is None:
            defuser_steps[-1] = defuser_steps[-1].model_copy(update={"bomb_state": bomb})
        if expert_steps[-1].bomb_state is None:
            expert_steps[-1] = expert_steps[-1].model_copy(update={"bomb_state": bomb})

    out_dir = HYBRID_RECORDER_ROOT / f"hybrid_{time.strftime('%Y-%m-%dT%H-%M-%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for steps, role_name in ((defuser_steps, "defuser"), (expert_steps, "expert")):
        content = instance.get_player_content_by_role(role_name)  # type: ignore[arg-type]
        record = ExperimentPlayerRecord(
            experiment_instance=instance,
            player_content=content,
            step_records=steps,
            is_hard_crash=False,
            gptnt_version=provenance.gptnt_version,
            release_commit=provenance.release_commit,
            release_tag=provenance.release_tag,
            release_protected_content_digest=provenance.release_protected_content_digest,
            protected_content_digest=provenance.protected_content_digest,
            protected_content_modified=provenance.protected_content_modified,
        )
        path = out_dir / f"experiment-{instance.attempt_name}-{content.uuid}.parquet"
        write_player_record_parquet(
            blobbed_steps=[blob_step(s) for s in steps],
            footer=footer_from_player_record(record),
            output_path=path,
        )
        paths.append(path)

    db_path = Paths().experiments_db
    ingest_player_records(player_record_paths=paths, db_path=db_path, max_workers=2)
    elapsed = time.time() - t0
    meta = {
        "session_id": str(session_id),
        "attempt_name": instance.attempt_name,
        "parquet_dir": str(out_dir),
        "db_path": str(db_path),
        "seed": seed,
        "export_seconds": elapsed,
        "template": str(template_path),
    }
    (out_dir / "hybrid_export.json").write_text(json.dumps(meta, indent=2))
    print(
        f"💾 Exported hybrid dialogue → Dialogue Viewer\n"
        f"   attempt: {instance.attempt_name}\n"
        f"   db: {db_path}\n"
        f"   Open GPTNT app → Dialogue Viewer → Load experiments → filter hybrid-vlm / hybrid-expert"
    )
    return meta
