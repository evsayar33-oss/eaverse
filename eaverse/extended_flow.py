"""Eaverse V7: multi-asset and deeper historical as-of flow research.

Real Binance Vision spot hourly archives, independently SHA-verified.
Eight registered economic event families, research-only and NO PAPER/LIVE.
Existing Eaverse production/PAPER and previous V5 hypotheses are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import early_flow, pit_binance_pilot

SCHEMA = "eaverse.extended_flow.v7"
SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT",
)
MONTHS = tuple(f"{year}-{month:02d}" for year in (2023, 2024, 2025) for month in range(1, 13))
FAMILIES = early_flow.FAMILIES + (
    "flow_absorption", "flow_regime_flip",
    "compressed_flow", "flow_persistence",
)
HORIZONS = early_flow.HORIZONS
DIRECTIONS = early_flow.DIRECTIONS
MIN_CASES = early_flow.MIN_CASES
NUM_HYPOTHESES = len(FAMILIES) * len(HORIZONS) * len(DIRECTIONS)
NO_PROMOTION = {"paper_eligible": False, "live_allowed": False, "forward_validated": False}
RESEARCH_RIGHTS = "PERSONAL_NONCOMMERCIAL_RESEARCH_ONLY"
MIN_HOURS = 24 * 365 * 3


def hash_obj(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _verified_months(blobs, symbol):
    if symbol not in SYMBOLS:
        raise ValueError("UNREGISTERED_RESEARCH_ASSET")
    if tuple(sorted(blobs)) != MONTHS:
        raise ValueError("HISTORICAL_MONTH_COVERAGE_NOT_CONTIGUOUS")
    rows = []
    for month in MONTHS:
        rows.extend(early_flow._hourly_csv(blobs[month], symbol, month))
    timestamps = pd.DatetimeIndex([r[0] for r in rows], tz="UTC")
    if timestamps.has_duplicates or not timestamps.is_monotonic_increasing:
        raise ValueError("DUPLICATE_OR_OUT_OF_ORDER_HOURLY_BARS")
    expected = pd.date_range("2023-01-01T00:00:00Z", "2025-12-31T23:00:00Z", freq="h")
    if not timestamps.equals(expected):
        raise ValueError("MISSING_EXTRA_OR_NONHOURLY_SOURCE_ROWS")
    if len(rows) < MIN_HOURS:
        raise ValueError("INSUFFICIENT_THREE_YEAR_HISTORY")
    df = pd.DataFrame(
        [r[1:] for r in rows], index=timestamps,
        columns=("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base"),
        dtype=float,
    )
    return df


def download(symbol, fetcher=pit_binance_pilot.fetch_bytes):
    """No venue switching: a failed Binance file blocks that symbol's experiment."""
    if symbol not in SYMBOLS:
        raise ValueError("UNREGISTERED_RESEARCH_ASSET")
    blobs, manifests = {}, []
    for month in MONTHS:
        file = f"{symbol}-1h-{month}.zip"
        uri = f"{pit_binance_pilot.BASE_URL}/{symbol}/1h/{file}"
        zipped = fetcher(uri, pit_binance_pilot.MAX_ZIP_BYTES)
        checksum = fetcher(uri + ".CHECKSUM", pit_binance_pilot.MAX_CHECKSUM_BYTES)
        original = pit_binance_pilot.unpack_verified(zipped, checksum, file)
        blobs[month] = original
        manifests.append({
            "month": month, "venue": "BINANCE_SPOT", "asset": symbol,
            "zip_sha256": hashlib.sha256(zipped).hexdigest(),
            "csv_sha256": hashlib.sha256(original).hexdigest(),
            "checksum_verified": True, "source_url": uri,
        })
    return _verified_months(blobs, symbol), manifests


