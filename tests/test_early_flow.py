"""Eaverse V5 offline hourly causal research contracts.

Mock provider files only. These tests do NOT constitute live-market alpha.
"""
import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from eaverse import early_flow, pit_binance_pilot


def sample_bars():
    dt=pd.date_range("2024-01-01","2025-12-31T23:00:00",freq="h",tz="UTC")
    n=len(dt)
    r=np.arange(n)
    # A mean-reverting, artificially quiet price path, not a market backtest.
    opn=100+.15*np.sin(r*.02)
    close=opn*(1+.0002*np.sin(r*.07))
    vol=100+20*np.cos(r*.13)
    taker=vol*(.50+.15*np.sin(r*.13))
    return pd.DataFrame({
       "open":opn,"high":np.maximum(opn,close)*1.001,
       "low":np.minimum(opn,close)*.999,
       "close":close,"volume":vol,
       "quote_volume":vol*opn,"taker_buy_base":taker,
    },index=dt)


def monthly_csv(month, micro=False, missing_hour=None):
    first=pd.Timestamp(month+"-01",tz="UTC")
    last=first+pd.offsets.MonthBegin(1)
    rows=[]
    for hour in pd.date_range(first,last-pd.Timedelta(hours=1),freq="h"):
        if hour==missing_hour:
            continue
        unit=1_000_000 if micro else 1000
        start=int(hour.timestamp()*unit)
        end=start+3600*unit-1
        rows.append([start,100,101,99,100,100,end,10000,500,60,6000,0])
    buf=io.StringIO()
    writer=csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue().encode()


