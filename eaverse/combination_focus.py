"""Persistent single-asset focus for the shared combination catalogue.

Only the common combination worker is focused. PAPER, ordinary discovery and
independent sources retain their own schedules. Foundation completion requires
every core design to be visited; blocked or unavailable data stays explicit.
"""
from __future__ import annotations

import time
from datetime import timedelta
from itertools import zip_longest

from . import combination_debt as debt, combination_research as cr, coverage, data, research

SECONDS = 180
MAX_SLOTS = 2048
REVISIT_EVERY = 4
STATE = """CREATE TABLE IF NOT EXISTS combination_focus_state(
 version TEXT PRIMARY KEY,asset TEXT,mode TEXT,assignments INTEGER NOT NULL)"""
HISTORY = """CREATE TABLE IF NOT EXISTS combination_focus_history(
 version TEXT NOT NULL,asset TEXT NOT NULL,status TEXT NOT NULL,
 core_visited INTEGER NOT NULL,core_registered INTEGER NOT NULL,
 core_blocked INTEGER NOT NULL,last_day TEXT NOT NULL,due TEXT NOT NULL,
 PRIMARY KEY(version,asset))"""


def initialize(db):
    db.execute(STATE)
    db.execute(HISTORY)
    db.execute(cr.SCHEMA)
    db.execute(cr.PENDING)
    debt.initialize(db)


def ordered_assets(catalogs):
    seeds, rest = [], []
    for market in data.MARKETS:
        symbols = set(catalogs.get(market.name, ()))
        seeds.append([market.name+":"+s for s in market.symbols if s in symbols])
        rest.append([market.name+":"+s for s in sorted(symbols-set(market.symbols))])
    return [asset for groups in (seeds,rest) for row in zip_longest(*groups)
            for asset in row if asset is not None]


def progress(db, asset):
    pending = debt.counts(db, cr.VERSION)
    frames = {}
    for frame in cr.FRAMES:
        total = sum(cr.total(frame,lane) for lane in cr.CORE_LANES)
        visited = sum(cr.cursor(db,asset,frame,lane) for lane in cr.CORE_LANES)
        blocked = sum(pending.get((asset,frame,lane),0) for lane in cr.CORE_LANES)
        frames[frame] = {"registered":total,"visited":visited,"blocked":blocked,
                         "evaluated":visited-blocked,"unvisited":total-visited}
    return frames


def totals(frames):
    return {key:sum(row[key] for row in frames.values())
            for key in ("registered","visited","blocked","evaluated","unvisited")}


def select(db, catalogs, today):
    initialize(db)
    candidates = ordered_assets(catalogs)
    state = db.execute("SELECT asset,mode,assignments FROM combination_focus_state WHERE version=?",
                       (cr.VERSION,)).fetchone()
    current,mode,assignments = tuple(state) if state else (None,None,0)
    if current in candidates:
        return current,mode
    if current and current.split(":",1)[0] not in catalogs:
        # A manual market-filtered run cannot displace the persistent focus.
        return None,None
    if current:
        stamp = totals(progress(db,current))
        record(db,current,"waiting_listing",stamp,today,7)
    rows = {r["asset"]:r for r in db.execute(
        "SELECT * FROM combination_focus_history WHERE version=?", (cr.VERSION,))}
    fresh = [a for a in candidates if a not in rows]
    due = sorted((a for a in candidates if a in rows and rows[a]["due"]<=today.isoformat()),
                 key=lambda a:(rows[a]["last_day"],a))
    # Dedicated foundation first; periodic continuation/debt visits prevent
    # earlier assets being forgotten while the broad universe grows.
    pool = (due or fresh) if (assignments and assignments%REVISIT_EVERY==0) else (fresh or due)
    if not pool:
        db.execute("INSERT OR REPLACE INTO combination_focus_state VALUES (?,?,?,?)",
                   (cr.VERSION,None,None,assignments))
        return None,None
    asset=pool[0]
    mode="continuation" if asset in rows and rows[asset]["core_visited"]==rows[asset]["core_registered"] else "foundation"
    db.execute("INSERT OR REPLACE INTO combination_focus_state VALUES (?,?,?,?)",
               (cr.VERSION,asset,mode,assignments+1))
    return asset,mode


def record(db,asset,status,stamp,today,delay):
    db.execute("INSERT OR REPLACE INTO combination_focus_history VALUES (?,?,?,?,?,?,?,?)",
               (cr.VERSION,asset,status,stamp["visited"],stamp["registered"],stamp["blocked"],
                today.isoformat(),(today+timedelta(days=delay)).isoformat()))


def release(db,asset,status,today,delay=1):
    stamp=totals(progress(db,asset))
    record(db,asset,status,stamp,today,delay)
    db.execute("UPDATE combination_focus_state SET asset=NULL,mode=NULL WHERE version=? AND asset=?",
               (cr.VERSION,asset))
    return stamp


def retry_available(db,asset,frame,lane,token):
    args=(asset,frame,lane,cr.VERSION)
    debt.migrate_lane(db,args)
    if not debt.count(db,args):
        return False
    row=db.execute("SELECT next_rank,stop_rank,token FROM combination_debt_sweep WHERE "+debt.KEY,args).fetchone()
    return not row or int(row[0])<int(row[1]) or row[2]!=token


