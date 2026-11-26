import json
import random
import unittest
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from eaverse import data, engine, indicator_bank as bank, indicator_research as ir, research
from test_research import market_frame


class IndicatorTests(unittest.TestCase):
    def test_requested_and_all_single_families_are_reachable(self):
        specs=[ir.plan("asset","1d",2*i) for i in range(len(bank.FAMILIES))]
        self.assertEqual([s["rules"][0]["name"] for s in specs],list(bank.FAMILIES))
        mixes=[ir.plan("asset","1d",i) for i in (1,3,5)]
        self.assertEqual({s["logic"] for s in mixes},{"majority","all","any"})
        for s in mixes:
            self.assertEqual(tuple(r["name"] for r in s["rules"]),bank.REQUESTED)
        for name in bank.FAMILIES:
            rng=random.Random(name)
            variants={research.canonical(bank.choose(name,rng)) for _ in range(40)}
            self.assertGreater(len(variants),15,name)

    def test_formulas_use_no_future_bars_in_multiple_parameter_settings(self):
        bars=market_frame(1100)
        for name in bank.FAMILIES:
            rng=random.Random(name)
            for i in range(6):
                rule=bank.choose(name,rng,default=i==0)
                with self.subTest(name=name,rule=rule):
                    m=bank.measure(bars,rule)
                    prefix=bank.measure(bars.iloc[:-35],rule)
                    pd.testing.assert_series_equal(m.iloc[:-35],prefix)
                    self.assertGreater(m.iloc[bank.warmup(rule):].notna().sum(),0)
                    flag,valid=bank.signal(bars,rule)
                    self.assertFalse((flag&~valid).any())

    def test_cci_mean_absolute_deviation_rsi_and_stochastic_rsi(self):
        bars=market_frame(300)
        rng=random.Random(4)
        cci=bank.choose("cci",rng,True)
        cci["params"]["n"]=5
        prices=(bars.high+bars.low+bars.close)/3
        a=prices.iloc[-5:]
        expected=(a.iloc[-1]-a.mean())/(.015*(a-a.mean()).abs().mean())
        self.assertAlmostEqual(bank.measure(bars,cci).iloc[-1],expected)
        rule=bank.choose("stochastic_rsi",rng,True)
        r=bank.rsi(bars.close,14,"wilder")
        expected=(100*(r-r.rolling(14).min())/(r.rolling(14).max()-r.rolling(14).min())).rolling(3).mean()
        pd.testing.assert_series_equal(bank.measure(bars,rule),expected)
        normal=bank.measure(bars,bank.choose("stochastic",rng,True))
        self.assertFalse(normal.equals(expected))
        rising=pd.Series(range(1,100),dtype=float)
        self.assertAlmostEqual(bank.rsi(rising,14,"wilder").iloc[-1],100.)

    def test_displaced_cloud_uses_historical_spans_and_bollinger_parameters(self):
        bars=market_frame(200)
        rng=random.Random(4)
        cloud=bank.choose("ichimoku",rng,True)
        actual=bank.measure(bars,cloud)
        def midpoint(n):
            past=bars.iloc[:-26].iloc[-n:]
            return (past.high.max()+past.low.min())/2
        a=(midpoint(9)+midpoint(26))/2
        b=midpoint(52)
        c=bars.close.iloc[-1]
        expected=100*(max(0,c-max(a,b))+min(0,c-min(a,b)))/c
        self.assertAlmostEqual(actual.iloc[-1],expected)
        self.assertTrue(actual.iloc[:77].isna().all())
        bb=bank.choose("bollinger",rng,True)
        x=bars.close.iloc[-20:]
        expected=(x.iloc[-1]-x.mean()+2*x.std(ddof=0))/(4*x.std(ddof=0))
        self.assertAlmostEqual(bank.measure(bars,bb).iloc[-1],expected)
        before=bank.measure(bars,bb)
        bb["params"]["multiplier"]=3
        self.assertFalse(before.equals(bank.measure(bars,bb)))

    def test_wilder_seed_and_supertrend_bands(self):
        x=pd.Series([1.,2.,3.,6.,9.])
        np.testing.assert_allclose(bank.average(x,3,"wilder").iloc[2:],[2.,10/3,47/9])
        bars=pd.DataFrame({"open":[10.,10.,10.,6.,12.],"close":[10.,10.,10.,6.,12.],
                           "high":[11.,11.,11.,7.,13.],"low":[9.,9.,9.,5.,11.],
                           "volume":[1.]*5})
        values=bank.supertrend(bars,2,1,"sma")
        self.assertAlmostEqual(values.iloc[1],8.)
        self.assertAlmostEqual(values.iloc[2],8.)
        self.assertGreater(values.iloc[3],bars.close.iloc[3])
        self.assertLess(values.iloc[4],bars.close.iloc[4])

    def test_flat_stochastic_rsi_is_undefined_not_a_trade(self):
        bars=market_frame(200)
        bars[["open","high","low","close"]]=100.
        rule=bank.choose("stochastic_rsi",random.Random(1),True)
        flag,valid=bank.signal(bars,rule)
        self.assertFalse(valid.any())
        self.assertFalse(flag.any())
        self.assertEqual(bank.rsi(bars.close,14,"wilder").iloc[-1],50.)

    def test_persistent_queue_and_completed_trials_by_family(self):
        bars=market_frame(850)
        with engine.connect(":memory:") as db:
            for i in range(2*len(bank.FAMILIES)):
                result=ir.run_asset(db,"CRYPTO:TEST",data.MARKETS[0],bars)
                self.assertEqual(result["1d"]["position"],i)
                self.assertNotEqual(result["1d"]["status"],"deferred_runtime")
            counts={r["indicator"]:r["daily"] for r in ir.counts(db)}
            for name in bank.FAMILIES:
                self.assertEqual(counts[name],1,name)
            self.assertGreaterEqual(counts["combination"],3)
            for row in db.execute("SELECT * FROM attempts"):
                d,v=json.loads(row["discovered"]),json.loads(row["validated"])
                self.assertFalse(d["pass"])
                self.assertFalse(v["paper_eligible"])
                ev=d["return_evidence"]
                self.assertEqual(ev["sha256"],research.digest({k:x for k,x in ev.items() if k!="sha256"}))
                for key,metrics in (("discovery",d["metrics"]),("validation",v["metrics"])):
                    self.assertAlmostEqual(float(np.prod(1+np.asarray(ev[key]))-1),metrics["net"])
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],0)
            first=db.execute("SELECT id FROM attempts ORDER BY rowid LIMIT 1").fetchone()[0]
            db.execute("UPDATE attempts SET spec='',policy='' WHERE id=?",(first,))
            self.assertEqual(ir.counts(db)[0]["daily"],1)

    def test_history_and_deadline_do_not_count_as_tests(self):
        with engine.connect(":memory:") as db:
            result=ir.run_asset(db,"asset",data.MARKETS[0],market_frame(130))
            self.assertEqual(result["1d"]["status"],"blocked_history")
            self.assertEqual(result["1w"]["status"],"blocked_history")
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0],0)
            # Same planned position retries once usable history exists.
            result=ir.run_asset(db,"asset",data.MARKETS[0],market_frame(850))
            self.assertEqual(result["1d"]["position"],0)
            self.assertEqual(result["1d"]["status"],"evaluated")
            self.assertEqual(ir.counts(db)[0]["daily"],1)
            result=ir.run_asset(db,"asset",data.MARKETS[0],market_frame(850),deadline=0)
            self.assertEqual(result["1d"]["status"],"deferred_runtime")
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0],1)
