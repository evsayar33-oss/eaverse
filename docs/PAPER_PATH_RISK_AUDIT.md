# Free, real PAPER path-risk sensitivity (read-only)

The existing official Eaverse Release PAPER panel contains the *actually*
completed PAPER daily net returns and matched benchmark daily returns for
matured experiments. This addition reads the archived Release JSON only.
It **never** changes the return model, frozen strategy, original PAPER
verdict, persisted SQLite, Telegram or any execution decision.

For each fully completed valid ledger (minimum 30 paired bars), it reports
observed compounded return and drawdown; 600 circular stationary bootstrap
resamples for blocks of 5, 10 and 20 bars; 5th/median/95th resampled-path
return, drawdown and paired benchmark-excess sensitivity. Random indices are
joint for strategy and benchmark, and deterministic for each frozen SHA.
Invalid, partial, revised, or unobserved ledgers have NO calculated
statistics. Previous PAPER failures stay failures.

**Limits:** Recombining 90 observed daily returns is not simulation of
different market regimes or prospective order-book fills, is not an
independent confidence interval for future profitability, and does not
correct for previous strategy-selection bias or repeated testing. Terminal
wealth from mere trade reordering would be invariant; this is a
return-block resampling sensitivity, not trade-order shuffle. Output
explicitly provides no future profit probability.

Runs as an extra lightweight step in the already scheduled
`Native data-first strategy research (NO ORDERS)` workflow, only when
its real signed Release panel is present, and saves a JSON companion under
the existing 90-day research artifact directory. Requires no new secrets,
dependencies, APIs or extra schedule.

Run offline regression: `python -m unittest tests/test_paper_risk_audit.py -v`
