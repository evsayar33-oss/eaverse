"""Research-only strategy grammar and causal OHLC risk execution.

No orders, no change to legacy PAPER. Generated configurations are a
deterministic, outcome-independent search; no claim of an exhaustive domain.
"""
from __future__ import annotations

import json
import math
import os
import random
import time

import numpy as np
import pandas as pd

from . import coverage, data, research

VERSION = "coverage-2"
LANES = ("parameter", "grammar", "risk")
SPECIAL = ("macd_multi", "bollinger_multi", "rsi_multi", "stochastic_multi", "adx_multi")
POLICY = {"version": VERSION, "execution": "next_open_then_OHLC_stop_first",
          "cost_multiplier": 2, "borrow_apr_assumption": .05, "max_gross": 1,
          "selection": "none", "retrospective_only": True, "paper_eligible": False,
          "live_allowed": False, "intrabar_ambiguity": "adverse_stop_first",
          "trailing": "prior_completed_bar_extreme", "risk_atr": "prior_completed_bar",
          "path": "compounded_calendar_week_returns"}
FAMILIES = tuple(dict.fromkeys([r[0] for r in coverage.rule_grid("1d")])) + SPECIAL


def choose_rule(frame, name, rng):
    if name == "macd_multi":
        fast = rng.choice((5, 8, 12, 16, 20))
        return (name, fast, rng.choice((26, 35, 50, 60)), rng.choice((3, 5, 9, 12)), rng.choice(("above", "below", "cross_up", "cross_down")))
    if name == "bollinger_multi":
        return (name, rng.choice((10, 15, 20, 30, 40, 50)), rng.choice((.5, 1., 1.5, 2., 2.5, 3.)), rng.choice(("sma", "ema")), rng.choice(("above", "below", "cross_up", "cross_down")))
    if name == "rsi_multi":
        return (name, rng.choice((5, 7, 10, 14, 21, 28)), rng.choice((20, 30, 40, 50, 60, 70, 80)), rng.choice(("wilder", "sma")), rng.choice(("above", "below", "cross_up", "cross_down")))
    if name == "stochastic_multi":
        return (name, rng.choice((5, 9, 14, 21, 28)), rng.choice((1, 3, 5)), rng.choice((20, 30, 50, 70, 80)), rng.choice(("above", "below", "cross_up", "cross_down")))
    if name == "adx_multi":
        return (name, rng.choice((7, 10, 14, 21, 28)), rng.choice((7, 14, 21)), rng.choice((15, 20, 25, 30, 40)), rng.choice(("above", "below")))
    return rng.choice([r for r in coverage.rule_grid(frame) if r[0] == name])


def plan(asset, frame, lane, position):
    rng = random.Random(research.digest([VERSION, asset, frame, lane, position]))
    name = FAMILIES[position % len(FAMILIES)]
    if lane == "grammar":
        size = 2 + position % 5
        names = [name] + rng.sample([n for n in FAMILIES if n != name], size-1)
    else:
        names = [name]
    rules = [choose_rule(frame, n, rng) for n in names]
    risk = {"atr_window": rng.choice((7, 14, 21, 28)),
            "stop": rng.choice((None, 1., 1.5, 2., 3., 4.)),
            "tp": rng.choice((None, 1., 2., 3., 4., 6.)),
            "trail": rng.choice((None, 1., 1.5, 2., 3.)),
            "partial": rng.choice((False, True)),
            "staged": rng.choice((False, True)),
            "max_hold": rng.choice((None, 3, 5, 10, 20, 40))}
    if lane == "parameter":
        risk.update(stop=None, tp=None, trail=None, partial=False, staged=False, max_hold=None)
    if lane == "risk" and all(risk[k] is None for k in ("stop", "tp", "trail", "max_hold")):
        risk["stop"] = 2.
    return {"frame":frame, "lane":lane, "rules":rules,
            "logic":rng.choice(("all", "any", "majority", "sequence", "filter", "weighted")) if len(rules)>1 else "all",
            "direction":rng.choice(coverage.DIRECTIONS), "confirmation":rng.choice((1,2,3)),
            "size":rng.choice((.25,.5,1.)), "risk":risk}


