"""V7 evidence audit: real summary diagnostics, no invented PBO or trades."""
import unittest

from eaverse.statistical_audit import review, wilson_interval


def fixture():
    def fold(n=20):
        return {"cases_nonoverlapping": n, "prior_same_hour_controls": n,
                "directional_hit_rate": 0.6, "data_status": "DESCRIPTIVE_ONLY"}
    row = {
        "spec": {"source": "BINANCE_SPOT_1H", "direction": 1},
        "paper_eligible": False, "trade_authorized": False,
        "holdout_opened_after_preselection": False,
        "decision": "NO_DISCOVERY_AND_VALIDATION_REPLICATION",
        "folds": {"discovery": fold(), "validation": fold(),
                  "untouched_holdout": {"data_status": "SEALED_UNTIL_PRESELECTION_PASSES",
                                        "cases_nonoverlapping": None,
                                        "prior_same_hour_controls": None,
                                        "directional_hit_rate": None}},
    }
    rows = [{**row, "id": str(i)} for i in range(64)]
    return {"schema": "eaverse.extended_flow.v7.1",
            "hypothesis_count": 64, "hours": 26302,
            "source_zip_provenance": [{"checksum_verified": True,
                                      "venue": "BINANCE_SPOT"} for _ in range(36)],
            "paper_eligible": False, "live_allowed": False, "forward_validated": False,
            "independent_venue_verified": False,
            "policy": {"multiple_testing_count_per_asset": 64},
            "decisions": {"NO_DISCOVERY_AND_VALIDATION_REPLICATION": 64},
            "results": rows}


class V7AuditTests(unittest.TestCase):
    def test_real_counts_have_intervals_but_no_invented_inference(self):
        out = review(fixture())
        self.assertEqual(len(out["hypotheses"]), 64)
        self.assertIsNone(out["PBO"])
        self.assertIsNone(out["monte_carlo"])
        self.assertFalse(out["paper_eligible"])
        self.assertIsNotNone(out["hypotheses"][0]["fold_uncertainty"]
                             ["discovery"]["wilson95_descriptive_only"])
        self.assertIsNone(out["hypotheses"][0]["fold_uncertainty"]
                          ["untouched_holdout"]["wilson95_descriptive_only"])

    def test_holdout_seal_violation_fails_closed(self):
        r = fixture()
        r["results"][0]["folds"]["untouched_holdout"]["data_status"] = "DESCRIPTIVE_ONLY"
        with self.assertRaisesRegex(ValueError, "HOLDOUT_ACCESSED"):
            review(r)

    def test_source_checksum_proof_required(self):
        r = fixture()
        r["source_zip_provenance"][0]["checksum_verified"] = False
        with self.assertRaisesRegex(ValueError, "SOURCE_MANIFEST"):
            review(r)

    def test_no_paper_promotion(self):
        r = fixture()
        r["results"][3]["paper_eligible"] = True
        with self.assertRaisesRegex(ValueError, "TRADING_FLAG"):
            review(r)

    def test_wilson_edge_cases(self):
        self.assertIsNone(wilson_interval(None, None))
        self.assertEqual(wilson_interval(1.0, 1)[1], 1.0)
        with self.assertRaises(ValueError):
            wilson_interval(1.5, 10)


if __name__ == "__main__":
    unittest.main()
