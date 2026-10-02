# Stock vs overlay diff (communication prompts only)

Overlays append to `storage/prompts/requirements_communication_{defuser,expert}.md`. Role files (`roles_*.md`) still say "user" for API framing; overlays add **outbound radio** rules so `send_message` text says Expert/Defuser, not "user".

## Stock already requires

**Defuser:** ask if uncertain; concise factual answers; full module descriptions; no module names; report green LED / strike; no lists in messages.

**Expert:** targeted clarification if ambiguous; one module at a time; no verbatim manual; reconfirm contradictory details; concise instructions.

## Overlay adds (beyond stock)

### Defuser

- Radio partner wording (not "the user").
- Compact wire rows (count, colors top→bottom, serial odd/even, cuts).
- Answer the question first; avoid widget dumps.
- One clarifying question before ambiguous cuts.
- Strike → restate view, ask next step.

### Expert

- Radio partner wording (not "the user").
- One question or one instruction per message.
- Verify manual branch facts before cut/press; one targeted ask, no guess.
- Branch-unlocking questions (counts, colors, serial parity, batteries, indicators, ports).
- Explicit "does not match manual" + re-check.
- Position-based instructions ("4th wire from top"); wait for outcome.
- After strike: re-establish state, recompute — no blind repeat.

## What stock does *not* require (overlay tests these)

- Read-back before destructive action.
- Ban on leading yes/no ("Can you see an LED?") — not explicit in stock.
- Contradiction callouts across turns.
- Strike repair protocol as a fixed sequence.

## Ablation (later)

Split `overlay_*.md` into challenge / clarify / collaborate slices on the module with the largest stock↔overlay gap only.
