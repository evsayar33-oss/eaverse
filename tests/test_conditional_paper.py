"""Actual native simulator fixtures: no historical PAPER fabricated."""
import gzip
import hashlib
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from eaverse import conditional_paper as native, combination_research as combo, coverage, data, engine, foundry, indicator_research as indicator, research

M=data.MARKETS[0]
RISK={"atr_window":14,"stop":None,"tp":None,"trail":None,"partial":False,"staged":False,"max_hold":None}
COVERAGE={"frame":"1d","lane":"single","rules":[["sma",5,None,"above"]],"vote":1,
          "event":None,"direction":"long","style":"state","sizing":"full"}
FOUNDRY={"frame":"1d","lane":"grammar","rules":[["sma",5,None,"above"]],"logic":"all",
         "direction":"long","confirmation":1,"size":1.,"risk":RISK}
COMBO={"frame":"1d","rules":[{"family":"legacy:sma","rule":["sma",5,None,"above"]}],
       "logic":"all","direction":"long","confirmation":1,"size":1.,"risk":RISK,
       "events":[],"event_logic":"all","event_mode":"only"}
INDICATOR=indicator.plan("CRYPTO:BTC-USD","1d",6)
SPECS={coverage.VERSION:COVERAGE,foundry.VERSION:FOUNDRY,combo.VERSION:COMBO,indicator.VERSION:INDICATOR}


def frame(n=1600):
    # Deliberately synthetic repeated regimes for deterministic regression only.
    i=np.arange(n)
    p=100*np.exp(.0003*i+.07*np.sin(i*2*np.pi/32))
    return pd.DataFrame({"open":p,"close":p,"high":p*1.01,"low":p*.99,"volume":10000},
                        index=pd.date_range("2020-01-01",periods=n))


def now_for(bars):
    return datetime.combine(bars.index[-1].date(),datetime.min.time(),tzinfo=UTC)+timedelta(hours=14)


def seed(db,version,spec,asset="CRYPTO:BTC-USD",bars=None,override=None):
    if bars is None:
        bars=frame()
    current,target,_,warm,_=native.prepare(version,spec,bars,M,{}, {})
    boundary=warm+(len(current)-warm)*3//4
    if version==coverage.VERSION:
        d={"metrics":coverage.evaluate_fold(current,target,warm,boundary,M.cost_bps),"pass":False}
        v={"metrics":coverage.evaluate_fold(current,target,boundary,len(current),M.cost_bps)}
    else:
        d_metrics,train=foundry.execute(current,target,warm,boundary,spec["risk"],M.cost_bps)
        v_metrics,valid=foundry.execute(current,target,boundary,len(current),spec["risk"],M.cost_bps)
        evidence={"discovery":train,"validation":valid}
        evidence["sha256"]=research.digest(evidence)
        d={"metrics":d_metrics,"return_evidence":evidence,"pass":False}
        v={"metrics":v_metrics}
    if override:
        override(d,v)
    ident=research.digest([version,spec])
    db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (version,asset,ident,"fixture",research.canonical(spec),M.source,data.fingerprint(current),
                str(current.index[-1].date()),"test",research.canonical(native.POLICIES[version]),
                research.canonical(d),research.canonical(v)))
    return ident


