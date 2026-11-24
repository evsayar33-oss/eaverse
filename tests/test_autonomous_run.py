"""Exercise the complete autonomous run, not just individual discovery functions."""
import os
import unittest
from unittest.mock import patch

from eaverse import data, engine
from test_research import market_frame


class AutonomousRunTests(unittest.TestCase):
    def test_enabled_event_calendar_does_not_shadow_coverage_module(self):
        bars = market_frame(2000)
        market = data.MARKETS[0]
        day = bars.index[-1].date().isoformat()
        dates = {"US_CPI": [d.date().isoformat() for d in bars.index[::15]]}
        calendar = {"US_CPI": {"fetched": day}}
        with engine.connect(":memory:") as db, patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(engine.events, "refresh", return_value=(dates, calendar)), \
                patch.object(engine.universe, "refresh", return_value=(["BTC-USD"], {"listing_date": day})), \
                patch.object(engine, "fetch_bars", return_value=(bars, None)):
            report = engine.run(db, [market.name], 3, event_budget=1, asset_limit=1)
            result = report["assets"]["CRYPTO:BTC-USD"]
            self.assertEqual(report["event_sources"], calendar)
            self.assertGreater(report["coverage_debt_trials"], 0)
            self.assertGreater(report["foundry_trials"], 0)
            self.assertNotEqual(result["coverage_debt"].get("status"), "error")
            self.assertNotEqual(result["foundry"].get("status"), "error")
            self.assertNotEqual(result["foundry_cohort"].get("status"), "error")

    def test_disabled_events_still_run_coverage(self):
        with engine.connect(":memory:") as db, patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            report = engine.run(db, [], 1, event_budget=0)
            self.assertEqual(report["event_sources"], {})
            self.assertEqual(report["coverage_debt_trials"], 0)
