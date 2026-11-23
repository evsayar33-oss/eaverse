"""V9 Eaverse-native causal evidence gate. Offline, deterministic, fail-closed.

The research proposal queue is NOT an alpha backtest. These functions check
genuine content hashes, market identities, release vintages and independent
evidence before any proposed experiment is allowed to use a dataset.
No order, PAPER or LIVE permission can be issued by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "eaverse.pit_integrity.v9"
RIGHTS = {"OFFICIAL_PUBLIC_RESEARCH", "VERIFIED_PERSONAL_RESEARCH"}
NATIVE_FAMILIES = {
    "spot_taker_base_volume", "venue_tape", "venue_ohlcv",
    "l2_orderbook_sequence", "funding_settlement", "open_interest_ratios",
}
MIN_HISTORY_DAYS = 730


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("PIT_TIME_NOT_OFFSET_AWARE")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise ValueError("PIT_TIME_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("PIT_TIME_NOT_OFFSET_AWARE")
    return parsed.astimezone(timezone.utc)


def verified_blob(root, relative_path, expected_sha):
    """Requires real file bytes, not an asserted hash or string status."""
    root = Path(root).resolve()
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("ARCHIVE_PATH_REQUIRED")
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError("ARCHIVE_PATH_ESCAPE_OR_MISSING")
    if not (isinstance(expected_sha, str) and len(expected_sha) == 64
            and all(c in "0123456789abcdef" for c in expected_sha)):
        raise ValueError("ARCHIVE_SHA256_INVALID")
    h = hashlib.sha256()
    with candidate.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != expected_sha:
        raise ValueError("ARCHIVE_SHA256_MISMATCH")
    return {"sha256": expected_sha, "bytes": candidate.stat().st_size}


def entity_asof(history, *, identity, decision_at):
    """Full historical symbol/issuer identity; no current-ticker backfill."""
    t = stamp(decision_at)
    if not isinstance(history, list) or not history:
        raise ValueError("NO_DELIST_AWARE_ENTITY_HISTORY")
    timeline = []
    for version in history:
        if version.get("stable_id") != identity or not version.get("symbol"):
            raise ValueError("ENTITY_PERMANENT_ID_MISMATCH")
        begin = stamp(version["valid_from_utc"])
        finish = stamp(version["valid_to_utc"]) if version.get("valid_to_utc") else None
        if finish is not None and finish <= begin:
            raise ValueError("ENTITY_INVALID_LIFESPAN")
        if not version.get("original_listing_and_symbol_publication_verified", False):
            raise ValueError("ENTITY_EFFECTIVE_DATE_NOT_PIT_PUBLICATION")
        if version.get("delisted") and finish is None:
            raise ValueError("DELIST_DATE_NOT_RECORDED")
        timeline.append((begin, finish, version))
    timeline.sort(key=lambda v: v[0])
    for left, right in zip(timeline, timeline[1:]):
        if left[1] is None or left[1] > right[0]:
            raise ValueError("ENTITY_OVERLAPPING_VINTAGES")
    matching = [v for begin, finish, v in timeline
                if begin <= t and (finish is None or t < finish)]
    if len(matching) != 1:
        raise ValueError("ENTITY_NOT_LISTED_AT_DECISION_TIME")
    return matching[0]


def revision_asof(revisions, *, observation_at, decision_at):
    """Select publication-visible source vintage, never the latest revision."""
    observation = stamp(observation_at)
    decision = stamp(decision_at)
    eligible = []
    versions = set()
    for row in revisions:
        vintage = row.get("revision_id")
        if not isinstance(vintage, str) or not vintage or vintage in versions:
            raise ValueError("DUPLICATE_OR_MISSING_REVISION")
        versions.add(vintage)
        published = stamp(row["revision_published_at_utc"])
        if published < observation:
            raise ValueError("REVISION_BEFORE_OBSERVATION")
        if not row.get("original_publication_evidence_verified"):
            raise ValueError("UNVERIFIED_REVISION_PUBLICATION_CLOCK")
        if published <= decision:
            eligible.append((published, vintage, row))
    if not eligible:
        raise ValueError("NO_REVISION_VISIBLE_AT_DECISION")
    eligible.sort(key=lambda r: (r[0], r[1]))
    return eligible[-1][2]


def validate_native_book(snapshot, deltas):
    """Native sequence-chain L2 replay; cross-venue stitching forbidden."""
    venue, instrument = snapshot.get("venue"), snapshot.get("instrument")
    if not venue or not instrument or snapshot.get("sequence") is None:
        raise ValueError("BOOK_SNAPSHOT_IDENTITY_REQUIRED")
    sequence = int(snapshot["sequence"])
    if sequence < 0:
        raise ValueError("BOOK_SEQUENCE_INVALID")
    state = {"bids": {}, "asks": {}}
    def apply(updates):
        for change in updates:
            side = change.get("side")
            if side not in state:
                raise ValueError("BOOK_SIDE_INVALID")
            price = float(change["price"])
            qty = float(change["quantity"])
            if not (math.isfinite(price) and math.isfinite(qty) and price > 0 and qty >= 0):
                raise ValueError("BOOK_PRICE_OR_QUANTITY_INVALID")
            if qty == 0:
                state[side].pop(price, None)
            else:
                state[side][price] = qty
        if not state["bids"] or not state["asks"] or max(state["bids"]) >= min(state["asks"]):
            raise ValueError("BOOK_CROSSED_OR_INCOMPLETE")
    apply(snapshot["levels"])
    previous_at = stamp(snapshot["published_at_utc"])
    for delta in deltas:
        if delta.get("venue") != venue or delta.get("instrument") != instrument:
            raise ValueError("CROSS_VENUE_BOOK_SPLICE_FORBIDDEN")
        if delta.get("first_sequence") != sequence + 1 or delta.get("last_sequence") != sequence + 1:
            raise ValueError("BOOK_GAP_OR_REORDER")
        published = stamp(delta["published_at_utc"])
        if published < previous_at:
            raise ValueError("BOOK_CLOCK_REVERSED")
        sequence += 1
        previous_at = published
        apply(delta["levels"])
    return {"status": "NATIVE_SEQUENCE_CONTIGUOUS", "venue": venue,
            "instrument": instrument, "last_sequence": sequence,
            "last_public_at_utc": previous_at.isoformat(),
            "best_bid": max(state["bids"]), "best_ask": min(state["asks"]),
            "l2_not_an_execution_fill": True}


def independent_confirmation(primary, secondary, *, decision_at, max_lag_seconds=86400):
    """Different API endpoints for the same filing are NOT independent."""
    deadline = stamp(decision_at)
    if primary.get("economic_event_id") != secondary.get("economic_event_id"):
        raise ValueError("DIFFERENT_ECONOMIC_EVENTS")
    if primary.get("asset_identity") != secondary.get("asset_identity"):
        raise ValueError("CROSS_ASSET_CORROBORATION")
    p_root, s_root = primary.get("origin_lineage_root"), secondary.get("origin_lineage_root")
    if not p_root or not s_root or p_root == s_root:
        raise ValueError("SHARED_UNDERLYING_INFORMATION_NOT_INDEPENDENT")
    if not primary.get("publisher") or not secondary.get("publisher") or primary["publisher"] == secondary["publisher"]:
        raise ValueError("SAME_PUBLISHER_NOT_INDEPENDENT")
    a, b = stamp(primary["public_at_utc"]), stamp(secondary["public_at_utc"])
    if max(a, b) > deadline or abs((a-b).total_seconds()) > max_lag_seconds:
        raise ValueError("CORROBORATION_NOT_AVAILABLE_AT_DECISION")
    return {"status": "INDEPENDENT_SOURCE_CORROBORATED",
            "economic_event_id": primary["economic_event_id"],
            "distinct_information_roots": 2,
            "independent_quotes_are_not_fused_or_filled": True}


def validate_study_contract(study, *, decision_at):
    """Purged, forward-only study declaration; never inspect sealed outcomes."""
    if study.get("protocol_sha256") != digest(study.get("protocol", {})):
        raise ValueError("UNSIGNED_STUDY_PROTOCOL")
    reg = stamp(study["registered_at_utc"])
    if reg > stamp(decision_at):
        raise ValueError("STUDY_REGISTERED_AFTER_DECISION")
    trials = study.get("familywise_trials")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise ValueError("NO_MULTIPLE_TESTING_FAMILY")
    folds = study.get("folds", [])
    if [x.get("name") for x in folds] != ["discovery", "validation", "untouched_holdout"]:
        raise ValueError("CHRONOLOGICAL_FOLDS_REQUIRED")
    prior_end = None
    for fold in folds:
        start, stop = stamp(fold["start_utc"]), stamp(fold["end_utc"])
        if start >= stop or (prior_end and start <= prior_end + timedelta(days=study.get("embargo_days", 0))):
            raise ValueError("PURGED_EMBARGO_FOLD_OVERLAP")
        prior_end = stop
    if (not isinstance(study.get("embargo_days"), int) or study["embargo_days"] < 1
        or study.get("venue_native_costs_verified") is not True
        or study.get("matched_controls_verified") is not True):
        raise ValueError("COST_CONTROL_EMBARGO_NOT_VERIFIED")
    if study.get("holdout_is_sealed") is not True or "holdout_results" in study:
        raise ValueError("HOLDOUT_PREMATURELY_EXPOSED")
    return {"status": "PROTOCOL_PREREGISTERED_HOLDOUT_SEALED",
            "familywise_trials": trials, "embargo_days": study["embargo_days"],
            "holdout_evaluated": False, "prospective_evidence": False}


def preflight(design, bundle=None, *, root=None):
    """One mandatory path from V8 designs to real source-backed research.

    A bundle must be populated from audited native archives, not guesses.
    Passing this gate only authorizes *research*, never alpha/PAPER/LIVE.
    """
    base = {"schema": SCHEMA, "hypothesis_id": design["id"],
            "status": "BLOCKED_NO_AUDITED_PIT_BUNDLE",
            "research_ready": False, "holdout_opened": False,
            "prospective_validated": False, "paper_eligible": False,
            "live_allowed": False}
    if bundle is None:
        return base
    try:
        if root is None or bundle.get("design_id") != design["id"]:
            raise ValueError("UNBOUND_OR_MISSING_EVIDENCE_ROOT")
        if bundle.get("asset") != design["asset"] or bundle.get("market") != design["market"]:
            raise ValueError("EVIDENCE_ASSET_MISMATCH")
        decision = bundle["decision_at_utc"]
        stamp(decision)
        history = bundle["history"]
        if (history.get("full_delist_price_history_verified") is not True
            or history.get("corporate_actions_asof_verified") is not True
            or history.get("full_session_coverage_verified") is not True):
            raise ValueError("SURVIVORSHIP_OR_CORPORATE_ACTION_GAP")
        if (stamp(history["end_utc"]) - stamp(history["start_utc"])).days < MIN_HISTORY_DAYS:
            raise ValueError("INSUFFICIENT_LONG_HISTORY")
        if stamp(history["end_utc"]) > stamp(decision):
            raise ValueError("HISTORY_CONTAINS_FUTURE")
        verified_blob(root, history["archive_path"], history["raw_sha256"])
        stable = bundle["asset_identity"]
        entity_asof(bundle["entity_history"], identity=stable, decision_at=decision)
        from . import source_council
        sources = {x["id"]: x for x in source_council.SOURCES}
        evidence_by_id = {x["source_id"]: x for x in bundle["source_receipts"]}
        if len(evidence_by_id) != len(bundle["source_receipts"]):
            raise ValueError("DUPLICATE_SOURCE_RECEIPTS")
        if not set(design["source_ids"]).issubset(evidence_by_id):
            raise ValueError("DESIGN_SOURCE_RECEIPTS_MISSING")
        for source_id, row in evidence_by_id.items():
            reference = sources[source_id]
            if row["market"] != design["market"] or row["asset_identity"] != stable:
                raise ValueError("SOURCE_MARKET_OR_IDENTITY_MISMATCH")
            if row["venue"] != reference["venue"] or row["feature_family"] != reference["feature_family"]:
                raise ValueError("VENUE_OR_FEATURE_FAMILY_SUBSTITUTED")
            if (row.get("research_rights") not in RIGHTS
                or row.get("legal_automation_verified") is not True
                or row.get("official_publication_verified") is not True
                or row.get("vintage_history_verified") is not True):
                raise ValueError("RIGHTS_CLOCK_OR_VINTAGE_UNVERIFIED")
            if stamp(row["official_publication_at_utc"]) > stamp(decision):
                raise ValueError("UNPUBLISHED_SOURCE_AT_DECISION")
            if stamp(row["first_seen_at_utc"]) < stamp(row["official_publication_at_utc"]):
                raise ValueError("BACKDATED_ACTUAL_FIRST_SEEN")
            if not isinstance(row.get("revision_id"), str) or not row["revision_id"]:
                raise ValueError("REVISION_ID_REQUIRED")
            revision_asof(row["revisions"],
                          observation_at=row["observation_at_utc"],
                          decision_at=decision)
            verified_blob(root, row["archive_path"], row["raw_sha256"])
            if row["revision_id"] != revision_asof(
                    row["revisions"], observation_at=row["observation_at_utc"],
                    decision_at=decision)["revision_id"]:
                raise ValueError("REVISION_USED_BEFORE_PUBLISHED")
            if reference["feature_family"] in NATIVE_FAMILIES:
                if (row.get("exchange_native") is not True or
                    row.get("native_gap_count") != 0 or
                    not row.get("native_instrument")):
                    raise ValueError("NATIVE_VENUE_TAPE_INCOMPLETE")
                if source_id in design["source_ids"] and row["native_instrument"] != bundle["asset"]:
                    raise ValueError("NATIVE_INSTRUMENT_MISMATCH")
                if source_id not in design["source_ids"] and row.get("cross_venue_identity_mapping_verified") is not True:
                    raise ValueError("CORROBORATION_CROSS_VENUE_IDENTITY_NOT_VERIFIED")
        if (not bundle.get("primary_event") or not bundle.get("independent_confirmation")):
            raise ValueError("INDEPENDENT_SOURCE_PROOF_MISSING")
        primary = bundle["primary_event"]
        confirming = bundle["independent_confirmation"]
        if primary.get("source_id") not in design["source_ids"]:
            raise ValueError("PRIMARY_NOT_REGISTERED_IN_DESIGN")
        if confirming.get("source_id") not in evidence_by_id or confirming["source_id"] == primary["source_id"]:
            raise ValueError("CONFIRMATION_NOT_AN_AUDITED_INDEPENDENT_SOURCE")
        for event in (primary, confirming):
            receipt = evidence_by_id[event["source_id"]]
            if (event.get("origin_lineage_root") != receipt.get("origin_lineage_root")
                or event.get("publisher") != receipt.get("venue")
                or stamp(event["public_at_utc"]) < stamp(receipt["official_publication_at_utc"])):
                raise ValueError("EVENT_NOT_LINKED_TO_AUDITED_SOURCE")
        independent_confirmation(primary, confirming, decision_at=decision)
        for receipt in bundle["source_receipts"]:
            if receipt.get("feature_family") == "l2_orderbook_sequence":
                proof = bundle.get("native_l2")
                if not proof or proof["snapshot"]["venue"] != receipt["venue"]:
                    raise ValueError("L2_NATIVE_SEQUENCE_NOT_PROVEN")
                validated = validate_native_book(proof["snapshot"], proof["deltas"])
                if validated["instrument"] != receipt["native_instrument"]:
                    raise ValueError("L2_INSTRUMENT_MISMATCH")
                if stamp(validated["last_public_at_utc"]) > stamp(decision):
                    raise ValueError("FUTURE_L2_DELTA_IN_EVENT")
        contract = validate_study_contract(bundle["study"], decision_at=decision)
        return {**base, "status": "PIT_RESEARCH_READY_NOT_ALPHA",
                "research_ready": True, "contract": contract,
                "evidence_digest": digest({
                    "design": design["id"], "history": history["raw_sha256"],
                    "sources": sorted((x["source_id"], x["raw_sha256"], x["revision_id"])
                                      for x in bundle["source_receipts"]),
                    "study": bundle["study"]["protocol_sha256"],
                })}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        # No exception can elevate a flawed bundle. Do not expose vendor data.
        reason = str(exc)
        allowed = reason if reason.isascii() and reason.replace("_", "").isalnum() and len(reason) <= 90 else type(exc).__name__
        return {**base, "status": "BLOCKED_FAIL_CLOSED",
                "reason": allowed}