def adaptive_plan(db, asset, frame, lane, position):
    """Every other parameter/grammar turn explores; the other mutates a
    discovery-only parent. Validation metrics are never read for selection."""
    spec=plan(asset,frame,lane,position)
    if lane == "risk" or position % 2 == 0:
        return spec
    parents=[]
    for row in db.execute("SELECT id,spec,discovered FROM attempts WHERE version=? AND asset=? AND family=? AND spec<>'' ORDER BY rowid DESC LIMIT 100",
                          (VERSION,asset,"coverage:"+frame+":"+lane)):
        report=json.loads(row["discovered"]).get("metrics",{})
        if report.get("entries",0)>=2 and report.get("status")=="evaluated":
            score=report["net"]-report["drawdown"]
            parents.append((score,row["id"],json.loads(row["spec"])))
    if not parents:
        return spec
    _,parent_id,parent=max(parents,key=lambda x:(x[0],x[1]))
    rng=random.Random(research.digest([VERSION,asset,frame,lane,position,parent_id]))
    # Jointly mutate two or more dimensions while keeping the original rule family.
    slot=rng.randrange(len(parent["rules"]))
    parent["rules"][slot]=choose_rule(frame,parent["rules"][slot][0],rng)
    parent["confirmation"]=spec["confirmation"]
    parent["size"]=spec["size"]
    parent["direction"]=spec["direction"]
    if lane=="grammar":
        parent["logic"]=spec["logic"]
        parent["risk"]=spec["risk"]
    # Parent provenance is stored outside the trial identity.
    return parent


def warmup(spec):
    spans=[coverage.warmup_for({"rules":[r for r in spec["rules"] if r[0] not in SPECIAL]}),
           spec["risk"]["atr_window"]+3]
    for rule in spec["rules"]:
        name=rule[0]
        if name=="macd_multi":
            spans.append(rule[2]+rule[3]+3)
        elif name=="adx_multi":
            spans.append(2*rule[1]+2*rule[2]+3)
        elif name=="stochastic_multi":
            spans.append(rule[1]+rule[2]+3)
        elif name in ("rsi_multi","bollinger_multi"):
            spans.append(rule[1]+3)
    return max(spans)


def comparison(x, level, op):
    if op == "above":
        return x > level
    if op == "below":
        return x < level
    if op == "cross_up":
        return (x > level) & (x.shift() <= level)
    if op == "cross_down":
        return (x < level) & (x.shift() >= level)
    raise ValueError("unknown operator")


def true_range(bars):
    c = bars.close
    return pd.concat([bars.high-bars.low, (bars.high-c.shift()).abs(), (bars.low-c.shift()).abs()], axis=1).max(axis=1)


