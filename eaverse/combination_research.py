"""A lazily indexed, per-asset coverage catalog across both indicator engines.

Family-level coverage is finite but enormous. No pre-materialized Cartesian
grid, no outcome-based exclusion and no claim that every parameter value or
all registered combinations have already been tested.
"""
from __future__ import annotations

import math
import os
import random
import time
from functools import lru_cache
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from . import coverage, data, events, foundry, indicator_bank as bank, research, surface

VERSION = "coverage-combinations-v1"
BANK = tuple("bank:"+n for n in bank.FAMILIES)
LEGACY = tuple("legacy:"+n for n in foundry.FAMILIES)
FAMILIES = BANK+LEGACY
FRAMES = ("1d","1w")
TECH_LANES = ("bank_pair","cross_pair","single","legacy_pair","same_family","triple","medium","higher","technical_settings")
EVENT_LANES = ("mixed_pair","event_single","event_pair","mixed_small","event_higher","mixed_higher","event_settings")
MARKET_EVENTS = ("PRICE_UP_SHOCK","PRICE_DOWN_SHOCK","GAP_UP","GAP_DOWN","VOLUME_SPIKE",
                 "VOLATILITY_EXPANSION","VOLATILITY_CONTRACTION","BREAKOUT_HIGH","BREAKDOWN_LOW",
                 "TREND_REVERSAL_UP","TREND_REVERSAL_DOWN","DRAWDOWN")
LANES = TECH_LANES+EVENT_LANES
CORE_LANES = ("single","bank_pair","cross_pair","legacy_pair","same_family",
              "event_single","event_pair","mixed_pair")
TECH_SCHEDULE = ("bank_pair","cross_pair","single","legacy_pair","same_family","triple",
                 "bank_pair","cross_pair","medium","single","legacy_pair","higher","technical_settings")
EVENT_SCHEDULE = ("mixed_pair","event_single","event_pair","mixed_small","mixed_pair",
                  "event_higher","event_single","mixed_higher","event_settings")
SETTINGS = ("technical_settings","event_settings")
MIN_BARS = 120
MAX_PENDING = 64
MIN_EVENTS = 3
POLICY = {**foundry.POLICY,"version":VERSION,"registry":"bank_and_legacy_rule_types",
          "catalog":"every_unordered_family_subset_size; boolean_modes; event_only_avoid; directions",
          "settings":"separate_seeded_parameter_variations_not_exhaustive",
          "min_bars":MIN_BARS,"min_events_per_fold":MIN_EVENTS,"max_pending_per_lane":MAX_PENDING,
          "event_clock":"first_completed_bar_strictly_after_release_then_next_open",
          "event_cutoff":"earliest_available_source_snapshot",
          "pending":"bounded_retry_no_silent_skip","matched_unfiltered_control":True}
SCHEMA = """CREATE TABLE IF NOT EXISTS combination_cursor(
 asset TEXT NOT NULL,frame TEXT NOT NULL,lane TEXT NOT NULL,version TEXT NOT NULL,
 next_rank TEXT NOT NULL,PRIMARY KEY(asset,frame,lane,version))"""
PENDING = """CREATE TABLE IF NOT EXISTS combination_pending(
 asset TEXT NOT NULL,frame TEXT NOT NULL,lane TEXT NOT NULL,version TEXT NOT NULL,rank TEXT NOT NULL,
 reason TEXT NOT NULL,token TEXT NOT NULL,checked TEXT NOT NULL,
 PRIMARY KEY(asset,frame,lane,version,rank))"""


def event_names(frame):
    return tuple(sorted(events.FAMILIES))+tuple(e for e in events.CALENDAR
          if frame=="1d" or not e.startswith("WEEKDAY_"))+MARKET_EVENTS


def unrank(items,k,rank):
    """Lexicographic k-subset unranking, without constructing combinations."""
    n=len(items)
    if not 0<=k<=n or not 0<=rank<math.comb(n,k):
        raise ValueError("combination rank out of range")
    out=[]
    start=0
    for remaining in range(k,0,-1):
        for i in range(start,n-remaining+1):
            block=math.comb(n-i-1,remaining-1)
            if rank<block:
                out.append(items[i])
                start=i+1
                break
            rank-=block
    return tuple(out)


