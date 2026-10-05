# Success Without Grounding: What GPTNT Wires Scores Hide

Independent research building on **GPTNT** (Parekh, McCallum, Al-Hasan, Nikandrou, Suglia, Konstas, 2026; arXiv 2606.28514), a benchmark where two AI agents play *Keep Talking and Nobody Explodes*. Not affiliated with the GPTNT authors.

**Author:** Devadhathan Maruthamangalam Dharmatheja · Edinburgh · October 2026

---

## TL;DR

- Stock agents solved **29 of 60** single-module Wires games (48%). Only **5** of those wins came from an accurate shared description of the bomb.
- Correct descriptions only happened on **3-wire** bombs. On 6-wire bombs the Defuser was **never** correct (0/30), yet the team won **22/30**, because the bomb's rule tolerated the error or the Expert's colour cue let the Defuser correct itself at the moment of cutting.
- After a wrong cut, teams recovered only **3** times in 60 games.
- In a 10-game matched pilot, a prompt-only communication overlay kept wins the same (**5 vs 5**) but changed **how** they were won: grounded wins **0 → 4**, and the first correct descriptions of 6-wire bombs (**3/5** vs **0/12** for stock on the same bombs).

**Status:** results are preliminary. A blind hand-check of 20 games against the automatic labels is in progress.

---

## Setup

