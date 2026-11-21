import gzip
import json
import subprocess
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

from eaverse import archive, checkpoint, data, engine, events, research, universe
from test_research import market_frame


class ExpansionTests(unittest.TestCase):
    def test_official_listing_parsers_filter_invalid_entries(self):
        sec = json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                          "data": [[1, "Issuer", "BRK.B", "NYSE"], [2, "OTC", "TICK", "OTC"]]})
        self.assertEqual(universe.sec_symbols(sec), {"BRK-B"})
        kap = '<a href="/tr/sirket-bilgileri/ozet/1107-turk-hava"><div>THYAO</div>'
        self.assertEqual(universe.kap_symbols(kap), {"THYAO.IS"})
        pairs = json.dumps([{"id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD", "status": "online"},
                            {"id": "ETH-EUR", "base_currency": "ETH", "quote_currency": "EUR", "status": "online"}])
        self.assertEqual(universe.coinbase_symbols(pairs), {"BTC-USD"})

    def test_us_snapshot_preserves_coverage_when_live_source_fails(self):
        with engine.connect(":memory:") as db, patch.object(universe, "fetch", side_effect=TimeoutError):
            market = data.MARKETS[1]
            symbols, status = universe.refresh(db, market, date(2026, 11, 21))
        self.assertGreater(len(symbols), 7000)
        self.assertEqual(status["coverage_mode"], "bundled_snapshot")
        self.assertIn("AAPL", symbols)

    def test_release_lookup_retries_transient_failure(self):
        error = subprocess.CalledProcessError(1, ["gh"], stderr="upstream gateway (HTTP 502)")
        with patch.object(checkpoint, "repository", return_value="owner/repo"), patch.object(
            checkpoint, "gh", side_effect=[error, '{"assets":[]}']
        ) as command, patch.object(checkpoint.time, "sleep"):
            self.assertEqual(checkpoint.assets(), [])
        self.assertEqual(command.call_count, 2)

    def test_rotation_prioritizes_paper_and_advances_discovery(self):
        market = data.MARKETS[0]
        now = datetime(2026, 11, 21, tzinfo=UTC)
        symbols = ["BTC-USD", "ETH-USD", "LINK-USD", "AVAX-USD", "SOL-USD"]
        with engine.connect(":memory:") as db:
            db.execute(
                """INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                   VALUES ('CRYPTO:XRP-USD','{}',10,'x','x','x','x','x','x','{}','active')"""
            )
            first = universe.queue(db, market, symbols, now.date(), 3)
            self.assertEqual(first[0], "XRP-USD")
            for symbol in first:
                universe.record(db, market.name, symbol, now, None)
            second = universe.queue(db, market, symbols, now.date(), 3)
            self.assertEqual(second[0], "XRP-USD")
            self.assertFalse(set(first[1:]) & set(second[1:]))

    def test_publish_day_and_weekend_are_not_executable_event_intervals(self):
        index = pd.bdate_range("2026-11-19", periods=5)
        masks = events.masks(index, {"US_CPI": ["2026-11-20"], "FOMC": ["2026-11-23"]})
        self.assertEqual(index[masks["US_CPI", 1]][0].date(), date(2026, 11, 23))
        self.assertEqual(index[masks["US_CPI", 2]][0].date(), date(2026, 11, 24))
        joint = events.select_mask(masks, [("US_CPI", 2), ("FOMC", 1)], "and")
        self.assertEqual(index[joint][0].date(), date(2026, 11, 24))
        either = events.select_mask(masks, [("US_CPI", 1), ("FOMC", 1)], "or")
        self.assertEqual(int(either.sum()), 2)

    def test_event_trial_saves_daily_costed_returns_and_does_not_repeat(self):
        bars = market_frame(750)
        dates = {"US_CPI": [d.date().isoformat() for d in bars.index[::15]],
                 "FOMC": [d.date().isoformat() for d in bars.index[::20]]}
        with engine.connect(":memory:") as db:
            first, daily = events.evaluate(db, "asset", bars, dates, "today", "snapshot", 35, 8)
            second, _ = events.evaluate(db, "asset", bars, dates, "today", "snapshot", 35, 8)
        self.assertEqual(first["new_trials"], 8)
        self.assertEqual(len(daily), 8 * 179)
        for item in first["trials"]:
            rows = [record for record in daily if record["experiment"] == item["id"]]
            self.assertAlmostEqual(np.prod([1 + row["net"] for row in rows]) - 1, item["recent"]["net"])
        self.assertFalse({row["id"] for row in first["trials"]}.intersection(row["id"] for row in second["trials"]))

    def test_alfred_dates_and_stale_calendar_cap(self):
        raw = '<a data-release-date="2026-11-10">CPI</a><a data-release-date="2026-12-10">future</a>'
        self.assertEqual(events.parse_dates(raw, events.VINTAGE, "%Y-%m-%d", date(2026, 11, 21)), ["2026-11-10"])
        bars = market_frame(750)
        known_until = bars.index[650].date().isoformat()
        releases = [day.date().isoformat() for day in bars.index[::15] if day.date().isoformat() <= known_until]
        with engine.connect(":memory:") as db:
            result, _ = events.evaluate(db, "asset", bars, {"US_CPI": releases}, "x", "hash", 35, 24,
                                        cutoffs={"US_CPI": known_until})
        affected = [trial for trial in result["trials"] if ("US_CPI" in {a for a, _ in trial["design"][0]})]
        self.assertTrue(affected)
        self.assertTrue(all(trial["study_end"] == known_until for trial in affected))

    def test_mutation_settings_and_legacy_signal_are_causal(self):
        bars = market_frame()
        parent = {"rules": [["rsi_trend", 14]], "sizing": "full", "confirmation": 1, "vote": 1}
        chosen = research.expand("asset", set(), {}, [parent], 30)
        self.assertEqual(len({identity for identity, _ in chosen}), 30)
        self.assertTrue(any(spec.get("levels") for _, spec in chosen))
        self.assertTrue(any(any(name in research.EXTENDED for name, _ in spec["rules"]) for _, spec in chosen))
        partial = research.features(bars.iloc[:-50], [spec for _, spec in chosen])
        full = research.features(bars, [spec for _, spec in chosen])
        for key in full:
            pd.testing.assert_series_equal(full[key].iloc[:-50], partial[key])
        pd.testing.assert_series_equal(research.features(bars, [parent])["rsi_trend", 14],
                                       research.features(bars)["rsi_trend", 14])

    def test_compaction_waits_for_checked_upload_and_keeps_seen_identity(self):
        row = ("1", "CRYPTO:BTC-USD", "one", "sma", "{}", "coinbase", "data", "date", "code", "{}",
               '{"pass":true}', '{"pass":false}')
        with engine.connect(":memory:") as db:
            with db:
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            with patch.object(archive, "upload", side_effect=RuntimeError("not durable")):
                with self.assertRaises(RuntimeError):
                    archive.compact(db, limit=1)
            self.assertEqual(db.execute("SELECT policy FROM attempts").fetchone()[0], "{}")

            def verify(path, sha, tag):
                item = json.loads(gzip.decompress(path.read_bytes()))
                self.assertEqual(item["row"]["id"], "one")
                self.assertTrue(sha in path.name)
                return "https://example.org/archive"

            with patch.object(archive, "upload", side_effect=verify):
                archive.compact(db, limit=1)
            saved = db.execute("SELECT id,policy,discovered FROM attempts").fetchone()
            self.assertEqual((saved["id"], saved["policy"]), ("one", ""))
            self.assertTrue(json.loads(saved["discovered"])["pass"])
            self.assertEqual(db.execute("SELECT records FROM history_archives").fetchone()[0], 1)
