"""Executable, prospective, research-only conditional PAPER for native strategies.

Historical screens are not PAPER observations. Existing legacy PAPER and frozen
Foundry cohorts are untouched. Each native identity is decided once; missing
source/evidence is durable pending debt, not a fabricated failure or success.
"""
from __future__ import annotations

import gzip
import hashlib
import inspect
import json
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from functools import lru_cache

import numpy as np
import pandas as pd

from . import (combination_research as combo, coverage, data, events, foundry,
               indicator_bank, indicator_research as indicator, research, recovery_risk, surface, universe)

VERSION = "conditional-native-paper-v2"
LEGACY_ADAPTER_HASH = "bf71b0e182624cee748eba5fc73dbb76339be5d87c479ac24d382a22cfbad7fc"
ENGINES = (coverage.VERSION, foundry.VERSION, combo.VERSION, indicator.VERSION)
POLICIES = {coverage.VERSION:coverage.POLICY,foundry.VERSION:foundry.POLICY,
            combo.VERSION:combo.POLICY,indicator.VERSION:indicator.POLICY}
FORWARD_BARS = 90
MIN_ENTRIES = 6
MIN_VALID_ENTRIES = 2
ARCHIVE_WORKERS = 3
ARCHIVE_SECONDS = 420
BACKLOG_SECONDS = 1800
# Legacy archive batches were larger than the current 16 MiB writer target.
# Match the established durable-checkpoint expansion envelope; stream one row at a time.
MAX_ARCHIVE_EXPANDED = 768 * 1024 * 1024
MAX_ARCHIVE_LINE = 8 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS native_paper_candidates(
 version TEXT NOT NULL, asset TEXT NOT NULL, id TEXT NOT NULL, source TEXT NOT NULL,
 family TEXT NOT NULL, asof TEXT NOT NULL, archive TEXT, record TEXT,
 initial TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, checked TEXT,
 qualification TEXT, forward TEXT, PRIMARY KEY(version,asset,id)
);
CREATE INDEX IF NOT EXISTS native_paper_status ON native_paper_candidates(status,asset,checked);
CREATE TABLE IF NOT EXISTS native_paper_archives(sha TEXT PRIMARY KEY,checked TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS native_paper_archive_attempts(sha TEXT PRIMARY KEY,checked TEXT NOT NULL,status TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS native_paper_policy_migration(
 version TEXT NOT NULL,asset TEXT NOT NULL,id TEXT NOT NULL,policy TEXT NOT NULL,
 previous TEXT NOT NULL,migrated TEXT NOT NULL,PRIMARY KEY(version,asset,id,policy));
CREATE TABLE IF NOT EXISTS native_paper_restarts(
 version TEXT NOT NULL,asset TEXT NOT NULL,id TEXT NOT NULL,stamp TEXT NOT NULL,
 previous TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(version,asset,id,stamp));
"""
PENDING = ("queued","waiting_source","waiting_calendar","waiting_history","qualified_waiting_capacity","policy_recheck")
ACTIVE = "conditional_active"


def canonical(value):
    return research.canonical(value)


def metrics_pair(row):
    d=json.loads(row["discovered"])
    v=json.loads(row["validated"]) if row["validated"] else {}
    return d,v,d.get("metrics",d.get("discovery_metrics",d)),v.get("metrics",d.get("validation_metrics",v))


def positive(train,valid):
    return all(isinstance(x.get("net"),(int,float)) and math.isfinite(x["net"]) and x["net"]>0
               for x in (train,valid))


def entries(m):
    val=m.get("entries",m.get("trades"))
    return val if isinstance(val,int) and val>=0 else None


def initial_gate(train,valid):
    """Pre-screen known history without a percentage drawdown cutoff."""
    if not positive(train,valid):
        return "nonpositive_history"
    for m in (train,valid):
        if m.get("status") in ("ruin","ruin_stress","no_entries"):
            return "ruin_or_no_entries"
        dd=m.get("drawdown")
        if not isinstance(dd,(int,float)) or not math.isfinite(dd) or dd<0:
            return "missing_risk_evidence"
        if dd>=1 or m.get("net",0)<=-1:
            return "capital_ruin"
    n,m=entries(train),entries(valid)
    if n is None or m is None:
        return "missing_trade_evidence"
    if n+m<MIN_ENTRIES or m<MIN_VALID_ENTRIES or n<MIN_VALID_ENTRIES:
        return "insufficient_trade_evidence"
    return None


def record_status(row):
    d,v,train,valid=metrics_pair(row)
    reason=initial_gate(train,valid)
    if (d.get("superlab_receipt") or {}).get("status")=="INVALID_EVIDENCE" or d.get("outcome")=="invalid_evidence":
        return "invalid_evidence","central_contract_rejected",train,valid
    has_record=bool(row["spec"] and row["policy"] and row["validated"])
    if reason:
        if reason.startswith("missing_"):
            return ("awaiting_archive" if not has_record and d.get("archive") else "invalid_evidence"),reason,train,valid
        return "screen_rejected",reason,train,valid
    if not has_record:
        return "awaiting_archive","full_original_evidence_required",train,valid
    return "queued",None,train,valid


def verify_record(row):
    d,v,train,valid=metrics_pair(row)
    spec=json.loads(row["spec"])
    if row["version"] not in ENGINES or research.digest([row["version"],spec])!=row["id"]:
        raise ValueError("native identity mismatch")
    original=json.loads(row["policy"]);expected=POLICIES[row["version"]]
    # Generator scheduling metadata does not change the executable hypothesis.
    # Trading, risk, costs, clock and frame semantics must still match.
    execution_keys=("version","execution","cost_multiplier","short_borrow_apr_assumption",
        "borrow_apr_assumption","max_gross","intrabar_ambiguity","trailing","risk_atr","path",
        "weekly","event_clock","event_cutoff","matched_unfiltered_control")
    if any(original.get(k)!=expected[k] for k in execution_keys if k in expected):
        raise ValueError("native frozen execution policy mismatch")
    if spec.get("frame") not in ("1d","1w") or spec.get("direction") not in coverage.DIRECTIONS:
        raise ValueError("unsupported native setup")
    # Foundry-family paths were originally saved by their own executor.
    path=d.get("return_evidence")
    if row["version"]!=coverage.VERSION:
        if not path or path.get("sha256")!=research.digest({k:v for k,v in path.items() if k!="sha256"}):
            raise ValueError("missing or altered native return-path evidence")
        check_path(path["discovery"],train)
        check_path(path["validation"],valid)
    return spec


def check_path(path,metric):
    if not path or any(not math.isfinite(x) or x< -1 for x in path):
        raise ValueError("invalid compounded return evidence")
    if not math.isclose(math.prod(1+x for x in path)-1,metric["net"],rel_tol=1e-8,abs_tol=1e-8):
        raise ValueError("return arithmetic mismatch")


def harvest(db):
    """Decide pre-screen for every recorded dual-positive result, including compacted trials."""
    added=0
    sql="""SELECT * FROM attempts a WHERE version IN (?,?,?,?)
        AND COALESCE(json_extract(discovered,'$.metrics.net'),json_extract(discovered,'$.net'),
                     json_extract(discovered,'$.discovery_metrics.net'))>0
        AND COALESCE(json_extract(validated,'$.metrics.net'),json_extract(validated,'$.net'),
                     json_extract(discovered,'$.validation_metrics.net'))>0
        AND NOT EXISTS (SELECT 1 FROM native_paper_candidates c WHERE c.asset=a.asset AND c.version=a.version AND c.id=a.id)
        ORDER BY a.rowid"""
    for row in db.execute(sql,ENGINES):
        status,reason,train,valid=record_status(row)
        d=json.loads(row["discovered"])
        original=None
        if status=="queued":
            try:
                verify_record(row)
                original=canonical(dict(row))
            except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:
                status,reason="invalid_evidence",str(exc)[:200]
        db.execute("""INSERT OR IGNORE INTO native_paper_candidates
            (version,asset,id,source,family,asof,archive,record,initial,status,reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (row["version"],row["asset"],row["id"],row["source"],row["family"],row["asof"],
             d.get("archive"),original,canonical({"discovery":train,"validation":valid}),status,reason))
        added+=1
    return added


def migrate_policy(db,now):
    """Reopen v1 percent-risk decisions once, retain old decision and forward evidence."""
    rows=list(db.execute("""SELECT c.* FROM native_paper_candidates c
        WHERE (c.status IN ('qualified_waiting_capacity','conditional_active','conditional_paused',
                            'conditional_completed_positive','conditional_completed_failed')
               OR (c.status IN ('screen_rejected','recheck_rejected') AND
                   c.reason IN ('historical_drawdown_limit','chronological_fold_risk')))
          AND NOT EXISTS (SELECT 1 FROM native_paper_policy_migration m
             WHERE m.version=c.version AND m.asset=c.asset AND m.id=c.id AND m.policy=?)
          AND (c.qualification IS NULL OR coalesce(json_extract(c.qualification,'$.risk_policy'),'')<>?)""",
          (VERSION,recovery_risk.VERSION)))
    for row in rows:
        db.execute("INSERT INTO native_paper_policy_migration VALUES (?,?,?,?,?,?)",
                   (row["version"],row["asset"],row["id"],VERSION,canonical(dict(row)),now.isoformat()))
        if row["status"].startswith("conditional_"):
            # A genuine observation is never overwritten; zero-bar experiments may
            # be freshly registered after requalification. Old mature results stay sealed.
            f=json.loads(row["forward"] or "{}")
            if f.get("observed_bars",0)>0 or row["status"].startswith("conditional_completed"):
                continue
        original=json.loads(row["record"]) if row["record"] else dict(db.execute(
            "SELECT * FROM attempts WHERE version=? AND asset=? AND id=?",
            (row["version"],row["asset"],row["id"])).fetchone())
        status,reason,train,valid=record_status(original)
        details=None
        if status=="queued":
            try:
                verify_record(original);details=canonical(original)
            except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:
                status,reason="invalid_evidence",str(exc)[:220]
        db.execute("""UPDATE native_paper_candidates SET status=?,reason=?,checked=NULL,
            record=?,archive=coalesce(?,archive),initial=?,qualification=NULL,forward=NULL
            WHERE version=? AND asset=? AND id=?""",
            (status,reason,details,json.loads(original["discovered"]).get("archive"),
             canonical({"discovery":train,"validation":valid}),row["version"],row["asset"],row["id"]))
    return len(rows)

def restore_archive(db,path,sha,expected,now):
    if not 0<expected<=30000 or path.stat().st_size>32*1024*1024:
        raise ValueError("archive outside size/count bounds")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        raise ValueError("native archive checksum mismatch")
    seen=set();size=restored=0
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        while line:=stream.readline(MAX_ARCHIVE_LINE+1):
            row_bytes=len(line.encode())
            if row_bytes>MAX_ARCHIVE_LINE:
                raise ValueError("native archive record exceeds line bound")
            size+=row_bytes
            if size>MAX_ARCHIVE_EXPANDED:
                raise ValueError("native archive exceeds checkpoint expansion bound")
            item=json.loads(line);row=item["row"];kind=item["type"]
            key=(kind,row["version"],row["asset"],row["id"])
            if kind not in ("technical","event") or key in seen or len(seen)>=expected:
                raise ValueError("archive count or identity invalid")
            seen.add(key)
            if kind!="technical" or row["version"] not in ENGINES:
                continue
            prior=db.execute("""SELECT * FROM native_paper_candidates WHERE version=? AND asset=? AND id=?
                    AND status='awaiting_archive' AND archive=?""",(*key[1:],sha)).fetchone()
            if prior is None:
                continue
            ledger=db.execute("SELECT * FROM attempts WHERE version=? AND asset=? AND id=?",key[1:]).fetchone()
            if not ledger or json.loads(ledger["discovered"]).get("archive")!=sha or any(
                row[k]!=ledger[k] for k in ("source","snapshot","asof","code")):
                raise ValueError("archive does not match permanent attempt evidence")
            status,reason,train,valid=record_status(row)
            if status=="queued":
                try:
                    verify_record(row)
                except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:
                    status,reason="invalid_evidence",str(exc)[:200]
            original=canonical(row) if status=="queued" else None
            db.execute("""UPDATE native_paper_candidates SET record=?,initial=?,status=?,reason=?
                WHERE version=? AND asset=? AND id=?""",
                (original,canonical({"discovery":train,"validation":valid}),status,reason,*key[1:]))
            restored+=1
    if len(seen)!=expected:
        raise ValueError("archive record count mismatch")
    # No missing raw row can be silently called recovered.
    if db.execute("SELECT 1 FROM native_paper_candidates WHERE archive=? AND status='awaiting_archive'",(sha,)).fetchone():
        raise ValueError("archive lacks expected native candidate")
    db.execute("INSERT OR IGNORE INTO native_paper_archives VALUES (?,?)",(sha,now.isoformat()))
    return restored


def recover_archives(db,now,seconds=ARCHIVE_SECONDS):
    """Fetch bounded independent archive groups; do not duplicate archived trials."""
    repo=os.environ["GITHUB_REPOSITORY"]
    rows=[dict(r) for r in db.execute("""SELECT h.sha,h.url,h.records,count(*) AS pending
        FROM native_paper_candidates c JOIN history_archives h ON h.sha=c.archive
        LEFT JOIN native_paper_archive_attempts a ON a.sha=h.sha
        WHERE c.status='awaiting_archive'
          AND (a.checked IS NULL OR substr(a.checked,1,10)<?
               OR a.status='recovered' OR (a.status='pending' AND a.error='native archive expanded size cap'))
        GROUP BY h.sha,h.url,h.records ORDER BY min(coalesce(a.checked,'')),pending DESC,h.sha""",
        (now.date().isoformat(),))]
    if not rows:
        return []
    from .delivery import gh
    def download(row,root):
        sha=row["sha"];name=f"research-{sha}.jsonl.gz"
        match=re.fullmatch(r"https://github\.com/"+re.escape(repo)+r"/releases/download/(research-history-[0-9]{4}-[0-9]{2})/"+re.escape(name),row["url"])
        if not match or not re.fullmatch(r"[a-f0-9]{64}",sha):
            raise ValueError("unsupported native archive URL")
        target=Path(root)/sha;target.mkdir()
        gh("release","download",match[1],"-R",repo,"--pattern",name,"--dir",str(target))
        return target/name
    results=[]
    deadline=time.monotonic()+seconds
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=ARCHIVE_WORKERS) as pool:
        for offset in range(0,len(rows),ARCHIVE_WORKERS):
            if time.monotonic()>=deadline:
                break
            batch=rows[offset:offset+ARCHIVE_WORKERS]
            futures=[pool.submit(download,row,tmp) for row in batch]
            for row,future in zip(batch,futures,strict=True):
                db.execute("SAVEPOINT native_archive")
                try:
                    n=restore_archive(db,future.result(),row["sha"],row["records"],now)
                    results.append({"sha":row["sha"],"status":"recovered","added":n})
                except Exception as exc:
                    db.execute("ROLLBACK TO native_archive")
                    results.append({"sha":row["sha"],"status":"pending","reason":str(exc)[:180]})
                finally:
                    db.execute("RELEASE native_archive")
                last=results[-1]
                db.execute("INSERT OR REPLACE INTO native_paper_archive_attempts VALUES (?,?,?,?)",
                           (row["sha"],now.isoformat(),last["status"],last.get("reason")))
    return results


class Waiting(ValueError):
    def __init__(self,status,reason):
        super().__init__(reason)
        self.status=status


def macro_names(version,spec):
    if version==coverage.VERSION:
        name=spec["event"][0] if spec.get("event") else None
        return (name,) if name in events.FAMILIES else ()
    if version==combo.VERSION:
        return tuple(sorted({e["name"] for e in spec["events"] if e["name"] in events.FAMILIES}))
    return ()


def complete_event_hash(names,dates,through):
    return research.digest({n:sorted(set(d for d in dates[n] if str(d)[:10]<=through)) for n in names})


def prepare(version,spec,bars,market,dates,cutoffs):
    current=bars if spec["frame"]=="1d" else coverage.weekly(bars)
    names=macro_names(version,spec)
    if any(not dates.get(n) or not cutoffs.get(n) for n in names):
        raise Waiting("waiting_calendar","publication calendar unavailable")
    if names:
        current=current.loc[:min(cutoffs[n] for n in names)]
    if len(current)<120:
        raise Waiting("waiting_history","fewer than 120 eligible bars")
    if version==coverage.VERSION:
        warm=coverage.warmup_for(spec)
        target=coverage.desired(current,spec,dates,365 if market.calendar is None else 252)
        valid=pd.Series(True,index=current.index)
        for name,n,_,_ in spec["rules"]:
            if name in coverage.NORMALIZED:
                valid &= coverage.measurement(current,name,n).notna()
            elif name=="vwma":
                valid &= current.volume.rolling(n).sum()>0
    elif version==foundry.VERSION:
        warm=foundry.warmup(spec)
        target=foundry.desired(current,spec)
        valid=pd.concat([foundry.signal(current,r)[1] for r in spec["rules"]],axis=1).all(axis=1)
    elif version==indicator.VERSION:
        warm=indicator.warmup(spec)
        target,valid=indicator.targets(current,spec)
    elif version==combo.VERSION:
        event_warm=[2*e.get("window",1)+e["lookback"]+3 for e in spec["events"] if e["name"] in combo.MARKET_EVENTS]
        warm=max([spec["risk"]["atr_window"]+3]+[combo.rule_warmup(r) for r in spec["rules"]]+event_warm)+spec["confirmation"]+(len(spec["rules"]) if spec["logic"]=="sequence" else 1)
        target,valid=combo.technical_target(current,spec)
        raw,event_valid=combo.event_gate(current,spec,dates)
        valid &= event_valid
        target=target.where(valid,0.)
        if spec["events"]:
            target=target.where(raw if spec["event_mode"]=="only" else ~raw,0.)
    else:
        raise ValueError("unknown native engine")
    if len(current)<warm+120:
        raise Waiting("waiting_history","fewer than 120 eligible bars after native warmup")
    if not target.index.equals(current.index) or not np.isfinite(target.to_numpy()).all() or target.abs().max()>1+1e-12:
        raise ValueError("invalid native target exposure")
    return current,target,valid,warm,names


def coverage_reference(bars,weights,start,stop,cost_bps):
    """Scalar, independently implemented open-to-open replay of the coverage model."""
    equity=peak=1.;drawdown=0.;marked=0.;previous=0.;n=0;fee=2*cost_bps/10000
    path=[]
    values=weights.to_numpy(dtype=float);opens=bars.open.to_numpy(dtype=float)
    highs=bars.high.to_numpy(dtype=float);lows=bars.low.to_numpy(dtype=float)
    elapsed=np.diff(bars.index.to_numpy())/np.timedelta64(1,"D")
    for i in range(start,stop-1):
        w=float(values[i-1]) if i else 0.
        op=float(opens[i])
        change=float(opens[i+1])/op-1
        days=float(elapsed[i])
        gross=1+w*change-max(-w,0)*.05*days/365
        if w and (not previous or math.copysign(1,w)!=math.copysign(1,previous)):
            n+=1
        previous=w
        if gross<=0 or 1+w*(float(highs[i])/op-1)<=0:
            return {"net":-1.,"drawdown":1.,"entries":n,"status":"ruin_stress"}
        charge=1-fee*abs(w-marked)
        adverse=(lows[i] if w>=0 else highs[i])/op-1
        stressed=charge*(1+w*adverse-max(-w,0)*.05*days/365)
        if stressed<=0:
            return {"net":-1.,"drawdown":1.,"entries":n,"status":"ruin_stress","ruin":True,"stress":"fee_borrow_adverse"}
        factor=charge*gross
        marked=w*(1+change)/gross
        if i==stop-2:
            factor*=1-fee*abs(marked)
        if factor<=0:
            return {"net":-1.,"drawdown":1.,"entries":n,"status":"ruin_stress"}
        path.append(factor-1.)
        equity*=factor;peak=max(peak,equity);drawdown=max(drawdown,1-equity/peak)
    result={"net":equity-1,"drawdown":drawdown,"entries":n,"status":"evaluated" if n else "no_entries"}
    result.update(recovery_risk.measure(path,bars.index[start+1:stop],bars.index[start]))
    return result


def fold(version,current,target,start,stop,spec,cost):
    if stop-start<2:
        raise Waiting("waiting_history","insufficient executable fold")
    if version==coverage.VERSION:
        metrics=coverage.evaluate_fold(current,target,start,stop,cost)
        ref=coverage_reference(current,target,start,stop,cost)
        if ref.get("stress")=="fee_borrow_adverse":
            return ref
        # A ruin result is disqualified; exact trade count can differ because
        # the vectorized engine counts all precomputed entries before ruin.
        if metrics["status"]!="ruin_stress" and (
            ref["status"]!=metrics["status"] or ref["entries"]!=metrics["entries"] or any(
                not math.isclose(ref[k],metrics[k],abs_tol=1e-9,rel_tol=1e-9) for k in ("net","drawdown"))):
            raise ValueError("coverage independent replay mismatch")
        if metrics["status"]=="ruin_stress":
            return {**metrics,"ruin":True}
        # The independent replay computes recovery on the executable next-open path.
        return {**metrics,**{k:v for k,v in ref.items() if k not in metrics}}
    metrics,path=foundry.execute(current,target,start,stop,spec["risk"],cost,return_full_path=True)
    check_path(path,metrics)
    return {**metrics,**recovery_risk.measure(path,current.index[start:start+len(path)],current.index[start])}


def evaluate(row,bars,market,dates,cutoffs):
    original=json.loads(row["record"])
    spec=verify_record(original)
    version=row["version"]
    current,target,valid,warm,names=prepare(version,spec,bars,market,dates,cutoffs)
    if str(current.index[-1].date())<row["asof"]:
        raise Waiting("waiting_history","current source no longer covers original evaluation cutoff")
    boundary=warm+(len(current)-warm)*3//4
    if any(valid.iloc[a:b].mean()<.8 for a,b in ((warm,boundary),(boundary,len(current)))):
        raise Waiting("waiting_history","insufficient defined signals")
    # Preserve event occurrence rules from the original research lane.
    if version==combo.VERSION and spec["events"]:
        raw,ok=combo.event_gate(current,spec,dates)
        starts=(raw&~raw.shift(fill_value=False)).shift(1,fill_value=False)
        absent=((~raw)&ok).shift(1,fill_value=False)
        if any(starts.iloc[a:b-1].sum()<combo.MIN_EVENTS or absent.iloc[a:b-1].sum()<combo.MIN_EVENTS
               for a,b in ((warm,boundary),(boundary,len(current)))):
            raise Waiting("waiting_calendar","insufficient independent event or control episodes")
    if version==coverage.VERSION and names:
        mask=coverage.event_mask(current.index,names[0],dates)
        if not mask.iloc[warm:boundary].any() or not mask.iloc[boundary:].any():
            raise Waiting("waiting_calendar","insufficient original macro occurrences")
    train=fold(version,current,target,warm,boundary,spec,market.cost_bps)
    validm=fold(version,current,target,boundary,len(current),spec,market.cost_bps)
    reason=initial_gate(train,validm)
    if not reason:
        reason=recovery_risk.reason(train) or recovery_risk.reason(validm)
    # Three disjoint chronological reset folds, beginning after the native warmup.
    edges=np.linspace(warm,len(current),4,dtype=int)
    folds=[]
    continuous=None
    if not reason:
        folds=[fold(version,current,target,int(a),int(b),spec,market.cost_bps) for a,b in zip(edges[:-1],edges[1:])]
        if any(f["status"] in ("ruin","ruin_stress") or f.get("ruin") for f in folds):
            reason="capital_ruin"
        elif sum(f["net"]>0 for f in folds)<2 or math.prod(1+f["net"] for f in folds)<=1 or sum(entries(f) or 0 for f in folds)<MIN_ENTRIES:
            reason="chronological_fold_robustness"
    if not reason:
        # Continuous replay prevents an artificial fold boundary hiding a long loss.
        continuous=fold(version,current,target,warm,len(current),spec,market.cost_bps)
        reason=recovery_risk.reason(continuous)
    receipt=json.loads(original["discovered"]).get("superlab_receipt")
    if os.getenv("GITHUB_ACTIONS")=="true" and not receipt:
        try:
            from research.portfolio_qualification.eaverse_coverage import audit_record
            receipt=audit_record(original,"technical")
        except ImportError:
            raise Waiting("waiting_source","central research contract unavailable")
    if (receipt or {}).get("status")=="INVALID_EVIDENCE":
        raise ValueError("central research receipt invalid")
    q={"discovery":train,"validation":validm,"folds":folds,"continuous":continuous,"reason":reason,
       "risk_policy":recovery_risk.VERSION,
       "current_snapshot":data.fingerprint(current),"from":str(current.index[0].date()),
       "asof":str(current.index[-1].date()),"bars":len(current),
       "arithmetic_audit":"independent_open_replay" if version==coverage.VERSION else "native_return_path_compounding",
       "superlab_receipt":receipt,"retrospective_only":True,"live_allowed":False}
    return spec,current,q,names


@lru_cache(maxsize=1)
def adapter_hash():
    # A different signal/price/execution implementation cannot extend a frozen experiment.
    h=hashlib.sha256()
    for module in (coverage,foundry,combo,indicator,indicator_bank,research,surface,data,events,recovery_risk):
        h.update(Path(module.__file__).read_bytes())
    for fn in (prepare,fold,coverage_reference,forward_window,complete_event_hash,advance):
        h.update(inspect.getsource(fn).encode())
    h.update(canonical({"version":VERSION,"forward_bars":FORWARD_BARS,"risk":recovery_risk.VERSION,"max_recovery_days":recovery_risk.MAX_RECOVERY_DAYS,"entries":MIN_ENTRIES}).encode())
    return h.hexdigest()


def forward_window(current,frame,registered):
    if frame=="1d":
        return np.flatnonzero(current.index>pd.Timestamp(registered))
    # Weekly opens represent the first session of a whole calendar week.
    # Never count a week that began before or on the registration date.
    return np.flatnonzero(current.index.to_period("W-SUN").start_time>pd.Timestamp(registered))


def update(db,row,status,reason,now,qual=None,forward=None):
    db.execute("""UPDATE native_paper_candidates SET status=?,reason=?,checked=?,
         qualification=coalesce(?,qualification),forward=coalesce(?,forward)
         WHERE version=? AND asset=? AND id=?""",
        (status,reason,now.isoformat(),canonical(qual) if qual is not None else None,
         canonical(forward) if forward is not None else None,row["version"],row["asset"],row["id"]))


def admit(db,row,bars,market,dates,cutoffs,now):
    if row["source"]!=market.source:
        raise Waiting("waiting_source","exact original venue/provider unavailable")
    spec,current,q,names=evaluate(row,bars,market,dates,cutoffs)
    if q["reason"]:
        update(db,row,"recheck_rejected",q["reason"],now,q)
        return "recheck_rejected"
    through=str(current.index[-1].date())
    state={"version":VERSION,"adapter_sha256":adapter_hash(),"registered_utc":now.isoformat(),
           "registered_date":now.date().isoformat(),"frame":spec["frame"],"direction":spec["direction"],
           "data_start":str(bars.index[0].date()),"data_through":str(bars.index[-1].date()),
           "data_sha256":data.fingerprint(bars),"event_through":through,
           "event_sha256":complete_event_hash(names,dates,through),"source":market.source,
           "cost_bps":market.cost_bps,"observed_bars":0,"target_bars":FORWARD_BARS,
           "short_borrow_assumed":spec["direction"]!="long","conditional_only":True,"risk_policy":recovery_risk.VERSION,
           "paper_approved":False,"live_allowed":False}
    update(db,row,ACTIVE,None,now,q,state)
    return ACTIVE


def advance(db,row,bars,market,dates,cutoffs,now):
    state=json.loads(row["forward"]);original=json.loads(row["record"])
    spec=verify_record(original);version=row["version"]
    if state["adapter_sha256"] not in (adapter_hash(),LEGACY_ADAPTER_HASH) or state["source"]!=market.source or state["cost_bps"]!=market.cost_bps:
        raise ValueError("frozen native executor/source/cost changed")
    base=bars.loc[state["data_start"]:]
    previous=base.loc[:state["data_through"]]
    if previous.empty or data.fingerprint(previous)!=state["data_sha256"]:
        raise ValueError("frozen native PAPER price prefix revised or absent")
    names=macro_names(version,spec)
    if any(not dates.get(n) or not cutoffs.get(n) for n in names):
        raise Waiting("waiting_calendar","native observation calendar unavailable")
    if complete_event_hash(names,dates,state["event_through"])!=state["event_sha256"]:
        raise ValueError("frozen native PAPER publication history revised")
    current,target,valid,warm,names=prepare(version,spec,base,market,dates,cutoffs)
    if str(current.index[-1].date())<state["event_through"]:
        raise Waiting("waiting_calendar","calendar/data cutoff moved backward")
    future=forward_window(current,spec["frame"],state["registered_date"])
    # Coverage measures open-to-open returns, requiring a further observation.
    completed=max(0,len(future)-(1 if version==coverage.VERSION else 0))
    observed=min(FORWARD_BARS,completed)
    if observed<state["observed_bars"]:
        raise ValueError("native PAPER observations moved backward")
    if observed and valid.iloc[future[:observed]].mean()<.8:
        raise Waiting("waiting_history","insufficient defined indicators in forward observations")
    state.update(data_through=str(base.index[-1].date()),data_sha256=data.fingerprint(base),
                 event_through=str(current.index[-1].date()),
                 event_sha256=complete_event_hash(names,dates,str(current.index[-1].date())),
                 observed_bars=observed,last_check=now.isoformat())
    status=ACTIVE
    if observed>=2:
        first=int(future[0])
        stop=first+observed+(1 if version==coverage.VERSION else 0)
        metrics=fold(version,current,target,first,stop,spec,market.cost_bps)
        state.update(metrics=metrics,first_observed=str(current.index[first].date()),
                     observed_through=str(current.index[stop-1].date()),
                     provisional=observed<FORWARD_BARS)
        risk_reason=recovery_risk.reason(metrics)
        if risk_reason in ("capital_ruin","recovery_time_exceeded") or observed==FORWARD_BARS:
            passed=observed==FORWARD_BARS and metrics["status"]=="evaluated" and metrics["net"]>0 and risk_reason is None and (entries(metrics) or 0)>=MIN_ENTRIES
            status="conditional_completed_positive" if passed else "conditional_completed_failed"
            state["conditional_gate_pass"]=passed
            state["forward_validation_complete"]=True
            state["risk_decision"]=risk_reason
    state["adapter_sha256"]=adapter_hash()
    state["risk_policy"]=recovery_risk.VERSION
    update(db,row,status,state.get("risk_decision"),now,forward=state)
    return status


def priority_assets(db,market,today):
    """One data fetch per asset, not one per strategy. New and due work is fair."""
    active=[r[0].split(":",1)[1] for r in db.execute("""SELECT asset FROM native_paper_candidates
       WHERE status=? AND asset LIKE ? AND (checked IS NULL OR substr(checked,1,10)<?)
       GROUP BY asset ORDER BY min(coalesce(checked,'')),asset""",(ACTIVE,market+":%",today.isoformat()))]
    candidates=[r[0].split(":",1)[1] for r in db.execute("""SELECT asset FROM native_paper_candidates
       WHERE status IN ('queued','waiting_source','waiting_calendar','waiting_history','qualified_waiting_capacity','policy_recheck')
         AND asset LIKE ? AND (checked IS NULL OR substr(checked,1,10)<?)
       GROUP BY asset ORDER BY min(coalesce(checked,'')),min(asof),asset""",
       (market+":%",today.isoformat()))]
    return active,candidates


def run(db,aligned_panel,aligned_markets,dates,cutoffs,now,seconds=BACKLOG_SECONDS):
    """Unlimited admissions, bounded wall-clock work with durable fair continuation."""
    deadline=time.monotonic()+seconds
    counts={};checked=0
    active=list(db.execute("SELECT * FROM native_paper_candidates WHERE status=? ORDER BY checked,asset,id",(ACTIVE,)))
    # Reserve half the lane for unscreened work so a large observation registry
    # cannot prevent additional admissions. Remaining time is shared below.
    active_deadline=time.monotonic()+seconds/2
    for row in active:
        if time.monotonic()>=active_deadline:
            break
        asset=row["asset"]
        if asset not in aligned_panel:
            continue
        if row["checked"] and row["checked"][:10]>=now.date().isoformat() and not row["reason"]:
            continue
        db.execute("SAVEPOINT native_forward")
        try:
            status=advance(db,row,aligned_panel[asset],aligned_markets[asset],dates,cutoffs,now)
        except Waiting as exc:
            db.execute("ROLLBACK TO native_forward")
            update(db,row,ACTIVE,str(exc),now)
            status="forward_waiting"
        except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:
            db.execute("ROLLBACK TO native_forward")
            reason=str(exc)[:220]
            state=json.loads(row["forward"] or "{}")
            if state.get("observed_bars",0)==0 and "frozen native PAPER price prefix" in reason:
                # A zero-observation series can be restarted with fresh data, but
                # its prior registration is retained and never backdated.
                db.execute("INSERT OR IGNORE INTO native_paper_restarts VALUES (?,?,?,?,?,?)",
                    (row["version"],row["asset"],row["id"],now.isoformat(),canonical(dict(row)),reason))
                try:
                    status=admit(db,row,aligned_panel[asset],aligned_markets[asset],dates,cutoffs,now)
                except Waiting as exc2:
                    update(db,row,exc2.status,str(exc2),now)
                    status=exc2.status
                except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc2:
                    update(db,row,"invalid_evidence",str(exc2)[:220],now)
                    status="invalid_evidence"
            elif row["reason"]==reason:
                update(db,row,"conditional_paused",reason,now)
                status="conditional_paused"
            else:
                update(db,row,ACTIVE,reason,now)
                status="forward_integrity_waiting"
        finally:
            db.execute("RELEASE native_forward")
        counts[status]=counts.get(status,0)+1
    candidates={}
    for row in db.execute("""SELECT * FROM native_paper_candidates
        WHERE status IN ('queued','waiting_source','waiting_calendar','waiting_history','qualified_waiting_capacity','policy_recheck')
          AND (checked IS NULL OR substr(checked,1,10)<? OR status='qualified_waiting_capacity')
        ORDER BY coalesce(checked,''),asof,version,id""",(now.date().isoformat(),)):
        if row["asset"] in aligned_panel:
            candidates.setdefault(row["asset"],[]).append(row)
    while candidates and time.monotonic()<deadline:
        for asset,rows in list(candidates.items()):
            if time.monotonic()>=deadline:
                break
            row=rows.pop(0)
            if not rows:
                del candidates[asset]
            db.execute("SAVEPOINT native_candidate")
            try:
                status=admit(db,row,aligned_panel[asset],aligned_markets[asset],dates,cutoffs,now)
            except Waiting as exc:
                db.execute("ROLLBACK TO native_candidate")
                status=exc.status
                update(db,row,status,str(exc),now)
            except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:
                db.execute("ROLLBACK TO native_candidate")
                status="invalid_evidence"
                update(db,row,status,str(exc)[:220],now)
            finally:
                db.execute("RELEASE native_candidate")
            counts[status]=counts.get(status,0)+1
            checked+=1
    return {"outcomes":counts,"checked_this_run":checked,"runtime_bound_seconds":seconds}

def observe_unavailable(db,asset,error,now):
    db.execute("""UPDATE native_paper_candidates SET reason=?,checked=?
        WHERE asset=? AND status=?""",(error[:220],now.isoformat(),asset,ACTIVE))
    db.execute("""UPDATE native_paper_candidates SET status='waiting_source',reason=?,checked=?
        WHERE asset=? AND status IN ('queued','waiting_source','waiting_history','waiting_calendar')""",
        (error[:220],now.isoformat(),asset))


def summary(db):
    counts={r[0]:r[1] for r in db.execute("SELECT status,count(*) FROM native_paper_candidates GROUP BY status")}
    reasons=[dict(r) for r in db.execute("""SELECT status,reason,count(*) AS count FROM native_paper_candidates
        WHERE reason IS NOT NULL GROUP BY status,reason ORDER BY count DESC LIMIT 80""")]
    by_engine=[dict(r) for r in db.execute("""SELECT version AS engine,status,count(*) AS count
        FROM native_paper_candidates GROUP BY version,status ORDER BY version,status""")]
    rows=[]
    for r in db.execute("""SELECT * FROM native_paper_candidates
        WHERE status LIKE 'conditional_%' OR status='qualified_waiting_capacity'
        ORDER BY CASE status WHEN 'conditional_active' THEN 0 ELSE 1 END,checked DESC,asset,id"""):
        q=json.loads(r["qualification"] or "{}");f=json.loads(r["forward"] or "{}")
        spec=json.loads(json.loads(r["record"])["spec"]) if r["record"] else {}
        rows.append({"asset":r["asset"],"engine":r["version"],"id":r["id"],"frame":spec.get("frame"),
             "direction":spec.get("direction"),"status":r["status"],"reason":r["reason"],
             "historical_net":q.get("validation",{}).get("net"),"historical_drawdown":q.get("validation",{}).get("drawdown"),
             "historical_recovery_days":(q.get("continuous") or {}).get("max_recovery_days"),
             "forward_recovery_days":f.get("metrics",{}).get("max_recovery_days"),
             "registered":f.get("registered_date"),"observed_bars":f.get("observed_bars"),
             "target_bars":f.get("target_bars"),"forward_net":f.get("metrics",{}).get("net"),
             "forward_drawdown":f.get("metrics",{}).get("drawdown"),"provisional":f.get("provisional"),
             "conditional_only":True})
    decisions=[]
    for r in db.execute("""SELECT version,asset,id,asof,status,reason,initial FROM native_paper_candidates
        ORDER BY CASE status WHEN 'conditional_active' THEN 0 WHEN 'qualified_waiting_capacity' THEN 1
        WHEN 'queued' THEN 2 ELSE 3 END,asset,version,id LIMIT 5000"""):
        evidence=json.loads(r["initial"])
        decisions.append({"engine":r["version"],"asset":r["asset"],"id":r["id"],"asof":r["asof"],
                          "status":r["status"],"reason":r["reason"],"discovery_net":evidence["discovery"].get("net"),
                          "validation_net":evidence["validation"].get("net"),
                          "validation_drawdown":evidence["validation"].get("drawdown")})
    return {"version":VERSION,"total":sum(counts.values()),"counts":counts,"by_engine":by_engine,
            "reasons":reasons,"records":rows,"decisions":decisions,
            "policy_migrations":db.execute("SELECT count(*) FROM native_paper_policy_migration").fetchone()[0],
            "zero_bar_restarts":db.execute("SELECT count(*) FROM native_paper_restarts").fetchone()[0],"capacity":None,"unlimited_conditional_capacity":True,"risk_policy":recovery_risk.VERSION,
            "max_recovery_days":recovery_risk.MAX_RECOVERY_DAYS,"forward_target_bars":FORWARD_BARS,
            "live_allowed":False,"central_accept":False,"paper_approved":False}