def signal(bars, rule):
    name = rule[0]
    if name not in SPECIAL:
        valid = pd.Series(True, index=bars.index)
        if name in coverage.NORMALIZED:
            valid = coverage.measurement(bars, name, rule[1]).notna()
        elif name == "vwma":
            valid = bars.volume.rolling(rule[1]).sum() > 0
        return coverage.flag(bars, rule), valid
    c = bars.close
    if name == "macd_multi":
        _, fast, slow, smooth, op = rule
        line = c.ewm(span=fast, min_periods=fast, adjust=False).mean()-c.ewm(span=slow, min_periods=slow, adjust=False).mean()
        x = line-line.ewm(span=smooth, min_periods=smooth, adjust=False).mean()
        return comparison(x, 0, op), x.notna()
    if name == "bollinger_multi":
        _, n, k, avg, op = rule
        center = c.rolling(n).mean() if avg == "sma" else c.ewm(span=n,min_periods=n,adjust=False).mean()
        sd = c.rolling(n).std()
        x = (c-center)/sd.where(sd>0)
        threshold = -k if op in ("below","cross_down") else k
        return comparison(x, threshold, op), x.notna()
    if name == "rsi_multi":
        _, n, level, avg, op = rule
        ch = c.diff()
        gain, loss = ch.clip(lower=0), -ch.clip(upper=0)
        if avg == "wilder":
            gain, loss = (x.ewm(alpha=1/n,min_periods=n,adjust=False).mean() for x in (gain,loss))
        else:
            gain, loss = (x.rolling(n).mean() for x in (gain,loss))
        x = 100*gain/(gain+loss).where(gain+loss>0)
        return comparison(x,level,op), x.notna()
    if name == "stochastic_multi":
        _, n, smooth, level, op = rule
        low, high = bars.low.rolling(n).min(), bars.high.rolling(n).max()
        x = (100*(c-low)/(high-low).where(high>low)).rolling(smooth).mean()
        return comparison(x,level,op), x.notna()
    _, n, smooth, level, op = rule
    up, down = bars.high.diff(), -bars.low.diff()
    atr = true_range(bars).ewm(alpha=1/n,min_periods=n,adjust=False).mean()
    p = up.where((up>down)&(up>0),0).ewm(alpha=1/n,min_periods=n,adjust=False).mean()/atr.where(atr>0)
    m = down.where((down>up)&(down>0),0).ewm(alpha=1/n,min_periods=n,adjust=False).mean()/atr.where(atr>0)
    dx = 100*(p-m).abs()/(p+m).where(p+m>0)
    x = dx.ewm(alpha=1/smooth,min_periods=smooth,adjust=False).mean()
    return comparison(x,level,op), x.notna()


def desired(bars,spec):
    pairs = [signal(bars,r) for r in spec["rules"]]
    flags = [x[0] for x in pairs]
    valid = pd.concat([x[1] for x in pairs],axis=1).all(axis=1)
    votes = sum(x.astype(int) for x in flags)
    logic = spec["logic"]
    if logic == "all":
        result = votes == len(flags)
    elif logic == "any":
        result = votes > 0
    elif logic == "majority":
        result = votes >= math.ceil(len(flags)/2)
    elif logic == "sequence":
        result = flags[-1].copy()
        for i,f in enumerate(reversed(flags[:-1]),1):
            result &= f.shift(i,fill_value=False)
    elif logic == "filter":
        result = flags[0] & (votes >= 2)
    elif logic == "weighted":
        result = sum((i+1)*f.astype(int) for i,f in enumerate(flags)) >= math.ceil(len(flags)*(len(flags)+1)/4)
    else:
        raise ValueError("unknown grammar")
    value = result.astype(float)
    if spec["direction"] == "short":
        value = -value
    elif spec["direction"] == "long_short":
        value = 2*value-1
    value = value.where(valid,0)
    confirm = spec["confirmation"]
    if confirm>1:
        stable = value.eq(value.shift()).rolling(confirm-1).sum().eq(confirm-1)
        value = value.where(stable,0)
    return value*spec["size"]


