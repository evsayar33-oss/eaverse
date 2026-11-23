"""Read-only V7 evidence adequacy and uncertainty audit.

Consumes actual source-verified V7 summary observations. It MUST NOT infer
CSCV/PBO, DSR, White Reality Check or Monte Carlo from only fold averages.
No trading or PAPER decision is possible in this module.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

SCHEMA = "eaverse.v7.statistical_evidence_audit.v1"
BLOCKERS = (
    "ALIGNED_UNSELECTED_VARIANT_RETURN_PANEL_NOT_CAPTURED",
    "MULTIPLE_TESTING_ADJUSTED_RETURN_PATH_TEST_NOT_RUN",
    "BLOCK_BOOTSTRAP_AND_MONTE_CARLO_PATHS_NOT_RUN",
    "UNTOUCHED_PROSPECTIVE_VENUE_FILL_EVIDENCE_NOT_CAPTURED",
    "HISTORICAL_PUBLICATION_VINTAGES_NOT_PROVEN",
    "INDEPENDENT_EXECUTION_AND_REAL_SPREAD_IMPACT_NOT_PROVEN",
)


def wilson_interval(hit_rate, n, z=1.95996398454):
    """Descriptive binomial interval, NOT an autocorrelation-robust alpha test."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return None
    if isinstance(hit_rate, bool) or not isinstance(hit_rate, (float, int)):
        return None
    if not math.isfinite(hit_rate) or not 0 <= hit_rate <= 1:
        raise ValueError("INVALID_REPORTED_HIT_RATE")
    denom = 1 + z * z / n
    center = (hit_rate + z * z / (2 * n)) / denom
    rad = z * math.sqrt(hit_rate * (1 - hit_rate) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - rad), 5), round(min(1.0, center + rad), 5)]


def review(report):
    """Fail closed on inconsistent V7 source, sealed holdout, or trial accounting."""
    if not isinstance(report, dict) or report.get("schema") != "eaverse.extended_flow.v7.1":
        raise ValueError("NOT_REGISTERED_V7_RESEARCH_REPORT")
    results = report.get("results")
    source = report.get("source_zip_provenance")
    if not isinstance(results, list) or len(results) != 64:
        raise ValueError("V7_MISSING_OR_EXTRA_HYPOTHESES")
    if len({row.get("id") for row in results if isinstance(row, dict)}) != 64:
        raise ValueError("V7_HYPOTHESIS_IDS_INVALID_OR_DUPLICATED")
    if report.get("hypothesis_count") != 64 or report.get("hours") != 26302:
        raise ValueError("V7_INCONSISTENT_REGISTERED_HISTORY")
    if (not isinstance(source, list) or len(source) != 36
            or any(not isinstance(x, dict) or x.get("checksum_verified") is not True
                   or x.get("venue") != "BINANCE_SPOT"
                   for x in source)):
        raise ValueError("V7_SOURCE_MANIFEST_NOT_VERIFIED")
    for name in ("paper_eligible", "live_allowed", "forward_validated"):
        if report.get(name) is not False:
            raise ValueError("V7_UNAUTHORIZED_PROMOTION_FLAG")
    if report.get("independent_venue_verified") is not False:
        raise ValueError("V7_INDEPENDENT_VENUE_NOT_PROVEN")
    if report.get("policy", {}).get("multiple_testing_count_per_asset") != 64:
        raise ValueError("V7_MISSING_MULTIPLICITY_FAMILY")

    counts = Counter()
    details = []
    for row in results:
        spec = row.get("spec")
        folds = row.get("folds")
        if not isinstance(spec, dict) or not isinstance(folds, dict):
            raise ValueError("V7_BAD_HYPOTHESIS_OR_FOLD")
        if spec.get("source") != "BINANCE_SPOT_1H":
            raise ValueError("V7_SOURCE_ID_MISMATCH")
        if row.get("paper_eligible") is not False or row.get("trade_authorized") is not False:
            raise ValueError("V7_TRADING_FLAG_IN_HYPOTHESIS")
        opened = row.get("holdout_opened_after_preselection") is True
        hold = folds.get("untouched_holdout")
        if not isinstance(hold, dict):
            raise ValueError("V7_HOLDOUT_FOLD_MISSING")
        if not opened and hold.get("data_status") != "SEALED_UNTIL_PRESELECTION_PASSES":
            raise ValueError("V7_HOLDOUT_ACCESSED_BEFORE_SELECTION")
        if opened and hold.get("data_status") == "SEALED_UNTIL_PRESELECTION_PASSES":
            raise ValueError("V7_HOLDOUT_OPENED_BUT_NOT_EVALUATED")

        uncertainty = {}
        for fold_name in ("discovery", "validation", "untouched_holdout"):
            fold = folds.get(fold_name)
            if not isinstance(fold, dict):
                raise ValueError("V7_FOLD_MISSING")
            n = fold.get("cases_nonoverlapping")
            hits = fold.get("directional_hit_rate")
            if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 0):
                raise ValueError("V7_INVALID_NONOVERLAPPING_COUNT")
            if n == 0 and hits is not None:
                raise ValueError("V7_HIT_RATE_WITHOUT_CASES")
            uncertainty[fold_name] = {
                "cases_nonoverlapping": n,
                "matched_prior_controls": fold.get("prior_same_hour_controls"),
                "observed_hit_rate": hits,
                "wilson95_descriptive_only": wilson_interval(hits, n),
                "is_dependent_event_adjusted": False,
            }
        counts[str(row.get("decision"))] += 1
        details.append({
            "id": row["id"], "decision": row.get("decision"),
            "direction": spec.get("direction"),
            "holdout_legitimately_opened": opened,
            "fold_uncertainty": uncertainty,
            "cscv_pbo": None, "dsr": None, "white_reality_check_p": None,
            "stationary_bootstrap_drawdown": None,
            "statistical_significance_verified": False,
            "paper_eligible": False, "live_allowed": False,
        })
    if dict(counts) != report.get("decisions"):
        raise ValueError("V7_RECORDED_DECISIONS_MISMATCH")
    return {
        "schema": SCHEMA, "symbol": report.get("symbol"),
        "source_digest": report.get("source_digest"),
        "policy_sha256": report.get("policy_sha256"),
        "hypothesis_family_trials": 64,
        "source_months_verified": 36,
        "descriptive_hypotheses_reviewed": len(details),
        "decision_counts": dict(counts),
        "status": "SOURCE_VERIFIED_RESEARCH_BUT_NO_INDEPENDENT_STATISTICAL_ACCEPTANCE",
        "missing_proofs": list(BLOCKERS),
        "comparison_note": ("Wilson intervals use reported non-overlapping event hit counts; "
                            "serial dependence, shared controls, variant correlation and "
                            "selection are NOT corrected. No alpha claim is permitted."),
        "PBO": None, "DSR": None, "white_reality_check": None, "monte_carlo": None,
        "read_only_research": True, "paper_eligible": False,
        "live_allowed": False, "automatic_promotion_allowed": False,
        "hypotheses": details,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = review(json.loads(args.report.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                   allow_nan=False) + "\n", encoding="utf-8")
    print("V7_STATISTICAL_EVIDENCE", result["symbol"], result["status"],
          "missing_proofs", len(result["missing_proofs"]), "NO_PAPER_LIVE")


if __name__ == "__main__":
    main()
