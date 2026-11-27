"""Prove historical success survives capacity and integrity failures."""
import gzip
import hashlib
import json
import tempfile
from pathlib import Path
import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from eaverse import data, engine, paper_admission as admission, paper_archive, research
from test_research import market_frame

NOW = datetime(2026,11,27,18,tzinfo=UTC)
CRYPTO = data.MARKETS[0]


class PaperAdmissionTests(unittest.TestCase):
    def seed_attempt(self, db, asset, spec=None, source="coinbase", audit="matched", passed=True):
        spec=spec or next(research.catalog())
        ident=research.digest(spec)
        d={"pass":True,"net":0.15}
        v={"pass":passed,"net":0.04,"drawdown":0.1,"execution_audit":{"status":audit}}
        db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (research.VERSION,asset,ident,"sma",research.canonical(spec),source,"hash",
                    "2024-01-01","code",research.canonical(research.POLICY),research.canonical(d),research.canonical(v)))
        return ident

    def insert_active(self,db,asset,origin=None,cursor=None):
        return db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,cursor,
            version,policy,origin,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,'active')""",
            (asset,research.canonical(next(research.catalog())),35,"2026-11-28","2025-01-01","2026-11-26","old",
             cursor,research.VERSION,research.canonical(research.POLICY),research.canonical(origin or {}))).lastrowid

    def test_healthy_market_capacity_does_not_discard_positive_candidates(self):
        bars=market_frame(1000)
        with engine.connect(":memory:") as db, patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            # The original global cap of 12 would reject the distinct crypto asset.
            for i in range(12):
                self.insert_active(db,"US:T"+str(i))
            ident=self.seed_attempt(db,"CRYPTO:BTC-USD")
            self.assertEqual(admission.seed(db),1)
            self.assertEqual(admission.priority_assets(db,"CRYPTO",NOW.date()),["BTC-USD"])
            with patch.object(research,"history_metrics",return_value={"pass":True,"net":0.05}), \
                 patch.object(admission.reference,"audit_validation",return_value={"status":"matched"}):
                out=admission.admit(db,"CRYPTO:BTC-USD",CRYPTO,bars,NOW)
            self.assertEqual(out["status"],"admitted")
            self.assertEqual(out["start"],"2026-11-28")
            row=db.execute("SELECT * FROM paper WHERE sequence=?",(out["sequence"],)).fetchone()
            self.assertIsNone(row["cursor"])
            self.assertEqual(json.loads(row["origin"])["id"],ident)
            archived,_=paper_archive.read_prefix(db,row["sequence"])
            self.assertEqual(data.fingerprint(archived),row["digest"])
            self.assertEqual(admission.seed(db),0)
            self.assertIsNone(admission.admit(db,"CRYPTO:BTC-USD",CRYPTO,bars,NOW))
            self.assertEqual(db.execute("SELECT count(*) FROM paper WHERE status='active'").fetchone()[0],13)

    def test_old_percentage_only_rejected_discovery_is_eligible_for_fresh_recheck(self):
        with engine.connect(":memory:") as db:
            ident=self.seed_attempt(db,"CRYPTO:BTC-USD")
            d={"pass":False,"net":.3,"drawdown":.55,"trades":12,"profitable_folds":2}
            db.execute("UPDATE attempts SET policy=?,discovered=?,validated=NULL WHERE id=?",
                (research.canonical(research.LEGACY_POLICY),research.canonical(d),ident))
            self.assertEqual(admission.seed(db),1)
            row=db.execute("SELECT status,validation FROM paper_candidates").fetchone()
            self.assertEqual(row["status"],"waiting")
            self.assertEqual(row["validation"],"{}")

    def test_full_market_capacity_retains_waiting_row(self):
        with engine.connect(":memory:") as db:
            for i in range(admission.CAPACITY["CRYPTO"]):
                self.insert_active(db,"CRYPTO:T"+str(i))
            self.seed_attempt(db,"CRYPTO:BTC-USD")
            admission.seed(db)
            self.assertIsNone(admission.admit(db,"CRYPTO:BTC-USD",CRYPTO,market_frame(1000),NOW))
            self.assertEqual(db.execute("SELECT status FROM paper_candidates").fetchone()[0],"waiting")

    def test_pending_nonmatched_and_research_only_and_failed_gate_are_distinct(self):
        with engine.connect(":memory:") as db:
            self.seed_attempt(db,"CRYPTO:BTC-USD",audit="unsupported_fractional_sizing")
            self.seed_attempt(db,"CRYPTO:BINANCE_SPOT:BTCUSDT",source="binance_spot")
            self.seed_attempt(db,"CRYPTO:ETH-USD",passed=False)
            self.seed_attempt(db,"CRYPTO:SOL-USD",audit="mismatch")
            admission.seed(db)
            results={r[0]:r[1] for r in db.execute("SELECT asset,status FROM paper_candidates")}
            self.assertEqual(results,{"CRYPTO:BTC-USD":"audit_pending",
                                      "CRYPTO:BINANCE_SPOT:BTCUSDT":"research_only","CRYPTO:SOL-USD":"invalid_evidence"})
            self.assertEqual(admission.summary(db)["total_candidates"],3)

    def test_recheck_failure_is_terminal_for_exact_identity(self):
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            self.seed_attempt(db,"CRYPTO:BTC-USD");admission.seed(db)
            with patch.object(research,"history_metrics",return_value={"pass":False}), \
                 patch.object(admission.reference,"audit_validation",return_value={"status":"matched"}):
                out=admission.admit(db,"CRYPTO:BTC-USD",CRYPTO,market_frame(1000),NOW)
            self.assertEqual(out["status"],"recheck_failed")
            self.assertEqual(admission.seed(db),0)
            self.assertIsNone(admission.admit(db,"CRYPTO:BTC-USD",CRYPTO,market_frame(1000),NOW))
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],0)

    def test_two_persistent_integrity_failures_preserve_cursor_and_release_capacity(self):
        with engine.connect(":memory:") as db:
            seq=self.insert_active(db,"CRYPTO:BTC-USD",cursor="2026-11-26")
            err="ValueError: previously observed PAPER candles were revised or are missing"
            self.assertFalse(admission.pause_integrity(db,"CRYPTO:BTC-USD",err,NOW))
            db.execute("INSERT INTO paper_data_issues VALUES(?,?)",("CRYPTO:BTC-USD",err))
            self.assertTrue(admission.pause_integrity(db,"CRYPTO:BTC-USD",err,NOW))
            row=db.execute("SELECT * FROM paper WHERE sequence=?",(seq,)).fetchone()
            self.assertEqual(row["status"],"paused_integrity")
            self.assertEqual(row["cursor"],"2026-11-26")
            self.assertEqual(row["digest"],"old")
            self.assertFalse(json.loads(row["result"])["forward_validation_complete"])

    def test_repeated_replacement_stops_without_making_more_sequences(self):
        bars=market_frame(1000)
        with engine.connect(":memory:") as db:
            seq=self.insert_active(db,"CRYPTO:BTC-USD",origin={"replaces":123})
            engine.recheck_pending(db,"CRYPTO:BTC-USD",CRYPTO,bars,NOW)
            self.assertEqual(db.execute("SELECT status FROM paper WHERE sequence=?",(seq,)).fetchone()[0],"paused_integrity")
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],1)

    def test_old_same_strategy_is_not_repeated_in_paper(self):
        with engine.connect(":memory:") as db:
            ident=self.seed_attempt(db,"CRYPTO:BTC-USD")
            self.insert_active(db,"CRYPTO:BTC-USD",origin={"id":ident})
            admission.seed(db)
            self.assertEqual(db.execute("SELECT status FROM paper_candidates").fetchone()[0],"previous_paper")

    def test_verified_archived_positive_is_recovered_without_rewriting_attempt(self):
        with engine.connect(":memory:") as db, tempfile.TemporaryDirectory() as tmp:
            ident=self.seed_attempt(db,"CRYPTO:BTC-USD")
            original=dict(db.execute("SELECT * FROM attempts").fetchone())
            path=Path(tmp)/"history.gz"
            raw=(json.dumps({"type":"technical","row":original})+"\n").encode()
            path.write_bytes(gzip.compress(raw))
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            db.execute("UPDATE attempts SET spec='',policy='',validated=NULL,discovered=?",
                (research.canonical({"pass":True,"archive":sha,"outcome":"screened_not_final"}),))
            self.assertEqual(admission.seed(db),0)
            self.assertEqual(admission.restore_archive(db,path,sha,1),1)
            saved=db.execute("SELECT * FROM paper_candidates").fetchone()
            self.assertEqual(saved["id"],ident)
            self.assertEqual(json.loads(saved["record"]),original)
            self.assertEqual(db.execute("SELECT policy FROM attempts").fetchone()[0],"")
            with self.assertRaisesRegex(ValueError,"sha mismatch"):
                admission.restore_archive(db,path,"0"*64,1)

    def test_autonomous_cycle_nominates_and_admits_from_previous_attempt(self):
        bars=market_frame(1000)
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            self.seed_attempt(db,"CRYPTO:ETH-USD")
            with patch.object(engine.universe,"refresh",return_value=(["ETH-USD"],{"listing_date":NOW.date().isoformat()})), \
                 patch.object(engine,"fetch_bars",return_value=(bars,None)), \
                 patch.object(research,"history_metrics",return_value={"pass":True,"net":0.05,"drawdown":0.1}), \
                 patch.object(research,"direction_check",return_value={}), \
                 patch.object(admission.reference,"audit_validation",return_value={"status":"matched"}):
                report=engine.run(db,["CRYPTO"],1,event_budget=0,asset_limit=1,focus_slots=1)
            self.assertTrue(any(r["status"]=="admitted" and r["asset"]=="CRYPTO:ETH-USD"
                                for r in report["paper_admission"]["this_run"]))
            self.assertEqual(db.execute("SELECT count(*) FROM paper WHERE status='active'").fetchone()[0],1)
            self.assertEqual(report["paper_admission"]["recovered_nominations"],1)

    def test_waiting_source_does_not_erase_positive_candidate(self):
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            self.seed_attempt(db,"CRYPTO:BTC-USD");admission.seed(db)
            out=admission.admit(db,"CRYPTO:BTC-USD",CRYPTO,market_frame(400),NOW)
            self.assertEqual(out["status"],"waiting_source")
            self.assertEqual(db.execute("SELECT status FROM paper_candidates").fetchone()[0],"waiting")


if __name__=="__main__":
    unittest.main()
