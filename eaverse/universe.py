"""Refresh exchange listings and rotate research through the available symbols."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta
from urllib.request import Request, urlopen


NASDAQ = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
SEC = "https://www.sec.gov/files/company_tickers_exchange.json"
KAP = "https://www.kap.org.tr/tr/bist-sirketler"
COINBASE = "https://api.exchange.coinbase.com/products"
LIMITS = {"US": 40, "BIST": 32, "CRYPTO": 24}


def read_url(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Eaverse research)"})
    with urlopen(request, timeout=12) as response:
        return response.read(5 * 1024 * 1024).decode("utf-8-sig")


def nasdaq_symbols(raw):
    rows = csv.DictReader(io.StringIO(raw), delimiter="|")
    excluded = re.compile(r"\b(warrants?|rights?|preferred|depositary shares|units?)\b", re.I)
    return {
        row["Symbol"].replace(".", "-")
        for row in rows
        if row.get("Nasdaq Traded") == "Y" and row.get("Test Issue") == "N"
        and re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", row.get("Symbol") or "")
        and (row.get("ETF") == "Y" or not excluded.search(row.get("Security Name") or ""))
    }


def sec_symbols(raw):
    payload = json.loads(raw)
    fields = payload["fields"]
    listed = {"Nasdaq", "NYSE", "NYSE American", "NYSE Arca", "Cboe", "IEX"}
    return {
        row[fields.index("ticker")].replace(".", "-")
        for row in payload["data"]
        if row[fields.index("exchange")] in listed
        and re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z])?", row[fields.index("ticker")])
    }


def kap_symbols(raw):
    return {f"{code}.IS" for code in re.findall(
        r'<a href="/tr/sirket-bilgileri/ozet/[^"]+"><div>([A-Z][A-Z0-9]{1,9})</div>', raw
    )}


def coinbase_symbols(raw):
    return {
        item["id"] for item in json.loads(raw)
        if item.get("quote_currency") == "USD" and item.get("status") == "online"
        and not item.get("trading_disabled") and re.fullmatch(r"[A-Z0-9]+-USD", item["id"])
        and item.get("base_currency") not in {"USDC", "USDT", "DAI", "PYUSD", "EURC", "GUSD", "USD1"}
    }


def fetch(name):
    if name == "US":
        try:
            symbols = sec_symbols(read_url(SEC))
            if len(symbols) >= 1000:
                return SEC, symbols
        except (OSError, ValueError):
            pass
        return NASDAQ, nasdaq_symbols(read_url(NASDAQ))
    if name == "BIST":
        return KAP, kap_symbols(read_url(KAP))
    return COINBASE, coinbase_symbols(read_url(COINBASE))


def refresh(db, market, today):
    row = db.execute("SELECT * FROM listings WHERE market=?", (market.name,)).fetchone()
    error = None
    if row is None or row["checked"] != today.isoformat():
        try:
            source, fetched = fetch(market.name)
            minimum = {"US": 1000, "BIST": 300, "CRYPTO": 50}[market.name]
            if len(fetched) < minimum:
                raise ValueError(f"listing feed yielded {len(fetched)} symbols, expected at least {minimum}")
            db.execute("INSERT OR REPLACE INTO listings VALUES (?,?,?,?,?)",
                       (market.name, source, today.isoformat(), today.isoformat(), json.dumps(sorted(fetched))))
            row = db.execute("SELECT * FROM listings WHERE market=?", (market.name,)).fetchone()
        except (OSError, ValueError, KeyError, IndexError) as exc:
            error = f"{type(exc).__name__}: {exc}"[:200]
            if row:
                db.execute("UPDATE listings SET checked=? WHERE market=?", (today.isoformat(), market.name))
    catalog = set(json.loads(row["symbols"])) if row else set()
    return sorted(catalog.union(market.symbols)), {
        "source": row["source"] if row else None,
        "listing_date": row["asof"] if row else None,
        "listed": len(catalog), "listing_error": error,
    }


def queue(db, market, symbols, today, limit):
    prefix = market.name + ":"
    active = [row["asset"].removeprefix(prefix) for row in db.execute(
        "SELECT asset FROM paper WHERE status='active' AND asset LIKE ? ORDER BY sequence", (prefix + "%",))]
    progress = {row["symbol"]: row for row in db.execute(
        "SELECT * FROM asset_rotation WHERE market=?", (market.name,))}
    today_key = today.isoformat()
    pool = [symbol for symbol in symbols if symbol not in active
            and (symbol not in progress or progress[symbol]["due"] <= today_key)]
    # Unvisited listings are covered first. After that, the oldest visit goes next.
    pool.sort(key=lambda item: (progress[item]["visited"] if item in progress else "",
                                item not in market.symbols, item))
    slots = max(0, limit - len(active))
    known = [item for item in pool if item in progress and progress[item]["error"] is None]
    revisit = known[:slots // 4]
    rotating = [item for item in pool if item not in revisit][:slots-len(revisit)]
    return active + revisit + rotating


def record(db, market, symbol, now, status):
    today = now.date()
    previous = db.execute("SELECT failures FROM asset_rotation WHERE market=? AND symbol=?",
                          (market, symbol)).fetchone()
    failures = (previous["failures"] if previous else 0) + 1 if status else 0
    # A newly listed symbol with too little history returns to the queue next week.
    delay = 7 if status == "insufficient_history" else min(7, failures)
    due = (today + timedelta(days=delay)).isoformat()
    db.execute("INSERT OR REPLACE INTO asset_rotation VALUES (?,?,?,?,?,?)",
               (market, symbol, now.isoformat(), due, failures, status))
