"""Aligned real-history cohorts and sealed prospective tracking, research only."""
import hashlib
import json
import os
import random
from datetime import UTC,datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import data, empirical_trials, foundry, research, surface

VERSION="foundry-cohort-v1"
VARIANTS=16
FORWARD_BARS=90
SCHEMA="""CREATE TABLE IF NOT EXISTS foundry_cohorts(
 id TEXT PRIMARY KEY, market TEXT NOT NULL, asset TEXT NOT NULL,
 evidence TEXT NOT NULL, forward TEXT NOT NULL, status TEXT NOT NULL)"""
POLICY={"version":VERSION,"variants":VARIANTS,"forward_bars":FORWARD_BARS,
        "selection":"discovery_weekly_sharpe_only","calendar":"full interior calendar weeks",
        "execution":foundry.POLICY,"retrospective_not_untouched_oos":True,
        "paper_eligible":False,"live_allowed":False}

def code_hash():
    return hashlib.sha256(b"".join(Path(m.__file__).read_bytes() for m in (foundry,surface))).hexdigest()

def week_groups(index):
    return index.to_period("W-SUN")

def period_path(bars,target,start,stop,risk,cost):
    metrics,values=foundry.execute(bars,target,start,stop,risk,cost)
    dates=week_groups(bars.index[start:stop]).unique().astype(str).tolist()
    # Absorbing zero capital after actual ruin: no fabricated surviving returns.
    if len(values)<len(dates):
        if metrics["status"]!="ruin": raise ValueError("TRUNCATED_NONRUIN_PATH")
        values=values+[0.]*(len(dates)-len(values))
    if len(values)!=len(dates): raise ValueError("PATH_CALENDAR_MISMATCH")
    return dates,values,metrics

def study(bars,asset,cost,trial_count,cohort_number=0):
    specs=[]
    for i in range(VARIANTS):
        spec=foundry.plan(asset+":"+str(cohort_number),"1d",foundry.LANES[i%3],i+cohort_number*VARIANTS)
        # Use the registered generator; no performance-based trial elimination.
        specs.append(spec)
    ids=[research.digest([VERSION,s]) for s in specs]
    if len(set(ids))!=VARIANTS: raise ValueError("DUPLICATE_COHORT_SPEC")
    warm=max(foundry.warmup(s) for s in specs)
    periods=week_groups(bars.index)
    starts=np.flatnonzero(np.r_[True,periods[1:]!=periods[:-1]])
    usable=[int(x) for x in starts[1:] if x>=warm]
    if len(usable)<82:
        return None
    start,end=usable[0],usable[-1]
    boundary=usable[int((len(usable)-1)*.70)]
    matrix=[]
    for s in specs:
        t=foundry.desired(bars,s)
        train_dates,train,_=period_path(bars,t,start,boundary,s["risk"],cost)
        valid_dates,valid,_=period_path(bars,t,boundary,end,s["risk"],cost)
        matrix.append(train+valid)
    values=np.asarray(matrix,dtype=float).T
    dates=train_dates+valid_dates
    tr=len(train_dates)
    if len(set(dates))!=len(dates) or len(valid_dates)<20: return None
    selected=int(np.argmax(empirical_trials._sharpe(values[:tr])))
    trials=max(VARIANTS,trial_count+VARIANTS)
    pbo=empirical_trials.cscv_pbo(values)
    dsr=empirical_trials.deflated_sharpe_validation(values[tr:,selected],values[:tr],trial_count=trials)
    panel={"dates":dates,"ids":ids,"returns":values.tolist(),"train_weeks":tr}
    panel["sha256"]=research.digest(panel)
    return {"schema":VERSION,"asset":asset,"panel":panel,"specs":specs,
            "selected_id":ids[selected],"selected_spec":specs[selected],
            "trial_count":trials,"pbo":pbo.get("pbo"),"dsr":dsr.get("dsr"),
            "pbo_status":pbo["status"],"dsr_status":dsr["status"],
            "source_sha256":data.fingerprint(bars),"methodology_sha256":research.digest(POLICY),
            "paper_eligible":False,"live_allowed":False,"forward_validated":False}

