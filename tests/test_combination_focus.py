import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from eaverse import combination_focus as focus, combination_research as cr, data, engine
from test_research import market_frame


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.today=date(2026,11,26)
        self.catalogs={"CRYPTO":["BTC-USD","ETH-USD"],"US":["SPY","AAPL"],"BIST":["THYAO.IS"]}
        self.bars=market_frame(2000)

    def tiny_total(self,frame,lane):
        return None if lane in cr.SETTINGS else 2 if lane in cr.CORE_LANES else 10

    def test_same_asset_survives_reopen_until_core_scanned_with_debt(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"state.sqlite"
            with patch.object(cr,"total",side_effect=self.tiny_total), patch.object(cr,"evaluate",
                side_effect=lambda db,asset,market,bars,spec,lane,dates,cutoffs:
                    ("blocked_event_data","id") if lane=="event_single" else ("evaluated","id")):
                with engine.connect(path) as db:
                    with db:
                        self.assertEqual(focus.select(db,self.catalogs,self.today),("CRYPTO:BTC-USD","foundation"))
                        one=focus.batch(db,"CRYPTO:BTC-USD",data.MARKETS[0],self.bars,{}, {},
                                        deadline=time.monotonic()+10,max_slots=5)
                        focus.complete_slice(db,"CRYPTO:BTC-USD",one,self.today)
                    self.assertEqual(one["status"],"in_progress")
                    self.assertGreater(one["foundation_total"]["visited"],0)
                with engine.connect(path) as db:
                    with db:
                        self.assertEqual(focus.select(db,self.catalogs,self.today+timedelta(days=1)),
                                         ("CRYPTO:BTC-USD","foundation"))
                        two=focus.batch(db,"CRYPTO:BTC-USD",data.MARKETS[0],self.bars,{}, {},
                                        deadline=time.monotonic()+10,max_slots=120)
                        self.assertEqual(two["status"],"foundation_scanned")
                        self.assertEqual(two["foundation_total"]["unvisited"],0)
                        self.assertEqual(two["foundation_total"]["blocked"],4)
                        self.assertGreater(two["foundation_total"]["evaluated"],0)
                        focus.complete_slice(db,"CRYPTO:BTC-USD",two,self.today)
                        previous=db.execute("SELECT status FROM combination_focus_history WHERE asset='CRYPTO:BTC-USD'").fetchone()[0]
                        self.assertEqual(previous,"foundation_waiting_data")
                        self.assertEqual(focus.select(db,self.catalogs,self.today),("US:SPY","foundation"))
                        self.assertEqual(focus.progress(db,"CRYPTO:BTC-USD")["1d"]["blocked"],2)

    def test_missing_weekly_and_disabled_events_not_complete(self):
        with engine.connect(":memory:") as db, patch.object(cr,"total",side_effect=lambda f,l:1 if l not in cr.SETTINGS else None), \
                patch.object(cr,"evaluate",return_value=("evaluated","id")):
            asset,mode=focus.select(db,self.catalogs,self.today)
            result=focus.batch(db,asset,data.MARKETS[0],market_frame(600),{}, {},
                                deadline=time.monotonic()+10,max_slots=100,event_enabled=False)
            self.assertEqual(result["status"],"waiting_frame_or_event_data")
            self.assertIn("1w",result["unavailable_frames"])
            self.assertGreater(result["foundation_total"]["unvisited"],0)
            self.assertEqual(result["foundation"]["1w"]["visited"],0)
            self.assertEqual(result["foundation_total"]["blocked"],0)
            focus.complete_slice(db,asset,result,self.today)
            self.assertEqual(focus.select(db,self.catalogs,self.today),("US:SPY","foundation"))

    def test_actual_worker_visits_single_and_mixed_lanes_and_reuses_cursor(self):
        with engine.connect(":memory:") as db:
            asset,mode=focus.select(db,self.catalogs,self.today)
            first=focus.batch(db,asset,data.MARKETS[0],self.bars,{}, {},
                              deadline=time.monotonic()+10,max_slots=40)
            self.assertGreater(first["evaluated"],0)
            self.assertIn("1d:single",first["lanes"])
            self.assertTrue(any(l.split(":")[1] not in ("single","event_single") for l in first["lanes"]))
            old=cr.cursor(db,asset,"1d","single")
            second=focus.batch(db,asset,data.MARKETS[0],self.bars,{}, {},
                               deadline=time.monotonic()+10,max_slots=40)
            self.assertGreater(cr.cursor(db,asset,"1d","single"),old)
            self.assertGreater(second["foundation_total"]["visited"],first["foundation_total"]["visited"])
            self.assertEqual(db.execute("SELECT DISTINCT asset FROM attempts").fetchall()[0][0],asset)
            self.assertEqual(db.execute("SELECT COUNT(DISTINCT asset) FROM attempts").fetchone()[0],1)

    def test_source_pause_and_periodic_continuation_no_reset(self):
        with engine.connect(":memory:") as db, patch.object(cr,"total",side_effect=self.tiny_total):
            asset,_=focus.select(db,self.catalogs,self.today)
            focus.release(db,asset,"waiting_source",self.today,7)
            next_asset,_=focus.select(db,self.catalogs,self.today)
            self.assertNotEqual(next_asset,asset)
            # A manual filter cannot displace ongoing saved focus.
            self.assertEqual(focus.select(db,{"CRYPTO":["BTC-USD","ETH-USD"]},self.today),(None,None))
            self.assertEqual(focus.dashboard(db)["active_asset"],next_asset)
            focus.release(db,next_asset,"foundation_waiting_data",self.today,1)
            # Simulate a completed finite foundation, while higher-order work stays open.
            for frame in cr.FRAMES:
                for lane in cr.CORE_LANES:
                    cr.set_cursor(db,next_asset,frame,lane,cr.total(frame,lane))
            focus.release(db,next_asset,"foundation_evaluated",self.today,1)
            db.execute("UPDATE combination_focus_state SET assignments=4 WHERE version=?",(cr.VERSION,))
            self.assertEqual(focus.select(db,self.catalogs,self.today+timedelta(days=1)),(next_asset,"continuation"))
