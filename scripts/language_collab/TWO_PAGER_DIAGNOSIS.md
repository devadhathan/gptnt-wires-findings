# Diagnosis (stock Wires · GPT-5.2 × Haiku 4.5)

*Draft section for the 2-pager. Stock full-wires only: 60 games (10 seeds × sync/async × 3 attempts). No full overlay arm yet. Parser re-score: 0 unparsed among the 60 unique games.*

## The number that matters

**Of 29 solved games, only 2 are grounded** (Defuser colour order matched oracle *and* the cut was the seeded-correct wire): sync 286/3 and sync 563/3.

That is **2/29 ≈ 7% of “wins,”** and **2/60 ≈ 3% of all games.** Before the parser fix the scorer only recovered one of these; the headline does not change: almost every solve is luck on a wrong description.

The other solves break down as:

| solve_type | n | Meaning |
|---|---|---|
| cancelled_errors | 21 | Wrong wire list, but the cut still hit the true answer |
| wrong_cut | 3 | Cut the wrong slot; module still solved (seeded rule / click mismatch) |
| grounded | 2 | Description matched oracle and cut was correct |
| (empty) | 3 | Solved but description timing/type not classified |

So the 48% solve rate is **not** a collaboration/grounding success rate. It is mostly **cancelled error**: Expert applies a Wires rule to a hallucinated colour order and the Defuser’s click lands on the physically correct wire anyway (often via colour+ordinal instructions, or off-by-one lists that still pick the right slot).

## What the dialogues show (10 hand-checked games)

Checked in the Dialogue Viewer / matching parquet transcripts:

1. **sync 286/3 — grounded.** Clear 3-wire yellow/blue/yellow; Expert asks edge facts; cut last. Real win.
2. **sync 563/3 — grounded.** After zoom, blue/black/black; cut last. Real win. (Previously buried among resume duplicates / unparsed.)
3. **async 234/1 — cancelled_errors.** Opens with wrong top colour (red for blue); still cuts last/blue and solves.
4. **sync 845/1 — cancelled_errors.** Six-wire list skips/misorders top black; Expert says “third/white”; click solves.
5. **async 561/1 — wrong_cut_no_recovery.** Overview invents six wires; cut second; strike; never recovers.
6. **async 798/3 & sync 798/2 — false_solve_claim.** Wrong lists → wrong cut → Defuser reports solved/green while module is not.
7. **async 286/1 — module_misid.** Correct yellow/blue/yellow description early, but pair wanders into indicator/serial hunt and never cuts (Expert leaves Wires).
8. **async 563/1 — never_cut.** Initially reports two wires of three; spends the clock on edges; never executes a cut.
9. **async 960/2 — wrong_cut “solve.”** top-to-bottom list wrong (invents extra blue, misses white); cuts second; later path still marks solved with wrong_cut.

Pattern across failures and fake wins: **perception of the wire row is unstable** (missed top wire, invented colours, overview-before-zoom), and **repair after a strike is rare** (3 recoveries in the full set). When communication continues, it often leaves the module (serial/ports/Simon talk) instead of re-reading the same panel.

## Implications for the overlay question

Stock already tells agents to clarify. The measured failure is not “they never talk.” It is:

1. **Grounding:** descriptions frequently disagree with oracle order (only 12/60 exact matches after parser fix).
2. **Credit assignment:** solve rate credits cancelled_errors as success.
3. **Repair:** after a strike, agents rarely re-establish state and recompute.

Any overlay that only adds “ask more questions” will burn async clock without raising grounded solves. The `grounded_repair` smoke (async 561/1) already showed the intended zoom/label protocol partially firing — then a wrong overview list, a cut on B, a strike, edge wandering, and a **false post-strike report that all wires were intact**. That motivates the module-confirmation and honest post-strike rules added to the overlay text *before* spending more API budget.

## What not to claim yet

- No stock-vs-overlay effect size (overlay full arm not run).
- Do not quote raw solve% without solve_type.
- The interesting pre-registered claim is already visible in stock: **structured success under asymmetric vision is almost entirely cancelled error, not grounded collaboration.**
