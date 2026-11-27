# PAPER candidate admission lifecycle

The old global active count limit of 12 silently dropped positive nominations.
Repeated revisions before first observation also created arbitrarily many
replacement sequences; frozen started experiments could occupy slots forever.

## New behaviour

- Keep historical discovery and validation passes as durable per-asset,
  version and strategy-ID nominations before archive compaction. Recover intact
  historical nominations and one relevant checksum-verified archived group per
  research run. Archived permanent test identities remain untouched.
- Capacity is per market and bounded by half the normal scan budget (CRYPTO 12,
  US 20, BIST 16); no shared 12-slot cutoff. Keep at least half the ordinary
  rotation available for exploration. At most four admission evaluations per
  market per cycle, in oldest-nomination order; capacity overflow remains
  queued.
- To start PAPER, confirm the compatible strategy fingerprint, source, recorded
  central research receipt, both historical gates and matched independent
  execution audit on current completed bars. Unsupported fractional sizing,
  research-only source/engines and missing evidence do not imply admission.
  Admission is a fresh prospective registration, not a historical PAPER result
  or another counted strategy experiment. It preserves prior test results.
- An exact previously registered PAPER strategy is not queued again. A failed
  recheck is terminal for that exact strategy/asset/version.
- Two consecutive identical frozen-history integrity failures pause the old
  sequence and release its active slot. A never-started strategy may be
  requalified after an initial revision, but repeated revision pauses it rather
  than producing more replacement sequences. All old anchors, cursor, progress
  and original evidence remain intact. A paused experiment is not a completed
  PAPER pass or failure and is not automatically resumed; a new registration
  must satisfy the full rules and start prospectively.
- The dashboard distinguishes active, paused, waiting, previously monitored,
  invalid, audit-pending, and admitted cases. It exposes positive discovery and
  retrospective validation counts for research-only engines separately.
- Existing PAPER policy, cost model, 90-bar forward process and frozen engine
  are unchanged. No LIVE permission, ACCEPT or invented historical observation.

The Superlab research receipt is evidence review, not independent strategy ACCEPT.
The main daily legacy engine is the currently supported PAPER executor;
other research engine positives are visible but must obtain an executable
forward-compatible adapter before they can be admitted.
