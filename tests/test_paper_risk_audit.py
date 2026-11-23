"""Forward PAPER path stress never writes a trading decision."""
import copy
import unittest
from eaverse.paper_risk_audit import evaluate_one, evaluate_panel, _path


def record():
    return {
        "sequence": 5, "asset": "CRYPTO:BTC-USD", "status": "verified_paper",
        "result": {"data_sha256": "a" * 64, "start": "2026-01-01",
                   "end": "2026-04-01", "day_net": [0.002, -0.001] * 45,
                   "day_benchmark": [0.001, -0.002] * 45},
    }


class ForwardPathRiskTests(unittest.TestCase):
    def test_compounding_and_drawdown_have_true_initial_capital(self):
        r, d = _path([0.1, -0.2])
        self.assertAlmostEqual(r, -0.12)
        self.assertAlmostEqual(d, -0.2)

    def test_genuine_paper_only_no_new_approval(self):
        item = evaluate_one(record())
        self.assertEqual(item["observed_bars"], 90)
        self.assertEqual(item["status"], "EMPIRICAL_BOOTSTRAP_RESEARCH_ONLY")
        self.assertFalse(item["alpha_verified"])
        self.assertFalse(item["live_allowed"])
        self.assertEqual(item["paper_verdict_preserved"], "verified_paper")
        self.assertIsNone(item["stationary_block_sensitivity"]["10"]
                          ["future_profit_probability"])
        self.assertEqual(item["frozen_pair_sha256"],
                         evaluate_one(record())["frozen_pair_sha256"])
        self.assertEqual(item["stationary_block_sensitivity"],
                         evaluate_one(record())["stationary_block_sensitivity"])

    def test_nonmatured_and_missing_panel_are_not_synthetic_paper(self):
        r = record()
        r["status"] = "active"
        self.assertEqual(evaluate_one(r)["status"], "NOT_COMPLETED_PAPER")
        panel = {"schema": 1, "entries": [r]}
        self.assertEqual(evaluate_panel(panel)["completed_risk_diagnostics"], 0)

    def test_bad_returns_do_not_produce_quantiles(self):
        for bad in ([0.1] * 2, [float("nan")] * 90, [-1.01] * 90):
            r = record()
            r["result"]["day_net"] = bad
            self.assertEqual(evaluate_one(r)["status"], "INVALID_FORWARD_LEDGER")

    def test_existing_rejected_paper_never_becomes_approved(self):
        r = record()
        r["status"] = "paper_gate_failed"
        result = evaluate_one(r)
        self.assertEqual(result["paper_verdict_preserved"], "paper_gate_failed")
        self.assertFalse(result["alpha_verified"])


if __name__ == "__main__":
    unittest.main()
