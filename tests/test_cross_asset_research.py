import sqlite3,unittest
import numpy as np
import pandas as pd
from eaverse import cross_asset_research as c,engine
class CrossAssetResearchTests(unittest.TestCase):
 def panel(self):
  rng=np.random.default_rng(18);n=520;common=rng.normal(0,.012,n);idx=pd.date_range("2022-01-01",periods=n,freq="D");out={}
  for i,name in enumerate(("US:A","US:B","US:C","US:D")):
   close=100*np.exp(np.cumsum((.5+i*.1)*common+rng.normal(0,.006,n)));out[name]=pd.DataFrame({"close":close},index=idx)
  return out
 def test_train_only_and_no_promotion(self):
  r=c.analyze(self.panel());self.assertEqual(r["pca"]["fit"],"DISCOVERY_ONLY");self.assertLess(r["split"]["train_end"],r["split"]["validation_start"]);self.assertTrue(r["pairs"]);self.assertFalse(r["paper_eligible"]);self.assertFalse(r["trade_authorized"])
 def test_persistent_once(self):
  db=sqlite3.connect(":memory:");db.executescript(engine.SCHEMA);self.assertNotEqual(c.run(db,self.panel())["status"],"duplicate_not_counted");self.assertEqual(c.run(db,self.panel())["status"],"duplicate_not_counted")
