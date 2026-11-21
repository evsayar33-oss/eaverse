"""Completed bars from fixed, explicitly named market-data sources."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Market:
    name: str
    source: str
    symbols: tuple[str, ...]
    cost_bps: int
    calendar: str | None = None


MARKETS = (
    Market("CRYPTO", "coinbase", ("BTC-USD", "ETH-USD", "SOL-USD", "LINK-USD", "AVAX-USD", "XRP-USD"), 35),
    Market("US", "yahoo", ("SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA"), 10, "XNYS"),
    Market("BIST", "yahoo", ("THYAO.IS", "ASELS.IS", "ISCTR.IS", "KCHOL.IS", "TUPRS.IS", "BIMAS.IS"), 25, "XIST"),
)
COLUMNS = ["open", "high", "low", "close", "volume"]


def candle_request(symbol: str, start: datetime, end: datetime) -> list:
    params = urlencode({"granularity": 86400, "start": start.isoformat(), "end": end.isoformat()})
    request = Request(
        f"https://api.exchange.coinbase.com/products/{symbol}/candles?{params}",
        headers={"User-Agent": "eaverse-research/1.0"},
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.load(response)
                if not isinstance(payload, list):
                    raise ValueError("unexpected Coinbase response")
                return payload
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (TimeoutError, URLError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Coinbase retry exhausted")


def coinbase(symbol: str, now: datetime) -> pd.DataFrame:
    # Allow the provider ten minutes to publish the preceding UTC daily bar.
    end = (now - timedelta(minutes=10)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=2192)
    records = {}
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=280), end)
        for stamp, low, high, opn, close, volume in candle_request(symbol, cursor, stop):
            date = datetime.fromtimestamp(stamp, UTC)
            if cursor <= date < stop:
                record = (opn, high, low, close, volume)
                if date in records and records[date] != record:
                    raise ValueError(f"conflicting candles on {date.date()}")
                records[date] = record
        cursor = stop
    index = pd.DatetimeIndex(records, tz="UTC").tz_localize(None)
    bars = pd.DataFrame(list(records.values()), index=index, columns=COLUMNS).sort_index()
    if len(bars) < 600:
        raise ValueError("insufficient Coinbase history")
    expected = pd.date_range(bars.index[0], end.date() - timedelta(days=1), freq="D")
    gaps = expected.difference(bars.index)
    if len(gaps) and (expected > gaps[-1]).sum() >= 600:
        expected = expected[expected > gaps[-1]]
        bars = bars.loc[expected[0]:]
    return validate(bars, expected)


def equities(symbol: str, market: Market, now: datetime) -> pd.DataFrame:
    import exchange_calendars as xcals
    import yfinance as yf

    calendar = xcals.get_calendar(market.calendar)
    start = (now - timedelta(days=2192)).date()
    today = now.date()
    sessions = calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(today))
    completed = sessions[(calendar.schedule.loc[sessions, "close"] <= pd.Timestamp(now - timedelta(minutes=90))).to_numpy()]
    if completed.tz is not None:
        completed = completed.tz_localize(None)
    bars = yf.Ticker(symbol).history(
        start=start.isoformat(), end=(today + timedelta(days=1)).isoformat(),
        interval="1d", auto_adjust=True, actions=False, timeout=15,
    )
    if bars.empty:
        raise ValueError("Yahoo returned no bars")
    bars = bars.rename(columns=str.lower)[COLUMNS].copy()
    bars.index = bars.index.tz_localize(None).normalize()
    # An ongoing session is excluded explicitly. Holiday gaps come from the calendar.
    bars = bars.loc[bars.index <= completed[-1]]
    # Never synthesize missing equity bars. An older gap may be excluded only
    # when the latest contiguous window still exceeds the minimum sample.
    missing = completed.difference(bars.index)
    if len(missing) and (completed > missing[-1]).sum() >= 600:
        completed = completed[completed > missing[-1]]
        bars = bars.loc[bars.index >= completed[0]]
    return validate(bars, completed)


def validate(bars: pd.DataFrame, expected: pd.DatetimeIndex) -> pd.DataFrame:
    if len(bars) < 600:
        raise ValueError("fewer than 600 completed bars")
    if not bars.index.equals(expected):
        missing = expected.difference(bars.index)
        extra = bars.index.difference(expected)
        raise ValueError(f"unexpected session series: {len(missing)} missing, {len(extra)} extra")
    bars = bars.astype(float)
    if not np.isfinite(bars.to_numpy()).all() or (bars[COLUMNS[:4]] <= 0).any().any():
        raise ValueError("non-finite or non-positive OHLC")
    # Yahoo's adjusted OHLC can differ at the last binary float bit when
    # close equals a daily extreme. Keep the original values and fingerprint.
    tolerance = 1e-12
    if (bars.high < bars[["open", "close", "low"]].max(axis=1) * (1 - tolerance)).any():
        raise ValueError("inconsistent candle high")
    if (bars.low > bars[["open", "close", "high"]].min(axis=1) * (1 + tolerance)).any() or (bars.volume < 0).any():
        raise ValueError("inconsistent candle low or volume")
    return bars


def fingerprint(bars: pd.DataFrame) -> str:
    csv = bars.to_csv(index_label="date", float_format="%.12g", date_format="%Y-%m-%d")
    return hashlib.sha256(csv.encode()).hexdigest()


def load(market: Market, symbol: str, now: datetime) -> pd.DataFrame:
    if market.source == "coinbase":
        return coinbase(symbol, now)
    return equities(symbol, market, now)
