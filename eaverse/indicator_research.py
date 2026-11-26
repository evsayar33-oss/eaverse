"""Independent bounded indicator research, preserving frozen Foundry/PAPER code."""
from __future__ import annotations

import json
import math
import os
import random
import time
import numpy as np
import pandas as pd

from . import coverage, data, foundry, indicator_bank as bank, research

VERSION = "coverage-indicators-v1"
FRAMES = ("1d", "1w")
MIN_EVALUATION_BARS = 120
MAX_PENDING_PER_FRAME = 12
POLICY = {**foundry.POLICY, "version": VERSION, "generator": "round_robin_single_and_mixed",
          "minimum_evaluation_bars": MIN_EVALUATION_BARS, "pending_cap": MAX_PENDING_PER_FRAME}
PENDING = """CREATE TABLE IF NOT EXISTS indicator_pending (
 asset TEXT NOT NULL, frame TEXT NOT NULL, version TEXT NOT NULL, position INTEGER NOT NULL,
 required_bars INTEGER NOT NULL, PRIMARY KEY(asset,frame,version,position))"""


def plan(asset, frame, position):
    """Every two positions include one single-family trial; mixtures never replace singles."""
    rng = random.Random(research.digest([VERSION,asset,frame,position]))
    cycle, mix = divmod(position,2)
    first_rotation = cycle < len(bank.FAMILIES)
    if not mix:
        names = [bank.FAMILIES[cycle % len(bank.FAMILIES)]]
    elif cycle % len(bank.FAMILIES) in (0,1,2):
        names = list(bank.REQUESTED)  # Explicit all/any/majority seven-way trials in every rotation.
    else:
        names = rng.sample(list(bank.FAMILIES), 2+(cycle-1)%6)
    requested_mix = mix and cycle % len(bank.FAMILIES) in (0,1,2)
    baseline = (first_rotation and not mix) or (requested_mix and first_rotation)
    rules = [bank.choose(n,rng,default=baseline,frame=frame) for n in names]
    risk = {"atr_window": 14 if baseline else rng.choice((5,7,10,14,21,28,40,60)),
            "stop": None, "tp": None, "trail": None, "partial":False, "staged":False, "max_hold":None}
    if not baseline and cycle%3 != 0:
        risk.update(stop=rng.choice((None,.75,1,1.5,2,2.5,3,4,5)),
                    tp=rng.choice((None,1,1.5,2,3,4,5,6,8)),
                    trail=rng.choice((None,1,1.5,2,2.5,3,4)),
                    partial=rng.choice((False,True)), staged=rng.choice((False,True)),
                    max_hold=rng.choice((None,3,5,10,20,30,40,60)))
    if risk["tp"] is None:
        risk["partial"]=False
    logic = ("all" if not mix else ("majority","all","any")[cycle % len(bank.FAMILIES)]
             if requested_mix else rng.choice(("all","any","majority","sequence","filter","weighted")))
    return {"frame":frame, "rules":rules, "logic":logic,
            "direction":"long" if baseline else rng.choice(coverage.DIRECTIONS),
            "confirmation":1 if baseline else rng.choice((1,2,3,4,5)),
            "size":1. if baseline else rng.choice((.25,.5,.75,1.)), "risk":risk}


def warmup(spec):
    return max(max(bank.warmup(r) for r in spec["rules"]),spec["risk"]["atr_window"]+3)+len(spec["rules"])


def targets(bars,spec):
    pairs=[bank.signal(bars,r) for r in spec["rules"]]
    flags=[flag for flag,_ in pairs]
    valid=pd.concat([ok for _,ok in pairs],axis=1).all(axis=1)
    votes=sum(f.astype(int) for f in flags)
    logic=spec["logic"]
    if logic=="all":
        flag=votes==len(flags)
    elif logic=="any":
        flag=votes>0
    elif logic=="majority":
        flag=votes>=len(flags)//2+1
    elif logic=="filter":
        flag=flags[0]&(votes>=2)
    elif logic=="weighted":
        flag=sum((i+1)*f.astype(int) for i,f in enumerate(flags))>=math.ceil(len(flags)*(len(flags)+1)/4)
    elif logic=="sequence":
        flag=flags[-1].copy()
        valid=pairs[-1][1].copy()
        for i,(f,ok) in enumerate(reversed(pairs[:-1]),1):
            flag &= f.shift(i,fill_value=False)
            valid &= ok.shift(i,fill_value=False)
    else:
        raise ValueError("unknown indicator grammar")
    target=flag.astype(float)
    if spec["direction"]=="short":
        target=-target
    elif spec["direction"]=="long_short":
        target=2*target-1
    target=target.where(valid,0)
    confirm=spec["confirmation"]
    if confirm>1:
        stable=target.eq(target.shift()).rolling(confirm-1).sum().eq(confirm-1)
        target=target.where(stable,0)
    return target*spec["size"],valid


