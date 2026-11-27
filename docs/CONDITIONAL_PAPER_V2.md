# Capital survival and calendar recovery — conditional PAPER v2

The percentage drawdown cutoff is removed from new native and legacy PAPER
qualification. Drawdown magnitude is measured and reported, not a rejection
criterion. The legacy contract is retained solely for replaying sealed existing
ordinary PAPER experiments and identifying historical percent-threshold decisions.

## Exact risk gate
* A nonpositive equity factor, insolvency at an executable open, or modeled
  adverse intrabar insolvency is terminal. Costs, next-open execution, existing
  position sizes, stops, and assumed short borrow remain in the simulation.
  This is historical and prospective simulated evidence, not a guarantee of
  future solvency.
* Maximum time underwater is 180 **calendar days**, including weekends and
  currently unfinished underwater spells. A spell begins at the last achieved
  equity peak and ends at the first return to that peak. A new peak resets the
  clock. Even a small loss that takes longer than 180 days to recover fails;
  a large but swiftly recovered loss can pass. 180 is a configurable explicit
  policy choice, not a market law.
* Recovery is derived from each original executor's exact, net executable
  return path (not the old weekly compressed archive for daily strategies).
  The coverage path has a separate scalar audit. Foundry's default original
  archive and execution behavior stay immutable; the new adapter requests its
  full path with gap-solvency audit. A continuous full-history replay prevents
  a reset at a fold boundary hiding a prolonged loss.
* Positive discovery/validation net, existing minimum trades and chronological
  robustness remain. Historical checks are not a prospective holdout. Conditional
  observation lasts 90 future strategy bars, with early termination for modeled
  insolvency or excessive recovery time; no real orders or automatic LIVE.

## No artificial conditional candidate quota
A passing candidate receives its own virtual experiment immediately. Removed:
per-market 12/20/16, per-asset three, and per-run 192 candidate caps. The same
asset's validated price history is downloaded once and reused for all its
strategies. The data-only backlog lane has a 30-minute realistic wall-clock
budget each run; fair durable continuation handles genuine data/runtime limits
without discarding candidates or blocking ordinary exploration. The workflow
runs hourly, serialized; no parallel checkpoint writers.

## Migration and archives
On first use, every prior native percentage-risk rejection, prior capacity
waiting candidate, and never-started v1 observation is reconsidered once under
the new contract. Exact original strategy identities, prior decisions and
forward evidence remain in the migration ledger. Mature or already-observed
experiments are sealed; the verified legacy adapter hash permits continuation
without fabricated observations. Zero-bar price revisions may start a newly
qualified future-only experiment, preserving its old registration in the
restart ledger. A revision of actually observed prices remains an integrity hold.
The archive reader processes all eligible verified archive groups within a
wall-clock deadline (not a six-archive quota), with SHA256, exact source and
record identity, bounded record size and compressed/expanded size. Previously
read groups may be reopened if the changed policy creates newly eligible rows.
Ordinary legacy candidates rejected solely under the old percentage rule are
eligible for fresh verification, including missing historical validation if the
original discovery evidence was sufficient. Missing data/source/calendar
evidence is explicitly identified rather than synthesized.
