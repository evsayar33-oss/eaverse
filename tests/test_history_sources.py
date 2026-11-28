"""Regression coverage for expanded public-source listings and history."""
import json
import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pandas as pd

from eaverse import data, engine, history_sources as hs, research, universe
from test_research import market_frame


NOW=datetime(2026,11,26,15,tzinfo=UTC)


class HistorySourcesTests(unittest.TestCase):
    def test_venue_identity_and_spot_filters(self):
        rows={"symbols":[
            {"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","status":"TRADING","isSpotTradingAllowed":True},
            {"symbol":"USDCUSDT","baseAsset":"USDC","quoteAsset":"USDT","status":"TRADING","isSpotTradingAllowed":True},
            {"symbol":"BTCUSD","baseAsset":"BTC","quoteAsset":"USD","status":"BREAK","isSpotTradingAllowed":True},
            {"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","status":"TRADING","isSpotTradingAllowed":False},
        ]}
        self.assertEqual(hs.venue_symbols("BINANCE_SPOT",rows),{"BINANCE_SPOT:BTCUSDT"})
        rows2={"retCode":0,"result":{"category":"spot","list":[
            {"symbol":"BTCUSDC","baseCoin":"BTC","quoteCoin":"USDC","status":"Trading"},
            {"symbol":"AAPLXUSDT","baseCoin":"AAPLX","quoteCoin":"USDT","status":"Trading","symbolType":"xstocks"}]}}
        self.assertEqual(hs.venue_symbols("BYBIT_SPOT", rows2),{"BYBIT_SPOT:BTCUSDC"})
        rows3={"code":"0","data":[{"instId":"BTC-USDT","baseCcy":"BTC","quoteCcy":"USDT","state":"live","instType":"SPOT"},
                                 {"instId":"BTC-USDT-SWAP","baseCcy":"BTC","quoteCcy":"USDT","state":"live","instType":"SWAP"}]}
        self.assertEqual(hs.venue_symbols("OKX_SPOT",rows3),{"OKX_SPOT:BTC-USDT"})
        market=hs.market_for_symbol(data.MARKETS[0],"BINANCE_SPOT:BTCUSDT")
        self.assertEqual(market.source,"binance_spot")
        self.assertEqual(hs.market_for_symbol(data.MARKETS[0],"BTC-USD").source,"coinbase")

    def test_coinbase_long_retains_more_than_six_years(self):
        floor=hs.closed_end(NOW)-timedelta(days=2700)
        def page(symbol,start,end):
            return [[int(t.timestamp()),9,11,10,10.5,100]
                    for t in pd.date_range(max(floor,start),end-timedelta(days=1),freq="D")]
        with patch.object(data,"candle_request",side_effect=page):
            bars=hs.coinbase_long("BTC-USD",NOW)
        self.assertEqual(len(bars),2700)
        self.assertEqual(str(bars.index[0].date()),str(floor.date()))
        self.assertEqual(bars.attrs["history_status"],"provider_history_boundary")

    def test_venue_pagination_no_cross_source(self):
        end=int(hs.closed_end(NOW).timestamp()*1000)
        rows=[[str(end-i*hs.DAY_MS),"10","11","9","10.5","100","0","0","1"] for i in range(1,801)]
        def page(venue,pair,before):
            self.assertEqual((venue,pair),("OKX_SPOT","BTC-USDT"))
            return [r for r in rows if int(r[0])<before][:100]
        with patch.object(hs,"venue_page",side_effect=page):
            bars=hs.load(data.MARKETS[0],"OKX_SPOT:BTC-USDT",NOW)
        self.assertEqual(len(bars),800)
        self.assertEqual(bars.attrs["history_through"],"2026-11-25")

    def test_unfinished_okx_rejected(self):
        stamp=int(hs.closed_end(NOW).timestamp()*1000)-hs.DAY_MS
        with patch.object(hs,"venue_page",return_value=[[str(stamp),"10","11","9","10","1","0","0","0"]]):
            with self.assertRaisesRegex(ValueError,"unconfirmed"):
                hs.venue_history("OKX_SPOT","BTC-USDT",NOW)

    def test_gaps_never_filled(self):
        end=hs.closed_end(NOW)
        rows={end-timedelta(days=i):(10,11,9,10.5,100) for i in range(1,801) if i!=700}
        bars=hs.contiguous_crypto(rows,end,"provider_history_boundary",hs.CRYPTO_START)
        self.assertEqual(len(bars),699)
        self.assertEqual(bars.attrs["history_status"],"truncated_gap")
        rows.pop(end-timedelta(days=1))
        with self.assertRaises(ValueError):
            hs.contiguous_crypto(rows,end,"provider_history_boundary",hs.CRYPTO_START)

    def test_equities_1990_request_and_completed_calendar(self):
        market=data.MARKETS[1]
        cal=hs.equity_calendar(market.calendar,NOW.date())
        sessions=cal.sessions[cal.sessions<=pd.Timestamp(NOW.date())]
        sessions=sessions[(cal.schedule.loc[sessions,"close"]<=pd.Timestamp(NOW-timedelta(minutes=90))).to_numpy()]
        dates=sessions[-3000:]
        frame=pd.DataFrame({c:([11]*len(dates) if c=="high" else [9]*len(dates) if c=="low" else [10]*len(dates)) for c in data.COLUMNS},index=dates)
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value=frame
            bars=hs.equities_long("AAPL",market,NOW)
            self.assertEqual(ticker.return_value.history.call_args.kwargs["start"],"1990-01-01")
        self.assertEqual(len(bars),3000)
        self.assertEqual(bars.attrs["history_from"],str(dates[0].date()))

    def bist_fixture(self):
        market=data.MARKETS[2]
        cal=hs.equity_calendar(market.calendar,NOW.date())
        completed=cal.sessions[cal.schedule["close"]<=pd.Timestamp(NOW-timedelta(minutes=90))]
        dates=completed[-1000:]
        bars=pd.DataFrame({"open":10.0,"high":11.0,"low":9.0,"close":10.5,"volume":100.0},index=dates)
        return market,bars

    def test_bist_recovers_actual_daily_candle_with_same_end_and_adjustment(self):
        market,valid=self.bist_fixture()
        latest=valid.index[-1]
        damaged=valid.copy()
        damaged.loc[latest,["open","high","low","close"]]=float("nan")
        damaged.iloc[10,3]=-1
        # Older historic invalid rows must not poison the current window.
        with patch("yfinance.Ticker") as ticker:
            history=ticker.return_value.history
            history.side_effect=[damaged,valid.iloc[[-1]]]
            result=hs.load(market,"THYAO.IS",NOW)
        pd.testing.assert_frame_equal(result,valid.iloc[11:],check_freq=False)
        self.assertTrue(result.attrs["latest_daily_refetched"])
        self.assertEqual(result.attrs["excluded_history_through"],str(valid.index[10].date()))
        self.assertEqual(result.attrs["history_status"],"truncated_history_integrity:exact_daily_refetch")
        a,b=[c.kwargs for c in history.call_args_list]
        self.assertEqual(a["end"],b["end"])
        self.assertEqual(a["end"],str((latest+pd.Timedelta(days=1)).date()))
        self.assertEqual(b["start"],str(latest.date()))
        self.assertTrue(a["auto_adjust"] and b["auto_adjust"])
        self.assertEqual(b["interval"],"1d")

    def test_bist_exact_refetch_rejects_conflict_or_missing_latest(self):
        market,valid=self.bist_fixture()
        damaged=valid.copy()
        damaged.iloc[-1,3]=float("nan")
        conflict=valid.iloc[[-1]].copy()
        conflict.iloc[0,4]=101
        for replacement in (conflict,valid.iloc[[-2]],valid.iloc[0:0],damaged.iloc[[-1]]):
            with self.subTest(replacement=replacement),patch("yfinance.Ticker") as ticker,patch.object(data,"load") as legacy:
                ticker.return_value.history.side_effect=[damaged,replacement]
                with self.assertRaisesRegex(hs.BistHistoryError,"latest completed session"):
                    hs.load(market,"THYAO.IS",NOW)
                legacy.assert_not_called()
                self.assertEqual(ticker.return_value.history.call_count,2)

    def test_bist_missing_or_unfinished_days_are_not_filled(self):
        market,valid=self.bist_fixture()
        # No exact latest bar in the long request: fetch it from the same provider.
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.side_effect=[valid.iloc[:-1],valid.iloc[[-1]]]
            result=hs.load(market,"THYAO.IS",NOW)
        pd.testing.assert_frame_equal(result,valid,check_freq=False)
        # A recent missing session cannot be hidden by dropping only the hole.
        damaged=valid.drop(index=valid.index[-30])
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value=damaged
            with self.assertRaisesRegex(hs.BistHistoryError,"600 required"):
                hs.load(market,"THYAO.IS",NOW)
            self.assertEqual(ticker.return_value.history.call_count,1)

    def test_bist_calendar_conflict_is_trimmed_only_when_old(self):
        market,valid=self.bist_fixture()
        weekend=pd.Timestamp("2024-01-06")
        self.assertNotIn(weekend,valid.index)
        extra=valid.iloc[[0]].copy()
        extra.index=pd.DatetimeIndex([weekend])
        damaged=pd.concat([valid,extra]).sort_index()
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value=damaged
            result=hs.load(market,"THYAO.IS",NOW)
        pd.testing.assert_frame_equal(result,valid.loc[valid.index>weekend],check_freq=False)
        self.assertEqual(result.attrs["excluded_history_through"],str(weekend.date()))
        self.assertFalse(result.attrs["latest_daily_refetched"])

    def test_bist_short_listing_keeps_minimum_and_does_not_repeat_requests(self):
        market,valid=self.bist_fixture()
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value=valid.iloc[-180:]
            with self.assertRaisesRegex(hs.InsufficientEquityHistory,"fewer than 600"):
                hs.load(market,"NEW.IS",NOW)
            self.assertEqual(ticker.return_value.history.call_count,1)

    def test_bist_mature_probe_does_not_expand_budget_or_displace_paper(self):
        market=data.MARKETS[2]
        symbols=list(market.symbols)+["NEW1.IS","NEW2.IS","NEW3.IS"]
        with engine.connect(":memory:") as db:
            db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                VALUES ('BIST:TRACKED.IS','{}',25,'x','x','x','x','x','x','{}','active')""")
            for s in market.symbols:
                universe.record(db,market.name,s,NOW,"unavailable")
            selected=universe.queue(db,market,symbols,NOW.date(),4)
            self.assertEqual(selected[0],"TRACKED.IS")
            self.assertIn(selected[1],market.symbols)
            self.assertEqual(len(selected),4)
            self.assertEqual(len(set(selected)&set(market.symbols)),1)
            self.assertEqual(universe.queue(db,market,symbols,NOW.date(),1),["TRACKED.IS"])

    def test_legacy_cache_preserved_even_with_other_live_source(self):
        market=data.MARKETS[0]
        day=date(2026,11,26)
        with engine.connect(":memory:") as db:
            db.execute("INSERT INTO listings VALUES (?,?,?,?,?)",(market.name,universe.COINBASE,"2026-11-25","2026-11-25",json.dumps(["OLD-USD"])))
            output=[(url,{"BINANCE_SPOT:BTCUSDT"} if url==hs.LIST_ENDPOINTS["BINANCE_SPOT"] else None,
                     None if url==hs.LIST_ENDPOINTS["BINANCE_SPOT"] else "unavailable")
                    for url,_,_ in universe.source_definitions("CRYPTO")]
            with patch.object(universe,"fetch",return_value=output):
                symbols,report=universe.refresh(db,market,day)
            self.assertIn("OLD-USD",symbols)
            self.assertIn("BINANCE_SPOT:BTCUSDT",symbols)
            self.assertEqual(report["coverage_mode"],"partial_live")
            self.assertEqual(report["historical_listing_candidates_not_price_verified"],0)
            self.assertEqual({s["source"]:s["status"] for s in report["sources"]}[universe.COINBASE],"cached")

    def test_us_union_and_actual_observed_history(self):
        market=data.MARKETS[1]
        with engine.connect(":memory:") as db:
            output=[(universe.SEC,{"AAA","BBB"},None),(universe.NASDAQ_SCREEN,{"BBB","CCC"},None),(universe.NASDAQ,{"DDD"},None)]
            with patch.object(universe,"fetch",return_value=output):
                symbols,report=universe.refresh(db,market,date(2026,11,25))
            self.assertEqual(report["listed"],4)
            self.assertTrue({"AAA","BBB","CCC","DDD"}.issubset(symbols))
            output[0]=(universe.SEC,{"BBB"},None)
            with patch.object(universe,"fetch",return_value=output):
                symbols,report=universe.refresh(db,market,date(2026,11,26))
            self.assertNotIn("AAA",symbols)
            self.assertEqual(report["observed_listing_symbols"],4)
            self.assertEqual(report["historical_listing_candidates_not_price_verified"],1)

    def test_bist_codes_and_venue_rotation(self):
        self.assertEqual(universe.kap_symbols('<a href="/tr/sirket-bilgileri/ozet/1"><div>AAA, BBB</div>'),{"AAA.IS","BBB.IS"})
        self.assertEqual(universe.bist_etf_symbols('<td><b>APGLD.F</b></td><td>Not a ticker</td>'),{"APGLD.IS"})
        market=data.MARKETS[0]
        with engine.connect(":memory:") as db:
            pool=["BTC-USD","ETH-USD","BINANCE_SPOT:AUSDT","BINANCE_SPOT:BUSDT","BYBIT_SPOT:AUSDT","OKX_SPOT:A-USDT"]
            result=universe.queue(db,market,pool,NOW.date(),4)
            self.assertEqual(set(result),{"BTC-USD","BINANCE_SPOT:AUSDT","BYBIT_SPOT:AUSDT","OKX_SPOT:A-USDT"})

    def test_new_venue_success_is_research_only(self):
        market=hs.market_for_symbol(data.MARKETS[0],"BINANCE_SPOT:BTCUSDT")
        bars=market_frame(1000)
        with engine.connect(":memory:") as db, \
                patch.object(research,"history_metrics",return_value={"pass":True,"net":0.5,"drawdown":-0.05}), \
                patch.object(research,"direction_check",return_value={}), \
                patch("eaverse.reference.audit_validation",return_value={"status":"matched"}):
            result=engine.discover(db,"CRYPTO:BINANCE_SPOT:BTCUSDT",market,bars,NOW,1)
            self.assertTrue(result["candidate"]["validation"]["pass"])
            self.assertFalse(result["paper_eligible"])
            self.assertEqual(db.execute("SELECT source FROM attempts").fetchone()[0],"binance_spot")
            self.assertEqual(db.execute("SELECT count(*) FROM paper").fetchone()[0],0)

    def test_new_venues_never_use_legacy_prices(self):
        with patch.object(hs,"venue_history",side_effect=ValueError("provider unavailable")),patch.object(data,"load") as legacy:
            with self.assertRaisesRegex(ValueError,"provider unavailable"):
                hs.load(data.MARKETS[0],"BINANCE_SPOT:BTCUSDT",NOW)
        legacy.assert_not_called()


if __name__=="__main__":
    unittest.main()