class HourlyResearchTests(unittest.TestCase):
    def test_monthly_ms_and_us_UTC_real_exchange_schema(self):
        a=early_flow._hourly_csv(monthly_csv("2024-01"),"BTCUSDT","2024-01")
        b=early_flow._hourly_csv(monthly_csv("2025-01",micro=True),"BTCUSDT","2025-01")
        self.assertEqual((len(a),len(b)),(744,744))
        self.assertEqual(a[0][0].hour,0)
        self.assertEqual(b[1][0].hour,1)
        self.assertAlmostEqual(a[0][7],60.)

    def test_24_month_contiguity_and_missing_hours_never_imputed(self):
        chunks={m:monthly_csv(m,m.startswith("2025")) for m in early_flow.MONTHS}
        frame=early_flow.parse_months(chunks,"BTCUSDT")
        self.assertEqual(len(frame),17544)
        self.assertEqual(frame.index[0].isoformat(),"2024-01-01T00:00:00+00:00")
        chunks["2025-07"]=monthly_csv("2025-07",True,
                pd.Timestamp("2025-07-15T14:00:00Z"))
        with self.assertRaisesRegex(ValueError,"missing/extra"):
            early_flow.parse_months(chunks,"BTCUSDT")

    def test_checksum_fails_closed_before_a_fake_market_row(self):
        csvbytes=monthly_csv("2024-01")
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as z:
                z.writestr("BTCUSDT-1h-2024-01.csv",csvbytes)
            archive=buffer.getvalue()
        valid=(hashlib.sha256(archive).hexdigest()+
               "  BTCUSDT-1h-2024-01.zip").encode()
        self.assertEqual(
            pit_binance_pilot.unpack_verified(archive,valid,"BTCUSDT-1h-2024-01.zip"),
            csvbytes)
        with self.assertRaisesRegex(ValueError,"SHA256"):
            pit_binance_pilot.unpack_verified(archive,b"0"*64,
                                             "BTCUSDT-1h-2024-01.zip")

    def test_actual_one_hour_close_and_taker_quantity_mandatory(self):
        raw=monthly_csv("2025-01",True).decode().splitlines()
        row=raw[0].split(",")
        row[6]=str(int(row[0])+3_600_000) # wrong: microsecond-close difference
        raw[0]=",".join(row)
        with self.assertRaisesRegex(ValueError,"bad hourly"):
            early_flow._hourly_csv(("\n".join(raw)+"\n").encode(),"BTCUSDT","2025-01")
        raw=monthly_csv("2024-01").decode().splitlines()
        row=raw[0].split(","); row[9]="200"; raw[0]=",".join(row)
        with self.assertRaisesRegex(ValueError,"impossible"):
            early_flow._hourly_csv(("\n".join(raw)+"\n").encode(),"BTCUSDT","2024-01")

    def test_causal_rolling_features_and_pre_price_divergence(self):
        bars=sample_bars()
        features=early_flow.asof_features(bars)
        original=early_flow.flags(features,"flow_divergence",1)
        self.assertGreater(int(original.sum()),100)
        changed=bars.copy()
        changed.iloc[11000:, :] *= 1.2
        altered=early_flow.asof_features(changed)
        shifted=early_flow.flags(altered,"flow_divergence",1)
        np.testing.assert_array_equal(original[:11000],shifted[:11000])
        # A future taker observation cannot affect an earlier percentile.
        self.assertAlmostEqual(features.q85_prior168.iloc[800],
                               early_flow.asof_features(bars.iloc[:801]).q85_prior168.iloc[-1])

    def test_open_to_open_horizon_cannot_enter_before_signal(self):
        prices=np.array([100.,100.,110.,130.,130.])
        self.assertAlmostEqual(early_flow._outcome(prices,0,1,1),1000.0)
        self.assertAlmostEqual(early_flow._outcome(prices,0,1,-1),-1000.0)
        self.assertAlmostEqual(early_flow._outcome(prices,1,1,1),1818.1818,places=3)

    def test_prior_controls_require_maturity_and_same_utc_hour(self):
        bars=sample_bars()
        feats=early_flow.asof_features(bars)
        candidate=24*300
        active=np.zeros(len(feats),dtype=bool)
        control=early_flow._matched_prior_control(
            bars.open.to_numpy(),feats,active,candidate,24,24*250)
        if control is not None:
            self.assertEqual(control%24,candidate%24)
            self.assertLess(control+25,candidate)
        self.assertIsNone(early_flow._matched_prior_control(
            bars.open.to_numpy(),feats,active,200,24,195))

    def test_frozen_experiments_and_automatic_adequacy_decisions(self):
        bars=sample_bars()
        provenance=[{"month":m,"checksum_verified":True,
                     "zip_sha256":"a"*64,"csv_sha256":"b"*64} for m in early_flow.MONTHS]
        report=early_flow.analyze(
            bars,provenance,"BTCUSDT",captured_utc="2026-11-23T12:00:00Z")
        self.assertEqual(report["observed_hourly_bars"],17544)
        self.assertEqual(report["experiment_count"],32)
        self.assertTrue(report["counts_by_decision"])
        self.assertFalse(report["paper_eligible"])
        self.assertFalse(report["forward_validated"])
        self.assertEqual(len(set(x["id"] for x in report["results"])),32)
        for exp in report["results"]:
            self.assertTrue(set(exp["folds"])=={"discovery","validation","untouched_holdout"})
            self.assertFalse(exp["trade_authorized"])
            if exp["holdout_opened_only_after_discovery_validation"]:
                self.assertNotEqual(exp["folds"]["untouched_holdout"]["data_status"],
                                    "SEALED_UNTIL_PRESELECTION_PASSES")
            else:
                self.assertEqual(exp["folds"]["untouched_holdout"]["data_status"],
                                 "SEALED_UNTIL_PRESELECTION_PASSES")
                self.assertIsNone(exp["folds"]["untouched_holdout"]["cases_nonoverlapping"])
            if exp["direction"]=="RISK_AVOIDANCE_NOT_SHORT":
                self.assertIsNone(exp["folds"]["validation"]["mean_case_after_stressed_fees_bps"])
            if exp["decision"].startswith("EXPLORATORY"):
                self.assertFalse(exp["paper_eligible"])

    def test_no_fabricated_holdout_when_all_flow_signals_absent(self):
        bars=sample_bars()
        bars["taker_buy_base"]=bars["volume"]*.5
        provenance=[{"month":m,"checksum_verified":True,
                     "zip_sha256":"a"*64,"csv_sha256":"b"*64}
                    for m in early_flow.MONTHS]
        result=early_flow.analyze(bars,provenance,"ETHUSDT",
                                  captured_utc="2026-11-23T12:00:00Z")
        self.assertEqual(result["experiment_count"],32)
        self.assertFalse(result["forward_validated"])
        self.assertTrue(all(
            not item["holdout_opened_only_after_discovery_validation"] and
            item["folds"]["untouched_holdout"]["data_status"] ==
            "SEALED_UNTIL_PRESELECTION_PASSES"
            for item in result["results"]
        ))

    def test_metadata_ledger_chain_dedup_and_tamper(self):
        report={"captured_utc":"2026-11-23T12:00:00Z",
                "source_archive_digest":"archiveA","strategy_policy_hash":"policyA",
                "asset":"BTCUSDT","experiment_count":32,
                "counts_by_decision":{"BLOCK":32}}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"evidence.jsonl"
            self.assertEqual(early_flow.append_ledger(path,report)["records"],1)
            self.assertIn("UNCHANGED",early_flow.append_ledger(path,report)["status"])
            self.assertEqual(len(path.read_text().splitlines()),1)
            other={**report,"captured_utc":"2026-11-24T12:00:00Z",
                   "source_archive_digest":"archiveB"}
            self.assertEqual(early_flow.append_ledger(path,other)["records"],2)
            rows=path.read_text().splitlines()
            self.assertTrue(json.loads(rows[-1])["source_revision"])
            x=json.loads(rows[0]);x["asset"]="CHANGED"
            path.write_text(json.dumps(x)+"\n"+rows[1]+"\n")
            with self.assertRaisesRegex(ValueError,"SHA_CHAIN_INVALID"):
                early_flow.append_ledger(path,other)


if __name__=="__main__":
    unittest.main()
