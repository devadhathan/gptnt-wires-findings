# Language collaboration protocol

Official GPTNT runs only. Hybrid router is deprecated for this line of work.

## Overlays

```bash
.venv/bin/python scripts/language_collab/apply_overlays.py status
.venv/bin/python scripts/language_collab/apply_overlays.py apply    # local experiments
.venv/bin/python scripts/language_collab/apply_overlays.py restore  # before submission
```

See `STOCK_VS_OVERLAY.md` for the exact diff vs stock.

## Experiment manifests

Pairing: **GPT-5.2 Defuser** × **Haiku 4.5 Expert** (`with_best_expert`).

| Manifest | Games (per condition) |
| --- | --- |
| `runs/comm-overlay/pilot.yaml` | 12 (3 Wires × sync+async × 2 attempts) |
| `runs/comm-overlay/full-wires.yaml` | 60 |
| `runs/comm-overlay/full-keypad.yaml` | 60 |
| `runs/comm-overlay/full-whosonfirst.yaml` | 60 |

Suites: `configs/suites/comm-*.yaml` · missions: `configs/missions/{pilot_wires,wires_10,keypad_10,whosonfirst_10}/`

Full plan: `EXPERIMENT.md`