def event_mask(bars, feat, family, direction):
    """No future feature enters an event. Current CLOSE is only known after that bar."""
    if family in early_flow.FAMILIES:
        return early_flow.flags(feat, family, direction)
    imbal = direction * feat.imbalance
    ratio = feat.turnover_ratio
    candle = direction * feat.bar_return
    quiet = feat.quiet
    if family == "flow_absorption":
        mask = (imbal >= .10) & (candle <= 0) & (ratio >= 1.15) & quiet
    elif family == "flow_regime_flip":
        mask = (imbal >= .07) & (direction * feat.flow_24h_prior_mean <= -.04) & (ratio >= 1) & quiet
    elif family == "compressed_flow":
        returns = bars.close.pct_change()
        # Both volatility references exclude the current signal candle.
        near = returns.shift(1).rolling(24, min_periods=24).std()
        far = returns.shift(1).rolling(168, min_periods=168).std()
        mask = (near < .8 * far) & (imbal >= .08) & (ratio >= 1.15) & quiet
    elif family == "flow_persistence":
        # The preceding three completed hours must all agree directionally;
        # today's bar is not used to construct its own prior baseline.
        prior = (direction * feat.imbalance).shift(1).rolling(3, min_periods=3).min()
        mask = (prior >= .04) & (imbal >= .07) & (ratio >= 1.0) & quiet
    else:
        raise ValueError("UNKNOWN_RESEARCH_EVENT_FAMILY")
    return mask.fillna(False).to_numpy(dtype=bool)


def _preselection(study, direction):
    """Screen solely on discovery and validation; do not inspect holdout outcomes."""
    early = (study["discovery"], study["validation"])
    coverage = all(
        x["minimum_sample_satisfied"] and x["mean_matched_lift_bps"] is not None
        for x in early
    )
    if not coverage:
        return False, "INSUFFICIENT_CASES_OR_PRIOR_MATCHED_CONTROLS"
    if direction == 1:
        passing = all(
            x["mean_case_after_stressed_fees_bps"] is not None
            and x["mean_case_after_stressed_fees_bps"] > 0
            and x["mean_matched_lift_bps"] > 0
            for x in early
        )
    else:
        passing = all(
            x["mean_signed_gross_bps"] is not None
            and x["mean_signed_gross_bps"] > 0
            and x["mean_matched_lift_bps"] > 0
            for x in early
        )
    return passing, ("RESEARCH_PRESELECTION_PASSED" if passing
                     else "NO_DISCOVERY_AND_VALIDATION_REPLICATION")


def _next_work(results, symbol):
    """Deterministic evidence-driven *research proposals*, not trades or parameter mining.

    Must not use any untouched holdout metric for next hypothesis selection.
    """
    failures = Counter(x["preselection_status"] for x in results)
    proposals = [
        {"source_family": "DERIVATIVES_FUNDING", "event": "funding_surprise_after_publication",
         "why": "Independent leverage information, not another OHLC threshold",
         "status": "SOURCE_AND_PIT_SCHEMA_REQUIRED"},
        {"source_family": "INDEPENDENT_VENUE_TAPE", "event": "cross_venue_aggressor_divergence",
         "why": "Compare exchange-specific trade imbalances without mixing instruments",
         "status": "VENUE_HISTORY_AND_SYMBOL_UNIT_CHECK_REQUIRED"},
        {"source_family": "ORDERBOOK_L2", "event": "replenishment_before_price_change",
         "why": "Snapshot plus ordered updates and sequence-gap test required",
         "status": "HISTORICAL_SEQUENCE_AND_LICENSE_REQUIRED"},
    ]
    if failures["INSUFFICIENT_CASES_OR_PRIOR_MATCHED_CONTROLS"]:
        proposals.insert(0, {
            "source_family": "EXISTING_EXCHANGE_SPOT_TAPE",
            "event": "more_pre_registered_independent_cases",
            "why": "Insufficient event/control counts in discovery or validation",
            "status": "DATA_COLLECTION_REQUIRED",
        })
    for item in proposals:
        item["id"] = hash_obj([SCHEMA, symbol, item["source_family"], item["event"]])
        item["paper_eligible"] = False
    return proposals