def execute(bars, targets, start, stop, risk, cost_bps):
    """Cash + signed units; next open signal; gap stops, prior-bar trail.
    Ambiguous simultaneous stop/target uses stop first. TP never gets favorable
    gap improvement. No intrabar re-entry, leverage or assumed orderbook fills.
    """
    atr = true_range(bars).rolling(risk["atr_window"]).mean().shift().to_numpy()
    target = targets.shift().fillna(0).to_numpy()
    o,h,l,c = (bars[x].to_numpy(dtype=float) for x in ("open","high","low","close"))
    fee = 2*cost_bps/10000
    cash, units, prev_equity = 1.,0.,1.
    entry = anchor = 0.
    held, entries, exits, partials, adds = 0,0,0,0,0
    took_profit, added = False,False
    trailing_stop = None
    previous_signal, locked = 0,0
    path = []
    peak, dd = 1.,0.
    def order(delta, price):
        nonlocal cash,units
        cash -= delta*price + abs(delta)*price*fee
        units += delta
    def close(price):
        nonlocal exits
        if units:
            order(-units,price)
            exits += 1
    def levels(a):
        nonlocal trailing_stop
        sign = np.sign(units)
        stop_price = entry-sign*risk["stop"]*a if risk["stop"] is not None else None
        if risk["trail"] is not None:
            trailing = anchor-sign*risk["trail"]*a
            trailing_stop = trailing if trailing_stop is None else (max(trailing_stop,trailing) if sign>0 else min(trailing_stop,trailing))
            stop_price = trailing_stop if stop_price is None else (max(stop_price,trailing_stop) if sign>0 else min(stop_price,trailing_stop))
        take = entry+sign*risk["tp"]*a if risk["tp"] is not None and not took_profit else None
        return stop_price,take
    for i in range(start,stop):
        want = int(np.sign(target[i]))
        if want != previous_signal:
            locked = 0
        previous_signal = want
        a = atr[i]
        # Existing gap risk takes priority over a newly observed signal.
        if units and np.isfinite(a) and a>0:
            sign = np.sign(units)
            sl,tp = levels(a)
            if sl is not None and sign*(o[i]-sl)<=0:
                close(o[i]); locked=want
            elif tp is not None and sign*(o[i]-tp)>=0:
                if risk["partial"]:
                    order(-units/2,tp); took_profit=True; partials+=1
                else:
                    close(tp); locked=want
        if units and (want != np.sign(units) or (risk["max_hold"] is not None and held>=risk["max_hold"])):
            close(o[i]); locked=want if want else 0
        if i<stop-1 and not units and want and not locked and np.isfinite(a) and a>0:
            equity = cash
            if equity<=0:
                break
            size = abs(target[i])*(.5 if risk["staged"] else 1.)
            order(want*equity*size/(o[i]*(1+fee)),o[i])
            entry=anchor=o[i]; held=0; entries+=1; took_profit=False; added=False; trailing_stop=None
        elif units and risk["staged"] and not added and not took_profit and held>=1 and np.sign(units)*(c[i-1]-entry)>=a and i<stop-1:
            equity = cash+units*o[i]
            extra = max(0.,equity*abs(target[i])/(1+fee)-abs(units)*o[i])
            if extra>0:
                old=abs(units)
                order(np.sign(units)*extra/o[i],o[i])
                entry=(old*entry+(abs(units)-old)*o[i])/abs(units)
                adds+=1
            added=True
        if units and np.isfinite(a) and a>0:
            sign=np.sign(units)
            sl,tp=levels(a)
            adverse=l[i] if sign>0 else h[i]
            favorable=h[i] if sign>0 else l[i]
            if sl is not None and sign*(adverse-sl)<=0:
                # After new entry or changed ATR, stop may already be crossed at open.
                close(min(o[i],sl) if sign>0 else max(o[i],sl)); locked=want
            elif units<0 and cash+units*h[i]<=0:
                path.append(-1.); dd=1.; break
            elif tp is not None and sign*(favorable-tp)>=0:
                if risk["partial"]:
                    order(-units/2,tp); took_profit=True; partials+=1
                else:
                    close(tp); locked=want
            if units:
                if units<0:
                    days = max(1,(bars.index[i]-bars.index[i-1]).days) if i>0 else 1
                    cash -= abs(units)*o[i]*.05*days/365
                anchor=max(anchor,h[i]) if units>0 else min(anchor,l[i])
                held+=1
        if i==stop-1:
            close(c[i])
        equity=cash+units*c[i]
        if not math.isfinite(equity) or equity<=0:
            path.append(-1.); dd=1.; break
        path.append(equity/prev_equity-1)
        prev_equity=equity
        peak=max(peak,equity); dd=max(dd,1-equity/peak)
    if not path:
        raise ValueError("EMPTY_RISK_PATH")
    # Compound to weekly paths to cap archive size; daily drawdown remains source-only.
    weeks=bars.index[start:start+len(path)].to_period("W-SUN")
    grouped=pd.Series(path,index=weeks).groupby(level=0).apply(lambda x:float(np.prod(1+x)-1))
    net=float(np.prod(1+np.asarray(path))-1)
    metrics={"net":net,"drawdown":dd,"entries":entries,"exits":exits,"partial_exits":partials,"scale_ins":adds,
             "status":"ruin" if net<=-1 else "evaluated" if entries else "no_entries"}
    return metrics,grouped.tolist()


