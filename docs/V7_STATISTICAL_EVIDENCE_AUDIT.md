# V7 source-traceable statistical evidence audit (no orders)

The V7 strategy report currently has 64 hypothesis fold aggregates, not
the synchronized, per-interval, **unselected and selected variant net return
paths** required for CSCV PBO, DSR, White Reality Check or realistic Monte Carlo.
Those statistics cannot be honestly reconstructed from mean return or win rate.
This new research-only audit is therefore automatically run on each V7 asset's
actual `report.json`; it verifies 36 signed-source month flags, 64 hypotheses,
unopened holdouts and no-trading flags; reports the explicit multiplicity burden,
status and blockers; and calculates **descriptive Wilson 95% hit-rate intervals**
on the reported non-overlapping event counts.

Wilson is not an alpha significance test: source/venue dependence, overlapping
holding periods between variants, reused matched controls, volatility regimes,
and the 64-family search are not accounted for. Passing this audit is **not**
a PAPER or LIVE approval. It deliberately records PBO, DSR, White Reality
Check and Monte Carlo as `null` until genuine aligned raw inputs exist.

Further integration requires a versioned, separately collected *fold-local*
return/outcome panel with source SHA, first-published timestamp, per-venue
execution costs, explicit non-selected variants and family trial counts,
then an independent OOS/prospective evaluation. Do not open untouched holdouts,
splice old data into a new prospective ledger or change the original strategy
to make a test pass.

No changes to production engine, PAPER state, strategy catalog, acceptance
thresholds or trading workflows. Existing V7 research artifact gains one
`statistical_audit.json` next to `report.json`.

Command:
`python -m eaverse.statistical_audit --report out/extended_flow/report.json --out out/extended_flow/statistical_audit.json`
