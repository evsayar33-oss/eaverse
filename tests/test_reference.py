import unittest
import numpy as np
import pandas as pd
from test_research import market_frame
from eaverse import reference, research


class AuditTests(unittest.TestCase):
    def test_reference_detects_changed_fold_profit(self):
        bars = market_frame()
        weights = pd.Series(np.arange(len(bars)) % 11 < 5, bars.index).astype(float)
        report = research.history_metrics(bars, weights, bars.index[-180:], 10, 252)
        self.assertEqual(reference.audit_validation(bars, weights, 10, report)["round_trips"], 17)
        report["folds"][1]["net"] += 0.01
        self.assertEqual(reference.audit_validation(bars, weights, 10, report)["status"], "mismatch")
