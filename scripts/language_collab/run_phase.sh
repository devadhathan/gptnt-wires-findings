#!/usr/bin/env bash
# Run one module phase (stock or overlay). Example:
#   scripts/language_collab/run_phase.sh stock runs/comm-overlay/full-wires.yaml
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

CONDITION="${1:?usage: run_phase.sh stock|overlay <manifest.yaml>}"
MANIFEST="${2:?usage: run_phase.sh stock|overlay <manifest.yaml>}"
OUT_ROOT="${EXPERIMENT_RECORDER_OUTPUTS_ROOT:-output/comm-overlay}"
GPTNT="${GPTNT_BIN:-.venv/bin/gptnt}"
PY="${PYTHON:-.venv/bin/python}"
STEM="$(basename "$MANIFEST" .yaml)"

if [[ "$CONDITION" == "stock" ]]; then
  "$PY" scripts/language_collab/apply_overlays.py restore
else
  "$PY" scripts/language_collab/apply_overlays.py apply
fi

ts="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
export EXPERIMENT_RECORDER_OUTPUTS="${OUT_ROOT}/${STEM}-${CONDITION}-${ts}"
mkdir -p "$EXPERIMENT_RECORDER_OUTPUTS"
echo "Recording to: $EXPERIMENT_RECORDER_OUTPUTS"
"$PY" scripts/language_collab/write_run_meta.py "$EXPERIMENT_RECORDER_OUTPUTS" \
  --condition "$CONDITION" --manifest "$MANIFEST"

"$GPTNT" doctor "$MANIFEST"
"$GPTNT" generate "$MANIFEST" --force
"$GPTNT" run "$MANIFEST" --force

DB="${OUT_ROOT}/${STEM}-${CONDITION}.duckdb"
"$GPTNT" build-db "$EXPERIMENT_RECORDER_OUTPUTS" --output "$DB" --skip-filtering 2>/dev/null || \
"$GPTNT" build-db "$EXPERIMENT_RECORDER_OUTPUTS" --output "$DB"
"$GPTNT" results "$DB"

if [[ "$CONDITION" == "overlay" ]]; then
  "$PY" scripts/language_collab/apply_overlays.py restore
fi
