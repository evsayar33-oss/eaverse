# Strategy foundry V2 — research only
- Separate version coverage-2; no change to existing PAPER or coverage-1 identities.
- Six bounded experiments per loaded asset visit across 1d/1w: parameter,
  grammar and risk lanes. Outcome-independent seeded multidimensional
  settings (not all mathematical combinations); 26 inherited signal families
  plus independently parameterized MACD, Bollinger, RSI, stochastic and ADX.
- Grammar combines 2–6 rules using all/any/majority/ordered sequence/filter/
  weighted voting. Independent direction, confirmation and size settings.
- ATR stop, take profit, trailing, time exit, partial TP and staged entry.
  Signal at completed bar, next open; trailing uses completed prior extremes,
  same-bar ambiguity stop first, stop gaps at open. No actual fill/borrow
  guarantees or tick reconstruction. 2x fees and assumed 5% short borrow.
- Weekly compounded path for discovery and retrospective validation, with
  SHA256, retained in existing archive/checkpoint. Daily drawdown source-only.
- Every new main-engine technical/event trial is routed through immutable
  Superlab qualification code when available; missing contract explicit.
  Central gate records blockers; no full independent lab validation/ACCEPT.
  Superlab PR 73 independently reads the unarchived source checkpoint, verifies
  hash and path compounding; archived rows are counted, not replayed.
- No new paid APIs or scheduling. Other independently scheduled research
  workflows, exhaustive Cartesian search, multi-asset portfolio and
  independent forward/PBO/DSR for the new grammar remain open.
