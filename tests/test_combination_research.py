import itertools
import json
import math
import random
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from eaverse import combination_research as cr, data, engine, events, research
from test_research import market_frame


class CombinationTests(unittest.TestCase):
    def test_unrank_and_partition_are_exact(self):
        values=tuple("abcde")
        for size in range(6):
            combinations=list(itertools.combinations(values,size))
            self.assertEqual([cr.unrank(values,size,i) for i in range(len(combinations))],combinations)
        for frame in cr.FRAMES:
            n,e=len(cr.FAMILIES),len(cr.event_names(frame))
            expected=sum(math.comb(n,i)*math.comb(e,j)*cr.variants(i,j)
                         for i in range(n+1) for j in range(e+1) if i+j)
            expected+=len(cr.FAMILIES)*cr.variants(2,0)  # additional same-family two-setting pairs
            self.assertEqual(sum(cr.total(frame,l) for l in cr.LANES if l not in cr.SETTINGS),expected)
            for lane in ("bank_pair","cross_pair","legacy_pair","mixed_pair","same_family"):
                seen=set()
                for rank in range(cr.total(frame,lane)):
                    a,b,i,j,g,d=cr.selection("asset",frame,lane,rank)
                    seen.add((a,b,i,j,g,d))
                self.assertEqual(len(seen),cr.total(frame,lane),lane)

    def test_all_catalog_sizes_including_last_are_addressable(self):
        for frame in cr.FRAMES:
            for lane in ("triple","medium","higher","mixed_small","mixed_higher","event_higher"):
                for pos in (0,1,cr.total(frame,lane)//2,cr.total(frame,lane)-1):
                    a,b,i,j,g,d=cr.selection("asset",frame,lane,pos)
                    self.assertIn((len(a),len(b)),cr.layers(frame,lane))
                    self.assertEqual(len(set(a)),len(a))
                    self.assertEqual(len(set(b)),len(b))
        s=cr.plan("asset","1d","same_family",0)
        self.assertEqual(s["rules"][0]["family"],s["rules"][1]["family"])
        self.assertNotEqual(s["rules"][0]["rule"],s["rules"][1]["rule"])
        self.assertEqual(s["rules"],cr.plan("asset","1d","same_family",1)["rules"])
        self.assertEqual({cr.plan("asset","1d","bank_pair",i)["direction"] for i in range(3)},set(cr.coverage.DIRECTIONS))

    def test_source_cross_and_flow_event_signals_are_causal(self):
        bars=market_frame(1200)
        for lane in ("bank_pair","cross_pair","legacy_pair","same_family","triple","medium","higher","technical_settings"):
            spec=cr.plan("asset","1d",lane,0)
            a,ok=cr.technical_target(bars,spec)
            b,valid=cr.technical_target(bars.iloc[:-50],spec)
            pd.testing.assert_series_equal(a.iloc[:-50],b)
            pd.testing.assert_series_equal(ok.iloc[:-50],valid)
        for e in cr.MARKET_EVENTS:
            cond={"name":e,**cr.market_event_settings(e,random.Random(1),False)}
            a,ok=cr.market_event(bars,cond)
            b,valid=cr.market_event(bars.iloc[:-50],cond)
            pd.testing.assert_series_equal(a.iloc[:-50],b)
            pd.testing.assert_series_equal(ok.iloc[:-50],valid)

    def test_macro_clock_no_same_day_event_and_only_avoid_modes(self):
        bars=market_frame(10)
        spec={"event_logic":"all","events":[{"name":"US_CPI","offset":1,"lookback":1}]}
        dates={"US_CPI":[str(bars.index[3].date())]}
        flag,valid=cr.event_gate(bars,spec,dates)
        self.assertFalse(flag.iloc[3]);self.assertTrue(flag.iloc[4])
        self.assertEqual(flag.sum(),1)
        # A subsequent next-open execution sees the event no earlier than bar 5.
        self.assertTrue(flag.shift(1,fill_value=False).iloc[5])
        self.assertTrue(valid.all())

    def test_worker_persists_actual_cross_and_event_trials(self):
        bars=market_frame(2200)
        dates={name:[str(x.date()) for x in bars.index[::11]] for name in events.FAMILIES}
        cutoffs={name:str(bars.index[-1].date()) for name in events.FAMILIES}
        with engine.connect(":memory:") as db:
            first=cr.run_asset(db,"CRYPTO:TEST",data.MARKETS[0],bars,dates,cutoffs)
            self.assertEqual(first["1d"]["bank_pair"]["status"],"evaluated")
            self.assertEqual(first["1d"]["cross_pair"]["status"],"evaluated")
            self.assertIn("mixed_pair",first["1d"])
            self.assertEqual(cr.cursor(db,"CRYPTO:TEST","1d","bank_pair"),1)
            for _ in range(9): cr.run_asset(db,"CRYPTO:TEST",data.MARKETS[0],bars,dates,cutoffs)
            rows=list(db.execute("SELECT * FROM attempts WHERE version=?",(cr.VERSION,)))
            self.assertGreater(len(rows),10)
            self.assertTrue(any(r["family"]=="combination:1d:cross_pair" for r in rows))
            self.assertTrue(any(json.loads(r["spec"])["events"] for r in rows))
            for r in rows:
                d,v=json.loads(r["discovered"]),json.loads(r["validated"])
                self.assertFalse(d["pass"]); self.assertFalse(v["paper_eligible"])
                ev=d["return_evidence"]
                self.assertEqual(ev["sha256"],research.digest({k:x for k,x in ev.items() if k!="sha256"}))
                self.assertAlmostEqual(math.prod(1+x for x in ev["discovery"])-1,d["metrics"]["net"])
                self.assertAlmostEqual(math.prod(1+x for x in ev["validation"])-1,v["metrics"]["net"])
                if json.loads(r["spec"])["events"]: self.assertIn("matched_control",v)
            coverage=cr.summary(db)
            self.assertTrue(coverage)
            for item in coverage:
                self.assertEqual(int(item["covered_templates"])+int(item["not_covered"]),
                                 int(item["registered_templates"]))
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],0)

    def test_blocked_history_and_event_data_are_not_counted_and_retried(self):
        bars=market_frame(800)
        dates={}
        with engine.connect(":memory:") as db:
            db.execute(cr.SCHEMA);db.execute(cr.PENDING)
            # Pick a registered macro-event variant deterministically.
            position=next(i for i in range(cr.total("1d","event_single"))
                          if cr.plan("asset","1d","event_single",i)["events"][0]["name"]=="US_CPI")
            cr.set_cursor(db,"asset","1d","event_single",position)
            r=cr.run_lane(db,"asset",data.MARKETS[0],bars,"1d","event_single",{}, {},"a")
            self.assertEqual(r["status"],"blocked_event_data")
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM combination_pending").fetchone()[0],1)
            # Missing data cannot starve the next rank; a failed old retry may
            # be followed by another new design.
            r=cr.run_lane(db,"asset",data.MARKETS[0],bars,"1d","event_single",{}, {},"b")
            self.assertEqual(cr.cursor(db,"asset","1d","event_single"),position+2)
            days={"US_CPI":[str(x.date()) for x in bars.index[::10]]}
            cuts={"US_CPI":str(bars.index[-1].date())}
            r=cr.run_lane(db,"asset",data.MARKETS[0],bars,"1d","event_single",days,cuts,"c")
            self.assertEqual(r["rank"],str(position))
            self.assertEqual(r["status"],"evaluated")

    def test_event_budget_and_deadline_do_not_fake_tests(self):
        bars=market_frame(800)
        with engine.connect(":memory:") as db:
            r=cr.run_asset(db,"asset",data.MARKETS[0],bars,{}, {},deadline=0)
            self.assertEqual(r["1d"]["status"],"deferred_runtime")
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0],0)
            r=cr.run_asset(db,"asset",data.MARKETS[0],bars,{}, {},event_enabled=False)
            self.assertNotIn("mixed_pair",r["1d"])
            self.assertTrue(any(row["frame"]=="1w" and row["covered_templates"]=="0" for row in cr.summary(db)))
