"""Refresh exchange listings and rotate research through the available symbols."""

from __future__ import annotations

import csv
import html
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import zip_longest
from pathlib import Path
from urllib.request import Request, urlopen

from . import history_sources


NASDAQ = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
NASDAQ_SCREEN = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000"
SEC = "https://www.sec.gov/files/company_tickers_exchange.json"
KAP = "https://www.kap.org.tr/tr/bist-sirketler"
BIST_ETF = "https://www.borsaistanbul.com/katilim-finans/katilim-esasli-byfler"
COINBASE = "https://api.exchange.coinbase.com/products"
LIMITS = {"US": 40, "BIST": 32, "CRYPTO": 24}
REFERENCE = Path(__file__).parent / "reference_data"


def bundled_us():
    captured, *symbols = (REFERENCE / "us-exchange-symbols.txt").read_text().splitlines()
    return captured, set(symbols)


def read_url(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Eaverse research)"})
    with urlopen(request, timeout=12) as response:
        raw=response.read(history_sources.MAX_RESPONSE+1)
    if len(raw)>history_sources.MAX_RESPONSE:
        raise ValueError("listing response exceeds size cap")
    return raw.decode("utf-8-sig")


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


def nasdaq_screen_symbols(raw):
    rows = json.loads(raw)["data"]["table"]["rows"]
    return {
        row["symbol"].replace(".", "-")
        for row in rows
        if re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z])?", row.get("symbol") or "")
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
    symbols=set()
    for cell in re.findall(r'<a href="/tr/sirket-bilgileri/ozet/[^"]+"><div>(.*?)</div>', raw, re.S):
        for code in re.split(r"[\s,;/]+",html.unescape(re.sub(r"<[^>]+>","",cell)).strip()):
            if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}",code):
                symbols.add(code+".IS")
    return symbols


def bist_etf_symbols(raw):
    # Borsa Istanbul reports participation ETF instrument codes ending in .F.
    # Yahoo candidates are validated against actual price bars before research.
    output=set()
    for cell in re.findall(r"<td(?:\s[^>]*)?>(.*?)</td>",raw,re.S|re.I):
        code=html.unescape(re.sub(r"<[^>]+>","",cell)).strip()
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}\.F",code):
            output.add(code[:-2]+".IS")
    return output


def coinbase_symbols(raw):
    return {
        item["id"] for item in json.loads(raw)
        if item.get("quote_currency") == "USD" and item.get("status") == "online"
        and not item.get("trading_disabled") and re.fullmatch(r"[A-Z0-9]+-USD", item["id"])
        and item.get("base_currency") not in {"USDC", "USDT", "DAI", "PYUSD", "EURC", "GUSD", "USD1"}
    }


SOURCE_SCHEMA = """CREATE TABLE IF NOT EXISTS listing_sources(
 market TEXT NOT NULL,source TEXT NOT NULL,checked TEXT NOT NULL,asof TEXT,symbols TEXT,error TEXT,
 PRIMARY KEY(market,source))"""
SEEN_SCHEMA = """CREATE TABLE IF NOT EXISTS listing_seen(
 market TEXT NOT NULL,symbol TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
 PRIMARY KEY(market,symbol))"""


def source_definitions(name):
    if name=="US":
        return ((SEC,sec_symbols,1000),(NASDAQ_SCREEN,nasdaq_screen_symbols,1000),(NASDAQ,nasdaq_symbols,1000))
    if name=="BIST":
        return ((KAP,kap_symbols,300),(BIST_ETF,bist_etf_symbols,1))
    return ((COINBASE,coinbase_symbols,50),)+tuple(
        (url,lambda raw,v=venue:history_sources.venue_symbols(v,json.loads(raw)),20)
        for venue,url in history_sources.LIST_ENDPOINTS.items())


def fetch(name):
    def one(entry):
        url,parse,minimum=entry
        try:
            symbols=parse(read_url(url))
            if len(symbols)<minimum:
                raise ValueError(f"only {len(symbols)} listing symbols; expected {minimum}")
            return url,symbols,None
        except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            return url,None,f"{type(exc).__name__}: {exc}"[:200]
    # Do not stop at the first successful directory. Independent caches mean
    # a provider outage cannot discard another provider's last observed list.
    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(one,source_definitions(name)))


