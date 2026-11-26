"""Exercise the complete autonomous run, not just individual discovery functions."""
import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from eaverse import data, engine, research
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
            self.assertGreater(report["indicator_trials"], 0)
            self.assertGreater(report["new_indicator_trials"], 0)
            self.assertEqual(result["indicators"]["1d"]["status"], "evaluated")
            self.assertNotEqual(result["coverage_debt"].get("status"), "error")
            self.assertNotEqual(result["foundry"].get("status"), "error")
            self.assertNotEqual(result["foundry_cohort"].get("status"), "error")

    def test_disabled_events_still_run_coverage(self):
        with engine.connect(":memory:") as db, patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            report = engine.run(db, [], 1, event_budget=0)
            self.assertEqual(report["event_sources"], {})
            self.assertEqual(report["coverage_debt_trials"], 0)

    def seed_unstarted(self, db, bars, asset, start):
        spec = next(research.catalog())
        cutoff = engine.date(bars.index[799])
        origin = {"id": "test", "periods": 365,
                  "discovery": {"start": engine.date(bars.index[200]), "end": engine.date(bars.index[599])},
                  "validation": {"start": engine.date(bars.index[620]), "end": cutoff}}
        db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                      VALUES(?,?,?,?,?,?,?,?,?,?,'active')""",
                   (asset, research.canonical(spec), 35, start, engine.date(bars.index[400]), cutoff,
                    "original-unavailable-digest", research.VERSION, research.canonical(research.POLICY),
                    research.canonical(origin)))
        return db.execute("SELECT * FROM paper WHERE asset=?", (asset,)).fetchone()

    def test_missed_unstarted_revision_restarts_only_at_future_open(self):
        bars = market_frame(1000)
        market = data.MARKETS[0]
        now = datetime(2026, 11, 24, tzinfo=UTC)
        with engine.connect(":memory:") as db:
            old = self.seed_unstarted(db, bars, "CRYPTO:BTC-USD", "2025-04-01")
            with patch.object(research, "history_metrics", return_value={"pass": True}), \
                    patch("eaverse.reference.audit_validation", return_value={"status": "matched"}):
                engine.recheck_pending(db, old["asset"], market, bars, now)
            original, replacement = list(db.execute("SELECT * FROM paper ORDER BY sequence"))
            self.assertEqual(original["status"], "revised_before_start")
            self.assertEqual(original["digest"], old["digest"])
            self.assertEqual(replacement["status"], "active")
            self.assertEqual(replacement["start"], "2026-11-25")
            self.assertEqual(replacement["spec"], old["spec"])
            self.assertIsNone(replacement["cursor"])
            self.assertEqual(engine.advance_paper(db, old["asset"], bars)["status"], "waiting_for_next_open")
            self.assertEqual(db.execute("SELECT count(*) FROM paper_progress").fetchone()[0], 0)

    def test_failed_requalification_never_manufactures_paper(self):
        bars = market_frame(1000)
        with engine.connect(":memory:") as db:
            old = self.seed_unstarted(db, bars, "CRYPTO:BTC-USD", "2025-04-01")
            with patch.object(research, "history_metrics", return_value={"pass": False}), \
                    patch("eaverse.reference.audit_validation", return_value={"status": "matched"}):
                engine.recheck_pending(db, old["asset"], data.MARKETS[0], bars, datetime(2026, 11, 24, tzinfo=UTC))
            self.assertEqual(db.execute("SELECT count(*) FROM paper WHERE status='active'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT digest FROM paper").fetchone()[0], old["digest"])

    def test_frozen_paper_error_does_not_block_valid_current_research(self):
        bars = market_frame(2000)
        market = data.MARKETS[0]
        day = bars.index[-1].date().isoformat()
        with engine.connect(":memory:") as db, patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(engine.universe, "refresh", return_value=(["BTC-USD"], {"listing_date": day})), \
                patch.object(engine, "fetch_bars", return_value=(bars, None)):
            old = self.seed_unstarted(db, bars, "CRYPTO:BTC-USD", "2025-04-01")
            db.execute("UPDATE paper SET cursor=? WHERE sequence=?", ("2025-04-02", old["sequence"]))
            report = engine.run(db, [market.name], 3, event_budget=0, asset_limit=1)
            result = report["assets"][old["asset"]]
            self.assertIn("origin candles", result["paper_error"])
            self.assertGreater(result["new_trials"], 0)
            self.assertGreater(report["new_coverage_debt_trials"], 0)
            self.assertGreater(report["new_foundry_trials"], 0)
            self.assertEqual(db.execute("SELECT cursor FROM paper WHERE sequence=?", (old["sequence"],)).fetchone()[0], "2025-04-02")
            self.assertIn("origin candles", db.execute("SELECT error FROM paper_data_issues").fetchone()[0])
