"""Adversarial V9 tests: never grant PAPER, avoid leak and synthetic source joins."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from eaverse import causal_discovery as designs
from eaverse import research_integrity as gate


def utc(day):
    return day + "T12:00:00+00:00"


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.design = next(d for d in designs.designs()
                           if d["market"] == "CRYPTO" and d["asset"] == "BTCUSDT"
                           and d["event_features"] == ["aggressor_imbalance"])
        self.bundle = self._bundle()

    def archive(self, file, content):
        (self.root / file).write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    def receipt(self, source_id, venue, family, file, root, instrument, mapping=False):
        raw_sha = self.archive(file, (source_id + ":VALID_ARCHIVE_BYTES").encode())
        return {
            "source_id": source_id, "market": "CRYPTO",
            "asset_identity": "CRYPTO:BTC", "venue": venue,
            "feature_family": family, "research_rights": "OFFICIAL_PUBLIC_RESEARCH",
            "legal_automation_verified": True,
            "official_publication_verified": True, "vintage_history_verified": True,
            "exchange_native": True, "native_gap_count": 0,
            "native_instrument": instrument,
            "cross_venue_identity_mapping_verified": mapping,
            "origin_lineage_root": root,
            "archive_path": file, "raw_sha256": raw_sha,
            "observation_at_utc": utc("2023-03-01"),
            "official_publication_at_utc": utc("2023-03-05"),
            "first_seen_at_utc": utc("2023-03-06"),
            "revision_id": "v1",
            "revisions": [{
                "revision_id": "v1", "revision_published_at_utc": utc("2023-03-05"),
                "original_publication_evidence_verified": True,
            }],
        }

    def _bundle(self):
        history_sha = self.archive("price_history.bin", b"RESEARCH_PRICE_EVIDENCE")
        protocol = {"hypothesis_id": self.design["id"], "after_cost": True,
                    "holdout_preregistered": True}
        return {
            "design_id": self.design["id"], "market": "CRYPTO", "asset": "BTCUSDT",
            "asset_identity": "CRYPTO:BTC", "decision_at_utc": utc("2023-12-10"),
            "history": {"start_utc": utc("2019-01-01"),
                        "end_utc": utc("2023-12-01"),
                        "full_delist_price_history_verified": True,
                        "corporate_actions_asof_verified": True,
                        "full_session_coverage_verified": True,
                        "archive_path": "price_history.bin",
                        "raw_sha256": history_sha},
            "entity_history": [{
                "stable_id": "CRYPTO:BTC", "symbol": "BTCUSDT",
                "valid_from_utc": utc("2017-01-01"), "valid_to_utc": None,
                "delisted": False,
                "original_listing_and_symbol_publication_verified": True,
            }],
            "source_receipts": [
                self.receipt("BINANCE_SPOT_VISION", "BINANCE_SPOT",
                             "spot_taker_base_volume", "binance.bin",
                             "binance_native_tape", "BTCUSDT"),
                self.receipt("COINBASE_DAILY", "COINBASE_SPOT_USD",
                             "venue_ohlcv", "coinbase.bin",
                             "coinbase_independent_feed", "BTC-USD", True),
            ],
            "primary_event": {
                "economic_event_id": "BTC:liquidity-shock:1",
                "asset_identity": "CRYPTO:BTC",
                "source_id": "BINANCE_SPOT_VISION",
                "origin_lineage_root": "binance_native_tape",
                "publisher": "BINANCE_SPOT",
                "public_at_utc": utc("2023-03-05"),
            },
            "independent_confirmation": {
                "economic_event_id": "BTC:liquidity-shock:1",
                "asset_identity": "CRYPTO:BTC",
                "source_id": "COINBASE_DAILY",
                "origin_lineage_root": "coinbase_independent_feed",
                "publisher": "COINBASE_SPOT_USD",
                "public_at_utc": utc("2023-03-05"),
            },
            "study": {
                "protocol": protocol, "protocol_sha256": gate.digest(protocol),
                "registered_at_utc": utc("2023-12-02"),
                "familywise_trials": 148, "embargo_days": 7,
                "venue_native_costs_verified": True,
                "matched_controls_verified": True,
                "holdout_is_sealed": True,
                "folds": [
                    {"name": "discovery", "start_utc": utc("2020-01-01"),
                     "end_utc": utc("2021-01-01")},
                    {"name": "validation", "start_utc": utc("2021-02-01"),
                     "end_utc": utc("2022-01-01")},
                    {"name": "untouched_holdout", "start_utc": utc("2022-03-01"),
                     "end_utc": utc("2023-11-01")},
                ],
            }
        }

    def check_blocked(self, b, expected):
        result = gate.preflight(self.design, b, root=self.root)
        self.assertEqual(result["status"], "BLOCKED_FAIL_CLOSED", result)
        self.assertEqual(result["reason"], expected, result)
        self.assertFalse(result["research_ready"])
        self.assertFalse(result["paper_eligible"])
        self.assertFalse(result["live_allowed"])

    def test_design_with_no_real_evidence_is_blocked(self):
        r = gate.preflight(self.design)
        self.assertEqual(r["status"], "BLOCKED_NO_AUDITED_PIT_BUNDLE")
        self.assertFalse(r["paper_eligible"])

    def test_native_source_and_independent_venue_are_structurally_accepted(self):
        result = gate.preflight(self.design, self.bundle, root=self.root)
        self.assertEqual(result["status"], "PIT_RESEARCH_READY_NOT_ALPHA", result)
        self.assertEqual(result["contract"]["familywise_trials"], 148)
        self.assertFalse(result["contract"]["holdout_evaluated"])
        self.assertFalse(result["prospective_validated"])
        self.assertFalse(result["paper_eligible"])
        self.assertFalse(result["live_allowed"])
        self.assertEqual(result["evidence_digest"],
                         gate.preflight(self.design, self.bundle, root=self.root)["evidence_digest"])

    def test_archive_hash_and_path_enforced(self):
        corrupt = copy.deepcopy(self.bundle)
        (self.root / "binance.bin").write_bytes(b"tampered")
        self.check_blocked(corrupt, "ARCHIVE_SHA256_MISMATCH")
        with self.assertRaisesRegex(ValueError, "ARCHIVE_PATH_ESCAPE"):
            gate.verified_blob(self.root, "../outside", "0"*64)

    def test_observation_is_not_publication_and_revision_cannot_leak(self):
        latest = copy.deepcopy(self.bundle)
        latest["source_receipts"][0]["revisions"].append({
            "revision_id": "v2",
            "revision_published_at_utc": utc("2024-01-01"),
            "original_publication_evidence_verified": True})
        latest["source_receipts"][0]["revision_id"] = "v2"
        self.check_blocked(latest, "REVISION_USED_BEFORE_PUBLISHED")
        early = copy.deepcopy(self.bundle)
        early["source_receipts"][0]["first_seen_at_utc"] = utc("2023-02-01")
        self.check_blocked(early, "BACKDATED_ACTUAL_FIRST_SEEN")
        naïve = copy.deepcopy(self.bundle)
        naïve["source_receipts"][0]["official_publication_verified"] = False
        self.check_blocked(naïve, "RIGHTS_CLOCK_OR_VINTAGE_UNVERIFIED")

    def test_delisted_asset_not_replaced_by_today_ticker(self):
        gone = copy.deepcopy(self.bundle)
        gone["entity_history"][0]["delisted"] = True
        gone["entity_history"][0]["valid_to_utc"] = utc("2022-01-01")
        self.check_blocked(gone, "ENTITY_NOT_LISTED_AT_DECISION_TIME")
        no_prices = copy.deepcopy(self.bundle)
        no_prices["history"]["full_delist_price_history_verified"] = False
        self.check_blocked(no_prices, "SURVIVORSHIP_OR_CORPORATE_ACTION_GAP")

    def test_same_upstream_filing_not_independent(self):
        bad = copy.deepcopy(self.bundle)
        bad["independent_confirmation"]["origin_lineage_root"] = "binance_native_tape"
        self.check_blocked(bad, "EVENT_NOT_LINKED_TO_AUDITED_SOURCE")
        bad = copy.deepcopy(self.bundle)
        bad["source_receipts"].pop()
        self.check_blocked(bad, "CONFIRMATION_NOT_AN_AUDITED_INDEPENDENT_SOURCE")
        bad = copy.deepcopy(self.bundle)
        bad["source_receipts"][1]["origin_lineage_root"] = "binance_native_tape"
        bad["independent_confirmation"]["origin_lineage_root"] = "binance_native_tape"
        self.check_blocked(bad, "SHARED_UNDERLYING_INFORMATION_NOT_INDEPENDENT")

    def test_non_native_price_replacement_and_unverified_venue_fail(self):
        b = copy.deepcopy(self.bundle)
        b["source_receipts"][0]["venue"] = "COINBASE_SPOT_USD"
        self.check_blocked(b, "VENUE_OR_FEATURE_FAMILY_SUBSTITUTED")
        b = copy.deepcopy(self.bundle)
        b["source_receipts"][1]["cross_venue_identity_mapping_verified"] = False
        self.check_blocked(b, "CORROBORATION_CROSS_VENUE_IDENTITY_NOT_VERIFIED")

    def test_premature_holdout_and_missing_purged_embargo_fail(self):
        b = copy.deepcopy(self.bundle)
        b["study"]["holdout_results"] = {"fake_alpha": 999}
        self.check_blocked(b, "HOLDOUT_PREMATURELY_EXPOSED")
        b = copy.deepcopy(self.bundle)
        b["study"]["folds"][1]["start_utc"] = utc("2021-01-02")
        self.check_blocked(b, "PURGED_EMBARGO_FOLD_OVERLAP")

    def test_exchange_native_orderbook_gap_and_cross_venue(self):
        snapshot = {"venue": "OKX_EXCHANGE", "instrument": "BTC-USDT",
                    "sequence": 99, "published_at_utc": utc("2023-03-01"),
                    "levels": [{"side": "bids", "price": 25000, "quantity": 1},
                               {"side": "asks", "price": 25001, "quantity": 1}]}
        delta = {"venue": "OKX_EXCHANGE", "instrument": "BTC-USDT",
                 "first_sequence": 100, "last_sequence": 100,
                 "published_at_utc": utc("2023-03-01"),
                 "levels": [{"side": "bids", "price": 25000, "quantity": 2}]}
        ok = gate.validate_native_book(snapshot, [delta])
        self.assertEqual(ok["last_sequence"], 100)
        bad = dict(delta, first_sequence=101)
        with self.assertRaisesRegex(ValueError, "BOOK_GAP_OR_REORDER"):
            gate.validate_native_book(snapshot, [bad])
        with self.assertRaisesRegex(ValueError, "CROSS_VENUE"):
            gate.validate_native_book(snapshot, [dict(delta, venue="BINANCE_SPOT")])
        crossed = copy.deepcopy(delta)
        crossed["levels"] = [{"side": "bids", "price": 25002, "quantity": 1}]
        with self.assertRaisesRegex(ValueError, "BOOK_CROSSED"):
            gate.validate_native_book(snapshot, [crossed])

    def test_no_immutable_or_prospective_proof_promoted(self):
        no_root = gate.preflight(self.design, self.bundle)
        self.assertFalse(no_root["research_ready"])
        self.assertFalse(no_root["paper_eligible"])
        # All generated hypotheses lack signed, source-native PIT evidence.
        blocked = [gate.preflight(x) for x in designs.designs()]
        self.assertEqual(len(blocked), 148)
        self.assertTrue(all(not x["research_ready"] for x in blocked))


if __name__ == "__main__":
    unittest.main()