def analyze(bars, manifests, symbol, captured_utc=None):
    if symbol not in SYMBOLS:
        raise ValueError("UNREGISTERED_RESEARCH_ASSET")
    if not bars.index.equals(pd.date_range(
        "2023-01-01T00:00:00Z", "2025-12-31T23:00:00Z", freq="h"
    )):
        raise ValueError("SOURCE_HOURS_MUST_MATCH_PREREGISTERED_CALENDAR")
    if (len(manifests) != len(MONTHS) or
        [p.get("month") for p in manifests] != list(MONTHS) or
        not all(p.get("checksum_verified") is True and
                p.get("asset") == symbol and p.get("venue") == "BINANCE_SPOT"
                for p in manifests)):
        raise ValueError("MISSING_OR_CROSS_VENUE_SOURCE_PROVENANCE")
    feat = early_flow.asof_features(bars)
    splits = early_flow.folds(len(bars))
    reports = []
    for family in FAMILIES:
        for direction in DIRECTIONS:
            mask = event_mask(bars, feat, family, direction)
            for horizon in HORIZONS:
                spec = {
                    "family": family, "direction": direction, "horizon_hours": horizon,
                    "start": "2023-01-01", "end": "2025-12-31",
                    "entry": "NEXT_HOURLY_OPEN_AFTER_CLOSED_SIGNAL",
                    "source": "BINANCE_SPOT_1H",
                    "base_roundtrip_cost_bps": early_flow.BASE_ROUND_TRIP_BPS,
                    "stress_roundtrip_cost_bps": early_flow.ROUND_TRIP_BPS,
                    "folds": "50_25_25_EMBARGO_25H",
                }
                # This call cannot read holdout when open_holdout=False.
                screen = early_flow._evaluate_one(
                    bars, feat, family, direction, horizon, splits,
                    active_override=mask, open_holdout=False,
                )
                eligible, label = _preselection(screen, direction)
                if eligible:
                    study = early_flow._evaluate_one(
                        bars, feat, family, direction, horizon, splits,
                        active_override=mask, open_holdout=True,
                    )
                    hold = study["untouched_holdout"]
                    complete = (hold["minimum_sample_satisfied"]
                                and hold["mean_matched_lift_bps"] is not None)
                    if not complete:
                        decision = "HOLDOUT_INSUFFICIENT_INDEPENDENT_CASES"
                    elif direction == 1 and (
                        hold["mean_case_after_stressed_fees_bps"] > 0
                        and hold["mean_matched_lift_bps"] > 0
                    ):
                        decision = "RETROSPECTIVE_REPLICATION_FOR_FORWARD_REVIEW_ONLY"
                    elif direction == -1 and (
                        hold["mean_signed_gross_bps"] > 0
                        and hold["mean_matched_lift_bps"] > 0
                    ):
                        decision = "DOWNSIDE_RISK_ONLY_NOT_EXECUTABLE_SPOT_SHORT"
                    else:
                        decision = "INDEPENDENT_HOLDOUT_FAILED"
                else:
                    study = screen
                    decision = label
                reports.append({
                    "id": hash_obj([SCHEMA, symbol, spec]),
                    "spec": spec,
                    "preselection_status": label,
                    "holdout_opened_after_preselection": eligible,
                    "folds": study,
                    "decision": decision,
                    "direction_is_executable_spot_short": False,
                    "paper_eligible": False,
                    "forward_validated": False,
                    "trade_authorized": False,
                })
    if len(reports) != NUM_HYPOTHESES or len({r["id"] for r in reports}) != len(reports):
        raise ValueError("RESEARCH_DESIGN_IDS_NOT_UNIQUE")
    policy = {
        "schema": SCHEMA, "symbols": list(SYMBOLS), "months": list(MONTHS),
        "families": list(FAMILIES), "directions": list(DIRECTIONS),
        "horizons": list(HORIZONS), "fee_bps_stress": early_flow.ROUND_TRIP_BPS,
        "max_lag": "past-only", "holdout_sealed": True,
        "multiple_testing_count_per_asset": NUM_HYPOTHESES,
        "familywise_error_alpha_verified": False,
        "data_rights": RESEARCH_RIGHTS,
    }
    report = {
        "schema": SCHEMA, "symbol": symbol,
        "captured_utc": captured_utc or datetime.now(timezone.utc).isoformat(),
        "market": "CRYPTO", "venue": "BINANCE_SPOT",
        "hours": len(bars), "archive_months": len(manifests),
        "source_zip_provenance": manifests,
        "source_digest": hash_obj([
            (p["month"], p["zip_sha256"], p["csv_sha256"]) for p in manifests
        ]),
        "policy": policy, "policy_sha256": hash_obj(policy),
        "hypothesis_count": NUM_HYPOTHESES,
        "decisions": dict(Counter(r["decision"] for r in reports)),
        "results": reports,
        "future_research_proposals": _next_work(reports, symbol),
        "retrospective_history_does_not_prove_publication_vintages": True,
        "independent_venue_verified": False,
        "multiple_testing_corrected_alpha_verified": False,
        **NO_PROMOTION,
    }
    return report


