"""Durable PAPER nominations; never mistake historical profit for forward evidence."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from datetime import UTC, datetime, timedelta

from . import data, paper_archive, reference, research, universe

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_candidates (
    asset TEXT NOT NULL, version TEXT NOT NULL, id TEXT NOT NULL,
    source TEXT NOT NULL, spec TEXT NOT NULL, nominated_asof TEXT NOT NULL,
    discovery TEXT NOT NULL, validation TEXT NOT NULL, record TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT, checked_at TEXT, sequence INTEGER,
    PRIMARY KEY(asset,version,id)
);
CREATE TABLE IF NOT EXISTS paper_candidate_archive_checked(sha TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS paper_candidate_archive_policy_checked(sha TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS paper_candidate_archive_attempts(sha TEXT PRIMARY KEY,checked TEXT NOT NULL,error TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS paper_candidates_status ON paper_candidates(status,asset,nominated_asof);
"""

# Reserve at least half of each market's usual rotation for fresh exploration.
# A full capacity is a waiting state, not rejection or loss of evidence.
CAPACITY = {name: max(1, limit // 2) for name, limit in universe.LIMITS.items()}
PRIORITY_PER_MARKET = 4
ADMISSIONS_PER_MARKET_PER_RUN = 4
SOURCE_ALLOW = {"coinbase", "yahoo"}


def previous_identities(db):
    return {(row["asset"],row["version"],ident) for row in db.execute("SELECT asset,version,origin FROM paper")
            if (ident:=json.loads(row["origin"]).get("id"))}


def register(db,row,existing):
    if not row["spec"] or not row["policy"] or row["version"]!=research.VERSION:
        return 0
    d,v=json.loads(row["discovered"]),json.loads(row["validated"] or "{}")
    old=row["policy"]==research.canonical(research.LEGACY_POLICY)
    def only_old_limit(m):
        return (old and m.get("pass") is False and m.get("net",0)>0
                and m.get("trades",0)>=6 and m.get("profitable_folds",0)>=2
                and .25<m.get("drawdown",0)<1)
    rescue=only_old_limit(d) or only_old_limit(v)
    if not ((d.get("pass") is True and v.get("pass") is True)
            or (rescue and (d.get("pass") is True or only_old_limit(d))
                and (not v or v.get("pass") is True or only_old_limit(v)))):
        return 0
    status,reason="waiting",None
    if (row["asset"],row["version"],row["id"]) in existing:
        status,reason="previous_paper","exact_strategy_already_observed_or_nominated"
    elif row["source"] not in SOURCE_ALLOW:
        status,reason="research_only","source_not_forward_qualified"
    elif (d.get("superlab_receipt") or {}).get("status")=="INVALID_EVIDENCE":
        status,reason="invalid_evidence","central_contract_rejected"
    elif row["policy"] not in (research.canonical(research.POLICY),research.canonical(research.LEGACY_POLICY)):
        status,reason="research_only","incompatible_frozen_policy"
    elif v.get("execution_audit",{}).get("status")=="mismatch":
        status,reason="invalid_evidence","execution_audit_mismatch"
    elif v.get("execution_audit",{}).get("status")!="matched" and not rescue:
        status,reason="audit_pending",v.get("execution_audit",{}).get("status","missing_execution_audit")
    inserted=db.execute("INSERT OR IGNORE INTO paper_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
        (row["asset"],row["version"],row["id"],row["source"],row["spec"],row["asof"],
         row["discovered"],row["validated"] or "{}",research.canonical(dict(row)),status,reason))
    return inserted.rowcount


def seed(db):
    """Recover intact positive historical nominations and preserve all new ones."""
    existing=previous_identities(db)
    return sum(register(db,row,existing) for row in db.execute("""SELECT * FROM attempts
        WHERE version=? AND spec<>'' AND policy<>'' AND NOT EXISTS
        (SELECT 1 FROM paper_candidates c WHERE c.asset=attempts.asset AND c.version=attempts.version AND c.id=attempts.id)
        ORDER BY asof,asset,id""",(research.VERSION,)))


def restore_archive(db,path,sha,expected):
    """Verify complete archived evidence and its surviving permanent test keys."""
    if not 0<expected<=30000 or path.stat().st_size>32*1024*1024:
        raise ValueError("candidate archive exceeds bounds")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        raise ValueError("candidate archive sha mismatch")
    existing=previous_identities(db)
    seen=set()
    added=total_bytes=0
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        while line:=stream.readline(8*1024*1024+1):
            row_bytes=len(line.encode())
            if row_bytes>8*1024*1024:
                raise ValueError("candidate archive record too large")
            total_bytes+=row_bytes
            if total_bytes>768*1024*1024:
                raise ValueError("candidate archive decompression cap")
            item=json.loads(line);row=item["row"];kind=item["type"]
            key=(kind,row["version"],row["asset"],row["id"])
            if kind not in ("technical","event") or key in seen or len(seen)>=expected:
                raise ValueError("invalid candidate archive record")
            seen.add(key)
            if kind!="technical" or row["version"]!=research.VERSION:
                continue
            prior=db.execute("SELECT * FROM attempts WHERE version=? AND asset=? AND id=?",
                             (row["version"],row["asset"],row["id"])).fetchone()
            if not prior or json.loads(prior["discovered"]).get("archive")!=sha or any(
                prior[k]!=row[k] for k in ("source","snapshot","asof","code")):
                raise ValueError("candidate archive provenance mismatch")
            # Original archive is immutable; the separate admission queue keeps
            # only the positive nomination, not every archived full trial.
            added+=register(db,row,existing)
    if len(seen)!=expected:
        raise ValueError("candidate archive record count mismatch")
    db.execute("INSERT OR IGNORE INTO paper_candidate_archive_checked VALUES (?)",(sha,))
    db.execute("INSERT OR IGNORE INTO paper_candidate_archive_policy_checked VALUES (?)",(sha,))
    return added


def recover_one_archive(db,now=None):
    """Bounded recovery of old successful nominations that were compacted."""
    from datetime import UTC, datetime
    now=now or datetime.now(UTC)
    row=db.execute("""SELECT h.sha,h.url,h.records,max(a.asof) AS most_recent
        FROM attempts a JOIN history_archives h ON h.sha=json_extract(a.discovered,'$.archive')
        WHERE a.version=? AND a.policy='' AND (
          (json_extract(a.discovered,'$.pass')=1 AND json_extract(a.discovered,'$.outcome')='screened_not_final'
           AND json_extract(a.discovered,'$.validation_metrics.net')>0)
          OR (json_extract(a.discovered,'$.outcome') IN ('discovery_gate_failed','validation_gate_failed') AND
              json_extract(a.discovered,'$.discovery_metrics.net')>0 AND
              (json_extract(a.discovered,'$.discovery_metrics.drawdown')>.25 OR
               json_extract(a.discovered,'$.validation_metrics.drawdown')>.25)))
          AND NOT EXISTS (SELECT 1 FROM paper_candidate_archive_policy_checked c WHERE c.sha=h.sha)
          AND NOT EXISTS (SELECT 1 FROM paper_candidate_archive_attempts c WHERE c.sha=h.sha AND substr(c.checked,1,10)=?)
          AND NOT EXISTS (SELECT 1 FROM paper_candidates p WHERE p.asset=a.asset AND p.version=a.version AND p.id=a.id)
        GROUP BY h.sha,h.url,h.records ORDER BY most_recent DESC,h.sha LIMIT 1""",(research.VERSION,now.date().isoformat())).fetchone()
    if not row:
        return {"status":"complete","added":0}
    repo=os.environ["GITHUB_REPOSITORY"]
    sha=row["sha"]
    name=f"research-{sha}.jsonl.gz"
    pattern=r"https://github\.com/"+re.escape(repo)+r"/releases/download/(research-history-[0-9]{4}-[0-9]{2})/"+re.escape(name)
    match=re.fullmatch(pattern,row["url"])
    if not match or not re.fullmatch(r"[0-9a-f]{64}",sha):
        raise ValueError("unsupported candidate archive origin")
    from .delivery import gh
    db.execute("SAVEPOINT legacy_risk_archive")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            gh("release","download",match[1],"-R",repo,"--pattern",name,"--dir",tmp)
            added=restore_archive(db,Path(tmp)/name,sha,row["records"])
        result={"status":"recovered","added":added,"sha":sha}
    except Exception as exc:
        db.execute("ROLLBACK TO legacy_risk_archive")
        error=str(exc)[:220]
        db.execute("INSERT OR REPLACE INTO paper_candidate_archive_attempts VALUES (?,?,?)",(sha,now.isoformat(),error))
        result={"status":"pending","added":0,"sha":sha,"reason":error}
    finally:
        db.execute("RELEASE legacy_risk_archive")
    return result


def recover_archives(db,now,seconds=120):
    deadline=time.monotonic()+seconds
    results=[]
    while time.monotonic()<deadline:
        result=recover_one_archive(db,now)
        if result["status"]=="complete":
            break
        results.append(result)
    return {"status":"complete" if not results else "processed","added":sum(x["added"] for x in results),"archives":results}


def priority_assets(db, market, today):
    return [row[0].split(":",1)[1] for row in db.execute("""
        SELECT c.asset FROM paper_candidates c
        WHERE c.status='waiting' AND c.asset LIKE ?
          AND NOT EXISTS (SELECT 1 FROM paper p WHERE p.asset=c.asset AND p.status='active')
          AND NOT EXISTS (SELECT 1 FROM asset_rotation r WHERE r.market=?
              AND r.symbol=substr(c.asset,instr(c.asset,':')+1) AND r.due>?)
        GROUP BY c.asset ORDER BY min(c.nominated_asof), c.asset LIMIT ?
        """, (market+":%",market,today.isoformat(),PRIORITY_PER_MARKET))]


def mark(db,row,status,reason,now,sequence=None):
    db.execute("""UPDATE paper_candidates SET status=?,reason=?,checked_at=?,sequence=?
        WHERE asset=? AND version=? AND id=?""",
        (status,reason,now.isoformat(),sequence,row["asset"],row["version"],row["id"]))


def admit(db, asset, market, bars, now):
    """Requalify a previously fixed hypothesis on current closed data, then freeze.

    No new historical attempt identity or historical PAPER return is created.
    A failed admission is terminal for that exact hypothesis.
    """
    if db.execute("SELECT 1 FROM paper WHERE asset=? AND status='active'",(asset,)).fetchone():
        return None
    active=db.execute("SELECT count(*) FROM paper WHERE status='active' AND asset LIKE ?",(market.name+":%",)).fetchone()[0]
    if active >= CAPACITY[market.name]:
        return None
    row=db.execute("""SELECT * FROM paper_candidates WHERE asset=? AND status='waiting'
        ORDER BY nominated_asof,id LIMIT 1""",(asset,)).fetchone()
    if not row:
        return None
    if row["source"] != market.source or market.source not in SOURCE_ALLOW:
        mark(db,row,"research_only","source_mismatch",now)
        return {"status":"research_only","id":row["id"]}
    receipt=json.loads(row["discovery"]).get("superlab_receipt")
    if os.getenv("GITHUB_ACTIONS")=="true" and not receipt:
        # A historical record may predate the central review. Review its
        # original immutable record, never invent a receipt.
        original=json.loads(row["record"])
        try:
            from research.portfolio_qualification.eaverse_coverage import audit_record
            receipt=audit_record(original,"technical")
        except ImportError:
            return {"status":"waiting_central_review","id":row["id"]}
        except (ValueError,TypeError,KeyError,OverflowError):
            mark(db,row,"invalid_evidence","central_contract_rejected",now)
            return {"status":"invalid_evidence","id":row["id"]}
    if receipt and receipt.get("status")=="INVALID_EVIDENCE":
        mark(db,row,"invalid_evidence","central_contract_rejected",now)
        return {"status":"invalid_evidence","id":row["id"]}
    spec=json.loads(row["spec"])
    if research.digest(spec)!=row["id"]:
        mark(db,row,"invalid_evidence","strategy_identity_mismatch",now)
        return {"status":"invalid_evidence","id":row["id"]}
    if len(bars)<600 or bars.index[-1].strftime("%Y-%m-%d")<row["nominated_asof"]:
        return {"status":"waiting_source","id":row["id"]}
    periods=365 if market.calendar is None else 252
    discovery_index=bars.index[200:len(bars)-research.MIN_VALIDATION_BARS-4]
    validation_index=bars.index[-research.MIN_VALIDATION_BARS:]
    desired=research.target(bars,research.features(bars,[spec]),spec,periods)
    d=research.history_metrics(bars,desired,discovery_index,market.cost_bps,periods)
    v=research.history_metrics(bars,desired,validation_index,market.cost_bps,periods)
    v["execution_audit"]=reference.audit_validation(bars,desired,market.cost_bps,v)
    if not d["pass"] or not v["pass"]:
        mark(db,row,"recheck_failed","current_discovery_or_validation_failed",now)
        return {"status":"recheck_failed","id":row["id"]}
    if v["execution_audit"]["status"]!="matched":
        status="invalid_evidence" if v["execution_audit"]["status"]=="mismatch" else "audit_pending"
        mark(db,row,status,v["execution_audit"]["status"],now)
        return {"status":status,"id":row["id"]}
    now=max(now,datetime.now(UTC)) if os.getenv("GITHUB_ACTIONS")=="true" else now
    start=(now.date()+timedelta(days=1)).isoformat()
    if market.calendar:
        import exchange_calendars as xcals
        start=xcals.get_calendar(market.calendar).date_to_session(start,direction="next").strftime("%Y-%m-%d")
    anchor=bars.iloc[-400:]
    asof=bars.index[-1].strftime("%Y-%m-%d")
    origin={"id":row["id"],"source":market.source,"data_sha256":data.fingerprint(bars),
            "code":os.getenv("GITHUB_SHA","local"),"periods":periods,"calendar":market.calendar,
            "data_start":bars.index[0].strftime("%Y-%m-%d"),"data_end":asof,
            "history_cost_bps":2*market.cost_bps,"discovery":d,"validation":v,
            "nominated_asof":row["nominated_asof"],"superlab_receipt":receipt,
            "admission_checked_at":now.isoformat()}
    result=db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,'active')""",
        (asset,row["spec"],market.cost_bps,start,anchor.index[0].strftime("%Y-%m-%d"),asof,
         data.fingerprint(anchor),row["version"],research.canonical(research.POLICY),research.canonical(origin)))
    sequence=result.lastrowid
    frozen=db.execute("SELECT * FROM paper WHERE sequence=?",(sequence,)).fetchone()
    paper_archive.capture(db,frozen,anchor)
    db.execute("DELETE FROM paper_data_issues WHERE asset=?",(asset,))
    mark(db,row,"admitted","future_open_only",now,sequence)
    return {"status":"admitted","id":row["id"],"sequence":sequence,"start":start}


def pause_integrity(db, asset, error, now):
    """Two consecutive digest failures retire the slot, not the frozen evidence."""
    if not any(token in error for token in ("PAPER origin candles", "previously observed PAPER candles",
                                             "PAPER opening date is missing", "PAPER archive")):
        return False
    prior=db.execute("SELECT error FROM paper_data_issues WHERE asset=?",(asset,)).fetchone()
    if not prior or prior[0]!=error:
        return False
    row=db.execute("SELECT sequence,cursor FROM paper WHERE asset=? AND status='active'",(asset,)).fetchone()
    if not row:
        return False
    evidence={"reason":"persistent_source_integrity_failure","error":error,
              "checked_at":now.isoformat(),"last_verified_cursor":row["cursor"],
              "forward_validation_complete":False}
    db.execute("UPDATE paper SET status='paused_integrity',result=? WHERE sequence=?",
               (research.canonical(evidence),row["sequence"]))
    return True


def summary(db):
    counts={r[0]:r[1] for r in db.execute("SELECT status,count(*) FROM paper_candidates GROUP BY status")}
    active={r[0]:r[1] for r in db.execute("SELECT substr(asset,1,instr(asset,':')-1),count(*) FROM paper WHERE status='active' GROUP BY 1")}
    rows=[]
    for r in db.execute("""SELECT asset,id,nominated_asof,validation,status,reason,sequence
        FROM paper_candidates ORDER BY CASE status WHEN 'waiting' THEN 0 WHEN 'audit_pending' THEN 1 ELSE 2 END,
        nominated_asof DESC,asset,id LIMIT 250"""):
        v=json.loads(r["validation"])
        rows.append({"asset":r["asset"],"id":r["id"],"asof":r["nominated_asof"],"net":v.get("net"),
                     "drawdown":v.get("drawdown"),"status":r["status"],"reason":r["reason"],"sequence":r["sequence"]})
    positives = [dict(row) for row in db.execute("""
        SELECT version AS engine,count(*) AS positive_discovery_and_validation
        FROM attempts WHERE version<>?
          AND COALESCE(json_extract(discovered,'$.metrics.net'),json_extract(discovered,'$.net'),
                       json_extract(discovered,'$.discovery_metrics.net'))>0
          AND COALESCE(json_extract(validated,'$.metrics.net'),json_extract(validated,'$.net'),
                       json_extract(discovered,'$.validation_metrics.net'))>0
          AND COALESCE(json_extract(discovered,'$.superlab_receipt.status'),'')<>'INVALID_EVIDENCE'
          AND COALESCE(json_extract(discovered,'$.outcome'),'')<>'invalid_evidence'
        GROUP BY version ORDER BY version
        """, (research.VERSION,))]
    return {"historical_positive_by_engine":positives,"candidate_counts":counts,"active_by_market":active,"capacity_by_market":CAPACITY,
            "paused_integrity":db.execute("SELECT count(*) FROM paper WHERE status='paused_integrity'").fetchone()[0],
            "candidates":rows,"total_candidates":sum(counts.values()),"live_allowed":False}
