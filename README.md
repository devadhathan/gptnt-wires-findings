# Success Without Grounding in GPTNT Wires

Independent research on [GPTNT](https://github.com/GPTNT/gptnt) (Parekh et al., 2026; [arXiv 2606.28514](https://arxiv.org/abs/2606.28514)). Two AI agents play *Keep Talking and Nobody Explodes* — a Defuser that sees the bomb and an Expert that reads the manual. **Not affiliated with the GPTNT authors.**

**Author:** Devadhathan Maruthamangalam Dharmatheja · Edinburgh · October 2026

**Scope:** these are **single-module Wires missions** (sync and async), not the full multi-module bombs behind the paper's headline that no model defuses a bomb in real time. This study is not disputing that result.

**Full write-up:** [FINDINGS.md](FINDINGS.md)

---

## TL;DR

Stock agents solved **29/60** single-module Wires games (**48%**), but only **5** of those wins came from an accurate shared description of the bomb. Of the **31** losses, **20** were unfixed wrong cuts (`struck_no_fix`).

| | Stock (60) | Overlay pilot (10 async) |
| --- | --- | --- |
| Wins | 29 | 5 |
| Grounded wins | **5** | **4** |
| Wins without grounding (`cancelled_errors`) | **21** (**19** forgiving-rule / `rule_robust`, **2** colour rescues / `redundancy_rescue`) | 1 |
| Recovered after a strike | 3 | 0 |
| Losses with an unfixed wrong cut | **20 / 31** | 5 / 5 |
| Correct 6-wire descriptions | 0/30 | 3/5 on missions 234 & 845 |

A prompt-only communication overlay (`grounded_repair`) kept win rate flat (**5 vs 5** on matched games) but changed *how* wins happened: grounded wins **0 → 4**.

**Hand-check:** **12 of 20** blind labels agreed with the scorer; **all 8** disagreements were resolved against the game record. Published stock cancelled-error split: **19** forgiving-rule wins / **2** colour rescues. Details in [FINDINGS.md](FINDINGS.md#hand-check).

---

## Why this matters

Leaderboard-style solve rate treats “bomb defused” as success. On Wires, many solves happen when:

1. **Rule robustness** — the wrong description still yields the same cut under the manual, or
2. **Redundancy rescue** — the Expert names colour *and* position, so the Defuser can recover at click time.

So a high solve rate can hide a broken shared picture of the bomb. This repo scores every game against `bomb_state` and splits wins into `grounded` / `cancelled_errors` / `recovered_win`.

### Perception is the trigger; missing verification is the finding

Wires are only a few pixels thick in a 640×480 frame. GPT-5.2 often mis-sees them — especially the top wire. That perception error is the **trigger**. The **finding** is what follows: neither agent checks, doubts, or notices; they rarely re-describe after zooming; they almost never recover after a strike; and post-game reflections often call the dialogue accurate when it was not.

![Wires module (seed 234), enlarged crop](figures/top_wire_seed234.png)

*Example: top wire on a 6-wire module is easy to miss against the dark backing and Set-of-Marks outline.*

---

## What's in this repo

| Path | What it is |
| --- | --- |
| [FINDINGS.md](FINDINGS.md) | Full results, labels, limitations, cost |
| [evidence/](evidence/) | Summary CSVs + chat transcripts for quoted / handcheck games |
| [figures/top_wire_seed234.png](figures/top_wire_seed234.png) | Enlarged Wires crop used in the write-up |
| [handcheck/](handcheck/) | 20-game blind labelling sheet + SoM / crop / post-cut images |
| [scripts/summarize_runs.py](scripts/summarize_runs.py) | Scorer: grounding match, solve types, rule_robust / redundancy_rescue split |
| [scripts/language_collab/](scripts/language_collab/) | Overlay apply/run helpers and experiment notes |
| [scripts/language_collab/overlays/grounded_repair/](scripts/language_collab/overlays/grounded_repair/) | Prompt-only Defuser/Expert append |
| [runs/comm-overlay/](runs/comm-overlay/) | Manifests used for stock / overlay arms |

Full observation parquets under `output/` stay **gitignored** (large). Counts in FINDINGS can be checked from `evidence/` without re-running.

---

## Setup used for the study

| Setting | Value |
| --- | --- |
| Defuser | GPT-5.2 (thinking off) |
| Expert | Claude Haiku 4.5 (thinking off) |
| Module | Wires only (`wires_10`, rule seed 1764), **single-module missions** |
| Games | 60 stock (sync+async × 10 missions × 3 attempts) + 10 matched async overlay |
| Sampling | Temperature 0.6, max 1000 tokens, prompt caching **off** |

Stock prompts were verified unchanged (`gptnt doctor`, empty prompt diff). Overlay runs differ only in protected-content digest; suite and player fingerprints match stock.

---

## Reproduce (high level)

1. Install and run GPTNT from upstream docs: [gptnt.github.io/docs](https://gptnt.github.io/docs/).
2. Score a run directory:

```bash
.venv/bin/python scripts/summarize_runs.py output/comm-overlay/<run-folder>
```

3. Apply / revert the `grounded_repair` overlay via `scripts/language_collab/` (see `EXPERIMENT.md` / `PROTOCOL.md` there).
4. Hand-label from [handcheck/handcheck_sheet.md](handcheck/handcheck_sheet.md) — fill `MY LABEL` / `MY NOTE` only; automatic CSV labels are intentionally omitted from the sheet.
5. Or re-check published numbers from [`evidence/`](evidence/) summary CSVs and transcripts.

---

## Citation

If you use this analysis, cite GPTNT as the benchmark, and optionally this repository:

```
Parekh et al. GPTNT (2026). https://arxiv.org/abs/2606.28514
Devadhathan Maruthamangalam Dharmatheja. Success Without Grounding in GPTNT Wires (2026).
https://github.com/devadhathan/gptnt-wires-findings
```

---

## Licence / upstream

This repository is a fork of [GPTNT/gptnt](https://github.com/GPTNT/gptnt) with independent analysis on branch `language-collab-experiment`. Benchmark licence and authorship remain with the GPTNT authors — see their [LICENSE](LICENSE). The findings write-up, evidence pack, and handcheck materials are independent research notes.

---

<details>
<summary>Upstream GPTNT README (unchanged)</summary>

<div align='center'>

# GPTNT

_Can two AI agents talk each other through defusing a bomb?_

GPTNT is a benchmark for real-time collaboration between multimodal agents. Two AI agents play the
roles of _Defuser_ and _Expert_ in
[_Keep Talking and Nobody Explodes_ (KTANE)](https://keeptalkinggame.com). The Defuser sees the bomb,
the Expert reads the manual, and they must communicate to defuse it.

</div>

This benchmark uses the real game and the official manual. We have done as much as possible to make everything easy to use and run, and have put all the information to help you get started on our [documentation site](https://gptnt.github.io/docs/).

## Download

```bash
curl -fsSL \
  https://github.com/GPTNT/gptnt/releases/latest/download/gptnt.tar.gz |
  tar -xzf -

cd gptnt
mise install
mise run sync
```

Continue with [Install and check GPTNT](https://gptnt.github.io/docs/start-here/install-and-check/)
for checksum verification, prerequisites, and the first run.

</details>
