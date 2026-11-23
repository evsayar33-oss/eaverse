"""Independent Coinbase/Kraken USD price confirmation, never fallback trading data.

A recent public REST window does NOT provide Kraken's full downloadable
historical archive, Binance USDT quote equivalence, venue aggressor CVD,
funding, historic first-seen, or an executable trade strategy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

COINBASE = "https://api.exchange.coinbase.com"
KRAKEN = "https://api.kraken.com/0/public/OHLC"
SYMBOLS = {"BTC": ("BTC-USD", "XBTUSD"), "ETH": ("ETH-USD", "ETHUSD")}
MAX_BYTES = 1_500_000
MIN_DAYS = 25


def fetch_json(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("NON_HTTPS_SOURCE")
    if parsed.netloc == "api.exchange.coinbase.com":
        if parsed.path not in {f"/products/{s[0]}/candles" for s in SYMBOLS.values()}:
            raise ValueError("UNREGISTERED_COINBASE_PAIR")
    elif parsed.netloc == "api.kraken.com":
        if parsed.path != "/0/public/OHLC":
            raise ValueError("UNREGISTERED_KRAKEN_PATH")
    else:
        raise ValueError("UNREGISTERED_SOURCE_HOST")
    req = Request(url, headers={"User-Agent":"Eaverse-independent-noncommercial-research/1.0"})
    with urlopen(req, timeout=16) as res:
        raw = res.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError("CROSS_VENUE_SOURCE_TOO_LARGE")
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _coinbase(obj, last_closed):
    if not isinstance(obj, list):
        raise ValueError("COINBASE_UNEXPECTED_STRUCTURE")
    rows = {}
    for line in obj:
        if not isinstance(line, list) or len(line) != 6:
            raise ValueError("COINBASE_NOT_DAILY_CANDLE")
        timestamp,low,high,opn,close,volume = map(float,line)
        date=datetime.fromtimestamp(timestamp,timezone.utc)
        if date.date() > last_closed:
            continue
        if date.hour or date.minute or date.second or date.microsecond:
            raise ValueError("COINBASE_NON_MIDNIGHT_DAILY_ROW")
        if not (0<low<=min(opn,close)<=high and volume>=0):
            raise ValueError("COINBASE_INCONSISTENT_OHLC")
        key=date.date().isoformat()
        if key in rows:
            raise ValueError("COINBASE_DUPLICATE_DATE")
        rows[key]=close
    return rows


def _kraken(obj, last_closed):
    if obj.get("error"):
        raise ValueError("KRAKEN_API_ERROR")
    result=obj.get("result",{})
    names=[name for name in result if name!="last"]
    if len(names)!=1 or not isinstance(result[names[0]],list):
        raise ValueError("KRAKEN_UNEXPECTED_PAIR_STRUCTURE")
    rows={}
    for line in result[names[0]]:
        if not isinstance(line,list) or len(line)<7:
            raise ValueError("KRAKEN_BAD_OHLC_RECORD")
        stamp=float(line[0])
        date=datetime.fromtimestamp(stamp,timezone.utc)
        if date.date()>last_closed:
            continue
        if date.hour or date.minute or date.second or date.microsecond:
            raise ValueError("KRAKEN_NON_MIDNIGHT_DAILY_ROW")
        opn,hi,lo,close=(float(line[k]) for k in (1,2,3,4))
        if not 0<lo<=min(opn,close)<=hi:
            raise ValueError("KRAKEN_INCONSISTENT_OHLC")
        key=date.date().isoformat()
        if key in rows:
            raise ValueError("KRAKEN_DUPLICATE_DATE")
        rows[key]=close
    return rows


def compare(symbol, *, asof=None, fetcher=fetch_json):
    if symbol not in SYMBOLS:
        raise ValueError("UNREGISTERED_CROSS_VENUE_ASSET")
    now=asof or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AMBIGUOUS_REQUEST_CLOCK")
    now=now.astimezone(timezone.utc)
    last_closed=(now-timedelta(days=1)).date()
    # Bounded 60-day current-vintage snapshot only, not complete backfill.
    start=datetime.combine(last_closed-timedelta(days=65),
                           datetime.min.time(),tzinfo=timezone.utc)
    end=datetime.combine(last_closed+timedelta(days=1),
                         datetime.min.time(),tzinfo=timezone.utc)
    cb,kr=SYMBOLS[symbol]
    cb_url=(f"{COINBASE}/products/{cb}/candles?"+
            urlencode({"granularity":86400,"start":start.isoformat(),
                       "end":end.isoformat()}))
    kr_url=f"{KRAKEN}?{urlencode({'pair':kr,'interval':1440})}"
    coinbase,coinbase_sha=fetcher(cb_url)
    kraken,kraken_sha=fetcher(kr_url)
    a=_coinbase(coinbase,last_closed)
    b=_kraken(kraken,last_closed)
    shared=sorted(set(a)&set(b))
    if len(shared)<MIN_DAYS:
        raise ValueError("INDEPENDENT_VENUE_OVERLAP_INSUFFICIENT")
    deviations=[abs(b[k]/a[k]-1)*10000 for k in shared]
    median=statistics.median(deviations)
    result={
        "status":("INDEPENDENT_USD_PRICE_DIFFERENCE_HIGH"
                  if median>200 else "INDEPENDENT_USD_SPOT_PRICE_CORROBORATION_ONLY"),
        "asset":symbol,"quote":"USD",
        "source_pair_coinbase":cb,"source_pair_kraken":kr,
        "source_sha256_coinbase":coinbase_sha,
        "source_sha256_kraken":kraken_sha,
        "captured_utc":now.isoformat(),
        "coinbase_completed_daily":len(a),"kraken_completed_daily":len(b),
        "overlapping_completed_dates":len(shared),
        "first_overlap_date":shared[0],"last_overlap_date":shared[-1],
        "median_absolute_close_difference_bps":round(median,2),
        "max_absolute_close_difference_bps":round(max(deviations),2),
        "kraken_archive_full_history_downloaded":False,
        "binance_usdt_taker_data_can_be_replaced":False,
        "historical_vintage_pit_verified":False,
        "cross_venue_spread_proves_execution":False,
        "paper_eligible":False,"live_allowed":False,
    }
    return result


def report(fetcher=fetch_json, asof=None):
    states={}
    for symbol in SYMBOLS:
        try:
            states[symbol]=compare(symbol,fetcher=fetcher,asof=asof)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,
                TypeError,KeyError,json.JSONDecodeError) as exc:
            states[symbol]={
                "status":"INDEPENDENT_DATA_SOURCE_NOT_VERIFIED",
                "failure_type":type(exc).__name__,
                "no_synthetic_fallback":True,
                "paper_eligible":False,
            }
    return {"schema":"eaverse.cross_venue_reference.v1",
            "sources":states,"trading_source_switched":False,
            "no_broker_orders":True,"paper_eligible":False}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",type=Path,default=Path("out/cross_venue"))
    args=p.parse_args()
    result=report()
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"reference.json").write_text(
        json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("EAVERSE_CROSS_VENUE_ONLY",
          {s:v["status"] for s,v in result["sources"].items()})


if __name__=="__main__":
    main()