def run_asset(db,asset,market,bars,deadline=None):
    db.execute(coverage.SCHEMA)
    db.execute(PENDING)
    result={}
    for frame in FRAMES:
        if deadline is not None and time.monotonic()>=deadline:
            result[frame]={"status":"deferred_runtime"}
            continue
        current=bars if frame=="1d" else coverage.weekly(bars)
        args=(asset,frame,VERSION)
        if len(current)<MIN_EVALUATION_BARS+3:
            result[frame]={"status":"blocked_history","available_bars":len(current)}
            continue
        key=(asset,frame,"indicator_bank",VERSION)
        row=db.execute("SELECT next_trial FROM coverage_cursor WHERE asset=? AND timeframe=? AND lane=? AND version=?",key).fetchone()
        next_position=row[0] if row else 0
        pending=db.execute("""SELECT position FROM indicator_pending WHERE asset=? AND frame=? AND version=?
                             AND required_bars<=? ORDER BY position LIMIT 1""",(*args,len(current))).fetchone()
        position=pending[0] if pending else next_position
        spec=plan(asset,frame,position)
        required=warmup(spec)+MIN_EVALUATION_BARS
        identity=research.digest([VERSION,spec])
        family=spec["rules"][0]["name"] if len(spec["rules"])==1 else "combination"
        outcome={"position":position,"id":identity,"family":family,
                 "indicators":[r["name"] for r in spec["rules"]]}
        if len(current)<required:
            count=db.execute("SELECT COUNT(*) FROM indicator_pending WHERE asset=? AND frame=? AND version=?",args).fetchone()[0]
            if count>=MAX_PENDING_PER_FRAME and not pending:
                result[frame]={**outcome,"status":"blocked_pending_capacity","required_bars":required}
                continue
            db.execute("INSERT OR REPLACE INTO indicator_pending VALUES (?,?,?,?,?)",(*args,position,required))
            status="blocked_history"
        elif db.execute("SELECT 1 FROM attempts WHERE version=? AND asset=? AND id=?",(VERSION,asset,identity)).fetchone():
            status="duplicate_not_counted"
        else:
            start=warmup(spec)
            boundary=start+(len(current)-start)*3//4
            target,valid=targets(current,spec)
            if not valid.iloc[start:boundary].all() or not valid.iloc[boundary:].all():
                # Occasional zero-range or zero-volume windows are treated as
                # unavailable; do not count mostly undefined strategies as tests.
                enough=valid.iloc[start:boundary].mean()>=.8 and valid.iloc[boundary:].mean()>=.8
            else:
                enough=True
            if not enough:
                status="blocked_undefined_indicator"
            else:
                train,train_path=foundry.execute(current,target,start,boundary,spec["risk"],market.cost_bps)
                validation,valid_path=foundry.execute(current,target,boundary,len(current),spec["risk"],market.cost_bps)
                evidence={"discovery":train_path,"validation":valid_path}
                evidence["sha256"]=research.digest(evidence)
                discovered={"pass":False,"metrics":train,"return_evidence":evidence,"paper_eligible":False,
                            "valid_signal_fraction":float(valid.iloc[start:boundary].mean())}
                verified={"metrics":validation,"retrospective_only":True,"paper_eligible":False,
                          "valid_signal_fraction":float(valid.iloc[boundary:].mean())}
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (VERSION,asset,identity,"indicator:"+frame+":"+family,research.canonical(spec),market.source,
                            data.fingerprint(current),current.index[-1].date().isoformat(),os.getenv("GITHUB_SHA","local"),
                            research.canonical(POLICY),research.canonical(discovered),research.canonical(verified)))
                status="evaluated"
        if pending and status!="blocked_history":
            db.execute("DELETE FROM indicator_pending WHERE asset=? AND frame=? AND version=? AND position=?",(*args,position))
        if not pending:
            db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)",(*key,next_position+1))
        result[frame]={**outcome,"status":status}
    return result


def counts(db):
    """Compact archived identities keep their family; no need to parse large specs."""
    rows=list(db.execute("SELECT family,count(*) FROM attempts WHERE version=? GROUP BY family",(VERSION,)))
    result={name:{"1d":0,"1w":0} for name in (*bank.FAMILIES,"combination")}
    for family,n in rows:
        _,frame,name=family.split(":")
        result[name][frame]=n
    return [{"indicator":name,"daily":v["1d"],"weekly":v["1w"],"total":sum(v.values())}
            for name,v in result.items()]
