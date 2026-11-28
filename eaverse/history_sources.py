"""Longer daily history and explicitly separate public spot venue identities.

No source substitution across venues, no synthetic bars, no new PAPER
eligibility. Keep legacy data.py unchanged for frozen cohort hashes.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from . import data

BINANCE = "https://data-api.binance.vision"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
VENUES = {"BINANCE_SPOT":"binance_spot","BYBIT_SPOT":"bybit_spot","OKX_SPOT":"okx_spot"}
LIST_ENDPOINTS = {
    "BINANCE_SPOT": BINANCE+"/api/v3/exchangeInfo",
    "BYBIT_SPOT": BYBIT+"/v5/market/instruments-info?category=spot",
    "OKX_SPOT": OKX+"/api/v5/public/instruments?instType=SPOT",
}
QUOTE = {"USD","USDT","USDC"}
STABLE = {"USDC","USDT","DAI","PYUSD","EURC","GUSD","USD1","FDUSD","TUSD","USDP","USDE","USDS"}
EQUITY_START = "1990-01-01"
CRYPTO_START = datetime(2012,1,1,tzinfo=UTC)
DAY_MS = 86400000
MAX_RESPONSE = 12*1024*1024
HISTORY_SECONDS = 75


def market_for_symbol(market, symbol):
    if market.name=="CRYPTO" and ":" in symbol:
        venue, pair=symbol.split(":",1)
        if venue not in VENUES or not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)?",pair):
            raise ValueError("unsupported venue-qualified symbol")
        return replace(market,source=VENUES[venue])
    return market


def get_json(url, params=None):
    request=Request(url+("?" + urlencode(params) if params else ""),
                    headers={"User-Agent":"eaverse-public-research/1.0"})
    with urlopen(request,timeout=12) as response:
        raw=response.read(MAX_RESPONSE+1)
    if len(raw)>MAX_RESPONSE:
        raise ValueError("market response exceeds size cap")
    return json.loads(raw)


def venue_symbols(venue, obj):
    """Only reported live spot pairs; venue + quote are part of identity."""
    if venue=="BINANCE_SPOT":
        rows=obj["symbols"]
        fields=("symbol","baseAsset","quoteAsset")
        permitted=lambda r:r.get("status")=="TRADING" and r.get("isSpotTradingAllowed") is True
    elif venue=="BYBIT_SPOT":
        if obj.get("retCode")!=0 or obj["result"].get("category")!="spot":
            raise ValueError("Bybit spot listing error")
        rows=obj["result"]["list"]
        fields=("symbol","baseCoin","quoteCoin")
        permitted=lambda r:r.get("status")=="Trading" and str(r.get("symbolType","")).lower() not in ("xstock","xstocks","tradfi")
    elif venue=="OKX_SPOT":
        if obj.get("code")!="0":
            raise ValueError("OKX spot listing error")
        rows=obj["data"]
        fields=("instId","baseCcy","quoteCcy")
        permitted=lambda r:r.get("state")=="live" and r.get("instType")=="SPOT"
    else:
        raise ValueError("unsupported listing venue")
    output=set()
    for row in rows:
        pair,base,quote=(row.get(f,"") for f in fields)
        if not permitted(row) or quote not in QUOTE or base in STABLE:
            continue
        # Reject derivatives and mismatched pair/base/quote metadata.
        if not re.fullmatch(r"[A-Z0-9]+",base) or pair!=(base+"-"+quote if venue=="OKX_SPOT" else base+quote):
            continue
        output.add(venue+":"+pair)
    return output


def closed_end(now):
    return (now-timedelta(minutes=10)).replace(hour=0,minute=0,second=0,microsecond=0)


def contiguous_crypto(records,end,status,requested_start):
    index=pd.DatetimeIndex(records,tz="UTC").tz_localize(None)
    bars=pd.DataFrame(list(records.values()),index=index,columns=data.COLUMNS).sort_index()
    if bars.empty:
        raise ValueError("insufficient venue history")
    expected=pd.date_range(bars.index[0],end.date()-timedelta(days=1),freq="D")
    gaps=expected.difference(bars.index)
    if len(gaps) and (expected>gaps[-1]).sum()>=600:
        expected=expected[expected>gaps[-1]]
        bars=bars.loc[expected[0]:]
        status="truncated_gap"
    result=data.validate(bars,expected)
    result.attrs.update(history_status=status,requested_start=str(requested_start.date()),
                        history_from=str(result.index[0].date()),history_through=str(result.index[-1].date()),
                        history_bars=len(result))
    return result


def coinbase_long(symbol,now):
    end=closed_end(now)
    cursor=end
    records={}
    status="provider_history_boundary"
    deadline=time.monotonic()+HISTORY_SECONDS
    while cursor>CRYPTO_START:
        if time.monotonic()>deadline:
            status="older_history_runtime_cap";break
        start=max(CRYPTO_START,cursor-timedelta(days=280))
        try:
            rows=data.candle_request(symbol,start,cursor)
        except (OSError,ValueError,RuntimeError) as exc:
            if len(records)<600:
                raise
            status="older_page_unavailable:"+type(exc).__name__
            break
        page_count=0
        for stamp,lo,hi,opn,close,vol in rows:
            ts=datetime.fromtimestamp(stamp,UTC)
            if start<=ts<cursor:
                if ts.hour or ts.minute or ts.second or ts.microsecond:
                    raise ValueError("non-midnight Coinbase bar")
                record=(opn,hi,lo,close,vol)
                if ts in records and records[ts]!=record:
                    raise ValueError("conflicting Coinbase daily bars")
                records[ts]=record
                page_count+=1
        if not page_count:
            break
        cursor=start
        if cursor==CRYPTO_START:
            status="requested_floor_reached"
    return contiguous_crypto(records,end,status,CRYPTO_START)


def venue_page(venue,pair,before):
    """Return source-native daily rows strictly before the UTC boundary."""
    if venue=="BINANCE_SPOT":
        obj=get_json(BINANCE+"/api/v3/klines",{"symbol":pair,"interval":"1d","limit":1000,"endTime":before-1})
        if not isinstance(obj,list):
            raise ValueError("Binance kline response")
        for row in obj:
            if len(row)<7 or int(row[6])!=int(row[0])+DAY_MS-1:
                raise ValueError("Binance non-daily kline")
        return obj
    if venue=="BYBIT_SPOT":
        obj=get_json(BYBIT+"/v5/market/kline",{"category":"spot","symbol":pair,"interval":"D","limit":1000,"end":before-1})
        if obj.get("retCode")!=0 or obj["result"].get("category")!="spot" or obj["result"].get("symbol")!=pair:
            raise ValueError("Bybit wrong instrument response")
        return obj["result"]["list"]
    obj=get_json(OKX+"/api/v5/market/history-candles",{"instId":pair,"bar":"1Dutc","limit":100,"after":before})
    if obj.get("code")!="0":
        raise ValueError("OKX history response")
    return obj["data"]


def venue_history(venue,pair,now):
    end=closed_end(now)
    before=int(end.timestamp()*1000)
    floor=int(CRYPTO_START.timestamp()*1000)
    records={}
    status="provider_history_boundary"
    # OKX lower page size is bounded, even if provider repeats pages.
    deadline=time.monotonic()+HISTORY_SECONDS
    for _ in range(60 if venue=="OKX_SPOT" else 12):
        if time.monotonic()>deadline:
            status="older_history_runtime_cap";break
        if before<=floor:
            status="requested_floor_reached";break
        try:
            rows=venue_page(venue,pair,before)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            if len(records)<600:
                raise
            status="older_page_unavailable:"+type(exc).__name__
            break
        if not rows:
            break
        previous=before
        for row in rows:
            if len(row)<6:
                raise ValueError("invalid daily candle")
            stamp=int(row[0])
            if stamp%DAY_MS:
                raise ValueError("daily candle is not UTC aligned")
            if stamp>=before:
                raise ValueError("venue pagination repeated or returned unfinished window")
            if venue=="OKX_SPOT" and (len(row)!=9 or row[-1]!="1"):
                raise ValueError("unconfirmed OKX daily candle")
            previous=min(previous,stamp)
            if stamp<floor:
                continue
            ts=datetime.fromtimestamp(stamp/1000,UTC)
            record=tuple(float(v) for v in row[1:6])
            if ts in records and records[ts]!=record:
                raise ValueError("conflicting venue daily candles")
            records[ts]=record
        if previous>=before:
            raise ValueError("venue history did not progress")
        before=previous
    else:
        status="bounded_page_limit"
    return contiguous_crypto(records,end,status,CRYPTO_START)


@lru_cache(maxsize=8)
def equity_calendar(name,today):
    import exchange_calendars as xcals
    return xcals.get_calendar(name,start=EQUITY_START,end=(today+timedelta(days=2)).isoformat())


class BistHistoryError(ValueError):
    """Invalid or incomplete BIST data; do not hide it with a legacy retry."""


class InsufficientEquityHistory(ValueError):
    """A genuinely short listing, rather than a damaged historic series."""


def invalid_equity_rows(bars):
    """Apply the unchanged data.validate OHLCV checks to individual rows."""
    values=bars.astype(float)
    invalid=~np.isfinite(values).all(axis=1) | (values[data.COLUMNS[:4]]<=0).any(axis=1)
    tolerance=1e-12
    return (invalid | (values.high<values[["open","close","low"]].max(axis=1)*(1-tolerance))
            | (values.low>values[["open","close","high"]].min(axis=1)*(1+tolerance))
            | (values.volume<0))


def equity_frame(raw):
    bars=raw.rename(columns=str.lower)[data.COLUMNS].copy()
    bars.index=bars.index.tz_localize(None).normalize()
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise BistHistoryError("duplicate or unordered BIST daily bars")
    return bars


def bist_completed_history(ticker,bars,completed,end):
    """Recover an actual one-session Yahoo response, then keep a valid suffix."""
    latest=completed[-1]
    if len(completed[completed>=bars.index[0]])<600:
        raise InsufficientEquityHistory("fewer than 600 completed bars (short listing history)")
    refetched=False
    if latest not in bars.index or invalid_equity_rows(bars.loc[[latest]]).iloc[0]:
        try:
            # Yahoo can return null adjusted OHLC in a multi-year response yet
            # supply the complete DAILY candle for the identical end boundary.
            # Never use intraday last trade, zero fill, or an inferred close.
            raw=ticker.history(start=latest.date().isoformat(),end=end,interval="1d",
                               auto_adjust=True,actions=False,timeout=15)
            corrected=equity_frame(raw)
            if not corrected.index.equals(pd.DatetimeIndex([latest])) or invalid_equity_rows(corrected).any():
                raise ValueError("exact-session daily response is missing or invalid")
            if latest in bars.index:
                old=bars.loc[latest]
                new=corrected.iloc[0]
                # Remaining valid fields must agree. This prevents mixing
                # adjustment bases or silently replacing conflicting prices.
                comparable=np.isfinite(old) & (old>0)
                comparable.loc["volume"]=np.isfinite(old["volume"]) and old["volume"]>=0
                if not np.isclose(old[comparable],new[comparable],rtol=1e-12,atol=0).all():
                    raise ValueError("exact-session daily response conflicts with available OHLCV")
                if comparable["volume"] and old["volume"]!=new["volume"]:
                    raise ValueError("exact-session daily volume conflicts")
            bars=pd.concat([bars.drop(index=latest,errors="ignore"),corrected]).sort_index()
            refetched=True
        except Exception as exc:
            raise BistHistoryError(f"BIST latest completed session {latest.date()} unavailable: {type(exc).__name__}: {exc}") from exc
    expected=completed[completed>=bars.index[0]]
    missing=expected.difference(bars.index)
    extra=bars.index.difference(expected)
    invalid=bars.index[invalid_equity_rows(bars)]
    anomalies=missing.union(extra).union(invalid)
    status="provider_history_boundary"
    if len(anomalies):
        # Never delete a row inside the research window or accept a stale
        # final session. Only discard the old prefix through the last anomaly.
        cutoff=anomalies[-1]
        expected=expected[expected>cutoff]
        if len(expected)<600:
            raise BistHistoryError(f"BIST history integrity: {len(expected)} contiguous completed sessions after {cutoff.date()} (600 required)")
        bars=bars.loc[bars.index>cutoff]
        status="truncated_history_integrity"
    result=data.validate(bars,expected)
    if refetched:
        status+=":exact_daily_refetch"
    result.attrs.update(history_status=status,latest_daily_refetched=refetched,
                        excluded_history_through=str(anomalies[-1].date()) if len(anomalies) else None)
    return result


def equities_long(symbol,market,now,*,start=EQUITY_START):
    import yfinance as yf
    cal=equity_calendar(market.calendar,now.date())
    sessions=cal.sessions[cal.sessions<=pd.Timestamp(now.date())]
    completed=sessions[(cal.schedule.loc[sessions,"close"]<=pd.Timestamp(now-timedelta(minutes=90))).to_numpy()]
    if completed.tz is not None:
        completed=completed.tz_localize(None)
    if not len(completed):
        raise ValueError("no completed exchange sessions")
    # Keep both BIST requests on the same adjustment horizon and never ask
    # for an unfinished bar. Other markets retain the existing request.
    end=((completed[-1].date() if market.name=="BIST" else now.date())+timedelta(days=1)).isoformat()
    ticker=yf.Ticker(symbol)
    raw=ticker.history(start=start,end=end,interval="1d",auto_adjust=True,actions=False,timeout=15)
    if raw.empty:
        raise ValueError("Yahoo returned no completed history")
    if market.name=="BIST":
        bars=equity_frame(raw)
    else:
        bars=raw.rename(columns=str.lower)[data.COLUMNS].copy()
        bars.index=bars.index.tz_localize(None).normalize()
    bars=bars.loc[(bars.index>=completed[0])&(bars.index<=completed[-1])]
    if bars.empty:
        raise ValueError("Yahoo returned no completed history")
    if market.name=="BIST":
        result=bist_completed_history(ticker,bars,completed,end)
        status=result.attrs["history_status"]
    else:
        # A listing starts at its first observed bar; never manufacture pre-IPO prices.
        expected=completed[completed>=bars.index[0]]
        status="provider_history_boundary"
        gaps=expected.difference(bars.index)
        if len(gaps) and (expected>gaps[-1]).sum()>=600:
            expected=expected[expected>gaps[-1]]
            bars=bars.loc[expected[0]:]
            status="truncated_gap"
        result=data.validate(bars,expected)
    result.attrs.update(history_status=status,requested_start=start,
                        history_from=str(result.index[0].date()),history_through=str(result.index[-1].date()),
                        history_bars=len(result))
    return result


def load(market,symbol,now):
    market=market_for_symbol(market,symbol)
    if market.source in VENUES.values():
        venue,pair=symbol.split(":",1)
        return venue_history(venue,pair,now)
    if market.name=="BIST":
        try:
            return equities_long(symbol,market,now)
        except (BistHistoryError,InsufficientEquityHistory):
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            # A long-range transport/empty response may work with six years.
            # Apply the same BIST integrity checks on the shorter request.
            try:
                result=equities_long(symbol,market,now,start=(now-timedelta(days=2192)).date().isoformat())
            except (BistHistoryError,InsufficientEquityHistory):
                raise
            except Exception as fallback:
                raise BistHistoryError(f"Yahoo BIST history unavailable: {type(exc).__name__}: {exc}; retry: {type(fallback).__name__}: {fallback}") from fallback
            result.attrs["history_status"]+=":shorter_request"
            return result
    try:
        return coinbase_long(symbol,now) if market.source=="coinbase" else equities_long(symbol,market,now)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        # Same-source legacy fallback only, separately labelled. Frozen PAPER
        # data remains anchored to its original source and checksum.
        result=data.load(market,symbol,now)
        result.attrs.update(history_status="legacy_window_fallback:"+type(exc).__name__,
                            requested_start=EQUITY_START if market.calendar else str(CRYPTO_START.date()),
                            history_from=str(result.index[0].date()),history_through=str(result.index[-1].date()),
                            history_bars=len(result))
        return result