def append_ledger(path, report):
    """Immutable, per-asset SHA-linked *aggregate* detail, never raw trade tape."""
    path = Path(path)
    old = []
    previous = "GENESIS"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            digest = hash_obj({k: v for k, v in value.items() if k != "sha256"})
            if value.get("prev_sha256") != previous or value.get("sha256") != digest:
                raise ValueError("V7_SHA_CHAIN_TAMPER_OR_CORRUPTION")
            previous = digest
            old.append(value)
    if not all(report.get(k) is False for k in NO_PROMOTION):
        raise ValueError("NEW_RESEARCH_CANNOT_PROMOTE_TRADES")
    if len(report.get("results", [])) != NUM_HYPOTHESES:
        raise ValueError("INCOMPLETE_EXPERIMENT_EVIDENCE")
    stamp = (report["source_digest"], report["policy_sha256"])
    if old and (old[-1]["source_digest"], old[-1]["policy_sha256"]) == stamp:
        if old[-1]["study_digest"] != hash_obj(report["results"]):
            raise ValueError("SAME_SOURCE_POLICY_RESULTS_CHANGED")
        return {"status": "UNCHANGED_HISTORY_NOT_NEW_FORWARD_EVIDENCE", "records": len(old)}
    record = {
        "schema": "eaverse.extended_flow_evidence.v7",
        "captured_utc": report["captured_utc"],
        "asset": report["symbol"],
        "source_digest": report["source_digest"],
        "policy_sha256": report["policy_sha256"],
        "source_revision": bool(old and old[-1]["source_digest"] != report["source_digest"]),
        "study_digest": hash_obj(report["results"]),
        "full_hypothesis_studies": report["results"],
        "source_zip_provenance": report["source_zip_provenance"],
        "future_research_proposals": report["future_research_proposals"],
        "decision_counts": report["decisions"],
        "paper_eligible": False,
        "forward_validated": False,
        "prev_sha256": previous,
    }
    record["sha256"] = hash_obj(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    return {"status": "APPENDED_HISTORICAL_RESEARCH_ONLY", "records": len(old)+1}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", choices=SYMBOLS, required=True)
    p.add_argument("--out", type=Path, default=Path("out/extended_flow"))
    p.add_argument("--ledger", type=Path)
    args = p.parse_args()
    bars, manifests = download(args.symbol)
    report = analyze(bars, manifests, args.symbol)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.ledger:
        report["ledger"] = append_ledger(args.ledger, report)
    (args.out / "report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        "V7_REAL_SOURCE_RESEARCH", args.symbol,
        "hours", report["hours"], "archives", report["archive_months"],
        "hypotheses", report["hypothesis_count"],
        "decisions", report["decisions"], "paper", report["paper_eligible"],
    )


if __name__ == "__main__":
    main()