def batch(db,asset,market,bars,dates,cutoffs,*,deadline,max_slots=MAX_SLOTS,event_enabled=True,mode="foundation"):
    initialize(db)
    frames={"1d":bars,"1w":coverage.weekly(bars)}
    usable={f:b for f,b in frames.items() if len(b)>=cr.MIN_BARS+5}
    tokens={}
    for f,b in usable.items():
        price=data.fingerprint(b)
        tokens[f]=(price,research.digest([price,dates,cutoffs]))
    pairs=[(f,l) for f in usable for l in cr.LANES if event_enabled or l in cr.TECH_LANES]
    core=[(f,l) for f,l in pairs if l in cr.CORE_LANES]
    extended=[(f,l) for f,l in pairs if l not in cr.CORE_LANES]
    cv=cr.cursor(db,asset,"1d","focus_core_visit")
    ev=cr.cursor(db,asset,"1d","focus_extension_visit")
    stats={"asset":asset,"mode":mode,"slots":0,"evaluated":0,"covered_existing":0,
           "blocked":0,"lanes":{},"unavailable_frames":{f:len(b) for f,b in frames.items() if f not in usable}}
    for _ in range(max_slots):
        if time.monotonic()>=deadline:
            break
        unvisited=[(f,l) for f,l in core if cr.cursor(db,asset,f,l)<cr.total(f,l)]
        if mode=="foundation" and not unvisited:
            break
        singles=[(f,l) for f,l in unvisited if l in ("single","event_single")]
        preferred=singles or unvisited
        use_extension=(not preferred or (cv+ev+1)%5==0)
        if use_extension:
            available=[]
            for f,l in extended:
                limit=cr.total(f,l)
                p=cr.cursor(db,asset,f,l)
                if limit is None or p<limit or retry_available(db,asset,f,l,tokens[f][int(l in cr.EVENT_LANES)]):
                    available.append((f,l))
            for f,l in core:
                p=cr.cursor(db,asset,f,l)
                if p>=cr.total(f,l) and retry_available(db,asset,f,l,tokens[f][int(l in cr.EVENT_LANES)]):
                    available.append((f,l))
            # During the singles phase, also advance pairs, alongside larger designs.
            if singles:
                available.extend((f,l) for f,l in unvisited if (f,l) not in singles)
            if available:
                frame,lane=available[ev%len(available)]
                ev+=1
            elif preferred:
                frame,lane=preferred[cv%len(preferred)]
                cv+=1
            else:
                break
        else:
            frame,lane=preferred[cv%len(preferred)]
            cv+=1
        db.execute("SAVEPOINT focus_slot")
        try:
            result=cr.run_lane(db,asset,market,usable[frame],frame,lane,dates,cutoffs,
                               tokens[frame][int(lane in cr.EVENT_LANES)])
        except Exception as exc:
            db.execute("ROLLBACK TO focus_slot")
            db.execute("RELEASE focus_slot")
            stats.update(status="error",error=f"{type(exc).__name__}: {exc}"[:300])
            break
        db.execute("RELEASE focus_slot")
        # Advance the scheduling counters only when the durable trial/debt did.
        cr.set_cursor(db,asset,"1d","focus_core_visit",cv)
        cr.set_cursor(db,asset,"1d","focus_extension_visit",ev)
        stats["slots"]+=1
        status=result["status"]
        stats["evaluated"]+=int(status=="evaluated")
        stats["covered_existing"]+=int(status=="covered_existing")
        stats["blocked"]+=int(status.startswith("blocked_"))+int(bool(result.get("retry",{})) and result["retry"]["status"].startswith("blocked_"))
        group=stats["lanes"].setdefault(frame+":"+lane,{"slots":0,"evaluated":0,"blocked":0})
        group["slots"]+=1
        group["evaluated"]+=int(status=="evaluated")
        group["blocked"]+=int(status.startswith("blocked_"))
    stamp=progress(db,asset)
    stats["foundation"]=stamp
    stats["foundation_total"]=totals(stamp)
    remaining=any(cr.cursor(db,asset,f,l)<cr.total(f,l) for f,l in core)
    if stats.get("status")!="error":
        stats["status"]=("continuation_slice" if mode=="continuation" and usable else
                         "in_progress" if remaining else
                         "foundation_scanned" if stats["foundation_total"]["unvisited"]==0 else
                         "waiting_frame_or_event_data")
    return stats


def complete_slice(db,asset,result,today):
    status=result["status"]
    stamp=result["foundation_total"]
    if status=="in_progress":
        record(db,asset,status,stamp,today,0)
    elif status=="foundation_scanned":
        # A scanned catalogue with blockers is not a completed catalogue.
        release(db,asset,"foundation_waiting_data" if stamp["blocked"] else "foundation_evaluated",today)
    elif status=="continuation_slice":
        release(db,asset,"continuation_waiting_data" if stamp["blocked"] else "continuation",today)
    else:
        release(db,asset,status,today,7 if status=="waiting_frame_or_event_data" else 1)


def dashboard(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='combination_focus_state'").fetchone():
        return {}
    row=db.execute("SELECT asset,mode,assignments FROM combination_focus_state WHERE version=?",(cr.VERSION,)).fetchone()
    if not row:
        return {}
    asset,mode,assignments=row
    statuses={r[0]:r[1] for r in db.execute("SELECT status,count(*) FROM combination_focus_history WHERE version=? GROUP BY status",(cr.VERSION,))}
    return {"active_asset":asset,"mode":mode,"assignments":assignments,
            "foundation":progress(db,asset) if asset else {}, "asset_status_counts":statuses}
