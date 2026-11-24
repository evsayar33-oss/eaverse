import random
import unittest
from datetime import UTC,datetime
import pandas as pd
from eaverse import surface, cohort, foundry, data, engine
from test_research import market_frame

class SurfaceTests(unittest.TestCase):
    def test_every_family_causal(self):
        b=market_frame(1500)
        for name in surface.FAMILIES:
            rule=surface.choose("1d",name,random.Random(12))
            a=surface.measure(b,rule)
            c=surface.measure(b.iloc[:-30],rule)
            pd.testing.assert_series_equal(a.iloc[:-30],c)
            self.assertTrue(a.notna().any(),name)
    def test_cohort_dates_and_paths(self):
        b=market_frame(2000)
        report=cohort.study(b,"test",10,500)
        self.assertIsNotNone(report)
        p=report["panel"]
        self.assertEqual(len(p["returns"]),len(p["dates"]))
        self.assertEqual(len(p["ids"]),cohort.VARIANTS)
        self.assertEqual(len(set(p["dates"])),len(p["dates"]))
        self.assertFalse(report["paper_eligible"])
    def test_registration_no_fake_forward_and_revision_block(self):
        b=market_frame(2000)
        now=b.index[-1].to_pydatetime().replace(tzinfo=UTC)
        with engine.connect(":memory:") as db:
            r=cohort.run_asset(db,"CRYPTO:test",data.MARKETS[0],b,now)
            self.assertEqual(r["status"],"registered")
            r=cohort.run_asset(db,"CRYPTO:test",data.MARKETS[0],b,now)
            self.assertEqual(r["observed_new_bars"],0)
            altered=b.copy()
            altered.iloc[-1,0]*=1.001
            r=cohort.run_asset(db,"CRYPTO:test",data.MARKETS[0],altered,now)
            self.assertEqual(r["status"],"blocked_source_revision")
