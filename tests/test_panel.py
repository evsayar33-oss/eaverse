import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from eaverse import data, engine, metrics, panel, research
from test_research import market_frame


class PanelTests(unittest.TestCase):
    def test_trade_statistics_include_explicit_exit_fees(self):
        returns = pd.Series([0.10, -0.02, -0.05, -0.01, 0.0])
        weights = pd.Series([1.0, 0.0, 1.0, 0.0, 0.0])
        result = metrics.describe(returns, weights, 365)
        win, loss = 1.10 * 0.98 - 1, 0.95 * 0.99 - 1
        self.assertAlmostEqual(result["trade_win_rate"], 0.5)
        self.assertAlmostEqual(result["trade_expectancy"], (win + loss) / 2)
        self.assertAlmostEqual(result["profit_factor"], win / -loss)
        self.assertIsNone(result["sharpe"])

    def test_dashboard_reads_partial_progress_and_frozen_legacy_backtest(self):
        bars = market_frame()
        spec = next(research.catalog())
        anchor, cutoff, start = (engine.date(bars.index[i]) for i in (0, 399, 401))
        with tempfile.TemporaryDirectory() as work:
            db_path = Path(work) / "test.sqlite"
            with engine.connect(db_path) as db:
                db.execute(
                    """INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,'active')""",
                    ("CRYPTO:BTC-USD", research.canonical(spec), 35, start, anchor, cutoff,
                     data.fingerprint(bars.iloc[:400]), research.VERSION, research.canonical(research.POLICY),
                     json.dumps({"id": "test", "discovery": {"net": 0.12}, "validation": {"net": 0.20}})),
                )
                engine.advance_paper(db, "CRYPTO:BTC-USD", bars.iloc[:403])
            snapshot = panel.projection(
                db_path, {"time": datetime.now(UTC).isoformat(), "assets": {}}, "https://example.org/run"
            )
            paper = snapshot["entries"][0]
            self.assertEqual(paper["progress"]["bars"], 1)
            self.assertEqual(len(paper["progress"]["curve"]), 2)
            self.assertEqual(paper["progress"]["curve"][-1]["date"], paper["cursor"])
            self.assertNotIn("pass", paper["progress"]["stats"])
            self.assertEqual(paper["origin"]["discovery"]["net"], 0.12)
            feed = Path(work) / "paper.json"
            feed.write_text(json.dumps(snapshot))
            with patch.dict("os.environ", {"EAVERSE_DASHBOARD_FILE": str(feed)}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            self.assertFalse(app.exception)
            self.assertEqual([tab.label for tab in app.tabs], ["Güncel PAPER", "İzleme öncesi backtest"])
            self.assertIn("+12,00%", app.dataframe[0].value["Keşif net"].values)
            self.assertEqual(app.metric[0].label, "Net getiri")