def refresh(db, market, today):
    db.execute(SOURCE_SCHEMA)
    db.execute(SEEN_SCHEMA)
    stamp=today.isoformat()
    legacy=db.execute("SELECT * FROM listings WHERE market=?",(market.name,)).fetchone()
    # Migrate only a known individual source; keep its actual observation date.
    if legacy and legacy["symbols"] and legacy["asof"] and legacy["source"] in {url for url,_,_ in source_definitions(market.name)}:
        inserted=db.execute("INSERT OR IGNORE INTO listing_sources VALUES (?,?,?,?,?,NULL)",
            (market.name,legacy["source"],"",legacy["asof"],legacy["symbols"]))
        if inserted.rowcount:
            db.executemany("INSERT OR IGNORE INTO listing_seen VALUES (?,?,?,?)",
                [(market.name,s,legacy["asof"],legacy["asof"]) for s in json.loads(legacy["symbols"])])
    have=db.execute("SELECT count(*) FROM listing_sources WHERE market=? AND checked=?",(market.name,stamp)).fetchone()[0]
    error=None
    if have<len(source_definitions(market.name)):
        try:
            results=fetch(market.name)
        except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            results=[]
            error=f"{type(exc).__name__}: {exc}"[:200]
        for url,symbols,issue in results:
            if symbols is not None:
                db.execute("INSERT OR REPLACE INTO listing_sources VALUES (?,?,?,?,?,NULL)",
                           (market.name,url,stamp,stamp,json.dumps(sorted(symbols))))
                db.executemany("""INSERT INTO listing_seen VALUES (?,?,?,?)
                    ON CONFLICT(market,symbol) DO UPDATE SET last_seen=excluded.last_seen""",
                    [(market.name,s,stamp,stamp) for s in sorted(symbols)])
            else:
                db.execute("""INSERT INTO listing_sources VALUES (?,?,?,NULL,NULL,?)
                    ON CONFLICT(market,source) DO UPDATE SET checked=excluded.checked,error=excluded.error""",
                    (market.name,url,stamp,issue))
    rows=list(db.execute("SELECT * FROM listing_sources WHERE market=? ORDER BY source",(market.name,)))
    catalog=set()
    sources=[]
    for row in rows:
        symbols=set(json.loads(row["symbols"])) if row["symbols"] else set()
        catalog.update(symbols)
        sources.append({"source":row["source"],"asof":row["asof"],"listed":len(symbols),"error":row["error"],
                        "status":"live" if row["asof"]==stamp and not row["error"] else "cached" if symbols else "unavailable"})
    # Seed pre-upgrade cache only when its source did not return a fresh list.
    # Never label old candidates as live or relabel a delisted identity.
    if legacy and legacy["symbols"] and not catalog:
        old=set(json.loads(legacy["symbols"]))
        catalog.update(old)
        if old:
            sources.append({"source":legacy["source"],"asof":legacy["asof"],"listed":len(old),"error":error,"status":"legacy_cached"})
    if not catalog and market.name=="US":
        captured,catalog=bundled_us()
        sources.append({"source":"bundled_nasdaq_snapshot","asof":captured,"listed":len(catalog),"error":error,"status":"bundled_snapshot"})
    available=[s for s in sources if s["asof"] and s["listed"]]
    live=[s for s in available if s["status"]=="live"]
    date=max((s["asof"] for s in available),default=None)
    mode=("live" if live and len(live)==len(sources) else "partial_live" if live else
          "bundled_snapshot" if any(s["status"]=="bundled_snapshot" for s in sources) else "cached" if catalog else "seed_symbols")
    if catalog and date:
        db.execute("INSERT OR REPLACE INTO listings VALUES (?,?,?,?,?)",
                   (market.name,"multi-source" if len(available)>1 else available[0]["source"],date,stamp,json.dumps(sorted(catalog))))
    # Only actually observed listing dates contribute to this history; lifetime
    # counts are not a backfilled delist-aware universe.
    seen={r[0] for r in db.execute("SELECT symbol FROM listing_seen WHERE market=?",(market.name,))}
    issues=[s["error"] for s in sources if s["error"]]
    error=error or ("; ".join(issues)[:500] if issues else None)
    return sorted(catalog.union(market.symbols)), {
        "source":"multi-source" if len(available)>1 else available[0]["source"] if available else None,
        "listing_date":date,"listed":len(catalog),"listing_error":error,"coverage_mode":mode,
        "sources":sources,"observed_listing_symbols":len(seen),
        "historical_listing_candidates_not_price_verified":len(seen-catalog),
        "point_in_time_history_complete":False,
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
    # A rotating BIST batch can consist entirely of recent IPOs or previously
    # rejected symbols. Reserve one existing slot for a mature seed, so a
    # market-wide outage is evaluated against a real control on every run.
    # If all seeds are in backoff, probe just the oldest one, not the entire set.
    probe = []
    if market.name=="BIST" and slots:
        seeds=[s for s in market.symbols if s in symbols and s not in active]
        eligible=[s for s in seeds if s in pool]
        if seeds:
            probe=[min(eligible or seeds,key=lambda s:(progress[s]["visited"] if s in progress else "",s))]
            pool=[s for s in pool if s not in probe]
            slots-=1
    known = [item for item in pool if item in progress and progress[item]["error"] is None]
    revisit = known[:slots // 4]
    remaining=[item for item in pool if item not in revisit]
    if market.name=="CRYPTO":
        groups=[[s for s in remaining if ":" not in s]]
        groups.extend([s for s in remaining if s.startswith(venue+":")] for venue in history_sources.VENUES)
        remaining=[v for row in zip_longest(*groups) for v in row if v is not None]
    rotating = remaining[:slots-len(revisit)]
    return active + probe + revisit + rotating


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
