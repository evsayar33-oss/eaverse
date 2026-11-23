"""V7 Eaverse 2023-25 multi-asset, no future leakage, no promotion contracts."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from eaverse import early_flow, extended_flow, pit_binance_pilot


def csv_month(month, *, gap=False, invalid=False):
    y, m=map(int,month.split("-"))
    start=pd.Timestamp(f"{y}-{m:02d}-01T00:00:00Z")
    end=start+pd.offsets.MonthBegin(1)
    rows=[]
    for hour in pd.date_range(start,end-pd.Timedelta(hours=1),freq="h"):
        if gap and hour.day==15 and hour.hour==8:
            continue
        unit=1_000_000 if y>=2025 else 1000
        begin=int(hour.timestamp()*unit)
        close=begin+3600*unit-1
        volume=100.
        taker_buy=60.
        if invalid and hour.day==3 and hour.hour==8:
            taker_buy=120.
        rows.append([begin,100.,101.,99.,100.,volume,close,10000.,50,taker_buy,6000.,0])
    buf=io.StringIO()
    w=csv.writer(buf)
    w.writerows(rows)
    return buf.getvalue().encode()


def fake_bars():
    dates=pd.date_range("2023-01-01T00:00:00Z","2025-12-31T23:00:00Z",freq="h")
    n=len(dates)
    t=np.arange(n)
    opened=100.+.2*np.sin(t*.009)
    closing=opened*(1+.0001*np.sin(t*.12))
    volume=90.+30*np.sin(t*.013)**2
    taker=volume*(.50+.18*np.sin(t*.031))
    return pd.DataFrame({
        "open":opened,"high":np.maximum(opened,closing)*1.002,
        "low":np.minimum(opened,closing)*.998,
        "close":closing,"volume":volume,"quote_volume":opened*volume,
        "taker_buy_base":taker,
    },index=dates)


def manifests(symbol):
    return [{"month":m,"venue":"BINANCE_SPOT","asset":symbol,
             "zip_sha256":"a"*64,"csv_sha256":"b"*64,
             "checksum_verified":True,"source_url":"https://data.binance.vision/example"}
            for m in extended_flow.MONTHS]


class ExtendedFlowTests(unittest.TestCase):
    def test_eight_assets_three_years_eight_distinct_event_families(self):
        self.assertEqual(len(extended_flow.SYMBOLS),8)
        self.assertEqual(len(extended_flow.MONTHS),36)
        self.assertEqual(len(extended_flow.FAMILIES),8)
        self.assertEqual(extended_flow.NUM_HYPOTHESES,64)
        self.assertEqual(extended_flow.RESEARCH_RIGHTS,
                         "PERSONAL_NONCOMMERCIAL_RESEARCH_ONLY")

    def test_continuity_and_epoch_units(self):
        blobs={m:csv_month(m) for m in extended_flow.MONTHS}
        bars=extended_flow._verified_months(blobs,"SOLUSDT")
        self.assertEqual(len(bars),26304)
        self.assertEqual(bars.index[-1].isoformat(),"2025-12-31T23:00:00+00:00")
        blobs["2025-08"]=csv_month("2025-08",gap=True)
        with self.assertRaisesRegex(ValueError,"MISSING_EXTRA"):
            extended_flow._verified_months(blobs,"SOLUSDT")

    def test_official_zip_sha_verified_for_entire_historical_window(self):
        blobs={}
        for m in extended_flow.MONTHS:
            raw=csv_month(m)
            file=f"XRPUSDT-1h-{m}"
            temp=io.BytesIO()
            with zipfile.ZipFile(temp,"w",zipfile.ZIP_DEFLATED) as z:
                z.writestr(file+".csv",raw)
            zipdata=temp.getvalue()
            blobs[f"{pit_binance_pilot.BASE_URL}/XRPUSDT/1h/{file}.zip"]=zipdata
            blobs[f"{pit_binance_pilot.BASE_URL}/XRPUSDT/1h/{file}.zip.CHECKSUM"]=(
                hashlib.sha256(zipdata).hexdigest()+"  "+file+".zip"
            ).encode()
        def fetch(url, limit):
            self.assertLessEqual(len(blobs[url]),limit)
            return blobs[url]
        bars, evidence=extended_flow.download("XRPUSDT",fetch)
        self.assertEqual(len(bars),26304)
        self.assertEqual(len(evidence),36)
        self.assertTrue(all(x["checksum_verified"] for x in evidence))
        # Fail closed on one tampered official signature.
        key=f"{pit_binance_pilot.BASE_URL}/XRPUSDT/1h/XRPUSDT-1h-2025-10.zip.CHECKSUM"
        blobs[key]=b"f"*64
        with self.assertRaisesRegex(ValueError,"SHA256"):
            extended_flow.download("XRPUSDT",fetch)

    def test_incompatible_venue_and_hindsight_asset_rejected(self):
        bars=fake_bars()
        with self.assertRaisesRegex(ValueError,"UNREGISTERED"):
            extended_flow.analyze(bars,manifests("DOGEUSDT"),"DOGEUSDT")
        bad=manifests("BTCUSDT")
        bad[3]["venue"]="COINBASE_SPOT"
        with self.assertRaisesRegex(ValueError,"CROSS_VENUE"):
            extended_flow.analyze(bars,bad,"BTCUSDT")

    def test_all_new_features_are_asof_invariant_to_future_price(self):
        bars=fake_bars()
        earlier=bars.iloc[:12500].copy()
        later=bars.copy()
        later.iloc[12500:]=later.iloc[12500:]*1.1
        one=early_flow.asof_features(earlier)
        two=early_flow.asof_features(later)
        for family in extended_flow.FAMILIES:
            for direction in extended_flow.DIRECTIONS:
                before=extended_flow.event_mask(earlier,one,family,direction)
                after=extended_flow.event_mask(later,two,family,direction)
                np.testing.assert_array_equal(before,after[:len(before)])

    def test_fully_sealed_holdout_and_next_research_queue(self):
        bars=fake_bars()
        report=extended_flow.analyze(
            bars,manifests("AVAXUSDT"),"AVAXUSDT",
            captured_utc="2026-11-23T09:00:00Z")
        self.assertEqual(report["hours"],26304)
        self.assertEqual(report["archive_months"],36)
        self.assertEqual(report["hypothesis_count"],64)
        self.assertEqual(len({x["id"] for x in report["results"]}),64)
        self.assertFalse(report["paper_eligible"])
        self.assertFalse(report["forward_validated"])
        self.assertFalse(report["multiple_testing_corrected_alpha_verified"])
        for trial in report["results"]:
            hold=trial["folds"]["untouched_holdout"]
            if not trial["holdout_opened_after_preselection"]:
                self.assertEqual(hold["data_status"],
                                 "SEALED_UNTIL_PRESELECTION_PASSES")
                self.assertIsNone(hold["cases_nonoverlapping"])
            self.assertFalse(trial["trade_authorized"])
        self.assertTrue(report["future_research_proposals"])
        self.assertTrue(all(x["paper_eligible"] is False for x in
                            report["future_research_proposals"]))

    def test_chained_full_evidence_blocks_unchanged_or_tampered_data(self):
        bars=fake_bars()
        report=extended_flow.analyze(
            bars,manifests("BTCUSDT"),"BTCUSDT",
            captured_utc="2026-11-23T10:00:00Z")
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"ledger.jsonl"
            self.assertEqual(extended_flow.append_ledger(path,report)["records"],1)
            self.assertIn("UNCHANGED",extended_flow.append_ledger(path,report)["status"])
            rows=path.read_text().splitlines()
            self.assertEqual(len(rows),1)
            record=json.loads(rows[0])
            self.assertEqual(len(record["full_hypothesis_studies"]),64)
            record["paper_eligible"]=True
            path.write_text(json.dumps(record)+"\n")
            with self.assertRaisesRegex(ValueError,"TAMPER_OR_CORRUPTION"):
                extended_flow.append_ledger(path,report)


if __name__=="__main__":
    unittest.main()
