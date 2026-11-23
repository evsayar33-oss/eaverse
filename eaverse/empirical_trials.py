"""Source-authenticated, aligned, unselected V7 research-return diagnostics.

All 32 pre-registered LONG configurations are evaluated on the SAME weekly
calendar from discovery and validation folds only. Untouched holdout is never
read here. Returns are hypothetical one-unit, next-hour-open, fixed-horizon
realizations debited by the pre-registered stressed round-trip cost. They
are NOT exchange orders, executable fills, independent prospective alpha or
candidate approvals. The 32 downside-response hypotheses remain research-only.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import early_flow, extended_flow, statistical_audit

SCHEMA = "eaverse.v7.aligned_empirical_trials.v1"
BLOCKS = 8
BENCHMARK = "ZERO_EXPOSURE_CASH_NOT_MARKET_BETA_MATCHED"
TRIALS_PER_ASSET = 32
REGISTERED_CROSS_ASSET_LONG_TRIALS = 8 * TRIALS_PER_ASSET


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _sharpe(matrix):
    values = np.asarray(matrix, dtype=float)
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    mu = values.mean(axis=0)
    sd = values.std(axis=0, ddof=1) if len(values) > 1 else np.zeros(values.shape[1])
    return np.divide(mu, sd, out=np.full(values.shape[1], -np.inf),
                     where=np.isfinite(sd) & (sd > 1e-12))


def cscv_pbo(panel, blocks=BLOCKS):
    """Real CSCV on selected source-return vectors, not on fold averages."""
    a = np.asarray(panel, dtype=float)
    if a.ndim != 2 or not np.all(np.isfinite(a)):
        raise ValueError("ALIGNED_RETURN_PANEL_INVALID")
    n, variants = a.shape
    if blocks < 4 or blocks % 2 or n < blocks * 8 or variants < 2:
        return {"status": "INSUFFICIENT_ALIGNED_VARIANTS_OR_WEEKS",
                "pbo": None, "partitions": 0, "blocks": blocks}
    eligible = np.flatnonzero(np.std(a, axis=0) > 1e-12)
    if len(eligible) < 2:
        return {"status": "INSUFFICIENT_NONFLAT_VARIANTS",
                "pbo": None, "partitions": 0, "blocks": blocks}
    a = a[:, eligible]
    splits = np.array_split(np.arange(n), blocks)
    losers = []
    for included in itertools.combinations(range(blocks), blocks // 2):
        train = np.concatenate([splits[k] for k in included])
        test = np.concatenate([splits[k] for k in range(blocks) if k not in included])
        selected = int(np.argmax(_sharpe(a[train])))
        ranked = _sharpe(a[test])
        # rank in (0,1); below the cross-sectional median means overfitting.
        rank = (np.sum(ranked < ranked[selected]) +
                0.5 * np.sum(ranked == ranked[selected])) / len(ranked)
        losers.append(float(rank < .5))
    return {"status": "CALCULATED_RETROSPECTIVE_CSCV", "pbo": round(float(np.mean(losers)), 6),
            "partitions": len(losers), "blocks": blocks,
            "nonflat_variants": len(eligible), "input_was_real_aligned_history": True,
            "chronological_forward_test": False,
            "future_loss_probability": None}


def deflated_sharpe_validation(selected_validation, discovery_matrix, *,
                              trial_count=REGISTERED_CROSS_ASSET_LONG_TRIALS):
    """Approximate DSR for discovery-chosen variant on untouched *validation*.

    Uses full pre-registered search count, discovery cross-variant dispersion.
    Correlated models / serial dependence prevent an exact independent
    effective-trials interpretation, so DSR remains an exploratory diagnostic.
    """
    values = np.asarray(selected_validation, dtype=float)
    training = np.asarray(discovery_matrix, dtype=float)
    n = len(values)
    if (training.ndim != 2 or n < 20 or trial_count < training.shape[1] or
            np.std(values, ddof=1) <= 1e-12 or not np.all(np.isfinite(values))):
        return {"status": "INSUFFICIENT_VALIDATION_VARIATION", "dsr": None,
                "trial_count": trial_count}
    candidate_srs = _sharpe(training)
    candidate_srs = candidate_srs[np.isfinite(candidate_srs)]
    if len(candidate_srs) < 2:
        return {"status": "INSUFFICIENT_DISCOVERY_SEARCH_VARIATION", "dsr": None,
                "trial_count": trial_count}
    dispersion = max(float(np.std(candidate_srs, ddof=1)), 1 / math.sqrt(len(training)))
    distribution = NormalDist()
    z1 = distribution.inv_cdf(1 - 1 / trial_count)
    z2 = distribution.inv_cdf(1 - 1 / (trial_count * math.e))
    expected_max = dispersion * ((1 - .5772156649015329) * z1 + .5772156649015329 * z2)
    sharpe = float(_sharpe(values)[0])
    center = values - np.mean(values)
    std = float(np.std(values, ddof=0))
    third = float(np.mean((center / std) ** 3))
    fourth = float(np.mean((center / std) ** 4))
    variance = 1 - third * sharpe + (fourth - 1) * sharpe * sharpe / 4
    if not math.isfinite(variance) or variance <= 0:
        return {"status": "DSR_MOMENT_VARIANCE_INVALID", "dsr": None,
                "trial_count": trial_count}
    value = distribution.cdf((sharpe - expected_max) * math.sqrt(n - 1) /
                             math.sqrt(variance))
    return {
        "status": "CALCULATED_APPROXIMATE_SELECTION_ADJUSTED_DSR",
        "dsr": round(value, 6), "candidate_validation_weekly_sharpe": round(sharpe, 6),
        "expected_max_weekly_sharpe": round(expected_max, 6),
        "trial_count": trial_count, "validation_weeks": n,
        "independent_effective_trial_count_known": False,
        "unadjusted_for_weekly_serial_correlation": True,
        "not_a_forward_approval": True,
    }


def reality_check(panel, *, simulations=400, mean_block=3, seed=1123):
    """White-style centered stationary bootstrap of all candidate returns.

    This is an exploratory cash benchmark test on the *exposed historical
    discovery + validation panel*, not an untouched out-of-sample test.
    """
    a = np.asarray(panel, dtype=float)
    if a.ndim != 2 or not np.all(np.isfinite(a)):
        raise ValueError("INVALID_REALITY_CHECK_PANEL")
    n, k = a.shape
    if n < 32 or k < 2:
        return {"status": "INSUFFICIENT_WEEKS", "p_value": None}
    means = np.mean(a, axis=0)
    centered = a - means
    observed = float(max(means))
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(simulations):
        selected = np.empty(n, dtype=int)
        selected[0] = rng.integers(n)
        for j in range(1, n):
            selected[j] = (rng.integers(n) if rng.random() < 1 / mean_block
                           else (selected[j - 1] + 1) % n)
        if float(np.max(np.mean(centered[selected], axis=0))) >= observed:
            extreme += 1
    return {"status": "CALCULATED_RETROSPECTIVE_WHITE_STYLE_BOOTSTRAP",
            "p_value": round((extreme + 1) / (simulations + 1), 6),
            "resamples": simulations, "stationary_mean_block_weeks": mean_block,
            "untouched_holdout_used": False, "cash_only_comparator": True}


def _fold_panel(bars, features, specs, fold):
    """Independent chronological fold; debit cost at known EXIT time only."""
    first, last = fold
    n = len(bars)
    if not (0 <= first < last <= n):
        raise ValueError("INVALID_FOLD_BOUNDARIES")
    opens = bars.open.to_numpy(dtype=float)
    if not np.all(np.isfinite(opens)) or np.any(opens <= 0):
        raise ValueError("BAD_OPEN_IN_VERIFIED_ARCHIVE")
    matrix = np.zeros((last - first, len(specs)), dtype=float)
    counts = []
    for col, spec in enumerate(specs):
        horizon = spec["horizon_hours"]
        mask = extended_flow.event_mask(bars, features, spec["family"], 1)
        previous = first - horizon - 1
        n_events = 0
        for i in range(first, last - horizon - 1):
            if not mask[i] or i <= previous + horizon:
                continue
            exit_index = i + 1 + horizon
            net = (opens[exit_index] / opens[i + 1] - 1 -
                   early_flow.ROUND_TRIP_BPS / 10000)
            if net <= -1 or not math.isfinite(net):
                raise ValueError("IMPOSSIBLE_COSTED_EVENT_RETURN")
            if matrix[exit_index - first, col] != 0:
                raise ValueError("OVERLAPPING_EXIT_IN_SAME_HYPOTHESIS")
            matrix[exit_index - first, col] = net
            previous = i
            n_events += 1
        counts.append(n_events)
    hourly = pd.DataFrame(matrix, index=bars.index[first:last])
    # Explicitly exclude low-coverage boundary weeks; never fill missing
    # sessions or official exchange blackouts with simulated market returns.
    counts_per_week = hourly.iloc[:, 0].resample("W-SUN").size()
    weekly = np.expm1(np.log1p(hourly).resample("W-SUN").sum())
    weekly = weekly.loc[counts_per_week >= 5 * 24]
    return weekly, counts


def analyze(bars, report):
    """Never selects from, accesses, or scores the untouched holdout."""
    statistical_audit.review(report)
    if not bars.index.equals(extended_flow.expected_hours()):
        raise ValueError("SOURCE_HOURLY_CALENDAR_MISMATCH")
    features = extended_flow._apply_halt_guard(early_flow.asof_features(bars))
    folds = early_flow.folds(len(bars))
    rows = [r for r in report["results"] if r["spec"]["direction"] == 1]
    if len(rows) != TRIALS_PER_ASSET:
        raise ValueError("INCOMPLETE_LONG_HYPOTHESIS_FAMILY")
    specs = [r["spec"] for r in rows]
    # Grouping in existing registered V7 order is deterministic.
    train, train_counts = _fold_panel(bars, features, specs, folds["discovery"])
    validation, valid_counts = _fold_panel(bars, features, specs, folds["validation"])
    if len(train) < 32 or len(validation) < 16:
        raise ValueError("INCOMPLETE_CHRONOLOGICAL_RESEARCH_WEEKS")
    # No fold-specific outcome can enter from the held-out last quarter.
    if train.index.max() >= validation.index.min():
        raise ValueError("DISCOVERY_VALIDATION_LEAK")
    discovery_sr = _sharpe(train.to_numpy())
    chosen = int(np.argmax(discovery_sr))
    # Only train-qualified variant selection; validation is evaluated afterward.
    selected_validation = validation.to_numpy()[:, chosen]
    dsr = deflated_sharpe_validation(selected_validation, train.to_numpy())
    # CSCV deliberately ignores chronological order across split combinations:
    # it quantifies retrospective selection instability, NOT forward evidence.
    aligned = pd.concat((train, validation))
    aligned.columns = [r["id"] for r in rows]
    rounded = np.round(aligned.to_numpy(dtype=float), 12)
    if not np.all(np.isfinite(rounded)):
        raise ValueError("NONFINITE_HISTORICAL_CANDIDATE_RETURNS")
    panel = {"dates_utc_week_end": [v.isoformat() for v in aligned.index],
             "variant_ids": [r["id"] for r in rows],
             "net_return_paths": rounded.tolist()}
    panel["sha256"] = digest({k: v for k, v in panel.items() if k != "sha256"})
    pbo = cscv_pbo(rounded)
    rc = reality_check(rounded)
    return {
        "schema": SCHEMA, "symbol": report["symbol"], "source_digest": report["source_digest"],
        "source_policy_sha256": report["policy_sha256"],
        "source_hypotheses_sha256": extended_flow.hash_obj(report["results"]),
        "source_venue": "BINANCE_SPOT",
        "return_model": "ONE_UNIT_LONG_FIXED_HORIZON_EXIT_TIME_REALIZED_AFTER_2X_ROUNDTRIP_COSTS",
        "entry": "NEXT_OPEN_AFTER_COMPLETED_SIGNAL", "exit": "HORIZON_HOURS_LATER_OPEN",
        "cash_on_no_position_weeks": True,
        "training_fold": "DISCOVERY_ONLY", "selection": "HIGHEST_DISCOVERY_WEEKLY_SHARPE",
        "selected_discovery_variant_id": rows[chosen]["id"],
        "validation_fold": "VALIDATION_ONLY_NOT_UNTOUCHED_HOLDOUT",
        "matched_controls_in_this_matrix": False,
        "registered_total_trial_family_per_asset": 64,
        "registered_long_trial_count": TRIALS_PER_ASSET,
        "cross_asset_long_trial_family": REGISTERED_CROSS_ASSET_LONG_TRIALS,
        "candidate_events_discovery": train_counts,
        "candidate_events_validation": valid_counts,
        "weekly_discovery_rows": len(train), "weekly_validation_rows": len(validation),
        "pbo": pbo, "dsr": dsr, "white_reality_check": rc,
        "weekly_panel": panel,
        "correlated_hypotheses_effective_count_unknown": True,
        "external_benchmark_and_venue_fills_verified": False,
        "point_in_time_original_publication_vintage_verified": False,
        "untouched_holdout_read": False,
        "independent_prospective_validated": False,
        "paper_eligible": False, "live_allowed": False,
        "status": "EMPIRICAL_RETROSPECTIVE_STATISTICS_NOT_CENTRAL_ACCEPT",
    }


def append_ledger(path, result):
    """Append-only, separately hash-chained *derived* research paths.

    Original V7 aggregated evidence is never rewritten; identical source
    fingerprint and methodology may not manufacture new forward observations.
    """
    path = Path(path)
    previous = "GENESIS"
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            unsigned = {k: v for k, v in row.items() if k != "sha256"}
            if row.get("prev_sha256") != previous or digest(unsigned) != row.get("sha256"):
                raise ValueError("STATISTICAL_EVIDENCE_CHAIN_TAMPER")
            previous = row["sha256"]
            records.append(row)
    if result.get("paper_eligible") is not False or result.get("live_allowed") is not False:
        raise ValueError("STATISTICAL_REPORT_CANNOT_APPROVE_TRADES")
    stamp = (result["source_digest"], result["source_policy_sha256"],
             result["weekly_panel"]["sha256"])
    if records:
        latest = records[-1]
        old = (latest["source_digest"], latest["source_policy_sha256"],
               latest["weekly_panel"]["sha256"])
        if old == stamp:
            return {"status": "UNCHANGED_SOURCE_STATISTICS_NOT_NEW_FORWARD_EVIDENCE",
                    "records": len(records)}
    row = {**result, "prev_sha256": previous}
    row["sha256"] = digest(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, allow_nan=False,
                                separators=(",", ":")) + "\n")
    return {"status": "APPENDED_HISTORICAL_STATISTICS_ONLY",
            "records": len(records) + 1, "sha256": row["sha256"]}