class NativePaperTests(unittest.TestCase):
    def test_four_actual_executors_qualify_and_freeze_prospectively(self):
        b=frame();now=now_for(b)
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            assets={}
            for i,(version,spec) in enumerate(SPECS.items()):
                asset=f"CRYPTO:ASSET{i}-USD"
                seed(db,version,spec,asset,b)
                assets[asset]=b
            self.assertEqual(native.harvest(db),4)
            self.assertEqual(native.harvest(db),0)
            r=native.run(db,assets,{a:M for a in assets},{},{},now)
            self.assertEqual(r["outcomes"].get(native.ACTIVE),4,r)
            rows=list(db.execute("SELECT * FROM native_paper_candidates"))
            for row in rows:
                q=json.loads(row["qualification"]);f=json.loads(row["forward"])
                self.assertIsNone(q["reason"])
                self.assertEqual(len(q["folds"]),3)
                self.assertEqual(f["observed_bars"],0)
                self.assertFalse(f["paper_approved"])
                self.assertFalse(f["live_allowed"])
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],0)
            future=frame(1700)
            r=native.run(db,{a:future for a in assets},{a:M for a in assets},{},{},now_for(future))
            self.assertEqual(sum(r["outcomes"].values()),4)
            for row in db.execute("SELECT * FROM native_paper_candidates"):
                self.assertIn(row["status"],("conditional_completed_positive","conditional_completed_failed"))
                f=json.loads(row["forward"])
                self.assertEqual(f["observed_bars"],90)
                self.assertGreater(f["first_observed"],f["registered_date"])
                self.assertFalse(f["live_allowed"])

    def test_coverage_scalar_reference_is_independent_and_detects_bad_result(self):
        b=frame();cur,t,_,w,_=native.prepare(coverage.VERSION,COVERAGE,b,M,{}, {})
        true=coverage.evaluate_fold(cur,t,w,w+180,M.cost_bps)
        reference=native.coverage_reference(cur,t,w,w+180,M.cost_bps)
        self.assertAlmostEqual(true["net"],reference["net"],places=9)
        with patch.object(coverage,"evaluate_fold",return_value={**true,"net":true["net"]+.1}):
            with self.assertRaisesRegex(ValueError,"replay mismatch"):
                native.fold(coverage.VERSION,cur,t,w,w+180,COVERAGE,M.cost_bps)

    def test_historical_risk_and_entries_terminal_before_source_fetch(self):
        with engine.connect(":memory:") as db:
            seed(db,coverage.VERSION,COVERAGE,override=lambda d,v:v["metrics"].update(drawdown=.42))
            seed(db,foundry.VERSION,FOUNDRY,"CRYPTO:ETH-USD",override=lambda d,v:v["metrics"].update(entries=1))
            native.harvest(db)
            result=native.summary(db)
            self.assertEqual(result["counts"],{"screen_rejected":2})
            self.assertEqual({r["reason"] for r in result["reasons"]},
                             {"historical_drawdown_limit","insufficient_trade_evidence"})
            self.assertEqual(native.harvest(db),0)

    def test_archive_positive_is_restored_by_original_hash_without_repeating_attempt(self):
        with engine.connect(":memory:") as db,tempfile.TemporaryDirectory() as tmp:
            ident=seed(db,combo.VERSION,COMBO)
            row=dict(db.execute("SELECT * FROM attempts").fetchone())
            d=json.loads(row["discovered"]);v=json.loads(row["validated"])
            path=Path(tmp)/"native.gz"
            path.write_bytes(gzip.compress((json.dumps({"type":"technical","row":row})+"\n").encode()))
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            db.execute("UPDATE attempts SET spec='',policy='',validated=NULL,discovered=?",
                     (research.canonical({"pass":False,"archive":sha,"discovery_metrics":d["metrics"],"validation_metrics":v["metrics"]}),))
            native.harvest(db)
            self.assertEqual(native.summary(db)["counts"],{"awaiting_archive":1})
            self.assertEqual(native.restore_archive(db,path,sha,1,now_for(frame())),1)
            self.assertEqual(native.summary(db)["counts"],{"queued":1})
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT policy FROM attempts").fetchone()[0],"")
            with self.assertRaisesRegex(ValueError,"checksum"):
                native.restore_archive(db,path,"0"*64,1,now_for(frame()))

    def test_legacy_archive_above_64_mib_streams_and_retries_same_day(self):
        now=now_for(frame())
        with engine.connect(":memory:") as db,tempfile.TemporaryDirectory() as tmp:
            seed(db,coverage.VERSION,COVERAGE)
            row=dict(db.execute("SELECT * FROM attempts").fetchone())
            path=Path(tmp)/"legacy.gz"
            with gzip.open(path,"wt",encoding="utf-8",compresslevel=1) as out:
                out.write(json.dumps({"type":"technical","row":row})+"\n")
                for i in range(10):
                    out.write(json.dumps({"type":"event","row":{"version":"old","asset":"US:X","id":str(i),
                              "summary":"x"*(7*1024*1024)}})+"\n")
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            d,v,_,_=native.metrics_pair(row)
            db.execute("UPDATE attempts SET spec='',policy='',validated=NULL,discovered=?",
                       (research.canonical({"pass":False,"archive":sha,"discovery_metrics":d["metrics"],"validation_metrics":v["metrics"]}),))
            native.harvest(db)
            db.execute("INSERT INTO history_archives VALUES (?,?,?)",(sha,
                       f"https://github.com/example/repo/releases/download/research-history-2026-11/research-{sha}.jsonl.gz",11))
            db.execute("INSERT INTO native_paper_archive_attempts VALUES (?,?,?,?)",
                       (sha,now.isoformat(),"pending","native archive expanded size cap"))
            def download(*args):
                import shutil
                shutil.copyfile(path,Path(args[args.index("--dir")+1])/f"research-{sha}.jsonl.gz")
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":"example/repo"}),patch("eaverse.delivery.gh",side_effect=download):
                result=native.recover_archives(db,now)
            self.assertEqual(result[0]["status"],"recovered",result)
            self.assertEqual(result[0]["added"],1)
            self.assertEqual(native.summary(db)["counts"],{"queued":1})

    def test_revised_source_pauses_without_rewriting_frozen_progress(self):
        b=frame();now=now_for(b)
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            seed(db,coverage.VERSION,COVERAGE);native.harvest(db)
            a="CRYPTO:BTC-USD"
            native.run(db,{a:b},{a:M},{},{},now)
            more=frame(1610);native.run(db,{a:more},{a:M},{},{},now_for(more))
            old=db.execute("SELECT forward FROM native_paper_candidates").fetchone()[0]
            changed=frame(1611);changed.loc[changed.index[50],"close"]*=1.001
            r=native.run(db,{a:changed},{a:M},{},{},now_for(changed))
            self.assertEqual(r["outcomes"],{"forward_integrity_waiting":1})
            r=native.run(db,{a:changed},{a:M},{},{},now_for(changed)+timedelta(days=1))
            self.assertEqual(r["outcomes"],{"conditional_paused":1})
            row=db.execute("SELECT * FROM native_paper_candidates").fetchone()
            self.assertEqual(row["forward"],old)
            self.assertIn("price prefix",row["reason"])

    def test_weekly_forward_never_counts_registration_week(self):
        b=frame(3000)
        weekly=coverage.weekly(b)
        date="2026-11-25" # Wednesday
        positions=native.forward_window(weekly,"1w",date)
        self.assertGreater(weekly.index[positions[0]].to_period("W-SUN").start_time,pd.Timestamp(date))

    def test_macro_missing_data_is_pending_not_negative_or_future_imputed(self):
        spec={**COVERAGE,"lane":"macro","event":["US_CPI","only"]}
        with self.assertRaises(native.Waiting) as ctx:
            native.prepare(coverage.VERSION,spec,frame(),M,{}, {})
        self.assertEqual(ctx.exception.status,"waiting_calendar")

    def test_capacity_keeps_qualified_candidate_and_no_repeated_recheck(self):
        b=frame();now=now_for(b);a="CRYPTO:BTC-USD"
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}),patch.object(native,"PER_ASSET_ACTIVE",1):
            seed(db,coverage.VERSION,COVERAGE);seed(db,foundry.VERSION,FOUNDRY)
            native.harvest(db)
            r=native.run(db,{a:b},{a:M},{},{},now)
            self.assertEqual(r["outcomes"],{"conditional_active":1,"qualified_waiting_capacity":1})
            with patch.object(native,"evaluate",side_effect=AssertionError("repeated recheck")):
                r=native.run(db,{a:b},{a:M},{},{},now+timedelta(days=1))
            self.assertEqual(r["checked_this_run"],0)
            self.assertEqual(native.summary(db)["counts"].get("qualified_waiting_capacity"),1)

    def test_actual_autonomous_cycle_admits_native_candidate(self):
        b=frame()
        with engine.connect(":memory:") as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
            seed(db,coverage.VERSION,COVERAGE)
            with patch.object(engine.universe,"refresh",return_value=(["BTC-USD"],{"listing_date":"2026-11-27"})), \
                 patch.object(engine,"fetch_bars",return_value=(b,None)):
                report=engine.run(db,["CRYPTO"],1,event_budget=0,asset_limit=1,focus_slots=1)
            self.assertGreaterEqual(report["conditional_paper"]["counts"].get("conditional_active",0),1)
            self.assertGreaterEqual(report["conditional_paper"]["old_candidates_harvested"],1)
            self.assertEqual(report["conditional_paper"]["live_allowed"],False)

    def test_hash_tampering_rejected_before_any_forward_observation(self):
        with engine.connect(":memory:") as db:
            def tamper(d,v):
                d["return_evidence"]["discovery"][0]+=.1
            seed(db,foundry.VERSION,FOUNDRY,override=tamper)
            native.harvest(db)
            self.assertEqual(native.summary(db)["counts"],{"invalid_evidence":1})

    def test_dashboard_renders_conditional_with_no_legacy_or_forward_result(self):
        from streamlit.testing.v1 import AppTest
        from eaverse import panel
        bars=frame(); now=now_for(bars); asset="CRYPTO:BTC-USD"
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/"state.sqlite"
            with engine.connect(path) as db,patch.dict(os.environ,{"GITHUB_ACTIONS":"false"}):
                seed(db,coverage.VERSION,COVERAGE,asset,bars)
                native.harvest(db)
                native.run(db,{asset:bars},{asset:M},{},{},now)
            snapshot=panel.projection(path,{"time":now.isoformat(),"assets":{}},"https://example.org/run")
            self.assertEqual(snapshot["research"]["conditional_paper"]["counts"][native.ACTIVE],1)
            feed=Path(work)/"panel.json"
            feed.write_text(json.dumps(snapshot))
            with patch.dict(os.environ,{"EAVERSE_DASHBOARD_FILE":str(feed)}):
                app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
            self.assertFalse(app.exception)


if __name__=="__main__":
    unittest.main()
