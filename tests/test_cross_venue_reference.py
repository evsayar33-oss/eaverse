"""Kraken/Coinbase independently observed USD daily candles; no false trade fallback."""
import json
import unittest
from datetime import datetime,timedelta,timezone

from eaverse import cross_venue_reference as cv


def feeds(asset,now,*,diff=0):
    last=(now-timedelta(days=1)).date()
    coinbase,kraken=[],[]
    for day in range(55):
        d=last-timedelta(days=day)
        start=datetime.combine(d,datetime.min.time(),tzinfo=timezone.utc)
        ts=int(start.timestamp())
        price=100+day*.01
        coinbase.append([ts,price-1,price+1,price,price,120])
        kraken.append([ts,str(price),str(price+1),str(price-1),
                       str(price*(1+diff)),str(price),str(120),42])
    kraken.insert(0,[int(now.timestamp()),"101","102","100","101","101","120",42])
    return coinbase, {"error":[],"result":{"someExchangePair":kraken,"last":1000}}


class IndependentVenues(unittest.TestCase):
    def test_two_real_provider_response_shapes_and_only_comparison(self):
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        def fetch(url):
            coinbase,kraken=feeds("BTC",now)
            return (coinbase,"a"*64) if "coinbase" in url else (kraken,"b"*64)
        z=cv.compare("BTC",asof=now,fetcher=fetch)
        self.assertEqual(z["overlapping_completed_dates"],55)
        self.assertEqual(z["coinbase_completed_daily"],55)
        self.assertEqual(z["kraken_completed_daily"],55)
        self.assertEqual(z["median_absolute_close_difference_bps"],0)
        self.assertEqual(z["status"],"INDEPENDENT_USD_SPOT_PRICE_CORROBORATION_ONLY")
        self.assertFalse(z["binance_usdt_taker_data_can_be_replaced"])
        self.assertFalse(z["paper_eligible"])

    def test_large_venue_discrepancy_not_silently_arbitraged(self):
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        def fetch(url):
            coinbase,kraken=feeds("ETH",now,diff=.04)
            return (coinbase,"a"*64) if "coinbase" in url else (kraken,"b"*64)
        z=cv.compare("ETH",asof=now,fetcher=fetch)
        self.assertEqual(z["status"],"INDEPENDENT_USD_PRICE_DIFFERENCE_HIGH")
        self.assertFalse(z["cross_venue_spread_proves_execution"])

    def test_unfinished_candle_never_counts(self):
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        cb,k=feeds("BTC",now)
        cb.append([int(now.timestamp()),1,2,1.5,1.5,20])
        self.assertEqual(len(cv._coinbase(cb,(now-timedelta(days=1)).date())),55)
        self.assertEqual(len(cv._kraken(k,(now-timedelta(days=1)).date())),55)

    def test_bad_schema_duplicate_timestamp_or_no_overlap_fail(self):
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        cb,k=feeds("BTC",now)
        with self.assertRaisesRegex(ValueError,"COINBASE_DUPLICATE_DATE"):
            cv._coinbase(cb+cb[:1],(now-timedelta(days=1)).date())
        with self.assertRaisesRegex(ValueError,"KRAKEN_UNEXPECTED_PAIR"):
            cv._kraken({"error":[],"result":{"last":123}},(now-timedelta(days=1)).date())
        with self.assertRaisesRegex(ValueError,"OVERLAP"):
            cv.compare("BTC",asof=now,fetcher=lambda url:
                       (cb[:5],"a"*64) if "coinbase" in url else (k,"b"*64))

    def test_network_failure_is_explicit_no_synthetic_source(self):
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        z=cv.report(fetcher=lambda url: (_ for _ in ()).throw(
            ValueError("endpoint unavailable")),asof=now)
        self.assertEqual(set(z["sources"]),{"BTC","ETH"})
        self.assertTrue(all(x["status"]=="INDEPENDENT_DATA_SOURCE_NOT_VERIFIED"
                            for x in z["sources"].values()))
        self.assertFalse(z["trading_source_switched"])

    def test_unsupported_instruments_blocked(self):
        with self.assertRaisesRegex(ValueError,"UNREGISTERED"):
            cv.compare("DOGE")


if __name__=="__main__":
    unittest.main()
