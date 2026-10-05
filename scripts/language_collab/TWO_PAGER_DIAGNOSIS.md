# Diagnosis (stock Wires · GPT-5.2 × Haiku 4.5)

*Draft section for the 2-pager. Stock full-wires only: 60 games (10 seeds × sync/async × 3 attempts). No full overlay arm yet. Parser re-score: 0 unparsed among the 60 unique games.*

## Label meanings (one line each)

| Label | Meaning |
|---|---|
| `grounded` | Colour order matched oracle (and cut was correct when recorded). |
| `cancelled_errors` | Colour order was wrong, but the cut still hit the true wire. |
| `recovered_win` | Struck after a bad cut, then cut the right wire and solved. |
| `cut_slot_mismatch` | Solved, but the first recorded cut ≠ seeded answer (rare). |
| `uncategorized_solve` | Solved, but grounding/cut record incomplete (should stay rare). |
| `false_solve_claim` | Defuser said solved/green while `Wires.isSolved` was still False. |
| `other_module_talk` | Chat **mentioned** a different module type on a Wires-only bomb — not proof the Expert applied the wrong rules. |
| `never_cut` | No wire was ever cut before timeout / end. |
| `struck_no_fix` | Cut a wrong wire (strike) and never made the correct cut. |

*(Renamed: `wrong_cut` → `recovered_win` for wins-after-mistake; `module_misid` / “wrong puzzle” → `other_module_talk`; `wrong_cut_no_recovery` → `struck_no_fix`.)*

## The number that matters

**Of 29 solved games, 5 are grounded** (Defuser colour order matched oracle; when the cut slot was recorded it was also the seeded-correct wire): sync 286/3, sync 563/3, plus three seed-813 solves where the description matched but the cut slot was not recorded in the parquet.

That is **5/29 ≈ 17% of “wins,”** and **5/60 ≈ 8% of all games.** Still: most solves are luck on a wrong description.

Every solved game has exactly one `solve_type` (counts sum to 29):

| solve_type | n | Meaning |
|---|---|---|
| cancelled_errors | 21 | Colour order was wrong, but the cut still hit the true wire |
| grounded | 5 | Colour order matched oracle (cut correct when recorded) |
| recovered_win | 3 | Struck after a bad cut, then cut the right wire and solved |
| **total** | **29** | |

So the 48% solve rate is **not** a collaboration/grounding success rate. It is mostly **cancelled error**: Expert applies a Wires rule to a hallucinated colour order and the Defuser’s click lands on the physically correct wire anyway (often via colour+ordinal instructions, or off-by-one lists that still pick the right slot).

## What the dialogues show (10 hand-checked games)

Checked in the Dialogue Viewer / matching parquet transcripts:

1. **sync 286/3 — grounded.** Clear 3-wire yellow/blue/yellow; Expert asks edge facts; cut last. Real win.
2. **sync 563/3 — grounded.** After zoom, blue/black/black; cut last. Real win. (Previously buried among resume duplicates / unparsed.)
3. **async 234/1 — cancelled_errors.** Opens with wrong top colour (red for blue); still cuts last/blue and solves.
4. **sync 845/1 — cancelled_errors.** Six-wire list skips/misorders top black; Expert says “third/white”; click solves.
5. **async 561/1 — struck_no_fix.** Overview invents six wires; cut second; strike; never recovers.
6. **async 798/3 & sync 798/2 — false_solve_claim.** Wrong lists → wrong cut → Defuser reports solved/green while module is not.
7. **async 286/1 — other_module_talk.** Wire description was fine (yellow/blue/yellow); chat later **mentioned** another module / left the panel and never cut. (This label only means another module was mentioned — not that the Expert applied the wrong Wires rules.)
8. **async 563/1 — never_cut.** Initially reports two wires of three; spends the clock on edges; never executes a cut.
9. **async 960/2 — recovered_win.** Wrong list early; cut second (strike); later fixed the cut and solved — a win after a mistake, not a “wrong cut” result.
10. **async/sync 813 — grounded.** Description matched (`blue,white,blue`); bomb solved with 0 strikes. Earlier left unlabeled because `cut_slot` was empty in the scorer (defuser cut transition not in the paired parquet); still a grounded win by description + solve.

Pattern across failures and fake wins: **perception of the wire row is unstable** (missed top wire, invented colours, overview-before-zoom), and **repair after a strike is rare** (3 recoveries in the full set). When communication continues, it often leaves the module (serial/ports/other-module talk) instead of re-reading the same panel.

## Implications for the overlay question

Stock already tells agents to clarify. The measured failure is not “they never talk.” It is:

1. **Grounding:** descriptions frequently disagree with oracle order (only 12/60 exact matches after parser fix).
2. **Credit assignment:** solve rate credits cancelled_errors as success.
3. **Repair:** after a strike, agents rarely re-establish state and recompute.

Any overlay that only adds “ask more questions” will burn async clock without raising grounded solves.

### Overlay smoke = practice only (does not count)

The single `grounded_repair` game (async **561**/1 under `one-async-561-grounded_repair-*`) is **not** part of the overlay test. Overlay rules were edited *after* watching that game (module-confirmation + honest post-strike wording). Treat it as practice / prompt debugging.

**When the real overlay arm runs:**

1. Freeze `overlays/grounded_repair/{expert,defuser}.md` — no further text changes.
2. Replay bomb **561** (async att1) under that frozen text, with the rest of the suite.
3. Score only frozen-text games; do not mix practice and test.

## What not to claim yet

- No stock-vs-overlay effect size (overlay full arm not run; smoke does not count).
- Do not quote raw solve% without solve_type.
- Do not read `other_module_talk` as “Expert used the wrong rules” unless those dialogues were checked for actual wrong-rule application.
- The interesting pre-registered claim is already visible in stock: **structured success under asymmetric vision is almost entirely cancelled error, not grounded collaboration.**