def advance(db,row,bars,market,now):
    evidence=json.loads(row["evidence"])
    state=json.loads(row["forward"])
    if state["code"]!=code_hash():
        return {"status":"blocked_code_change"}
    anchor=pd.Timestamp(state["anchor"])
    current=bars.loc[bars.index>=anchor]
    observed=current.loc[current.index<=pd.Timestamp(state["observed_through"])]
    if not len(observed) or data.fingerprint(observed)!=state["observed_sha256"]:
        return {"status":"blocked_source_revision"}
    future=current.index[current.index>pd.Timestamp(state["registered_date"])]
    if len(future)>=FORWARD_BARS:
        stop=current.index.get_loc(future[FORWARD_BARS-1])+1
        start=current.index.get_loc(future[0])
        t=foundry.desired(current.iloc[:stop],evidence["selected_spec"])
        dates,values,metrics=period_path(current.iloc[:stop],t,start,stop,evidence["selected_spec"]["risk"],market.cost_bps)
        result={"dates":dates,"returns":values,"metrics":metrics,"source_sha256":data.fingerprint(current.iloc[:stop]),
                "bars":FORWARD_BARS,"paper_eligible":False,"live_allowed":False}
        result["sha256"]=research.digest(result)
        state["result"]=result
        status="forward_matured_research_only"
        current=current.iloc[:stop]
    else:
        status="collecting"
    state.update(observed_through=str(current.index[-1].date()),observed_sha256=data.fingerprint(current),
                 observed_new_bars=min(len(future),FORWARD_BARS))
    db.execute("UPDATE foundry_cohorts SET forward=?,status=? WHERE id=?",(research.canonical(state),status,row["id"]))
    return {"status":status,"observed_new_bars":state["observed_new_bars"],"id":row["id"]}

def run_asset(db,asset,market,bars,now):
    db.execute(SCHEMA)
    row=db.execute("SELECT * FROM foundry_cohorts WHERE market=? AND status='collecting' ORDER BY rowid LIMIT 1",(market.name,)).fetchone()
    if row:
        if row["asset"]!=asset: return {"status":"other_asset_collecting"}
        return advance(db,row,bars,market,now)
    # One active cohort per market; bounded historical panel storage.
    count=db.execute("SELECT count(*) FROM foundry_cohorts WHERE market=?",(market.name,)).fetchone()[0]
    if count>=24: return {"status":"cohort_retention_budget_reached"}
    trial_count=db.execute("SELECT count(*) FROM attempts").fetchone()[0]+count*VARIANTS
    evidence=study(bars,asset,market.cost_bps,trial_count,count)
    if evidence is None: return {"status":"blocked_aligned_history"}
    try:
        from research.portfolio_qualification.eaverse_report_gate import review_cohort
        evidence["superlab"]=review_cohort(evidence)
    except ImportError:
        evidence["superlab"]={"status":"contract_unavailable"}
    anchor=bars.iloc[-max(400,foundry.warmup(evidence["selected_spec"])+10):]
    state={"anchor":str(anchor.index[0].date()),"observed_through":str(anchor.index[-1].date()),
           "observed_sha256":data.fingerprint(anchor),"registered_utc":now.isoformat(),
           "registered_date":now.date().isoformat(),"code":code_hash(),"observed_new_bars":0}
    identity=research.digest([VERSION,market.name,asset,evidence["panel"]["sha256"],state["registered_utc"]])
    db.execute("INSERT INTO foundry_cohorts VALUES (?,?,?,?,?,?)",(identity,market.name,asset,research.canonical(evidence),research.canonical(state),"collecting"))
    return {"status":"registered","id":identity,"pbo":evidence["pbo"],"dsr":evidence["dsr"],"forward_bars":0}

def active_assets(db,market):
    db.execute(SCHEMA)
    return [row[0].removeprefix(market.name+":") for row in db.execute("SELECT asset FROM foundry_cohorts WHERE market=? AND status='collecting'",(market.name,))]