| Setting | Value |
| --- | --- |
| Defuser | GPT-5.2 (thinking off) |
| Expert | Claude Haiku 4.5 (thinking off) |
| Module | Wires only, single-module missions |
| Missions | GPTNT's 10 official Wires missions (`wires_10`) |
| Manual rule seed | 1764 (GPTNT's official value) |
| Modes | Sync (clock paused while models think) and async (real time) |
| Attempts | 3 per mission per mode, 60 games total |
| Sampling | Temperature 0.6, max 1000 tokens, prompt caching off |
| Scoring | Every game compared against the game's own ground truth (`bomb_state`) |

Stock prompts verified unchanged (`gptnt doctor`, empty prompt diff). Overlay runs differ from stock only in the protected-content digest; `suite_digest` and player capability fingerprints match.

### Labels

| Label | Meaning |
| --- | --- |
| `grounded` | Description matched the real bomb and the correct wire was cut |
| `cancelled_errors` | Description was wrong, but the correct wire was still cut |
| `recovered_win` | A wrong cut (strike), then the correct cut |
| `struck_no_fix` | A wrong cut, never corrected |
| `never_cut` | Timed out without cutting |
| `other_module_talk` | Chat named another module type on a Wires-only bomb |
| `false_solve_claim` | Defuser reported "solved" when the module was not solved |

---

## Finding 1: Wins hide poor grounding

| | Sync | Async | All |
| --- | --- | --- | --- |
| Solved | 16/30 | 13/30 | **29/60** |
| Timeouts | 14 | 17 | 31 |
| Strikeouts | 0 | 0 | 0 |

How the 29 wins happened: **5 grounded, 21 cancelled errors** (18 `rule_robust`, 3 `redundancy_rescue`), **3 recovered.**

## Finding 2: Grounding collapses as the bomb gets more detailed

| Wires | Games | Solved | Grounded | Correct description |
| --- | --- | --- | --- | --- |
| 3 | 18 | 7 | 5 | 12 |
| 4 | 6 | 0 | 0 | 0 |
| 5 | 6 | 0 | 0 | 0 |
| 6 | 30 | 22 | 0 | 0 |

6-wire bombs were never described correctly but won 73% of the time. The 4 and 5-wire bombs (one mission each) were also never described correctly and won 0%. **Whether a bomb was won depended more on how forgiving its rule was than on whether the agents shared an accurate picture.**

Two mechanisms explain wins without grounding:

- **Rule robustness:** the rule gives the same answer for the wrong and the true description (e.g. "if the last wire is blue, cut the last wire" when only the top wire was missed).
- **Redundancy rescue:** the Expert names both position and colour ("cut the second wire, the white one"); after zooming in, the Defuser follows the colour and cuts the correct wire despite an off-by-one description. 47 of 60 cut instructions included a colour.

## Finding 3: The agents don't repair

- After a wrong cut, only **3** recoveries in 60 games. Most failures are a strike followed by navigation until timeout.
- **2** false "solved" claims; the Expert accepted one.
- Both agents' post-game reflections often claim their descriptions were accurate when they weren't. **The agents cannot tell when their shared picture is wrong.**

## Other observations

- **Describe first, zoom later.** The Defuser described the wires from the overview in 57/60 games and re-described after zooming in only 6/60.
- **The missed top wire.** On 6-wire bombs the Defuser usually skips the topmost wire (dark against a dark backing, next to the module's Set-of-Marks outline), then adds an extra wire at the bottom to keep the count.
- **Sticking to the first answer.** In some games the Defuser repeated its wrong list even after zooming in with per-wire labels visible.
- **High run-to-run variance.** The same 3-wire mission went 4/4 in a pilot and 1/6 in the full run with identical configuration. Single-attempt scores on this benchmark are noisy.
- **Verbose messages.** Agents restate the whole bomb layout in full sentences every turn. In async mode every generated word costs clock time.

---

## Pilot: a communication overlay (10 matched async games)

`grounded_repair` adds short rules to both agents' prompts: zoom before describing, report labelled parts one at a time from the top, give label plus colour in every instruction, read back before cutting, confirm "solved" before moving on, and re-read the module after a strike. Same models, missions, attempts and settings as stock.

| | Stock | Overlay |
| --- | --- | --- |
| Wins | 5 | 5 |
| Grounded wins | **0** | **4** |
| Wins without grounding | 5 | 1 |
| Correct descriptions | 0 | 4 |
| Recoveries after a strike | 0 | 0 |

- Correct 6-wire descriptions on missions 234 and 845: **3/5** with overlay vs **0/12** stock.
- First ever win on mission 798 (stock 0/6), and it was grounded.
- Two stock wins on 845 were lost: the overlay removed the error-tolerant path and the agents did not reach a correct description instead.
- The repair rule did not work: every overlay loss was a wrong cut with no fix.

**Interpretation:** the overlay changed what the agents understood, not how often they won. Prompt-level grounding rules can turn tolerated errors into genuine shared understanding, but recovery after mistakes remains unsolved. With 10 games this is an early signal, not a firm result.

---

## Ideas to improve communication

1. **Complete the matched comparison.** Run the overlay on all 60 missions and attempts to make the pilot firm.
2. **Compact protocol (overlay v2).** Machines need facts in a fixed shape, not sentences. Test brevity-code formats that are short *and* grounded, e.g. Defuser `WIRES 6: A blue, B red, C red, D white, E white, F blue | STRIKES 0 | TIME 77`, Expert `CUT F BLUE`. Measure words per message, time to first instruction and async wins.
3. **A repair protocol that actually triggers.** After a strike, force a structured reset: re-read every labelled part, confirm which wire was cut, re-apply the rules before any navigation.
4. **Module confirmation.** Expert confirms the module type with the Defuser before applying any rules.
5. **Beyond Wires.** Test modules without colour redundancy (Keypad symbols, Who's On First) where errors are less likely to be tolerated.

---

## Limitations

- One module (Wires), one model pairing, 10 missions.
- The overlay pilot has 10 games; the win comparison has little statistical power.
- Labels come from an automatic parser checked against the game's ground truth; an independent blind hand-check is in progress.
- The 4 and 5-wire results come from one mission each.
- Single-module results are not comparable to GPTNT's leaderboard, which uses full multi-module bombs, and overlay runs modify protected prompts by design.

## Cost

| Run | Games | Approx. cost |
| --- | --- | --- |
| Stock full run | 60 | $24.63 |
| Overlay pilot | 10 | $3.57 |

## Acknowledgements

Built on the GPTNT benchmark and its official missions, manual and harness. All credit for the benchmark belongs to its authors.