def logic_options(n):
    return ("all",) if n<=1 else ("all","any") if n==2 else ("all","any","majority")


def event_logic_options(n):
    # Test unions first so rare/impossible same-bar intersections do not
    # monopolize the initial source-limited queue.
    return ("all",) if n<=1 else ("any","all") if n==2 else ("any","all","majority")


def direction_options(i):
    return coverage.DIRECTIONS if i else ("long","short")


def variants(i,j):
    return len(logic_options(i))*len(event_logic_options(j))*(2 if j else 1)*len(direction_options(i))


@lru_cache(maxsize=None)
def layers(frame,lane):
    n,m=len(FAMILIES),len(event_names(frame))
    if lane=="single": return ((1,0),)
    if lane in ("bank_pair","cross_pair","legacy_pair","same_family"): return ((2,0),)
    if lane=="triple": return ((3,0),)
    if lane=="medium": return tuple((i,0) for i in range(4,8))
    if lane=="higher": return tuple((i,0) for i in range(8,n+1))
    if lane=="event_single": return ((0,1),)
    if lane=="event_pair": return ((0,2),)
    if lane=="event_higher": return tuple((0,j) for j in range(3,m+1))
    if lane=="mixed_pair": return ((1,1),)
    if lane in ("mixed_small","mixed_higher"):
        # Small combinations progress independently of the exponential tail.
        return tuple(sorted(((i,j) for i in range(1,n+1) for j in range(1,m+1)
                             if (2<i+j<=4 if lane=="mixed_small" else i+j>4)),
                            key=lambda x:(sum(x),x[1],x[0])))
    raise ValueError("unknown finite catalog lane")


def subset_count(frame,lane,i,j):
    if lane=="bank_pair": return math.comb(len(BANK),2)
    if lane=="legacy_pair": return math.comb(len(LEGACY),2)
    if lane=="cross_pair": return len(BANK)*len(LEGACY)
    if lane=="same_family": return len(FAMILIES)
    return math.comb(len(FAMILIES),i)*math.comb(len(event_names(frame)),j)


@lru_cache(maxsize=None)
def layer_counts(frame,lane):
    return tuple(subset_count(frame,lane,i,j)*variants(i,j) for i,j in layers(frame,lane))


@lru_cache(maxsize=None)
def total(frame,lane):
    return sum(layer_counts(frame,lane)) if lane not in SETTINGS else None


def interleaved_rank(frame,lane,position):
    """Round robin across sizes until each finite layer is exhausted."""
    counts=layer_counts(frame,lane)
    if not 0<=position<sum(counts):
        raise ValueError("catalog rank out of range")
    lo,hi=0,max(counts)
    while lo+1<hi:
        mid=(lo+hi)//2
        if sum(min(mid,c) for c in counts)<=position:
            lo=mid
        else:
            hi=mid
    offset=position-sum(min(lo,c) for c in counts)
    active=[k for k,c in enumerate(counts) if c>lo]
    i,j=layers(frame,lane)[active[offset]]
    return i,j,lo


