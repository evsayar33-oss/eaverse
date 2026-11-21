import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from eaverse import checkpoint, data, delivery, engine, research


def market_frame(size=800):
    rng = np.random.default_rng(847)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.017, size)))
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.02, "low": close * 0.98,
        "close": close, "volume": rng.integers(10, 5000, size),
    }, index=pd.date_range("2023-01-01", periods=size))


class ResearchTests(unittest.TestCase):
    def test_directional_returns_use_next_eligible_open(self):
        bars = market_frame(6)
        bars["open"] = [100., 200., 200., 180., 180., 180.]
        bars["high"] = bars.open
        spec = {"rules": [["ema", 5]], "vote": 1, "sizing": "full", "confirmation": 1}
        signal = pd.Series(False, index=bars.index)
        result = research.direction_check(bars, signal.astype(float), spec, {("ema", 5): signal},
                                          bars.index[:4], bars.index[3:], 0, 365)
        for mode in ("short", "long_short"):
            self.assertAlmostEqual(result[mode]["discovery"]["net"], .1 - .05 / 365)
        self.assertEqual(result["long"]["discovery"]["net"], 0)

    def test_catalog_has_unique_strategy_combinations(self):
        specs = list(research.catalog())
        self.assertEqual(len(specs), 34_636)
        self.assertEqual(len({research.digest(spec) for spec in specs}), len(specs))
        initial = research.choose("CRYPTO:BTC-USD", set(), {}, 12)
        self.assertEqual({len(spec["rules"]) for _, spec in initial}, {1, 2, 3})
        old = {key for key, _ in initial}
        self.assertTrue(old.isdisjoint(key for key, _ in research.choose("CRYPTO:BTC-USD", old, {}, 12)))

    def test_signals_do_not_change_when_future_closes_change(self):
        bars = market_frame()
        changed = bars.copy()
        changed.iloc[-200:, :4] *= 4
        start = research.features(bars)
        end = research.features(changed)
        for key in start:
            pd.testing.assert_series_equal(start[key].iloc[:-200], end[key].iloc[:-200])

    def test_simulation_executes_after_signal_and_charges_round_trip(self):
        bars = market_frame(5)
        bars["open"] = [100.0, 200.0, 220.0, 220.0, 220.0]
        desired = pd.Series([1.0, 0.0, 0.0, 0.0, 0.0], index=bars.index)
        net, trades, _ = research.simulation(bars, desired, bars.index[0], bars.index[-1], 100)
        self.assertAlmostEqual((1 + net).prod(), 1.1 * 0.99 ** 2)
        self.assertEqual(trades, 1)
        self.assertEqual(net.iloc[0], 0.0)

    def test_missing_or_invalid_completed_bar_fails_source_validation(self):
        bars = market_frame()
        self.assertEqual(len(data.validate(bars, bars.index)), 800)
        with self.assertRaisesRegex(ValueError, "1 missing"):
            data.validate(bars.drop(bars.index[4]), bars.index)

    def test_two_failures_are_one_event_until_recovery(self):
        with engine.connect(":memory:") as db:
            for error in ["first", "second", "third", None, "first", "second"]:
                engine.health(db, "asset", error)
            self.assertEqual([row["id"] for row in db.execute("SELECT id FROM outbox ORDER BY id")],
                             ["data:asset:1", "data:asset:2"])

    def test_restored_sqlite_does_not_retest_the_same_spec(self):
        bars = market_frame()
        market = data.MARKETS[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite"
            with engine.connect(path) as db, db:
                first = engine.discover(db, "BTC", market, bars, datetime.now(UTC), 3)
                self.assertEqual(first["total_trials"], 3)
            with engine.connect(path) as db, db:
                engine.discover(db, "BTC", market, bars, datetime.now(UTC), 3)
                rows = list(db.execute("SELECT DISTINCT id FROM attempts"))
            self.assertEqual(len(rows), 6)

    def test_paper_freeze_cursor_and_revisions(self):
        bars = market_frame()
        spec = next(research.catalog())
        anchor, cutoff, start = (engine.date(bars.index[i]) for i in (0, 399, 401))
        with engine.connect(":memory:") as db:
            db.execute(
                """INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'active')""",
                ("BTC", research.canonical(spec), 35, start, anchor, cutoff,
                 data.fingerprint(bars.iloc[:400]), research.VERSION, research.canonical(research.POLICY),
                 research.canonical({"id": "test"})),
            )
            self.assertEqual(engine.advance_paper(db, "BTC", bars.iloc[:400])["status"], "waiting_for_next_open")
            self.assertEqual(engine.advance_paper(db, "BTC", bars.iloc[:450])["bars"], 48)
            revised = bars.copy()
            revised.iloc[410, 0] *= 1.01
            with self.assertRaisesRegex(ValueError, "previously observed"):
                engine.advance_paper(db, "BTC", revised)
            result = engine.advance_paper(db, "BTC", bars)
            self.assertEqual(result["bars"], research.PAPER_BARS)
            self.assertEqual(result["status"], "paper_gate_failed")
            self.assertIsNone(engine.advance_paper(db, "BTC", bars))

    def test_checkpoint_and_receipts_do_not_reset_after_retry(self):
        with engine.connect(":memory:") as db:
            engine.enqueue(db, "incident", "actionable")
            with patch.object(delivery, "telegram", return_value="telegram:42") as send:
                delivery.deliver(db)
                delivery.deliver(db)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(db.execute("SELECT receipt FROM outbox").fetchone()[0], "telegram:42")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(checkpoint, "assets", return_value=[]), patch.dict(os.environ, {"EAVERSE_BOOTSTRAP": "false"}):
                with self.assertRaisesRegex(RuntimeError, "checkpoint is missing"):
                    checkpoint.restore(Path(directory) / "state.sqlite")


if __name__ == "__main__":
    unittest.main()
