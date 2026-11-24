import unittest
import numpy as np
import pandas as pd
from eaverse import foundry, data, engine
from test_research import market_frame


class FoundryTests(unittest.TestCase):
    def test_family_rotation_and_generation(self):
        families=[(lambda rule: rule[1] if rule[0]=="surface" else rule[0])(foundry.plan("asset","1d","parameter",i)["rules"][0]) for i in range(len(foundry.FAMILIES))]
        self.assertEqual(set(families),set(foundry.FAMILIES))
        self.assertEqual(len(set(str(foundry.plan("asset","1d","grammar",i)) for i in range(100))),100)
        self.assertEqual(foundry.plan("a","1d","risk",1),foundry.plan("a","1d","risk",1))

    def test_no_future_signals(self):
        bars=market_frame(800)
        for i in range(len(foundry.FAMILIES)):
            spec=foundry.plan("asset","1d","grammar",i)
            a=foundry.desired(bars,spec)
            b=foundry.desired(bars.iloc[:-20],spec)
            pd.testing.assert_series_equal(a.iloc[:-20],b)

    def test_ambiguous_bar_stop_first(self):
        idx=pd.date_range("2025-01-01",periods=5)
        bars=pd.DataFrame({"open":[100.]*5,"high":[101.,101.,110.,101.,101.],"low":[99.,99.,90.,99.,99.],"close":[100.]*5,"volume":[1.]*5},index=idx)
        target=pd.Series([0.,1.,1.,1.,1.],index=idx)
        risk={"atr_window":1,"stop":1.,"tp":1.,"trail":None,"partial":False,"staged":False,"max_hold":None}
        m,p=foundry.execute(bars,target,2,5,risk,0)
        self.assertAlmostEqual(m["net"],-.02)
        self.assertEqual(m["entries"],1)
        self.assertAlmostEqual(np.prod(1+np.array(p))-1,m["net"])

    def test_gap_stop_and_partial(self):
        idx=pd.date_range("2025-01-01",periods=6)
        bars=pd.DataFrame({"open":[100.,100.,100.,95.,95.,95.],"high":[101.,101.,103.,96.,96.,96.],"low":[99.,99.,99.,94.,94.,94.],"close":[100.,100.,102.,95.,95.,95.],"volume":[1.]*6},index=idx)
        target=pd.Series([0.,1.,1.,1.,1.,1.],index=idx)
        risk={"atr_window":1,"stop":1.,"tp":1.,"trail":None,"partial":True,"staged":False,"max_hold":None}
        m,_=foundry.execute(bars,target,2,6,risk,0)
        self.assertEqual(m["partial_exits"],1)
        self.assertEqual(m["entries"],1)
        self.assertAlmostEqual(m["net"],-.015)

    def test_cursor_and_no_paper(self):
        bars=market_frame(2000)
        with engine.connect(":memory:") as db:
            with db:
                first=foundry.run_asset(db,"asset",data.MARKETS[0],bars)
                second=foundry.run_asset(db,"asset",data.MARKETS[0],bars)
            self.assertEqual(first["1d:parameter"]["position"],0)
            self.assertEqual(second["1d:parameter"]["position"],1)
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],0)
