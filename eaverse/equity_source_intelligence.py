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
MAX_SEC_ARCHIVE_SEGMENTS_PER_ISSUER=2
SEC_ARCHIVE_FILENAME_RE=re.compile(r"^CIK(\d{10})-submissions-(\d{3})\.json$")
VIX_FIXED_THRESHOLDS=(25,35)
SEC_AGENT_RE="^[^\\s]+(?:\\s+[^\\s]+)*\\s+[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"


def canonical(item):
    return json.dumps(item,sort_keys=True,separators=(",",":"),allow_nan=False)


def sha(item):
    return hashlib.sha256(canonical(item).encode()).hexdigest()


def get(url, limit=MAX_SOURCE_BYTES, agent=None):
    sec_archive=SEC_ARCHIVE_FILENAME_RE.fullmatch(url.rsplit("/",1)[-1])
    archive_allowed=(url.startswith("https://data.sec.gov/submissions/") and
                     sec_archive is not None and sec_archive.group(1) in SEC_CIKS and
                     url=="https://data.sec.gov/submissions/"+sec_archive.group(0))
    if not (url==BIST_URL or url==VIX_URL or archive_allowed or
            url in {f"{SEC_BASE}{cik}.json" for cik in SEC_CIKS}):
        raise ValueError("UNREGISTERED_REMOTE_DATA_SOURCE")
    if limit>MAX_SOURCE_BYTES:
        raise ValueError("REMOTE_SOURCE_LIMIT_EXCEEDED")
    if url.startswith(SEC_BASE) or archive_allowed:
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
    """Official date-only VIX CLOSE diagnostics; reject false OHLC/PIT claims.

    Several historical publisher OHLC records can be inconsistent while the
    closing VIX value is valid. Do not silently repair OHLC or use invalid
    HIGH/LOW for intraday fill claims. Keep exact integrity counters and SHA.
    """
    reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    fields=set(reader.fieldnames or [])
    if not {"DATE","OPEN","HIGH","LOW","CLOSE"}.issubset(fields):
        raise ValueError("VIX_COLUMN_SCHEMA_MISSING")
    closes={}
    duplicated=set()
    count=0
    anomalies=0
    bad_close=0
    repeated=0
    order_errors=0
    last_date=None
    first_bad_dates=[]
    for record in reader:
        label=record["DATE"].strip()
        try:
            date=(datetime.strptime(label,"%m/%d/%Y").date()
                  if "/" in label else datetime.strptime(label,"%Y-%m-%d").date())
            opn,hi,lo,close=(float(record[k]) for k in ("OPEN","HIGH","LOW","CLOSE"))
        except (TypeError,ValueError,KeyError) as exc:
            raise ValueError("INVALID_HISTORICAL_VIX_ROW") from exc
        count+=1
        if count>MAX_ROWS:
            raise ValueError("VIX_SOURCE_TOO_MANY_ROWS")
        if last_date is not None and date<=last_date:
            order_errors+=1
        last_date=date
        # Duplicated date is ambiguous, even when repeated CLOSE matches:
        # it cannot be an additional independent historical observation.
        if date in closes or date in duplicated:
            repeated+=1
            duplicated.add(date)
            closes.pop(date,None)
            if len(first_bad_dates)<6:
                first_bad_dates.append(date.isoformat())
            continue
        if not math.isfinite(close) or not 0<close<300:
            bad_close+=1
            if len(first_bad_dates)<6:
                first_bad_dates.append(date.isoformat())
            continue
        closes[date]=close
        # Allow close-only historical coverage, but never call the
        # entire daily OHLC feed "validated" if it is not physically sane.
        if (not all(math.isfinite(x) for x in (opn,hi,lo,close))
            or not 0<lo<=min(opn,close)<=hi<300):
            anomalies+=1
            if len(first_bad_dates)<6:
                first_bad_dates.append(date.isoformat())
    dates=sorted(closes)
    if not dates or len(dates)<6000 or dates[0].year>1994 or dates[-1].year<2025:
        raise ValueError("VIX_HISTORICAL_CLOSE_COVERAGE_INSUFFICIENT")
    spikes=Counter()
    previous=None
    for date in dates:
        close=closes[date]
        for threshold in VIX_FIXED_THRESHOLDS:
            if previous is not None and previous<threshold<=close:
                spikes[f"cross_above_{threshold}"]+=1
        previous=close
    suspect=bool(anomalies or bad_close or repeated or order_errors)
    return {
        "status":("OFFICIAL_VIX_CLOSE_HISTORY_HAS_OHLC_OR_DUPLICATE_ANOMALIES"
                  if suspect else "OFFICIAL_HISTORICAL_DAILY_VIX_FORMAT_VERIFIED"),
        "raw_source_rows":count,"rows":len(dates),
        "first_date":dates[0].isoformat(),"last_date":dates[-1].isoformat(),
        "ohlc_anomaly_rows":anomalies,
        "invalid_close_rows":bad_close,
        "duplicate_date_rows":repeated,
        "nonchronological_source_rows":order_errors,
        "first_ambiguous_dates_only":first_bad_dates,
        "historical_close_coverage_present":True,
        "historical_full_ohlc_replay_verified":not suspect,
        "fixed_regime_crossings":dict(spikes),
        "fixed_regime_crossings_are_retrospective_close_only":True,
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
    delisted_symbols=set()
    unknown_delisted=0
    invalid_delisted=0
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
        delist_label=str(record.get("delisted_in_year","")).strip().lower()
        if delist_label in {"true","1","yes"}:
            delisted_symbols.add(ticker)
        elif delist_label not in {"false","0","no"}:
            unknown_delisted+=1
            if delist_label:
                invalid_delisted+=1
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
        "symbols_flagged_delisted_in_reconstructed_rows":len(delisted_symbols),
        "rows_with_unknown_delisting_flag":unknown_delisted,
        "rows_with_unrecognized_delisting_flag":invalid_delisted,
        "delisting_labels_are_not_verified_delisting_event_clocks":True,
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


def sec_archive_coverage(raw, cik, captured, getter, agent):
    """Bounded historical SEC segments, preserving CIK and acceptance timestamps.

    The current submissions listing announces older files; each fetched file
    is independently SHA-256 recorded. This covers an issuer's filings, not
    the old tradable ticker, share class, delisted prices, or PIT universe.
    """
    result=sec_coverage(raw,cik,captured)
    root=json.loads(raw)
    files=root.get("filings",{}).get("files",[])
    if not isinstance(files,list):
        raise ValueError("SEC_HISTORICAL_FILES_SCHEMA_INVALID")
    result.update({
        "historical_archive_files_declared":len(files),
        "historical_archive_files_scanned":0,
        "historical_archive_segments":[],
        "historical_archive_complete_for_issuer":not bool(files),
        "extra_distinct_historical_events":0,
        "current_security_id_backdated":False,
        "historic_security_delisting_and_prices_verified":False,
        "archive_limit_segments_per_issuer":MAX_SEC_ARCHIVE_SEGMENTS_PER_ISSUER,
    })
    if not files:
        return result
    seen=set()
    try:
        recent=root["filings"]["recent"]
        seen={str(x) for x in recent["accessionNumber"]}
    except (TypeError,KeyError):
        raise ValueError("SEC_RECENT_ACCESSION_REFERENCE_MISSING")
    chronological=sorted(files,key=lambda r: (str(r.get("filingFrom","")),str(r.get("name",""))))
    for row in chronological[:MAX_SEC_ARCHIVE_SEGMENTS_PER_ISSUER]:
        name=row.get("name") if isinstance(row,dict) else None
        match=SEC_ARCHIVE_FILENAME_RE.fullmatch(name or "")
        if match is None or match.group(1)!=cik:
            raise ValueError("SEC_HISTORICAL_FILENAME_CIK_MISMATCH")
        endpoint="https://data.sec.gov/submissions/"+name
        try:
            blob=getter(endpoint,MAX_SOURCE_BYTES,agent)
            data=json.loads(blob)
            if not isinstance(data,dict):
                raise ValueError("SEC_HISTORICAL_ARRAY_SCHEMA_INVALID")
            original={
                "cik":int(cik),
                "filings":{"recent":data},
            }
            discovered=pit_adapters.sec_events(
                original,asset=f"US:CIK{cik}",retrieved_utc=captured)
            prior_ids=set(seen)
            unique=[event for event in discovered if event["id"] not in prior_ids]
            seen.update(event["id"] for event in discovered)
            result["extra_distinct_historical_events"]+=len(unique)
            result["historical_archive_segments"].append({
                "file":name, "declared_from":row.get("filingFrom"),
                "declared_to":row.get("filingTo"),
                "source_sha256":hashlib.sha256(blob).hexdigest(),
                "bytes":len(blob), "known_lagged_events":len(discovered),
                "additional_unique_events":len(unique),
                "earliest_event_eligible_utc":min(
                    (x["public_at_utc"] for x in unique),default=None),
                "status":"SOURCE_SEGMENT_VERIFIED_WITH_CONSERVATIVE_ACCEPTANCE_CLOCK",
                "historical_ticker_mapping_verified":False,
            })
            result["historical_archive_files_scanned"]+=1
        except (HTTPError,URLError,TimeoutError,OSError,UnicodeError,
                json.JSONDecodeError,ValueError,TypeError,KeyError) as exc:
            result["historical_archive_segments"].append({
                "file":name,"status":"SEGMENT_UNAVAILABLE_OR_INVALID",
                "failure_type":type(exc).__name__})
            break
    result["historical_archive_complete_for_issuer"]=(
        len(files)<=MAX_SEC_ARCHIVE_SEGMENTS_PER_ISSUER and
        result["historical_archive_files_scanned"]==len(files))
    result["historical_archive_additional_files_fetched"]=(
        result["historical_archive_files_scanned"]>0)
    result["status"]=(
        "SEC_RECENT_PLUS_BOUNDED_HISTORICAL_FILINGS_NOT_TICKER_PIT"
        if result["historical_archive_files_scanned"]
        else "SEC_RECENT_ONLY_HISTORICAL_SEGMENTS_BLOCKED")
    result["original_publication_first_seen_reconstructed"]=False
    result["full_delisted_stock_prices_or_returns"]=False
    return result


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
                lambda raw,cik=cik:sec_archive_coverage(
                    raw,cik,captured,getter,sec_agent),
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
