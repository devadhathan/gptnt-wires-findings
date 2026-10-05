# Research log

Running notes for the communication-overlay study (GPT-5.2 Defuser × Haiku 4.5 Expert).

---

## 2026-10-02 — Stock full-wires scored; overlay locked (smoke excluded)

**Run:** `output/comm-overlay/full-wires-stock-2026-10-02T13-09-38Z`  
**Scope:** 60 unique games (10 seeds × sync/async × 3 attempts). Stock prompts only.  
**Headline:** 29/60 solved (48%). Of those, **5 grounded** (5/29 ≈ 17% of wins; 5/60 ≈ 8% of games). Most wins are cancelled error, not grounded collaboration.

### Solve-type breakdown (29 wins; counts sum to 29)

| solve_type | n |
|---|---:|
| cancelled_errors | 21 |
| grounded | 5 |
| recovered_win | 3 |
| **total** | **29** |

Grounded wins: sync 286/3, sync 563/3, async 813/1, sync 813/1, sync 813/3.

### By wire count

| wires | n | solved | grounded | cancelled_errors | recovered_win | order_match | status: match | partial | mismatch |
|------:|--:|-------:|---------:|-----------------:|--------------:|------------:|--------------:|--------:|---------:|
| 3 | 18 | 7 | 5 | 0 | 2 | 12 | 12 | 0 | 6 |
| 4 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 |
| 5 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 |
| 6 | 30 | 22 | 0 | 21 | 1 | 0 | 0 | 26 | 4 |
| **all** | **60** | **29** | **5** | **21** | **3** | **12** | **12** | **26** | **22** |

All five grounded wins are on **3-wire** bombs. No order-match on 4/5/6-wire panels in this stock arm.

### Labels renamed (clearer meanings)

| Old | New | One-line meaning |
|---|---|---|
| `wrong_cut` (won after mistake) | `recovered_win` | Struck after a bad cut, then cut the right wire and solved. |
| `module_misid` / “wrong puzzle” | `other_module_talk` | Chat **mentioned** a different module type — not proof the Expert applied wrong rules. |
| `wrong_cut_no_recovery` | `struck_no_fix` | Cut a wrong wire (strike) and never made the correct cut. |

Full glossary + hand-checks: `scripts/language_collab/TWO_PAGER_DIAGNOSIS.md`. Scorer: `scripts/summarize_runs.py`.

### Overlay smoke excluded; text locked

- The one-game `grounded_repair` smoke (`one-async-561-grounded_repair-*`, async Wires-**561**) does **not** count: overlay wording was changed after watching it.
- Overlay text is now **locked**: `scripts/language_collab/overlays/grounded_repair/{expert,defuser}.md` — no further edits before/during the scored overlay arm.
- When the real overlay arm runs: replay bomb **561** under that frozen text; score only frozen-text games.

### Still to run

Full `grounded_repair` overlay arm (fair test under frozen rules). No stock-vs-overlay effect size yet.