def permute(rank,count,seed):
    """Deterministic bijection, varying starting families per asset."""
    if count<=1: return 0
    value=int(research.digest(seed),16)
    a=1+value%(count-1)
    while math.gcd(a,count)!=1:
        a=1+a%(count-1)
    return (a*rank+(value//count)%count)%count


def selection(asset,frame,lane,position):
    i,j,rank=interleaved_rank(frame,lane,position)
    base,mode=divmod(rank,variants(i,j))
    count=subset_count(frame,lane,i,j)
    base=permute(base,count,[VERSION,asset,frame,lane,i,j])
    if lane=="bank_pair":
        names=unrank(BANK,2,base); ev=()
    elif lane=="legacy_pair":
        names=unrank(LEGACY,2,base); ev=()
    elif lane=="cross_pair":
        a,b=divmod(base,len(LEGACY)); names=(BANK[a],LEGACY[b]); ev=()
    elif lane=="same_family":
        names=(FAMILIES[base],)*2; ev=()
    else:
        a,b=divmod(base,math.comb(len(event_names(frame)),j))
        names=unrank(FAMILIES,i,a); ev=unrank(event_names(frame),j,b)
    event_modes=("only","avoid") if j else (None,)
    q,di=divmod(mode,len(direction_options(i)))
    q,gate=divmod(q,len(event_modes))
    ti,ei=divmod(q,len(event_logic_options(j)))
    return names,ev,logic_options(i)[ti],event_logic_options(j)[ei],event_modes[gate],direction_options(i)[di]


def legacy_default(name):
    special={
        "macd_multi":("macd_multi",12,26,9,"above"),
        "bollinger_multi":("bollinger_multi",20,2.,"sma","above"),
        "rsi_multi":("rsi_multi",14,50,"wilder","above"),
        "stochastic_multi":("stochastic_multi",14,3,50,"above"),
        "adx_multi":("adx_multi",14,14,25,"above"),
    }
    if name in special: return special[name]
    levels={"rsi_recovery":30,"rsi_trend":50,"stoch_recovery":50,"adx":25,
            "volume":1.2,"low_vol":1.,"atr":2,"atr_pct":2,
            "range_pct":2,"realized_vol_pct":2}
    level=levels.get(name,0)
    return ("surface",name,20,14,"ema" if name=="ema" else "sma","close",level,"above")


def make_rule(frame,key,rng,default):
    engine,name=key.split(":",1)
    if engine=="bank":
        value=bank.choose(name,rng,default=default,frame=frame)
    else:
        value=legacy_default(name) if default else foundry.choose_rule(frame,name,rng)
    return {"family":key,"rule":value}


def plan(asset,frame,lane,position):
    rng=random.Random(research.digest([VERSION,asset,frame,lane,str(position)]))
    varied=lane in SETTINGS
    if varied:
        pool=TECH_LANES[:-1] if lane=="technical_settings" else EVENT_LANES[:-1]
        source=pool[position%len(pool)]
        rank=rng.randrange(total(frame,source))
        names,ev,logic,elogic,gate,direction=selection(asset,frame,source,rank)
    else:
        names,ev,logic,elogic,gate,direction=selection(asset,frame,lane,position)
    rules=[]
    for k in names:
        r=make_rule(frame,k,rng,not varied)
        if any(x["family"]==k for x in rules):
            # Deliberately test two settings of the same indicator, not two
            # copies of the same condition. The seeded rule is immutable.
            same_rng=rng if varied else random.Random(research.digest([VERSION,asset,frame,"same_family",k]))
            for _ in range(32):
                r=make_rule(frame,k,same_rng,False)
                if research.canonical(r)!=research.canonical(rules[-1]): break
            else: raise ValueError("duplicate within-family rule")
        rules.append(r)
    if varied and len(rules)>1:
        logic=rng.choice(("all","any","majority","sequence","filter","weighted"))
    conditions=[]
    for e in ev:
        condition={"name":e,"offset":rng.choice((1,2,3)) if varied and e in events.FAMILIES and frame=="1d" else 1,
                   "lookback":rng.choice((1,2,3,5)) if varied and e not in events.CALENDAR else 1}
        if e in MARKET_EVENTS:
            condition.update(market_event_settings(e,rng,varied))
        conditions.append(condition)
    risk={"atr_window":14,"stop":None,"tp":None,"trail":None,"partial":False,"staged":False,"max_hold":None}
    if varied:
        risk.update(atr_window=rng.choice((7,14,21,28)),stop=rng.choice((None,1.,2.,3.)),
                    tp=rng.choice((None,1.,2.,4.,6.)),trail=rng.choice((None,1.,2.,3.)),
                    staged=rng.choice((False,True)),max_hold=rng.choice((None,5,10,20,40)))
        if risk["tp"] is not None: risk["partial"]=rng.choice((False,True))
    return {"frame":frame,"rules":rules,"logic":logic,"events":conditions,"event_logic":elogic,
            "event_mode":gate,"direction":direction,
            "confirmation":rng.choice((1,2,3)) if varied else 1,"size":rng.choice((.25,.5,1.)) if varied else 1.,
            "risk":risk}


def rule_warmup(r):
    value=r["rule"]
    if r["family"].startswith("bank:"): return bank.warmup(value)
    return foundry.warmup({"rules":[value],"risk":{"atr_window":1}})


def rule_signal(bars,r):
    value=r["rule"]
    if r["family"].startswith("bank:"): return bank.signal(bars,value)
    flag,valid=foundry.signal(bars,value)
    if value[-1] in ("cross_up","cross_down"):
        valid=valid&valid.shift(fill_value=False)
    return flag&valid,valid


def combine(flags,logic):
    votes=sum(x.astype(int) for x in flags)
    if logic=="all": return votes==len(flags)
    if logic=="any": return votes>0
    if logic=="majority": return votes>=len(flags)//2+1
    if logic=="filter": return flags[0]&(votes>=2)
    if logic=="weighted":
        return sum((i+1)*f.astype(int) for i,f in enumerate(flags))>=math.ceil(len(flags)*(len(flags)+1)/4)
    if logic=="sequence":
        result=flags[-1].copy()
        for i,f in enumerate(reversed(flags[:-1]),1): result &= f.shift(i,fill_value=False)
        return result
    raise ValueError("unknown combination logic")


def technical_target(bars,spec):
    if not spec["rules"]:
        valid=pd.Series(True,index=bars.index)
        return pd.Series(-spec["size"] if spec["direction"]=="short" else spec["size"],index=bars.index),valid
    pairs=[rule_signal(bars,r) for r in spec["rules"]]
    flags=[x[0] for x in pairs]
    if spec["logic"]=="sequence":
        valid=pairs[-1][1].copy()
        for i,(_,ok) in enumerate(reversed(pairs[:-1]),1): valid &= ok.shift(i,fill_value=False)
    else:
        valid=pd.concat([x[1] for x in pairs],axis=1).all(axis=1)
    target=combine(flags,spec["logic"]).astype(float)
    if spec["direction"]=="short": target=-target
    elif spec["direction"]=="long_short": target=2*target-1
    target=target.where(valid,0.)
    confirm=spec["confirmation"]
    if confirm>1:
        target=target.where(target.eq(target.shift()).rolling(confirm-1).sum().eq(confirm-1),0.)
    return target*spec["size"],valid


def market_event_settings(name,rng,varied):
    n=rng.choice((5,10,14,20,30,40,60)) if varied else 20
    if name in ("PRICE_UP_SHOCK","PRICE_DOWN_SHOCK","GAP_UP","GAP_DOWN"):
        return {"threshold":rng.choice((.01,.02,.03,.05,.08)) if varied else .03}
    if name=="VOLUME_SPIKE":
        return {"window":n,"threshold":rng.choice((1.25,1.5,2.,3.,4.)) if varied else 2.}
    if name in ("VOLATILITY_EXPANSION","VOLATILITY_CONTRACTION"):
        return {"window":n,"threshold":rng.choice((1.25,1.5,2.,3.)) if varied else 1.5}
    if name=="DRAWDOWN":
        return {"window":n,"threshold":rng.choice((.03,.05,.1,.15,.2)) if varied else .1}
    return {"window":n}


def market_event(bars,condition):
    """OHLCV event detections with past-only baselines and explicit validity."""
    name=condition["name"]
    n=condition.get("window",20)
    threshold=condition.get("threshold",0.)
    c,h,l,v=bars.close,bars.high,bars.low,bars.volume
    if name in ("PRICE_UP_SHOCK","PRICE_DOWN_SHOCK"):
        value=c.pct_change(fill_method=None); flag=value>threshold if name=="PRICE_UP_SHOCK" else value<-threshold
    elif name in ("GAP_UP","GAP_DOWN"):
        value=bars.open/c.shift()-1; flag=value>threshold if name=="GAP_UP" else value<-threshold
    elif name=="VOLUME_SPIKE":
        mean=v.shift().rolling(n).mean()
        value=v/mean.where(mean>0); flag=value>threshold
    elif name in ("VOLATILITY_EXPANSION","VOLATILITY_CONTRACTION"):
        vol=c.pct_change(fill_method=None).rolling(n).std()
        base=vol.shift().rolling(n).mean()
        value=vol/base.where(base>0)
        flag=value>threshold if name=="VOLATILITY_EXPANSION" else value<1/threshold
    elif name=="BREAKOUT_HIGH":
        value=c/h.shift().rolling(n).max(); flag=value>1
    elif name=="BREAKDOWN_LOW":
        value=c/l.shift().rolling(n).min(); flag=value<1
    elif name in ("TREND_REVERSAL_UP","TREND_REVERSAL_DOWN"):
        value=c/c.rolling(n).mean()-1
        up=name=="TREND_REVERSAL_UP"
        flag=(value>0)&(value.shift()<=0) if up else (value<0)&(value.shift()>=0)
        value=value.where(value.shift().notna())
    elif name=="DRAWDOWN":
        value=c/c.rolling(n).max()-1;flag=value<-threshold
    else:
        raise ValueError("unknown market event")
    valid=value.notna()&np.isfinite(value)
    return flag&valid,valid


def event_gate(bars,spec,dates):
    index=bars.index
    if not spec["events"]:
        yes=pd.Series(True,index=index)
        return yes,yes
    masks=[];valid=[]
    for condition in spec["events"]:
        name=condition["name"]
        if name in MARKET_EVENTS:
            mask,ok=market_event(bars,condition)
        elif name in events.CALENDAR:
            mask=pd.Series(events.masks(index,{})[name,1],index=index)
            ok=pd.Series(True,index=index)
        else:
            # Do not use the same-day opening or a future event. The signal
            # becomes available on a completed bar strictly after publication.
            published=pd.DatetimeIndex(dates[name])
            published=published[published>=index[0]]
            positions=index.searchsorted(published,side="right")+condition["offset"]-1
            values=np.zeros(len(index),dtype=bool)
            values[positions[positions<len(index)]]=True
            mask=pd.Series(values,index=index)
            ok=pd.Series(True,index=index)
        if condition["lookback"]>1:
            window=condition["lookback"]
            mask=mask.rolling(window,min_periods=window).max().eq(1)
            ok=ok.rolling(window,min_periods=window).min().eq(1)
        masks.append(mask);valid.append(ok)
    ready=pd.concat(valid,axis=1).all(axis=1)
    return combine(masks,spec["event_logic"])&ready,ready


def evaluate(db,asset,market,current,spec,lane,dates,cutoffs):
    identity=research.digest([VERSION,spec])
    if db.execute("SELECT 1 FROM attempts WHERE version=? AND asset=? AND id=?",(VERSION,asset,identity)).fetchone():
        return "covered_existing",identity
    macro=tuple(e["name"] for e in spec["events"] if e["name"] in events.FAMILIES)
    if any(not dates.get(e) or not cutoffs.get(e) for e in macro):
        return "blocked_event_data",identity
    if macro:
        current=current.loc[current.index<=pd.Timestamp(min(cutoffs[e] for e in macro))]
    event_warm=[2*e.get("window",1)+e["lookback"]+3 for e in spec["events"] if e["name"] in MARKET_EVENTS]
    warm=max([spec["risk"]["atr_window"]+3]+[rule_warmup(r) for r in spec["rules"]]+event_warm)+spec["confirmation"]+(
        len(spec["rules"]) if spec["logic"]=="sequence" else 1)
    if len(current)<warm+MIN_BARS: return "blocked_history",identity
    boundary=warm+(len(current)-warm)*3//4
    target,valid=technical_target(current,spec)
    if any(valid.iloc[a:b].mean()<.8 for a,b in ((warm,boundary),(boundary,len(current)))):
        return "blocked_undefined_indicator",identity
    raw,event_valid=event_gate(current,spec,dates)
    valid=valid&event_valid
    if any(valid.iloc[a:b].mean()<.8 for a,b in ((warm,boundary),(boundary,len(current)))):
        return "blocked_undefined_event",identity
    control=target.where(valid,0.)
    if spec["events"]:
        # Count distinct event episodes at executable next opens, not an
        # expanded lookback pulse's individual bars.
        starts=(raw&~raw.shift(fill_value=False)).shift(1,fill_value=False)
        absent=((~raw)&valid).shift(1,fill_value=False)
        if any(starts.iloc[a:b-1].sum()<MIN_EVENTS for a,b in ((warm,boundary),(boundary,len(current)))):
            return "blocked_event_occurrences",identity
        if any(absent.iloc[a:b-1].sum()<MIN_EVENTS for a,b in ((warm,boundary),(boundary,len(current)))):
            return "blocked_event_control",identity
        target=control.where(raw if spec["event_mode"]=="only" else ~raw,0.)
    train,train_path=foundry.execute(current,target,warm,boundary,spec["risk"],market.cost_bps)
    check,check_path=foundry.execute(current,target,boundary,len(current),spec["risk"],market.cost_bps)
    evidence={"discovery":train_path,"validation":check_path}
    evidence["sha256"]=research.digest(evidence)
    d={"pass":False,"metrics":train,"return_evidence":evidence,"paper_eligible":False,
       "calendar_evidence":{e:{"sha256":research.digest(dates[e]),"cutoff":cutoffs[e]} for e in macro},
       "valid_signal_fraction":float(valid.iloc[warm:boundary].mean())}
    v={"metrics":check,"retrospective_only":True,"paper_eligible":False,
       "valid_signal_fraction":float(valid.iloc[boundary:].mean())}
    if spec["events"]:
        for obj,a,b in ((d,warm,boundary),(v,boundary,len(current))):
            baseline,_=foundry.execute(current,control,a,b,spec["risk"],market.cost_bps)
            obj["matched_control"]=baseline
            obj["tagged_bars"]=int(raw.iloc[a:b].sum())
            obj["executable_event_episodes"]=int(starts.iloc[a:b-1].sum())
    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (VERSION,asset,identity,"combination:"+spec["frame"]+":"+lane,research.canonical(spec),market.source,
         data.fingerprint(current),current.index[-1].date().isoformat(),os.getenv("GITHUB_SHA","local"),
         research.canonical(POLICY),research.canonical(d),research.canonical(v)))
    return "evaluated",identity


def cursor(db,asset,frame,lane):
    row=db.execute("SELECT next_rank FROM combination_cursor WHERE asset=? AND frame=? AND lane=? AND version=?",
                  (asset,frame,lane,VERSION)).fetchone()
    return int(row[0]) if row else 0


def set_cursor(db,asset,frame,lane,position):
    db.execute("INSERT OR REPLACE INTO combination_cursor VALUES (?,?,?,?,?)",
               (asset,frame,lane,VERSION,str(position)))


def run_lane(db,asset,market,current,frame,lane,dates,cutoffs,token):
    args=(asset,frame,lane,VERSION)
    p=cursor(db,asset,frame,lane)
    limit=total(frame,lane)
    pending_count=db.execute("SELECT count(*) FROM combination_pending WHERE asset=? AND frame=? AND lane=? AND version=?",args).fetchone()[0]
    retry=db.execute("""SELECT rank FROM combination_pending WHERE asset=? AND frame=? AND lane=? AND version=?
                       AND token<>? ORDER BY checked,rank LIMIT 1""",(*args,token)).fetchone()
    def inspect(rank,is_retry):
        spec=plan(asset,frame,lane,rank)
        status,identity=evaluate(db,asset,market,current,spec,lane,dates,cutoffs)
        if status.startswith("blocked_"):
            db.execute("INSERT OR REPLACE INTO combination_pending VALUES (?,?,?,?,?,?,?,?)",
                       (*args,str(rank),status,token,datetime.now(UTC).isoformat()))
        elif is_retry:
            db.execute("DELETE FROM combination_pending WHERE asset=? AND frame=? AND lane=? AND version=? AND rank=?",(*args,str(rank)))
        if not is_retry:
            set_cursor(db,asset,frame,lane,p+1)
        return {"status":status,"rank":str(rank),"id":identity,
                "indicators":[r["family"] for r in spec["rules"]],"events":[e["name"] for e in spec["events"]]}
    attempted=None
    if retry:
        attempted=inspect(int(retry[0]),True)
        if not attempted["status"].startswith("blocked_"):
            return attempted
    if limit is not None and p>=limit:
        return {"status":"waiting_blocked" if pending_count else "catalog_complete","retry":attempted}
    if pending_count>=MAX_PENDING:
        return {"status":"blocked_pending_capacity","pending":pending_count,"retry":attempted}
    result=inspect(p,False)
    if attempted: result["retry"]=attempted
    return result


def run_asset(db,asset,market,bars,dates,cutoffs,deadline=None,event_enabled=True):
    db.execute(SCHEMA);db.execute(PENDING)
    output={}
    for frame in FRAMES:
        current=bars if frame=="1d" else coverage.weekly(bars)
        if deadline is not None and time.monotonic()>=deadline:
            output[frame]={"status":"deferred_runtime"};continue
        if len(current)<MIN_BARS+5:
            set_cursor(db,asset,frame,"technical_visit",cursor(db,asset,frame,"technical_visit"))
            output[frame]={"status":"blocked_history","available_bars":len(current)};continue
        # Two technical lanes plus one event lane per visit. A blocked event
        # cannot consume the budget of the indicator-only queues.
        v=cursor(db,asset,frame,"technical_visit")
        evt=cursor(db,asset,frame,"event_visit")
        tech=[TECH_SCHEDULE[(v+i)%len(TECH_SCHEDULE)] for i in range(2)]
        scheduled=tech+([EVENT_SCHEDULE[evt%len(EVENT_SCHEDULE)]] if event_enabled else [])
        price_token=data.fingerprint(current)
        event_token=research.digest([price_token,dates,cutoffs])
        results={}
        for lane in scheduled:
            if deadline is not None and time.monotonic()>=deadline:
                results[lane]={"status":"deferred_runtime"}
                break
            results[lane]=run_lane(db,asset,market,current,frame,lane,dates,cutoffs,
                                   price_token if lane in tech else event_token)
            if lane in tech: v+=1
            else: evt+=1
        set_cursor(db,asset,frame,"technical_visit",v)
        if event_enabled: set_cursor(db,asset,frame,"event_visit",evt)
        output[frame]=results
    return output


def summary(db):
    """Compact auditable debt; arbitrary-precision counts are decimal strings."""
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='combination_cursor'").fetchone()
    if not exists: return []
    registered={f:sum(total(f,l) for l in LANES if l not in SETTINGS) for f in FRAMES}
    core={f:sum(total(f,l) for l in CORE_LANES) for f in FRAMES}
    pending={}
    for a,f,l,n in db.execute("SELECT asset,frame,lane,count(*) FROM combination_pending WHERE version=? GROUP BY asset,frame,lane",(VERSION,)):
        pending[a,f,l]=n
    result={}
    for a,f,l,p in db.execute("SELECT asset,frame,lane,next_rank FROM combination_cursor WHERE version=?",(VERSION,)):
        item=result.setdefault((a,f),{"asset":a,"frame":f,"covered_templates":0,"blocked":0,
                                     "core_covered":0,"core_blocked":0,
                                     "settings_attempted":0,"settings_blocked":0})
        if l not in LANES: continue
        b=pending.get((a,f,l),0)
        if l in SETTINGS:
            item["settings_attempted"]+=int(p)-b
            item["settings_blocked"]+=b
        else:
            item["covered_templates"]+=int(p)-b
            item["blocked"]+=b
            if l in CORE_LANES:
                item["core_covered"]+=int(p)-b
                item["core_blocked"]+=b
    for (a,f),item in result.items():
        count=registered[f]
        covered=item["covered_templates"]
        item.update(registered_templates=str(count),covered_templates=str(covered),
                    not_covered=str(count-covered),core_registered=core[f],
                    core_not_covered=core[f]-item["core_covered"])
    return sorted(result.values(),key=lambda r:(r["asset"],r["frame"]))

def external_scope():
    """Report channels whose source-specific evidence is not in the main OHLCV join."""
    from . import causal_discovery, extended_flow
    entries=[{"market":market,"channel":channel,"source":definition[2],
              "status":"separate_source_workflow_not_joined"}
             for market,channels in causal_discovery.CHANNELS.items() for channel,definition in channels.items()]
    entries.extend({"market":"CRYPTO","channel":name,"source":"BINANCE_SPOT_1H",
                    "status":"separate_hourly_workflow_not_joined"} for name in extended_flow.FAMILIES)
    return entries
