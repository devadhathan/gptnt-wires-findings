# Communication overlay experiment (official GPTNT)

**Question:** Do structured communication overlays help **GPT-5.2 Defuser × Haiku 4.5 Expert** on single modules, and does that help survive real-time (async) pressure?

**Not in scope:** `scripts/hybrid_central_router.py` — lab middleware only; no hybrid results in this study.

## Design

| Knob | Value |
| --- | --- |
| Model | Defuser **GPT-5.2**, Expert **Claude Haiku 4.5** (`with_best_expert`) |
| Conditions | Stock prompts vs full overlay (`apply_overlays.py`) |
| Time | Sync (paused) vs async (wall clock) |
| Modules | Wires, Keypad, Who's On First — 10 GPTNT seeds each |
| Repeats | Pilot: 3 missions × 2 attempts; full: 3 attempts/mission |

**Core scale:** 3 × 10 × 2 × 2 × 3 = **360 games** (after pilot).

## Fresh install workflow

From repo root, with `.env` loaded (Anthropic key, Redis, KTANE):

```bash
.venv/bin/python scripts/language_collab/apply_overlays.py status
.venv/bin/gptnt suite freeze    # registers new comm-* suites
.venv/bin/gptnt doctor runs/comm-overlay/pilot.yaml
```

## Pilot (12 + 12 games)

```bash
chmod +x scripts/language_collab/run_pilot.sh
scripts/language_collab/run_pilot.sh
```

Writes parquet under `output/comm-overlay/pilot-{stock,overlay}-<timestamp>/` and DuckDB summaries beside them.

## Full study (by module)

```bash
scripts/language_collab/run_phase.sh stock  runs/comm-overlay/full-wires.yaml
scripts/language_collab/run_phase.sh overlay runs/comm-overlay/full-wires.yaml
# repeat for full-keypad.yaml, full-whosonfirst.yaml
```

## View dialogue

```bash
.venv/bin/gptnt build-db output/comm-overlay/<run-dir> --output output/experiments.duckdb
.venv/bin/gptnt analyse   # Dialogue Viewer
```

## Hypotheses (pre-registered)

- **H1:** Overlays ↑ success in sync.
- **H2:** Benefit ↓ or reverses in async (clarification costs clock).
- **H3:** Larger on Keypad / Who's On First than Wires.
- **H4:** Fewer false confirmations / leading-question traps.

## After any overlay run

```bash
.venv/bin/python scripts/language_collab/apply_overlays.py restore
```

Protected prompts must be stock before any official submission workflow.
