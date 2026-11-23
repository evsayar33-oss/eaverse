"""Independent offline regression for the Eaverse historical PIT replay."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from eaverse import pit_adapters, pit_research


class PitReplayTests(unittest.TestCase):
    def frame(self, market):
        if market == "CRYPTO":
            idx = pd.date_range("2022-01-01", periods=800, freq="D")
        else:
            cal = xcals.get_calendar("XNYS" if market == "US" else "XIST")
            idx = cal.sessions_in_range("2022-01-01", "2025-12-30")[:800].tz_localize(None)
        close = 100 + np.arange(len(idx)) * .05
        opn = close * (1 + .002 * np.sin(np.arange(len(idx))))
        return pd.DataFrame({
            "date": idx.strftime("%Y-%m-%d"), "open": opn,
            "high": np.maximum(opn, close) * 1.015,
            "low": np.minimum(opn, close) * .985,
            "close": close, "volume": 150 + np.arange(len(idx)),
        })

    def package(self, market="US", raw_event_override=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        frame = self.frame(market)
        dates = pd.DatetimeIndex(frame.date)
        asset = {"US": "US:CIK0000320193", "BIST": "BIST:THYAO", "CRYPTO": "CRYPTO:BINANCE_SPOT:BTCUSDT"}[market]
        retrieved = datetime(2026, 11, 22, tzinfo=timezone.utc).isoformat()
        events = [
            {"id": f"event-{point}", "kind": "PUBLIC_EVENT",
             "market": market, "asset": asset, "public_at_utc": dates[point].tz_localize("UTC").isoformat(),
             "first_seen_utc": retrieved, "clock_basis": "OFFICIAL_PUBLICATION"}
            for point in range(10, 780, 17)
        ]
        if raw_event_override is not None:
            events = raw_event_override
        bar_bytes = frame.to_csv(index=False).encode()
        event_bytes = pit_adapters.event_jsonl(events)
        manifest = pit_adapters.make_manifest(
            market=market, asset=asset, price_source_id=f"test-price-{market}",
            source_id=f"test-events-{market}", bars_csv=bar_bytes,
            events_jsonl=event_bytes, retrieved_utc=retrieved,
            rights_status="OFFICIAL_PUBLIC_RESEARCH", cost_bps=25,
        )
        (root / "bars.csv").write_bytes(bar_bytes)
        (root / "events.jsonl").write_bytes(event_bytes)
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return path, events, manifest

    def test_three_markets_replay_independent_embargoed_folds(self):
        for market in ("US", "BIST", "CRYPTO"):
            with self.subTest(market=market):
                path, _, _ = self.package(market)
                report = pit_research.replay(path)
                self.assertEqual(report["bars"], 800)
                self.assertEqual(report["variants"], 6)
                self.assertFalse(report["paper_eligible"])
                self.assertFalse(report["alpha_verified"])
                self.assertFalse(report["forward_evidence"])
                for variant in report["discoveries"]:
                    self.assertEqual(set(variant["folds"]), {"discovery", "validation", "untouched_holdout"})
                    self.assertEqual(variant["status"], "RETROSPECTIVE_ONLY_NO_PROMOTION")
                    self.assertGreaterEqual(variant["folds"]["untouched_holdout"]["events"], 3)
                    self.assertTrue(np.isfinite(variant["folds"]["untouched_holdout"]["net"]))

    def test_weekend_publication_never_backdates_to_friday(self):
        index = pd.bdate_range("2026-11-20", periods=5)
        published = datetime(2026, 11, 21, 15, tzinfo=timezone.utc)
        point = pit_research._availability_index(index, published, "US")
        self.assertEqual(index[point].strftime("%Y-%m-%d"), "2026-11-23")
        signal, anchors = pit_research._event_desired(
            index,
            [({"kind": "ANNOUNCEMENT"}, published)],
            "US", "ANNOUNCEMENT", 1,
        )
        self.assertEqual(anchors, [point])
        self.assertEqual(signal.iloc[0], 0)
        self.assertEqual(signal.shift(1).fillna(0).iloc[point], 0)
        self.assertEqual(signal.shift(1).fillna(0).iloc[point + 1], 1)

    def test_us_filing_date_is_not_used_as_publication_clock(self):
        payload = {"cik": 320193, "filings": {"recent": {
            "accessionNumber": ["0001-24-000001", "0001-24-000002"],
            "form": ["8-K", "10-Q"],
            "acceptanceDateTime": ["2024-01-02T16:00:00", ""],
            "filingDate": ["2023-01-01", "2023-01-01"],
        }}}
        records = pit_adapters.sec_events(
            payload, asset="US:CIK0000320193", retrieved_utc="2024-01-10T00:00:00Z"
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["clock_basis"], "ACCEPTANCE_PLUS_24H")
        self.assertEqual(records[0]["public_at_utc"], "2024-01-03T21:00:00+00:00")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            pit_adapters.sec_events(payload, asset="US:AAPL", retrieved_utc="2024-01-10T00:00:00Z")

    def test_binance_flow_is_true_taker_volume_proxy_not_l2(self):
        start = 1704067200000  # 2024-01-01 00:00 UTC
        close = 1704153599999  # 2024-01-01 23:59:59.999 UTC
        row = [start, 100, 103, 99, 101, 100, close, 10000, 70, 70, 7000, 0]
        bars, events = pit_adapters.binance_daily_klines(
            (",".join(map(str, row)) + "\n").encode(), symbol="BTCUSDT",
            retrieved_utc="2024-01-03T00:00:00Z",
        )
        self.assertEqual(bars[0]["close"], 101)
        self.assertEqual(events[0]["kind"], "BINANCE_TAKER_BUY_DOMINANCE")
        self.assertAlmostEqual(events[0]["taker_base_imbalance"], .4)
        self.assertEqual(events[0]["clock_basis"], "EXCHANGE_BAR_CLOSE")
        self.assertEqual(events[0]["public_at_utc"], "2024-01-02T00:00:00.999000+00:00")
        row[9] = 120
        with self.assertRaisesRegex(ValueError, "taker-buy"):
            pit_adapters.binance_daily_klines((",".join(map(str, row)) + "\n").encode(),
                                               symbol="BTCUSDT", retrieved_utc="2024-01-03T00:00:00Z")

    def test_bist_and_cftc_economic_dates_do_not_replace_publication(self):
        with self.assertRaises(KeyError):
            pit_adapters.official_events(
                [{"event_id": "cot-1", "event_kind": "CFTC_COT",
                  "publication_evidence_url": "https://cftc.gov/example",
                  "observation_date": "2024-01-02"}],
                market="US", asset="US:SPY", retrieved_utc="2024-01-10T00:00:00Z",
            )
        event = pit_adapters.official_events(
            [{"event_id": "spk-1", "event_kind": "SPK_CAPITAL_INCREASE",
              "official_publication_utc": "2024-01-05T12:00:00Z",
              "publication_evidence_url": "https://spk.gov.tr/example"}],
            market="BIST", asset="BIST:THYAO", retrieved_utc="2024-01-10T00:00:00Z",
        )[0]
        self.assertEqual(event["clock_basis"], "OFFICIAL_PUBLICATION")

    def test_missing_market_day_is_not_filled(self):
        path, _, manifest = self.package()
        frame = self.frame("US").drop(index=200)
        blob = frame.to_csv(index=False).encode()
        (path.parent / "bars.csv").write_bytes(blob)
        manifest["bars_sha256"] = pit_research._digest(blob)
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "missing/extra daily sessions"):
            pit_research.replay(path)

    def test_hash_mismatch_does_not_run_study(self):
        path, _, _ = self.package()
        with (path.parent / "events.jsonl").open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            pit_research.replay(path)

    def test_ambiguous_or_future_publication_is_rejected(self):
        path, events, manifest = self.package()
        events[0]["public_at_utc"] = "2023-01-01T00:00:00"
        blob = pit_adapters.event_jsonl(events)
        (path.parent / "events.jsonl").write_bytes(blob)
        manifest["events_sha256"] = pit_research._digest(blob)
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "without timezone"):
            pit_research.replay(path)
        events[0]["public_at_utc"] = "2099-01-01T00:00:00Z"
        blob = pit_adapters.event_jsonl(events)
        (path.parent / "events.jsonl").write_bytes(blob)
        manifest["events_sha256"] = pit_research._digest(blob)
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "not yet published"):
            pit_research.replay(path)

    def test_backdated_first_seen_is_rejected(self):
        path, events, manifest = self.package()
        events[0]["first_seen_utc"] = "2020-01-01T00:00:00Z"
        blob = pit_adapters.event_jsonl(events)
        (path.parent / "events.jsonl").write_bytes(blob)
        manifest["events_sha256"] = pit_research._digest(blob)
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "backdated"):
            pit_research.replay(path)

    def test_unknown_rights_do_not_enable_historical_replay(self):
        path, _, manifest = self.package()
        manifest["rights_status"] = "UNKNOWN"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "rights unverified"):
            pit_research.replay(path)

    def test_conflicting_duplicate_event_identifiers_rejected(self):
        path, events, manifest = self.package()
        events.append({**events[0], "kind": "DIFFERENT_EVENT"})
        blob = pit_adapters.event_jsonl(events)
        (path.parent / "events.jsonl").write_bytes(blob)
        manifest["events_sha256"] = pit_research._digest(blob)
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "conflicting revisions"):
            pit_research.replay(path)


if __name__ == "__main__":
    unittest.main()
