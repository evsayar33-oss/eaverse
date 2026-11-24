import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import pandas as pd

from eaverse import coverage, data, engine
from test_research import market_frame


class CoverageDebtTests(unittest.TestCase):
    def test_registered_grid_unique_and_every_family_reachable(self):
        for frame in coverage.FRAMES:
            for lane in coverage.LANES:
                n = coverage.total(frame, lane)
                self.assertGreater(n, 0)
                self.assertIsNone(coverage.plan(frame, lane, n))
                initial = [coverage.plan(frame, lane, i) for i in range(len(coverage.templates(frame, lane)))]
                self.assertEqual(len({str(item) for item in initial}), len(initial))
                if lane == "single":
                    self.assertEqual({item["rules"][0][0] for item in initial},
                                     {rule[0] for rule in coverage.rule_grid(frame)})
            self.assertEqual({len(x[0]) for x in coverage.templates(frame, "synthesis")}, {3, 4, 5})

    def test_weekly_uses_only_complete_interior_weeks(self):
        bars = market_frame(800)
        w = coverage.weekly(bars)
        self.assertEqual(w.index[0], bars.index[7])
        self.assertLess(w.index[-1], bars.index[-1])
        self.assertTrue((w.volume > 0).all())

    def test_normalized_signals_are_prefix_causal(self):
        bars = market_frame()
        for family in coverage.NORMALIZED:
            a = coverage.measurement(bars, family, 20)
            b = coverage.measurement(bars.iloc[:-50], family, 20)
            pd.testing.assert_series_equal(a.iloc[:-50], b)

    def test_next_open_fee_and_short(self):
        bars = market_frame(5)
        bars["open"] = [100., 200., 220., 220., 220.]
        bars["high"] = bars.open
        target = pd.Series([1., 0., 0., 0., 0.], index=bars.index)
        result = coverage.evaluate_fold(bars, target, 0, 5, 50)
        self.assertAlmostEqual(result["net"], 1.1 * .99 ** 2 - 1)
        short = coverage.evaluate_fold(bars, -target, 0, 5, 0)
        self.assertAlmostEqual(short["net"], -.1 - .05 / 365)

    def test_durable_cursor_and_blocked_macro_not_counted(self):
        bars = market_frame(2000)
        with engine.connect(":memory:") as db:
            with db:
                first = coverage.run_asset(db, "asset", data.MARKETS[0], bars, {}, {})
            self.assertEqual(first["1d:macro"]["status"], "blocked_event_data")
            self.assertEqual(first["1d:single"]["completed"], 1)
            with db:
                second = coverage.run_asset(db, "asset", data.MARKETS[0], bars, {}, {})
            self.assertEqual(second["1d:single"]["completed"], 2)
            self.assertNotEqual(first["1d:single"]["id"], second["1d:single"]["id"])
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE version=?", (coverage.VERSION,)).fetchone()[0], 16)

    def test_short_history_does_not_advance(self):
        with engine.connect(":memory:") as db:
            result = coverage.run_asset(db, "asset", data.MARKETS[0], market_frame(200), {}, {})
            self.assertEqual(result["1d:single"]["status"], "blocked_history")
            self.assertEqual(result["1d:single"]["completed"], 0)

    def test_macro_strictly_after_release_date(self):
        index = pd.date_range("2026-01-01", periods=4)
        m = coverage.event_mask(index, "US_CPI", {"US_CPI": ["2026-01-02"]})
        self.assertEqual(m.tolist(), [False, False, True, False])


if __name__ == "__main__":
    unittest.main()
