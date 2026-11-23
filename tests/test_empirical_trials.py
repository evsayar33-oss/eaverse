"""Empirical V7 CSCV/DSR on genuine aligned source-return paths, never holdout."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from eaverse import empirical_trials, early_flow, extended_flow
from test_extended_flow import fake_bars, manifests


class EmpiricalTrialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = fake_bars()
        cls.report = extended_flow.analyze(
            cls.bars, manifests("BTCUSDT"), "BTCUSDT",
            captured_utc="2026-11-23T09:00:00Z")

    def test_all_non_selected_long_variants_aligned_and_costed(self):
        x = empirical_trials.analyze(self.bars, self.report)
        panel = x["weekly_panel"]
        self.assertEqual(len(panel["variant_ids"]), 32)
        self.assertGreater(len(panel["dates_utc_week_end"]), 60)
        self.assertEqual(len(panel["net_return_paths"]), len(panel["dates_utc_week_end"]))
        self.assertTrue(all(len(row) == 32 for row in panel["net_return_paths"]))
        self.assertEqual(panel["sha256"], empirical_trials.digest(
            {k: v for k, v in panel.items() if k != "sha256"}))
        self.assertFalse(x["paper_eligible"])
        self.assertFalse(x["untouched_holdout_read"])
        self.assertEqual(x["registered_total_trial_family_per_asset"], 64)
        self.assertEqual(x["cross_asset_long_trial_family"], 256)
        self.assertIn(x["pbo"]["status"],
                      ("CALCULATED_RETROSPECTIVE_CSCV", "INSUFFICIENT_NONFLAT_VARIANTS"))
        if x["pbo"]["pbo"] is not None:
            self.assertTrue(0 <= x["pbo"]["pbo"] <= 1)
            self.assertEqual(x["pbo"]["partitions"], 70)

    def test_adversarial_holdout_price_change_not_read(self):
        baseline = empirical_trials.analyze(self.bars, self.report)
        mutated = self.bars.copy()
        start = early_flow.folds(len(mutated))["untouched_holdout"][0]
        for name in ("open", "high", "low", "close"):
            mutated.iloc[start:, mutated.columns.get_loc(name)] *= 1.8
        changed = empirical_trials.analyze(mutated, self.report)
        self.assertEqual(baseline["weekly_panel"]["sha256"],
                         changed["weekly_panel"]["sha256"])
        self.assertEqual(baseline["pbo"], changed["pbo"])
        self.assertEqual(baseline["dsr"], changed["dsr"])

    def test_actual_cscv_counts_and_dsr_variation(self):
        random = np.random.default_rng(123)
        sample = random.normal(size=(96, 3)) * 0.01
        a = empirical_trials.cscv_pbo(sample)
        self.assertEqual(a["partitions"], 70)
        self.assertIsNotNone(a["pbo"])
        d = empirical_trials.deflated_sharpe_validation(
            sample[-40:, 0], sample[:56])
        self.assertIsNotNone(d["dsr"])
        self.assertLessEqual(d["dsr"], 1)
        self.assertGreaterEqual(d["dsr"], 0)
        self.assertIsNone(empirical_trials.cscv_pbo(np.zeros((96, 3)))["pbo"])

    def test_derivative_chain_is_idempotent_and_tamper_proof(self):
        result = empirical_trials.analyze(self.bars, self.report)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empirical.jsonl"
            self.assertEqual(empirical_trials.append_ledger(p, result)["records"], 1)
            self.assertIn("UNCHANGED", empirical_trials.append_ledger(p, result)["status"])
            records = [json.loads(line) for line in p.read_text().splitlines()]
            self.assertEqual(len(records), 1)
            records[0]["paper_eligible"] = True
            p.write_text(json.dumps(records[0]) + "\n")
            with self.assertRaisesRegex(ValueError, "CHAIN_TAMPER"):
                empirical_trials.append_ledger(p, result)

    def test_source_and_no_promotion_fail_closed(self):
        bad = copy.deepcopy(self.report)
        bad["source_zip_provenance"][0]["checksum_verified"] = False
        with self.assertRaisesRegex(ValueError, "SOURCE_MANIFEST"):
            empirical_trials.analyze(self.bars, bad)


if __name__ == "__main__":
    unittest.main()
