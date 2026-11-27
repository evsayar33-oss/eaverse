import sqlite3
import unittest

import numpy as np
import pandas as pd

from eaverse import data, engine, model_research


class StatisticalModelResearchTests(unittest.TestCase):
    def bars(self, n=720):
        rng = np.random.default_rng(7)
        vol = np.r_[np.full(n//3, .006), np.full(n//3, .025),
                    np.full(n-2*(n//3), .010)]
        ret = rng.normal(.0003, vol)
        close = 100*np.exp(np.cumsum(ret))
        index = pd.date_range("2020-01-01", periods=n, freq="D")
        return pd.DataFrame({"open":close*(1-rng.normal(0,.001,n)),
                             "high":close*1.01,"low":close*.99,"close":close,
                             "volume":rng.uniform(100,200,n)}, index=index)

    def test_models_are_chronological_and_never_promote(self):
        result = model_research.analyze(self.bars())
        self.assertEqual(result["status"], "RETROSPECTIVE_CAUSAL_DIAGNOSTIC_COMPLETE")
        self.assertLess(result["split"]["train_end"], result["split"]["validation_start"])
        self.assertFalse(result["paper_eligible"])
        self.assertFalse(result["trade_authorized"])
        self.assertFalse(result["direction"]["edge_claimed"])
        self.assertTrue(result["regimes"]["labels_derived_from_training_only"])

    def test_run_asset_persists_once(self):
        db = sqlite3.connect(":memory:")
        db.executescript(engine.SCHEMA)
        market = data.MARKETS[0]
        bars = self.bars()
        first = model_research.run_asset(db, "CRYPTO:TEST", market, bars)
        second = model_research.run_asset(db, "CRYPTO:TEST", market, bars)
        self.assertNotEqual(first["status"], "duplicate_not_counted")
        self.assertEqual(second["status"], "duplicate_not_counted")


if __name__ == "__main__":
    unittest.main()
