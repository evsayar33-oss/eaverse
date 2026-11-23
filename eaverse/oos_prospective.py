"""Strict event-first OOS/forward evaluator; retrospective success is never a trade license.

Only called AFTER research_integrity.preflight passes for real local archives.
Matched controls, venue-native costs, familywise multiplicity and no holdout
peeking are enforced. Prospective cases must carry newly collected first_seen
and immutable raw source evidence. No PAPER/LIVE action exists in this module.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta
from statistics import mean

from . import research_integrity as integrity

SCHEMA = "eaverse.prospective_event_evaluator.v9"
MIN_INDEPENDENT_CASES = 20
MIN_REGIMES = 2


def _fold(cases, spec, *, root, familywise_trials, registration, prospective=False):
    if not isinstance(cases, list) or len(cases) < MIN_INDEPENDENT_CASES:
        return {"status": "INSUFFICIENT_NONOVERLAPPING_EVENTS",
                "cases": len(cases) if isinstance(cases, list) else 0}
    start = integrity.stamp(spec["start_utc"])
    end = integrity.stamp(spec["end_utc"])
    last_exit = None
    seen = set()
    net = []
    excess = []
    winners = 0
    regimes = Counter()
    for case in cases:
        event_id = case.get("economic_event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ValueError("DUPLICATE_ECONOMIC_EVENT_IN_FOLD")
        seen.add(event_id)
        known = integrity.stamp(case["publication_at_utc"])
        first_seen = integrity.stamp(case["actual_first_seen_at_utc"])
        entered = integrity.stamp(case["entry_at_utc"])
        exited = integrity.stamp(case["exit_at_utc"])
        if not (start <= entered < exited <= end and known <= entered):
            raise ValueError("FUTURE_PUBLICATION_OR_OUT_OF_FOLD_EXECUTION")
        if first_seen < known:
            raise ValueError("BACKDATED_PROSPECTIVE_FIRST_SEEN")
        if prospective:
            if first_seen < registration or entered < first_seen or known < registration:
                raise ValueError("FORWARD_EVENT_NOT_NEWLY_OBSERVED_AFTER_REGISTRATION")
            if case.get("collector_first_seen_chain_verified") is not True:
                raise ValueError("NO_AUTHENTIC_PROSPECTIVE_FIRST_SEEN_CHAIN")
        if last_exit is not None and entered <= last_exit:
            raise ValueError("OVERLAPPING_OR_REUSED_TRADE_CASE")
        last_exit = exited
        if case.get("venue_native_execution_evidence") is not True:
            raise ValueError("EXECUTION_NOT_VENUE_NATIVE")
        integrity.verified_blob(root, case["execution_archive_path"],
                                case["execution_archive_sha256"])
        values = [case.get(key) for key in (
            "gross_bps", "matched_control_gross_bps", "one_way_fee_bps",
            "one_way_slippage_bps", "one_way_half_spread_bps",
            "one_way_impact_bps")]
        if any(isinstance(x, bool) or not isinstance(x, (int, float))
               or not math.isfinite(x) for x in values):
            raise ValueError("NONFINITE_OR_MISSING_EXECUTION_COST")
        gross, control, fee, slippage, spread, impact = map(float, values)
        if fee <= 0 or min(slippage, spread, impact) < 0:
            raise ValueError("NEGATIVE_OR_ZERO_EXECUTION_COST")
        costs = 2 * (fee + slippage + spread + impact)
        realised = gross - costs
        benchmark = control - costs
        improvement = realised - benchmark
        net.append(realised)
        excess.append(improvement)
        winners += improvement > 0
        regime = case.get("regime")
        if not isinstance(regime, str) or not regime:
            raise ValueError("MISSING_MARKET_REGIME_LABEL")
        regimes[regime] += 1
    count = len(cases)
    # Exact one-sided paired sign test; Bonferroni for whole registered
    # hypothesis family, not only selected survivors.
    p_raw = sum(math.comb(count, k) for k in range(winners, count + 1)) / (2 ** count)
    corrected = min(1.0, p_raw * familywise_trials)
    passes = (len(regimes) >= MIN_REGIMES and
              mean(net) > 0 and mean(excess) > 0 and corrected <= 0.05)
    return {
        "status": ("PASS_AFTER_COST_MATCHED_CONTROL_MULTIPLICITY"
                   if passes else "FAIL_AFTER_COST_OR_MULTIPLE_TESTING"),
        "cases": count, "regimes": dict(regimes),
        "mean_after_cost_bps": round(mean(net), 6),
        "mean_excess_over_control_bps": round(mean(excess), 6),
        "paired_sign_wins": winners,
        "familywise_trials": familywise_trials,
        "bonferroni_p_one_sided": round(corrected, 12),
        "execution_is_historical_replay_not_live_fill": True,
    }


def evaluate(design, bundle, samples, *, root):
    proof = integrity.preflight(design, bundle, root=root)
    base = {
        "schema": SCHEMA, "hypothesis_id": design["id"],
        "gate": proof["status"],
        "status": "BLOCKED_BEFORE_ECONOMIC_BACKTEST",
        "holdout_opened": False, "prospective_validated": False,
        "paper_eligible": False, "live_allowed": False,
    }
    if not proof["research_ready"]:
        return base
    study = bundle["study"]
    protocol = integrity.validate_study_contract(study, decision_at=bundle["decision_at_utc"])
    registration = integrity.stamp(study["registered_at_utc"])
    familywise = protocol["familywise_trials"]
    folds = study["folds"]
    try:
        discovery = _fold(samples.get("discovery", []), folds[0], root=root,
                          familywise_trials=familywise, registration=registration)
        base["discovery"] = discovery
        if not discovery["status"].startswith("PASS"):
            return {**base, "status": "DISCOVERY_REJECTED_HOLDOUT_STAYS_SEALED"}
        validation = _fold(samples.get("validation", []), folds[1], root=root,
                           familywise_trials=familywise, registration=registration)
        base["validation"] = validation
        if not validation["status"].startswith("PASS"):
            return {**base, "status": "VALIDATION_REJECTED_HOLDOUT_STAYS_SEALED"}
        # The holdout is opened once, only after preregistered two-fold pass.
        held = _fold(samples.get("untouched_holdout", []), folds[2], root=root,
                     familywise_trials=familywise, registration=registration)
        base["holdout_opened"] = True
        base["untouched_holdout"] = held
        if not held["status"].startswith("PASS"):
            return {**base, "status": "HOLDOUT_REJECTED_NO_PROSPECTIVE_PROMOTION"}
        forward_spec = samples.get("prospective_window")
        if not isinstance(forward_spec, dict):
            return {**base, "status": "HOLDOUT_PASS_BUT_NO_PROSPECTIVE_EVIDENCE"}
        if integrity.stamp(forward_spec["start_utc"]) < registration:
            raise ValueError("PROSPECTIVE_PERIOD_PRECEDES_REGISTRATION")
        prospective = _fold(samples.get("prospective", []), forward_spec, root=root,
                            familywise_trials=familywise,
                            registration=registration, prospective=True)
        base["prospective"] = prospective
        if not prospective["status"].startswith("PASS"):
            return {**base, "status": "PROSPECTIVE_REJECTED"}
        return {
            **base,
            "status": "ALL_RESEARCH_FOLDS_PASS_MANUAL_INDEPENDENT_AUDIT_REQUIRED",
            # Crucial: a case list may contain fabricated numbers; the
            # independent audit needs to verify archived fills, funding,
            # venue mechanics, event collection and evidence rights.
            "prospective_validated": False,
            "paper_eligible": False,
            "live_allowed": False,
            "manual_source_and_execution_audit_required": True,
        }
    except (KeyError, ValueError, TypeError, OSError) as exc:
        return {**base, "status": "EVALUATION_FAIL_CLOSED",
                "reason": str(exc)[:100]}
