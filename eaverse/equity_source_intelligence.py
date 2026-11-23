"""Eaverse V6: independent official US and reconstructed BIST source readiness.

Actual historical source data can inform the future Eaverse strategy engine,
but a date-only reconstruction is NEVER a historical publication timestamp.
All exports are source coverage and immutable fingerprints, not raw vendor
files, backfilled first_seen, executable trades, alpha or PAPER permission.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import pit_adapters

SCHEMA="eaverse.equity_source_assurance.v1"
BIST_COMMIT="21baf355201841f03b2be49e25fc4f51e5c63e06"
BIST_URL=("https://raw.githubusercontent.com/GocmenCo/bist-constituents/"
          +BIST_COMMIT+"/data/bist_constituents.csv")
VIX_URL="https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
SEC_CIKS=("0000320193","0000789019","0001045810")  # Apple, Microsoft, Nvidia
SEC_BASE="https://data.sec.gov/submissions/CIK"
MAX_SOURCE_BYTES=12_000_000
MAX_ROWS=120_000
VIX_FIXED_THRESHOLDS=(25,35)
SEC_AGENT_RE="^[^\\s]+(?:\\s+[^\\s]+)*\\s+[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"


def canonical(item):
    return json.dumps(item,sort_keys=True,separators=(",",":"),allow_nan=False)


def sha(item):
    return hashlib.sha256(canonical(item).encode()).hexdigest()


def get(url, limit=MAX_SOURCE_BYTES, agent=None):
    if not (url==BIST_URL or url==VIX_URL or
            url in {f"{SEC_BASE}{cik}.json" for cik in SEC_CIKS}):
        raise ValueError("UNREGISTERED_REMOTE_DATA_SOURCE")
    if limit>MAX_SOURCE_BYTES:
        raise ValueError("REMOTE_SOURCE_LIMIT_EXCEEDED")
    if url.startswith(SEC_BASE):
        if not isinstance(agent,str) or not re.fullmatch(SEC_AGENT_RE,agent.strip()):
            raise ValueError("SEC_USER_AGENT_CONTACT_REQUIRED")
        identity=agent.strip()
    else:
        identity="Eaverse historical research / source provenance"
    req=Request(url,headers={"User-Agent":identity,"Accept":"text/csv,application/json"})
    with urlopen(req,timeout=17) as resp:
        body=resp.read(limit+1)
    if len(body)>limit:
        raise ValueError("REMOTE_SOURCE_OVERSIZED")
    return body


def vix_coverage(raw):
    """Date-only retrospective close series: NEVER a same-session signal."""
    reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    fields=set(reader.fieldnames or [])
    if not {"DATE","OPEN","HIGH","LOW","CLOSE"}.issubset(fields):
        raise ValueError("VIX_COLUMN_SCHEMA_MISSING")
    rows={}
    for record in reader:
        label=record["DATE"].strip()
        try:
            date=(datetime.strptime(label,"%m/%d/%Y").date()
                  if "/" in label else datetime.strptime(label,"%Y-%m-%d").date())
            opn,hi,lo,close=(float(record[k]) for k in ("OPEN","HIGH","LOW","CLOSE"))
        except (TypeError,ValueError,KeyError) as exc:
            raise ValueError("INVALID_HISTORICAL_VIX_ROW") from exc
        if (date in rows or not all(math.isfinite(x) for x in (opn,hi,lo,close))
            or not 0<lo<=min(opn,close)<=hi<300):
            raise ValueError("INVALID_DUPLICATE_OR_INCONSISTENT_VIX")
        rows[date]=close
        if len(rows)>MAX_ROWS:
            raise ValueError("VIX_SOURCE_TOO_MANY_ROWS")
    dates=sorted(rows)
    if (len(dates)<6000 or dates[0].year>1994 or
        dates[-1].year<2025 or dates!=list(rows)):
        raise ValueError("VIX_HISTORICAL_COVERAGE_OR_SEQUENCE_INCOMPLETE")
    spikes=Counter()
    previous=None
    for date in dates:
        close=rows[date]
        for threshold in VIX_FIXED_THRESHOLDS:
            if previous is not None and previous<threshold<=close:
                spikes[f"cross_above_{threshold}"]+=1
        previous=close
    return {
        "status":"OFFICIAL_HISTORICAL_DAILY_VIX_FORMAT_VERIFIED",
        "rows":len(dates),"first_date":dates[0].isoformat(),
        "last_date":dates[-1].isoformat(),
        "fixed_regime_crossings":dict(spikes),
        "signal_availability_rule":"NEXT_SESSION_OR_LATER_ONLY",
        "original_date_only_archive_revisions_verified":False,
        "publication_clock_exactly_verified":False,
        "full_options_chain_data":False,
        "pit_factor_approved_for_paper":False,
    }


def bist_coverage(raw):
    """Quarters are exclusive XU030/XU050/XU100 tiers, not 30+50+100."""
    reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    needed={"year","snapshot_date","ticker","index"}
    if not needed.issubset(set(reader.fieldnames or [])):
        raise ValueError("BIST_MEMBERSHIP_SCHEMA_MISSING")
    rows=0
    tickers=set()
    seen={}
    duplicated_keys=0
    conflicting_tier_keys=0
    dates=defaultdict(Counter)
    for record in reader:
        d=str(record["snapshot_date"]).strip()
        ticker=str(record["ticker"]).strip()
        tier=str(record["index"]).strip().upper()
        try:
            datetime.strptime(d,"%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("BAD_BIST_SNAPSHOT_DATE") from exc
        if (not ticker or len(ticker)>30 or
            not re.fullmatch("[A-Z0-9.]+",ticker) or tier not in ("","XU030","XU050","XU100")):
            raise ValueError("BAD_BIST_TICKER_OR_EXCLUSIVE_TIER")
        key=d,ticker
        if key in seen:
            duplicated_keys+=1
            if seen[key]!=tier:
                conflicting_tier_keys+=1
            # Keep the original raw-row count, but never infer two
            # independent issuers or fabricate a clean historical identity.
        else:
            seen[key]=tier
        dates[d][tier]+=1
        tickers.add(ticker)
        rows+=1
        if rows>MAX_ROWS:
            raise ValueError("BIST_SOURCE_TOO_MANY_ROWS")
    complete={d for d,x in dates.items()
              if x["XU030"]==30 and x["XU050"]==20 and x["XU100"]==50}
    if rows<20_000 or len(dates)<100 or len(tickers)<500 or len(complete)<50:
        raise ValueError("INSUFFICIENT_BIST_RECONSTRUCTED_HISTORY")
    # In this community source an unpublished 2026-10 review may have
    # no full tiers. Incomplete snapshots are NEVER used as actual members.
    return {
        "status":("COMMUNITY_RECONSTRUCTED_HISTORY_HAS_TICKER_AMBIGUITIES"
                  if duplicated_keys else "COMMUNITY_RECONSTRUCTED_HISTORICAL_UNIVERSE_COVERAGE"),
        "duplicate_symbol_date_rows":duplicated_keys,
        "conflicting_tier_duplicate_rows":conflicting_tier_keys,
        "distinct_snapshot_symbol_keys":len(seen),
        "membership_rows":rows,"unique_issuer_symbols":len(tickers),
        "review_snapshots":len(dates),"complete_tier_snapshots":len(complete),
        "first_snapshot":min(dates),"latest_snapshot_date":max(dates),
        "last_full_30_20_50_snapshot":max(complete),
        "incomplete_snapshot_count":len(dates)-len(complete),
        "official_original_publication_timestamps_verified":False,
        "historical_delisted_issuer_prices_provided":False,
        "underlying_exchange_dataset_redistribution_license_verified":False,
        "universe_pit_trade_eligible":False,
        "membership_clock_is_effective_date_not_publication":True,
    }


def sec_coverage(raw, cik, captured):
    if cik not in SEC_CIKS:
        raise ValueError("SEC_ISSUER_NOT_PRE_REGISTERED")
    payload=json.loads(raw)
    if int(payload.get("cik",0))!=int(cik):
        raise ValueError("SEC_CIK_WRONG_ISSUER")
    native=pit_adapters.sec_events(
        payload,asset=f"US:CIK{cik}",retrieved_utc=captured)
    kinds=Counter(record["kind"] for record in native)
    times=[record["public_at_utc"] for record in native]
    return {
        "status":"SEC_RECENT_FILING_HISTORY_ONLY",
        "cik":cik,"included_filing_forms":dict(kinds),
        "events_with_conservative_acceptance_plus_24h_clock":len(native),
        "earliest_eligible_utc":min(times) if times else None,
        "latest_eligible_utc":max(times) if times else None,
        "historical_archive_additional_files_fetched":False,
        "current_ticker_lookback_mapping_is_pit_verified":False,
        "historical_delisted_prices_verified":False,
        "first_seen_is_actual_collector_utc":True,
        "independent_trading_alpha_proven":False,
    }


def _attempt(label, url, parser, getter, agent=None):
    data=None
    try:
        data=getter(url,MAX_SOURCE_BYTES,agent)
        findings=parser(data)
        return {**findings,"raw_source_sha256":hashlib.sha256(data).hexdigest(),
                "publisher_url":url,"bytes_verified":len(data)}
    except (HTTPError,URLError,TimeoutError,OSError,UnicodeError,
            json.JSONDecodeError,ValueError,TypeError,KeyError) as exc:
        # Preserve precise bounded source-quality reason without copying
        # source records, prices, private data, or provider response bodies.
        result={"status":"SOURCE_UNAVAILABLE_OR_INTEGRITY_REJECTED",
                "failure_type":type(exc).__name__,"publisher_url":url,
                "alpha_proven":False}
        if isinstance(exc, ValueError):
            reason=str(exc)
            result["validation_reason_code"]=(
                reason[:90] if re.fullmatch(r"[A-Z0-9_]+",reason) else
                "INVALID_FORMAT_OR_UNKNOWN_SOURCE_SCHEMA")
        if data is not None:
            result["raw_source_sha256"]=hashlib.sha256(data).hexdigest()
            result["bytes_received"]=len(data)
        return result


def audit(now=None, getter=get, sec_agent=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("collector must use timezone-aware UTC")
    captured=now.astimezone(timezone.utc).isoformat()
    sources={
        "CBOE_VIX_1990_HISTORY":_attempt(
            "CBOE",VIX_URL,vix_coverage,getter),
        "BIST_RECONSTRUCTED_CONSTITUENTS":_attempt(
            "BIST",BIST_URL,bist_coverage,getter),
    }
    for cik in SEC_CIKS:
        alias=f"SEC_CIK{cik}_SUBMISSIONS"
        url=f"{SEC_BASE}{cik}.json"
        if not sec_agent or not re.fullmatch(SEC_AGENT_RE,sec_agent.strip()):
            sources[alias]={
                "status":"SEC_REQUIRED_CONTACT_USER_AGENT_NOT_CONFIGURED",
                "publisher_url":url,"alpha_proven":False}
        else:
            sources[alias]=_attempt(alias,url,
                lambda raw,cik=cik:sec_coverage(raw,cik,captured),
                getter,sec_agent)
    return {
        "schema":SCHEMA,"captured_utc":captured,
        "source_count":len(sources),"sources":sources,
        "market_coverage":{"BIST":"HISTORICAL_UNIVERSE_DIAGNOSTIC_ONLY",
                           "US":"VIX_MACRO_AND_SEPARATE_ISSUER_FILING_EVENTS"},
        "historical_data_retrieved_today_does_not_recreate_old_first_seen":True,
        "price_issuer_corporate_action_pit_replay_verified":False,
        "eligible_for_strategy_qualification":False,
        "paper_eligible":False,"live_allowed":False,
        "raw_vendor_archive_exported":False}


def append(path, result):
    path=Path(path)
    prev="GENESIS"
    lines=[]
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            obj=json.loads(line)
            unsigned={k:v for k,v in obj.items() if k!="sha256"}
            if obj.get("prev_sha256")!=prev or sha(unsigned)!=obj.get("sha256"):
                raise ValueError("EQUITY_SOURCE_EVIDENCE_SHA_CHAIN_INVALID")
            prev=obj["sha256"]
            lines.append(obj)
    new_digest=sha(result["sources"])
    if lines and lines[-1]["source_coverage_digest"]==new_digest:
        return {"status":"UNCHANGED_SOURCE_NO_NEW_MARKET_EVIDENCE",
                "records":len(lines)}
    snapshot={
        "schema":"eaverse.equity_source_assurance_ledger.v1",
        "captured_utc":result["captured_utc"],
        "source_coverage_digest":new_digest,
        "full_source_coverage":result["sources"],
        "market_coverage":result["market_coverage"],
        "source_revision":bool(lines and
            lines[-1]["source_coverage_digest"]!=new_digest),
        "alpha_proven":False,"paper_eligible":False,
        "prev_sha256":prev,
    }
    snapshot["sha256"]=sha(snapshot)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a",encoding="utf-8") as f:
        f.write(canonical(snapshot)+"\n")
    return {"status":"APPENDED_SOURCE_COVERAGE_NOT_AN_ALPHA_TRIAL",
            "records":len(lines)+1,"new_sha256":snapshot["sha256"]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=Path("out/equity_source_intelligence"))
    parser.add_argument("--ledger",type=Path)
    args=parser.parse_args()
    result=audit(sec_agent=os.environ.get("SEC_RESEARCH_USER_AGENT"))
    args.out.mkdir(parents=True,exist_ok=True)
    if args.ledger:
        result["source_summary_ledger"]=append(args.ledger,result)
    (args.out/"coverage_report.json").write_text(
        json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n",
        encoding="utf-8")
    print("EAVERSE_EQUITY_HISTORY_SOURCES",
          {key:value["status"] for key,value in result["sources"].items()})
    print("QUALIFICATION_READY",result["eligible_for_strategy_qualification"])


if __name__=="__main__":
    main()