def run_asset(db,asset,market,bars,deadline=None):
    db.execute(coverage.SCHEMA)
    db.execute(coverage.PENDING)
    db.execute('CREATE TABLE IF NOT EXISTS foundry_frozen (asset TEXT,timeframe TEXT,lane TEXT,position INTEGER,spec TEXT,PRIMARY KEY(asset,timeframe,lane,position))')
    result={}
    for frame in coverage.FRAMES:
        current=bars if frame=="1d" else coverage.weekly(bars)
        for lane in LANES:
            if deadline is not None and time.monotonic()>=deadline:
                return result
            args=(asset,frame,lane,VERSION)
            row=db.execute("SELECT next_trial FROM coverage_cursor WHERE asset=? AND timeframe=? AND lane=? AND version=?",args).fetchone()
            pos=row[0] if row else 0
            retry=db.execute("SELECT position FROM coverage_pending WHERE asset=? AND timeframe=? AND lane=? AND version=? ORDER BY checked,position LIMIT 1",args).fetchone()
            for offset,new in ([(retry[0],False)] if retry else [])+[(pos,True)]:
                frozen_args=(asset,frame,lane,offset)
                frozen=db.execute('SELECT spec FROM foundry_frozen WHERE asset=? AND timeframe=? AND lane=? AND position=?',frozen_args).fetchone()
                spec=json.loads(frozen[0]) if frozen else adaptive_plan(db,asset,frame,lane,offset)
                ident=research.digest([VERSION,spec])
                minimum=warmup(spec)
                key=frame+":"+lane
                if len(current)<minimum+120:
                    from datetime import UTC,datetime
                    db.execute('INSERT OR REPLACE INTO foundry_frozen VALUES (?,?,?,?,?)',(*frozen_args,research.canonical(spec)))
                    db.execute("INSERT OR REPLACE INTO coverage_pending VALUES (?,?,?,?,?,?,?)",(*args,offset,"blocked_history",datetime.now(UTC).isoformat()))
                    if new:
                        db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)",(*args,pos+1))
                    result[key]={"status":"blocked_history","position":offset}
                    continue
                db.execute('DELETE FROM foundry_frozen WHERE asset=? AND timeframe=? AND lane=? AND position=?',frozen_args)
                if db.execute("SELECT 1 FROM attempts WHERE version=? AND asset=? AND id=?",(VERSION,asset,ident)).fetchone():
                    if new:
                        db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)",(*args,pos+1))
                    else:
                        db.execute("DELETE FROM coverage_pending WHERE asset=? AND timeframe=? AND lane=? AND version=? AND position=?",(*args,offset))
                    result[key]={"status":"duplicate_not_counted","position":offset}
                    continue
                boundary=minimum+(len(current)-minimum)*3//4
                target=desired(current,spec)
                train,train_path=execute(current,target,minimum,boundary,spec["risk"],market.cost_bps)
                valid,valid_path=execute(current,target,boundary,len(current),spec["risk"],market.cost_bps)
                ev={"discovery":train_path,"validation":valid_path}
                ev["sha256"]=research.digest(ev)
                report={"pass":False,"metrics":train,"return_evidence":ev,"paper_eligible":False}
                validation={"metrics":valid,"retrospective_only":True,"paper_eligible":False}
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (VERSION,asset,ident,"coverage:"+key,research.canonical(spec),market.source,
                            data.fingerprint(current),current.index[-1].date().isoformat(),os.getenv("GITHUB_SHA","local"),
                            research.canonical(POLICY),research.canonical(report),research.canonical(validation)))
                if new:
                    db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)",(*args,pos+1))
                else:
                    db.execute("DELETE FROM coverage_pending WHERE asset=? AND timeframe=? AND lane=? AND version=? AND position=?",(*args,offset))
                result[key]={"status":"evaluated","id":ident,"position":offset}
                break
    return result
