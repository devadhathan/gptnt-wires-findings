#!/usr/bin/env bash
# Run communication-overlay pilot: stock then overlay (official GPTNT only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

MANIFEST="runs/comm-overlay/pilot.yaml"
OUT_ROOT="${EXPERIMENT_RECORDER_OUTPUTS_ROOT:-output/comm-overlay}"
GPTNT="${GPTNT_BIN:-.venv/bin/gptnt}"
PY="${PYTHON:-.venv/bin/python}"

if [[ ! -x "$GPTNT" ]]; then
  echo "Missing $GPTNT — activate the project venv first." >&2
  exit 1
fi

run_condition() {
  local condition=$1
  echo ""
  echo "========== condition: $condition =========="
  if [[ "$condition" == "stock" ]]; then
    "$PY" scripts/language_collab/apply_overlays.py restore
  elif [[ "$condition" == "overlay" ]]; then
    "$PY" scripts/language_collab/apply_overlays.py apply
  else
    echo "unknown condition: $condition" >&2
    exit 1
  fi
  "$PY" scripts/language_collab/apply_overlays.py status

  local ts
  ts="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
  export EXPERIMENT_RECORDER_OUTPUTS="${OUT_ROOT}/pilot-${condition}-${ts}"
  mkdir -p "$EXPERIMENT_RECORDER_OUTPUTS"
  echo "Recording to: $EXPERIMENT_RECORDER_OUTPUTS"
  "$PY" scripts/language_collab/write_run_meta.py "$EXPERIMENT_RECORDER_OUTPUTS" \
    --condition "$condition" --manifest "$MANIFEST"

  "$GPTNT" doctor "$MANIFEST"
  "$GPTNT" generate "$MANIFEST" --force
  "$GPTNT" run "$MANIFEST" --force

  "$GPTNT" build-db "$EXPERIMENT_RECORDER_OUTPUTS" \
    --output "${OUT_ROOT}/pilot-${condition}.duckdb" \
    --skip-filtering 2>/dev/null || \
  "$GPTNT" build-db "$EXPERIMENT_RECORDER_OUTPUTS" \
    --output "${OUT_ROOT}/pilot-${condition}.duckdb"
  "$GPTNT" results "${OUT_ROOT}/pilot-${condition}.duckdb" || true
}

run_condition stock
run_condition overlay

"$PY" scripts/language_collab/apply_overlays.py restore
echo ""
echo "Pilot complete. Compare:"
echo "  ${OUT_ROOT}/pilot-stock.duckdb"
echo "  ${OUT_ROOT}/pilot-overlay.duckdb"
echo "Dialogue: gptnt analyse  →  Dialogue Viewer"
