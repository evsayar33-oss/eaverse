"""Run one checkpointable discovery and forward-simulation cycle."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import cohort, combination_focus, combination_research, conditional_paper, coverage, cross_asset_research, data, events, foundry, history_sources, indicator_research, model_research, paper_admission, paper_archive, qualification, reference, research, universe


SCHEMA = """
CREATE TABLE IF NOT EXISTS extended_checks(sequence INTEGER PRIMARY KEY, result TEXT);
CREATE TABLE IF NOT EXISTS direction_checks(asset TEXT, id TEXT, snapshot TEXT, result TEXT, PRIMARY KEY(asset,id));
CREATE TABLE IF NOT EXISTS idea_documents(repo TEXT,sha TEXT,PRIMARY KEY(repo,sha));
CREATE TABLE IF NOT EXISTS source_ideas(repo TEXT,sha TEXT,id TEXT,spec TEXT,PRIMARY KEY(repo,sha,id));
CREATE TABLE IF NOT EXISTS attempts (
    version TEXT NOT NULL, asset TEXT NOT NULL, id TEXT NOT NULL, family TEXT NOT NULL,
    spec TEXT NOT NULL, source TEXT NOT NULL, snapshot TEXT NOT NULL, asof TEXT NOT NULL,
    code TEXT NOT NULL, policy TEXT NOT NULL, discovered TEXT NOT NULL, validated TEXT,
    PRIMARY KEY(version, asset, id)
);
CREATE TABLE IF NOT EXISTS paper (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL, spec TEXT NOT NULL, cost INTEGER NOT NULL, start TEXT NOT NULL,
    anchor TEXT NOT NULL, cutoff TEXT NOT NULL, digest TEXT NOT NULL,
    cursor TEXT, history_digest TEXT, version TEXT NOT NULL, policy TEXT NOT NULL,
    origin TEXT NOT NULL, status TEXT NOT NULL, result TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_paper_per_asset ON paper(asset) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS paper_progress (
    sequence INTEGER PRIMARY KEY, metrics TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_candle_archive (
    sequence INTEGER NOT NULL, through_day TEXT NOT NULL, first_day TEXT NOT NULL,
    source TEXT NOT NULL, recorded_at TEXT NOT NULL, prefix_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(sequence,through_day)
);
CREATE TABLE IF NOT EXISTS paper_data_issues (asset TEXT PRIMARY KEY, error TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS health (
    asset TEXT PRIMARY KEY, failures INTEGER NOT NULL, episode INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    market TEXT PRIMARY KEY, source TEXT NOT NULL, asof TEXT NOT NULL, checked TEXT NOT NULL, symbols TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_rotation (
    market TEXT NOT NULL, symbol TEXT NOT NULL, visited TEXT NOT NULL, due TEXT NOT NULL, failures INTEGER NOT NULL,
    error TEXT, PRIMARY KEY(market,symbol)
);
CREATE TABLE IF NOT EXISTS event_catalog (
    family TEXT PRIMARY KEY, checked TEXT NOT NULL, asof TEXT NOT NULL, dates TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS event_calendar_versions (
    sha TEXT PRIMARY KEY, dates TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_trials (
    version TEXT NOT NULL, asset TEXT NOT NULL, id TEXT NOT NULL, snapshot TEXT NOT NULL,
    asof TEXT NOT NULL, calendar TEXT NOT NULL, summary TEXT NOT NULL, PRIMARY KEY(version,asset,id)
);
CREATE TABLE IF NOT EXISTS synthesis_parents (
    asset TEXT NOT NULL, id TEXT NOT NULL, spec TEXT NOT NULL, score REAL NOT NULL,
    PRIMARY KEY(asset,id)
);
CREATE INDEX IF NOT EXISTS synthesis_score ON synthesis_parents(score DESC);
CREATE TABLE IF NOT EXISTS history_coverage (
    asset TEXT NOT NULL, source TEXT NOT NULL, checked TEXT NOT NULL,
    first_bar TEXT NOT NULL, last_bar TEXT NOT NULL, bars INTEGER NOT NULL,
    status TEXT NOT NULL, snapshot TEXT NOT NULL,
    PRIMARY KEY(asset,source)
);
CREATE TABLE IF NOT EXISTS history_archives (
    sha TEXT PRIMARY KEY, url TEXT NOT NULL, records INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY, message TEXT NOT NULL, receipt TEXT
);
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.executescript(paper_admission.SCHEMA)
    db.executescript(conditional_paper.SCHEMA)
    return db


def enqueue(db, key, message):
    db.execute("INSERT OR IGNORE INTO outbox(id, message) VALUES (?, ?)", (key, message))


def health(db, asset, error):
    prior = db.execute("SELECT * FROM health WHERE asset = ?", (asset,)).fetchone()
    failures, episode = (prior["failures"], prior["episode"]) if prior else (0, 1)
    failures, episode = (failures + 1, episode) if error else (0, episode + (failures >= 2))
    db.execute("INSERT OR REPLACE INTO health VALUES (?, ?, ?)", (asset, failures, episode))
    if failures == 2:
        enqueue(db, f"data:{asset}:{episode}",
                f"⚠️ Eaverse · {asset}\nVeri veya PAPER sürekliliği iki ardışık taramada doğrulanamadı.\n{error}")


def date(index_value):
    return index_value.strftime("%Y-%m-%d")


def update_pending_paper_policy(db, now):
    # The initial checkpoint may contain proposals from the first publication.
    # Upgrade only experiments whose first eligible opening has not occurred.
    previous = {key: value for key, value in research.LEGACY_POLICY.items() if key != "paper_execution_delay_bars"}
    db.execute(
        "UPDATE paper SET policy=? WHERE status='active' AND cursor IS NULL AND start > ? AND policy=?",
        (research.canonical(research.LEGACY_POLICY), now.date().isoformat(), research.canonical(previous)),
    )


def recheck_pending(db, asset, market, bars, now):
    """Requalify never-started experiments without retrospectively starting PAPER."""
    row = db.execute("""SELECT * FROM paper WHERE asset=? AND status='active'
                        AND cursor IS NULL AND sequence NOT IN (SELECT sequence FROM paper_progress)""",
                     (asset,)).fetchone()
    if not row or row["version"] != research.VERSION or row["policy"] not in (research.canonical(research.POLICY),research.canonical(research.LEGACY_POLICY)):
        return
    window = bars.loc[row["anchor"]:row["cutoff"]]
    policy_upgrade=row["policy"]==research.canonical(research.LEGACY_POLICY)
    if data.fingerprint(window) == row["digest"] and not policy_upgrade:
        return
    origin = json.loads(row["origin"])
    if origin.get("replaces") and not policy_upgrade:
        # A replacement is allowed once, not forever. Preserve both the
        # original and replacement records and release this unusable slot.
        evidence={"reason":"repeated_origin_revision_before_first_observation",
                  "checked_at":now.isoformat(),"forward_validation_complete":False}
        db.execute("UPDATE paper SET status='paused_integrity',result=? WHERE sequence=?",
                   (research.canonical(evidence),row["sequence"]))
        return
    history = bars.loc[:row["cutoff"]]
    if len(history) < 600 or date(history.index[-1]) != row["cutoff"]:
        return
    spec = json.loads(row["spec"])
    periods = origin.get("periods", 365)
    weights = research.target(history, research.features(history, [spec]), spec, periods)
    revised = {}
    for label in ("discovery", "validation"):
        block = origin.get(label, {})
        if not block.get("start") or not block.get("end"):
            return
        dates = history.loc[block["start"]:block["end"]].index
        if len(dates) < 180 or date(dates[0]) != block["start"] or date(dates[-1]) != block["end"]:
            return
        revised[label] = research.history_metrics(history, weights, dates, row["cost"], periods)
    # A historical revision must not manufacture a prospective observation.
    # Keep the original row and restart from a future opening, even if the
    # previously scheduled opening has already elapsed.
    revised["validation"]["execution_audit"] = reference.audit_validation(
        bars.loc[:row["cutoff"]], weights, row["cost"], revised["validation"])
    accepted = all(block["pass"] for block in revised.values()) and revised["validation"]["execution_audit"]["status"] != "mismatch"
    evidence = {"reason": "recovery_policy_upgrade_before_first_observation" if policy_upgrade else "origin_revision_before_first_observation", "revalidation": revised,
                "checked_at": now.isoformat(), "replacement_eligible": accepted}
    db.execute("UPDATE paper SET status='revised_before_start',result=? WHERE sequence=?",
               (research.canonical(evidence), row["sequence"]))
    if accepted:
        start = max(row["start"], (now.date() + timedelta(days=1)).isoformat())
        if market.calendar:
            import exchange_calendars as xcals
            start = date(xcals.get_calendar(market.calendar).date_to_session(start, direction="next"))
        anchor = history.iloc[-400:]
        origin.update(revised, replaces=row["sequence"], data_start=date(history.index[0]),
                      data_sha256=data.fingerprint(history), code=os.getenv("GITHUB_SHA", "local"),
                      recovery=evidence)
        inserted=db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                      VALUES(?,?,?,?,?,?,?,?,?,?,'active')""",
                   (asset,row["spec"],row["cost"],start,date(anchor.index[0]),row["cutoff"],
                    data.fingerprint(anchor),row["version"],research.canonical(research.POLICY),research.canonical(origin)))
        frozen=db.execute("SELECT * FROM paper WHERE sequence=?",(inserted.lastrowid,)).fetchone()
        paper_archive.capture(db,frozen,anchor)


def extend_active_history(db, asset, bars, market):
    row = db.execute("""SELECT * FROM paper WHERE asset=? AND status='active'
                        AND sequence NOT IN (SELECT sequence FROM extended_checks)""", (asset,)).fetchone()
    if not row:
        return
    origin = json.loads(row["origin"])
    history = bars.loc[:row["cutoff"]]
    if not len(history) or date(history.index[0]) >= origin.get("data_start", ""):
        return
    spec = json.loads(row["spec"])
    periods = origin.get("periods", 365)
    desired = research.target(history, research.features(history, [spec]), spec, periods)
    dates = history.index[200:-research.MIN_VALIDATION_BARS-4]
    if len(dates) < 180:
        return
    result = research.history_metrics(history, desired, dates, market.cost_bps, periods)
    db.execute("INSERT INTO extended_checks VALUES (?,?)", (row["sequence"], research.canonical(result)))


def advance_paper(db, asset, bars):
    row = db.execute("SELECT * FROM paper WHERE asset = ? AND status = 'active'", (asset,)).fetchone()
    if row is None:
        return None
    if row["version"] != research.VERSION or row["policy"] not in (research.canonical(research.POLICY),research.canonical(research.LEGACY_POLICY)):
        raise ValueError("PAPER engine version changed; frozen experiment needs its original engine")
    anchored = bars.loc[row["anchor"]:]
    if data.fingerprint(anchored.loc[:row["cutoff"]]) != row["digest"]:
        raise ValueError("PAPER origin candles were revised or are missing")
    if row["cursor"] and data.fingerprint(anchored.loc[:row["cursor"]]) != row["history_digest"]:
        raise ValueError("previously observed PAPER candles were revised or are missing")
    # Archive only an already verified prefix. This never rewrites PAPER or
    # permits a source revision to pass the original digest checks.
    verified_day = row["cursor"] or row["cutoff"]
    paper_archive.capture(db, row, anchored.loc[:verified_day])
    observed = anchored.loc[row["start"]:]
    if not len(observed):
        return {"status": "waiting_for_next_open", "sequence": row["sequence"], "bars": 0}
    if date(observed.index[0]) != row["start"]:
        raise ValueError("PAPER opening date is missing")
    stop = observed.index[min(research.PAPER_BARS, len(observed) - 1)]
    spec = json.loads(row["spec"])
    periods = json.loads(row["origin"]).get("periods", 365)
    desired = research.target(anchored, research.features(anchored, [spec]), spec, periods)
    days = len(observed.loc[:stop]) - 1
    if days:
        metrics, returns, benchmark = research.paper_metrics(
            anchored, desired, row["start"], stop, row["cost"], row["sequence"],
            final=days >= research.PAPER_BARS, periods=periods,frozen_policy=json.loads(row["policy"]),
        )
        observed_opens = observed.loc[:stop].index[1:]
        progress = {
            "bars": days, "latest_open": date(stop), "stats": metrics,
            "curve": [{"date": row["start"], "strategy": 0.0, "benchmark": 0.0}] + [
                {"date": date(day), "strategy": float(strategy), "benchmark": float(reference)}
                for day, strategy, reference in zip(
                    observed_opens, (1 + returns).cumprod() - 1, (1 + benchmark).cumprod() - 1, strict=True
                )
            ],
            "median_quote_volume_30": float((observed.close * observed.volume).tail(30).median()),
        }
        db.execute("INSERT OR REPLACE INTO paper_progress VALUES (?,?)",
                   (row["sequence"], research.canonical(progress)))
    paper_archive.capture(db, row, anchored.loc[:stop])
    db.execute("UPDATE paper SET cursor=?, history_digest=? WHERE sequence=?",
               (date(stop), data.fingerprint(anchored.loc[:stop]), row["sequence"]))
    if days < research.PAPER_BARS:
        return {"status": "active", "sequence": row["sequence"], "bars": days}
    status = "verified_paper" if metrics["pass"] else "paper_gate_failed"
    result = {
        **metrics, "start": row["start"], "end": date(stop), "origin": json.loads(row["origin"]),
        "data_sha256": data.fingerprint(anchored.loc[:stop]),
        "day_net": [float(r) for r in returns], "day_benchmark": [float(r) for r in benchmark],
    }
    db.execute("UPDATE paper SET status=?, result=? WHERE sequence=?",
               (status, research.canonical(result), row["sequence"]))
    if metrics["pass"]:
        enqueue(
            db, f"paper:{row['sequence']}",
            f"✅ Eaverse · PAPER doğrulaması · {asset}\n"
            f"{row['start']} – {date(stop)} · {days} gün · {metrics['trades']} tamamlanan işlem\n"
            f"Net %{100 * metrics['net']:.2f} · al-tut %{100 * metrics['benchmark_net']:.2f} · "
            f"maksimum düşüş %{100 * metrics['drawdown']:.2f}\n"
            f"Hipotez: {result['origin']['id'][:12]} · PAPER #{row['sequence']} · "
            f"p={metrics['p_value']:.3g}, eşik={metrics['alpha']:.3g}\n"
            f"{os.environ.get('EAVERSE_EVIDENCE_URL', '')}\n"
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/releases/tag/research-state-v1",
        )
    return {"status": status, "sequence": row["sequence"], "bars": days, **metrics}


def discover(db, asset, market, bars, now, budget):
    records = list(db.execute("SELECT id,family,discovered FROM attempts WHERE asset=? AND version=?",
                              (asset, research.VERSION)))
    learning = {}
    for record in records:
        n, wins = learning.get(record["family"], (0, 0))
        learning[record["family"]] = (n + 1, wins + int(json.loads(record["discovered"])["pass"]))
    own = list(db.execute(
        "SELECT spec FROM synthesis_parents WHERE asset=? ORDER BY score DESC LIMIT 4", (asset,)))
    peers = list(db.execute(
        "SELECT spec FROM synthesis_parents WHERE asset<>? AND asset LIKE ? ORDER BY score DESC LIMIT 4",
        (asset, market.name + ":%")))
    # Only discovery-fold successes can seed changes or cross-asset transfer.
    out_of_market = list(db.execute(
        "SELECT spec FROM synthesis_parents WHERE asset NOT LIKE ? ORDER BY score DESC LIMIT 2",
        (market.name + ":%",)))
    parents = [json.loads(spec) for spec in dict.fromkeys(row["spec"] for row in own + peers + out_of_market)]
    from . import ideas
    sourced = ideas.pending(db, asset, budget // 4)
    transfer_sources = {}
    for source, key, raw in db.execute("""
        SELECT p.asset,p.id,p.spec FROM synthesis_parents p
        WHERE p.asset<>? AND p.asset LIKE ? AND NOT EXISTS (
          SELECT 1 FROM attempts a WHERE a.asset=? AND a.id=p.id AND a.version=?)
        ORDER BY p.score DESC,p.asset,p.id
    """, (asset, market.name + ":%", asset, research.VERSION)):
        if len(transfer_sources) >= budget // 4:
            break
        if key not in transfer_sources and key not in {k for k,_ in sourced}:
            transfer_sources[key] = source
            sourced.append((key,json.loads(raw)))
    tried = {record["id"] for record in records} | {key for key, _ in sourced}
    selected = sourced + research.expand(asset, tried, learning, parents, budget - len(sourced))
    snapshot = data.fingerprint(bars)
    code = os.getenv("GITHUB_SHA", "local")
    asof = date(bars.index[-1])
    periods = 365 if market.calendar is None else 252
    discovery_end = len(bars) - research.MIN_VALIDATION_BARS - 4
    validation_index = bars.index[-research.MIN_VALIDATION_BARS:]
    discovery_index = bars.index[200:discovery_end]
    signals = research.features(bars, [spec for _, spec in selected])
    candidates = []
    for key, spec in selected:
        desired = research.target(bars, signals, spec, periods)
        report = research.history_metrics(bars, desired, discovery_index, market.cost_bps)
        directions = research.direction_check(bars, desired, spec, signals, discovery_index, validation_index, market.cost_bps, periods)
        directions["source_asset"] = transfer_sources.get(key)
        db.execute("INSERT OR REPLACE INTO direction_checks VALUES (?,?,?,?)",
                   (asset,key,snapshot,research.canonical(directions)))
        family = ",".join(name for name, _ in spec["rules"])
        db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                   (research.VERSION, asset, key, family, research.canonical(spec), market.source,
                    snapshot, asof, code, research.canonical(research.POLICY), research.canonical(report)))
        if report["pass"]:
            candidates.append((key, spec, report))
            db.execute("INSERT OR IGNORE INTO synthesis_parents VALUES (?,?,?,?)",
                       (asset, key, research.canonical(spec), report["net"]))
    db.execute("""DELETE FROM synthesis_parents WHERE asset=? AND id NOT IN (
        SELECT id FROM synthesis_parents WHERE asset=? ORDER BY score DESC,id LIMIT 20
    )""", (asset, asset))
    # One candidate per batch/asset is chosen by discovery evidence before
    # validation. Reuse of historical data makes this a screen. PAPER is prospective.
    nominee = None
    if candidates:
        key, spec, discovery_report = min(candidates, key=research.ranking_key)
        desired = research.target(bars, signals, spec, periods)
        validation = research.history_metrics(bars, desired, validation_index, market.cost_bps, periods)
        discovery_report = research.history_metrics(bars, desired, discovery_index, market.cost_bps, periods)
        from .reference import audit_validation
        validation["execution_audit"] = audit_validation(bars, desired, market.cost_bps, validation)
        db.execute("UPDATE attempts SET validated=? WHERE version=? AND asset=? AND id=?",
                   (research.canonical(validation), research.VERSION, asset, key))
        nominee = {"id": key, "spec": spec, "validation": validation}
        # PAPER nominations are durably persisted from attempts after the
        # central evidence review. Capacity must not silently discard a win.
    return {"new_trials": len(selected), "total_trials": len(records) + len(selected), "candidate": nominee,
            "snapshot_sha256": snapshot, "from": date(bars.index[0]), "asof": asof,
            "paper_eligible": market.source in ("coinbase","yahoo"),
            "history":{"source":market.source,"bars":len(bars),"from":date(bars.index[0]),"through":asof,
                       "status":bars.attrs.get("history_status","legacy_or_fixture"),
                       "requested_start":bars.attrs.get("requested_start"),
                       "latest_daily_refetched":bars.attrs.get("latest_daily_refetched",False),
                       "excluded_history_through":bars.attrs.get("excluded_history_through")}}


def fetch_bars(market, symbol, now):
    try:
        return history_sources.load(market, symbol, now), None
    except Exception as exc:
        # The provider can raise transport and parsing errors from several libraries.
        # Strategy/evidence errors are handled separately and never hidden here.
        return None, f"{type(exc).__name__}: {exc}"[:300]


def process_native_backlog(db,market_names,aligned_panel,aligned_markets,dates,cutoffs,now,seconds=conditional_paper.BACKLOG_SECONDS):
    """Separate data-only lane; one download per asset for all candidate strategies."""
    started=time.monotonic()
    fetch_deadline=started+seconds/3
    markets={m.name:m for m in data.MARKETS if m.name in market_names}
    queues=[]
    for name,market in markets.items():
        active,pending=conditional_paper.priority_assets(db,name,now.date())
        # Interleave active observations and new work; neither can monopolize data.
        ordered=[]
        from itertools import zip_longest
        for a,b in zip_longest(active,pending):
            for symbol in (a,b):
                if symbol and symbol not in ordered:
                    ordered.append(symbol)
        # Materialize while this market/name is bound. A deferred generator
        # would capture the final market and send other symbols to its provider.
        queues.append(iter([(market,symbol) for symbol in ordered if f"{name}:{symbol}" not in aligned_panel]))
    scheduled=[]
    while queues:
        remaining=[]
        for queue in queues:
            item=next(queue,None)
            if item:
                scheduled.append(item);remaining.append(queue)
        queues=remaining
    fetched=0;failed=0
    for offset in range(0,len(scheduled),8):
        if time.monotonic()>=fetch_deadline:
            break
        batch=[(history_sources.market_for_symbol(m,s),s) for m,s in scheduled[offset:offset+8]]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(lambda item:fetch_bars(*item,now),batch))
        for (m,s),(bars,error) in zip(batch,results,strict=True):
            asset=f"{m.name}:{s}"
            if error:
                conditional_paper.observe_unavailable(db,asset,error,now)
                failed+=1
            else:
                aligned_panel[asset]=bars;aligned_markets[asset]=m;fetched+=1
    result=conditional_paper.run(db,aligned_panel,aligned_markets,dates,cutoffs,now,
                                 seconds=max(1,seconds-(time.monotonic()-started)))
    return {**result,"data_only_fetched":fetched,"data_only_unavailable":failed,
            "data_only_remaining":max(0,len(scheduled)-fetched-failed)}
 
 
def run(db, market_names, budget, event_budget=6, asset_limit=None, daily_report=None, *,
        focus_seconds=combination_focus.SECONDS, focus_slots=combination_focus.MAX_SLOTS):
    now = datetime.now(UTC)
    deadline = time.monotonic() + 14 * 60
    first_technical = db.execute('SELECT coalesce(max(rowid),0) FROM attempts').fetchone()[0]
    first_event = db.execute('SELECT coalesce(max(rowid),0) FROM event_trials').fetchone()[0]
    with db:
        update_pending_paper_policy(db, now)
        recovered_nominations=paper_admission.seed(db)
        native_migrated=conditional_paper.migrate_policy(db,now)
        native_harvested=conditional_paper.harvest(db)
        native_archive_recovery=[]
        archive_recovery={"status":"not_requested","added":0}
        if os.getenv("GITHUB_ACTIONS") == "true":
            db.execute("SAVEPOINT candidate_archive")
            try:
                archive_recovery=paper_admission.recover_archives(db,now)
            except Exception as exc:
                db.execute("ROLLBACK TO candidate_archive")
                archive_recovery={"status":"error","error":str(exc)[:200],"added":0}
            finally:
                db.execute("RELEASE candidate_archive")
        if os.getenv("GITHUB_ACTIONS") == "true":
            from . import ideas
            ideas.refresh(db)
        event_dates, event_coverage = events.refresh(db, now.date()) if event_budget else ({}, {})
        calendar_cutoffs = {}
        if event_budget:
            db.execute("INSERT OR IGNORE INTO event_calendar_versions VALUES (?,?)",
                       (research.digest(event_dates), research.canonical(event_dates)))
            calendar_cutoffs = {name: item["fetched"] for name, item in event_coverage.items()}
            stale_calendars = [name for name, limit in calendar_cutoffs.items()
                               if limit < (now.date() - timedelta(days=3)).isoformat()]
            health(db, "macro-calendar", "stale " + ", ".join(stale_calendars) if stale_calendars else None)
        queues = []
        catalogs = {}
        market_reports = {}
        for market in data.MARKETS:
            if market.name not in market_names:
                continue
            symbols, listing = universe.refresh(db, market, now.date())
            listing_asof = listing["listing_date"]
            stale_listing = listing_asof is None or listing_asof < (now.date() - timedelta(days=3)).isoformat()
            health(db, f"listings:{market.name}", "live listing catalog unavailable or stale" if stale_listing else None)
            selected = universe.queue(db, market, symbols, now.date(), asset_limit or universe.LIMITS[market.name])
            priority=paper_admission.priority_assets(db,market.name,now.date())
            active_symbols={r[0].split(":",1)[1] for r in db.execute(
                "SELECT asset FROM paper WHERE status='active' AND asset LIKE ?",(market.name+":%",))}
            # Do not grow the normal scan budget. Queue old qualified ideas
            # ahead of fresh rotation, with a bounded number per market.
            active_first=[s for s in selected if s in active_symbols]
            priority=list(dict.fromkeys(s for s in priority if s in symbols and s not in active_first))
            if priority:
                broad=[s for s in selected if s not in active_first and s not in priority]
                selected=(active_first+priority+broad)[:max(asset_limit or universe.LIMITS[market.name],len(active_first))]
            for symbol in reversed(cohort.active_assets(db, market)):
                if symbol not in selected:
                    # At most one extra research asset, never displace existing PAPER.
                    selected.insert(0, symbol)
            catalogs[market.name] = symbols
            queues.append((market, selected))
            market_reports[market.name] = {**listing, "universe": len(symbols), "scheduled": len(selected),
                                          "screened": 0, "unavailable": 0,
                                          "insufficient_history": 0, "failure_reasons": {}}
        focus_asset,focus_mode=combination_focus.select(db,catalogs,now.date())
        if focus_asset:
            name,symbol=focus_asset.split(":",1)
            for market,selected in queues:
                if market.name==name:
                    priority={r[0].split(":",1)[1] for r in db.execute(
                        "SELECT asset FROM paper WHERE status='active' AND asset LIKE ?",(name+":%",))}
                    priority.update(cohort.active_assets(db,market))
                    if symbol not in priority:
                        if symbol in selected:
                            selected.remove(symbol)
                        offset=sum(s in priority for s in selected)
                        selected.insert(offset,symbol)
                    elif symbol not in selected:
                        selected.insert(0,symbol)
                    market_reports[name]["scheduled"]=len(selected)
    queues=[(market,iter(selected)) for market,selected in queues]
    # Reserve a bounded focus slice; active PAPER is still allowed its
    # original deadline. The broad discovery engines retain their schedules.
    general_deadline=deadline-focus_seconds if focus_asset else deadline
    paper_remaining={r[0] for r in db.execute("SELECT asset FROM paper WHERE status='active'")
                     if r[0].split(":",1)[0] in catalogs}
    focus_data=None
    report = {"time": now.isoformat(), "policy": research.POLICY, "assets": {}, "markets": market_reports,
              "event_sources": event_coverage}
    daily_records = []
    aligned_panel = {}
    aligned_markets = {}
    # Interleave providers and give active PAPER rows the first slots on their market.
    scheduled = []
    while queues:
        next_round = []
        for market, queue in queues:
            symbol = next(queue, None)
            if symbol:
                scheduled.append((market, symbol))
                next_round.append((market, queue))
        queues = next_round
    def execute_focus():
        if focus_data is None:
            report["combination_focus_slice"]={"asset":focus_asset,"status":"deferred_runtime"}
        elif focus_data[2] is not None:
            delay=7 if any(term in focus_data[2] for term in ("fewer than 600","insufficient Coinbase history")) else 1
            with db:
                stamp=combination_focus.release(db,focus_asset,"waiting_source",now.date(),delay)
            report["combination_focus_slice"]={"asset":focus_asset,"status":"waiting_source",
                                               "reason":focus_data[2],"foundation_total":stamp}
        else:
            market,bars,_=focus_data
            try:
                with db:
                    result=combination_focus.batch(db,focus_asset,market,bars,event_dates,calendar_cutoffs,
                        deadline=min(deadline,time.monotonic()+focus_seconds),max_slots=focus_slots,
                        event_enabled=bool(event_budget),mode=focus_mode)
                    combination_focus.complete_slice(db,focus_asset,result,now.date())
            except Exception as exc:
                print(f"Combination focus error for {focus_asset}: {exc}",flush=True)
                with db:
                    stamp=combination_focus.release(db,focus_asset,"error",now.date())
                result={"asset":focus_asset,"status":"error","error":f"{type(exc).__name__}: {exc}"[:300],
                        "foundation_total":stamp}
            report["combination_focus_slice"]=result
            report["assets"].setdefault(focus_asset,{})["combinations"]=result
    focus_done=False
    scheduled=[(history_sources.market_for_symbol(market,symbol),symbol) for market,symbol in scheduled]
    for batch_start in range(0, len(scheduled), 6):
        if time.monotonic() > deadline or (time.monotonic() > general_deadline and not paper_remaining):
            report["deferred_for_runtime"] = len(scheduled) - batch_start
            break
        batch = scheduled[batch_start:batch_start+6]
        with ThreadPoolExecutor(max_workers=4) as executor:
            prices = list(executor.map(lambda item: fetch_bars(*item, now), batch))
        for (market, symbol), (bars, failure) in zip(batch, prices):
            asset = f"{market.name}:{symbol}"
            tracked = db.execute("SELECT 1 FROM paper WHERE asset=? AND status='active'", (asset,)).fetchone()
            paper_remaining.discard(asset)
            if asset==focus_asset:
                focus_data=(market,bars,failure)
            if failure is None:
                aligned_panel[asset] = bars
                aligned_markets[asset] = market
                try:
                    with db:
                        paper, paper_error = None, None
                        db.execute("SAVEPOINT paper_update")
                        try:
                            recheck_pending(db, asset, market, bars, now)
                            paper = advance_paper(db, asset, bars)
                            extend_active_history(db, asset, bars, market)
                        except ValueError as exc:
                            db.execute("ROLLBACK TO paper_update")
                            if not tracked:
                                raise
                            paper_error = f"{type(exc).__name__}: {exc}"[:300]
                            print(f"PAPER integrity hold for {asset}: {paper_error}", flush=True)
                        finally:
                            db.execute("RELEASE paper_update")
                        if paper_error:
                            paper_admission.pause_integrity(db,asset,paper_error,now)
                            db.execute("INSERT OR REPLACE INTO paper_data_issues VALUES (?,?)", (asset, paper_error))
                        else:
                            db.execute("DELETE FROM paper_data_issues WHERE asset=?", (asset,))
                        # Valid current bars can support independent research even
                        # when the frozen PAPER history cannot be reproduced.
                        result = discover(db, asset, market, bars, now, budget)
                        if paper_error:
                            result["paper_error"] = paper_error
                        if event_budget:
                            result["events"], daily = events.evaluate(
                                db, asset, bars, event_dates, result["asof"], result["snapshot_sha256"],
                                market.cost_bps, event_budget,
                                result["candidate"]["spec"] if result["candidate"] else None,
                                365 if market.calendar is None else 252, calendar_cutoffs,
                            )
                            daily_records.extend(daily)
                        # A coverage diagnostic failure must not erase existing PAPER progress.
                        db.execute("SAVEPOINT coverage_research")
                        try:
                            result["coverage_debt"] = coverage.run_asset(
                                db, asset, market, bars, event_dates, calendar_cutoffs, deadline=general_deadline)
                        except Exception as exc:
                            db.execute("ROLLBACK TO coverage_research")
                            result["coverage_debt"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
                            print(f"Coverage research error for {asset}: {exc}", flush=True)
                        finally:
                            db.execute("RELEASE coverage_research")
                        db.execute("SAVEPOINT foundry_research")
                        try:
                            result["foundry"] = foundry.run_asset(db, asset, market, bars, general_deadline)
                        except Exception as exc:
                            db.execute("ROLLBACK TO foundry_research")
                            result["foundry"] = {"status":"error","error":f"{type(exc).__name__}: {exc}"[:300]}
                            print(f"Foundry research error for {asset}: {exc}",flush=True)
                        finally:
                            db.execute("RELEASE foundry_research")
                        # Keep new indicators separate from legacy PAPER and cohort identities.
                        db.execute("SAVEPOINT indicator_research")
                        try:
                            result["indicators"] = indicator_research.run_asset(db, asset, market, bars, general_deadline)
                        except Exception as exc:
                            db.execute("ROLLBACK TO indicator_research")
                            result["indicators"] = {"status":"error","error":f"{type(exc).__name__}: {exc}"[:300]}
                            print(f"Indicator research error for {asset}: {exc}",flush=True)
                        finally:
                            db.execute("RELEASE indicator_research")
                        db.execute("SAVEPOINT model_research")
                        try:
                            result["statistical_models"] = model_research.run_asset(db, asset, market, bars)
                        except Exception as exc:
                            db.execute("ROLLBACK TO model_research")
                            result["statistical_models"] = {"status":"error","error":f"{type(exc).__name__}: {exc}"[:300]}
                            print(f"Statistical model research error for {asset}: {exc}", flush=True)
                        finally:
                            db.execute("RELEASE model_research")
                        if time.monotonic() < general_deadline - 30:
                            db.execute("SAVEPOINT cohort_research")
                            try:
                                result["foundry_cohort"] = cohort.run_asset(db, asset, market, bars, now)
                            except Exception as exc:
                                db.execute("ROLLBACK TO cohort_research")
                                result["foundry_cohort"] = {"status":"error","error":f"{type(exc).__name__}: {exc}"[:300]}
                                print(f"Cohort research error for {asset}: {exc}",flush=True)
                            finally:
                                db.execute("RELEASE cohort_research")
                        record=result["history"]
                        db.execute("INSERT OR REPLACE INTO history_coverage VALUES (?,?,?,?,?,?,?,?)",
                            (asset,record["source"],now.isoformat(),record["from"],record["through"],
                             record["bars"],record["status"],result["snapshot_sha256"]))
                        report["assets"][asset] = {**result, "paper": paper}
                        universe.record(db, market.name, symbol, now, None)
                        if tracked or symbol in market.symbols:
                            health(db, asset, paper_error)
                    market_reports[market.name]["screened"] += 1
                except ValueError as exc:
                    # A frozen PAPER data revision or incompatible engine is actionable.
                    if not tracked:
                        raise
                    failure = f"{type(exc).__name__}: {exc}"[:300]
            if failure:
                status = ("insufficient_history" if any(message in failure for message in
                          ("fewer than 600", "insufficient Coinbase history")) else "unavailable")
                report["assets"][asset] = {"error": failure, "coverage": status}
                with db:
                    universe.record(db, market.name, symbol, now, status)
                    conditional_paper.observe_unavailable(db,asset,failure,now)
                    if tracked:
                        db.execute("INSERT OR REPLACE INTO paper_data_issues VALUES (?,?)", (asset, failure))
                    if tracked or symbol in market.symbols:
                        health(db, asset, failure)
                outcome=market_reports[market.name]
                outcome["unavailable"] += 1
                outcome["insufficient_history"] += int(status=="insufficient_history")
                reasons=outcome["failure_reasons"]
                reasons[failure]=reasons.get(failure,0)+1
            print(asset, f"coverage error: {failure}" if failure else "screened", flush=True)
        # Once active PAPER has been advanced, spend the reserved slice on
        # the same focus asset before the remaining broad rotation.
        if focus_asset and not focus_done and focus_data is not None and not paper_remaining:
            execute_focus()
            focus_done=True
            general_deadline=deadline
    if focus_asset and not focus_done:
        execute_focus()
    with db:
        report["cross_asset_research"] = cross_asset_research.run(db, aligned_panel)
    report["history_summary"]={}
    for name in market_reports:
        rows=[dict(r) for r in db.execute("SELECT * FROM history_coverage WHERE asset LIKE ?",(name+":%",))]
        counts={}
        for r in rows:
            counts[r["source"]]=counts.get(r["source"],0)+1
        report["history_summary"][name]={
            "validated_assets":len(rows),"oldest_verified_bar":min((r["first_bar"] for r in rows),default=None),
            "largest_validated_series":max((r["bars"] for r in rows),default=0),
            "over_six_years":sum((datetime.fromisoformat(r["last_bar"])-datetime.fromisoformat(r["first_bar"])).days>2192 for r in rows),
            "by_source":counts,"last_observation_only":True}
    report["combination_focus"]=combination_focus.dashboard(db)
    with db:
        for name, outcome in market_reports.items():
            market_error=None
            if not outcome["screened"] and outcome["unavailable"]:
                short=outcome["insufficient_history"]
                other=outcome["unavailable"]-short
                reasons=sorted(outcome["failure_reasons"].items(),key=lambda r:(-r[1],r[0]))
                detail="; ".join(f"{count}x {reason}" for reason,count in reasons[:2])
                market_error=(f"0/{outcome['unavailable']} varlık tarandı; {short} yetersiz geçmiş, "
                              f"{other} veri/bütünlük sorunu. {detail}")[:450]
            # No scheduled/attempted symbol is not proof that a failed
            # market recovered. Preserve the existing failure episode.
            if outcome["screened"] or outcome["unavailable"]:
                health(db, f"market:{name}", market_error)
    if daily_report:
        events.write_daily(daily_report, daily_records)
    with db:
        report["superlab_qualification"] = qualification.review_new(db, first_technical, first_event)
        new_nominations=paper_admission.seed(db)
        admissions=[]
        counts={}
        admission_deadline=time.monotonic()+45
        queue=db.execute("""SELECT asset,min(nominated_asof) AS first
            FROM paper_candidates WHERE status='waiting' GROUP BY asset ORDER BY first,asset""").fetchall()
        for item in queue:
            asset=item["asset"]
            if asset not in aligned_panel or time.monotonic()>admission_deadline:
                continue
            market=aligned_markets[asset]
            if counts.get(market.name,0)>=paper_admission.ADMISSIONS_PER_MARKET_PER_RUN:
                continue
            db.execute("SAVEPOINT paper_admission")
            try:
                outcome=paper_admission.admit(db,asset,market,aligned_panel[asset],now)
                if outcome:
                    counts[market.name]=counts.get(market.name,0)+1
                    admissions.append({"asset":asset,**outcome})
            except (ValueError,KeyError,TypeError) as exc:
                db.execute("ROLLBACK TO paper_admission")
                counts[market.name]=counts.get(market.name,0)+1
                admissions.append({"asset":asset,"status":"recheck_error","reason":str(exc)[:200]})
            finally:
                db.execute("RELEASE paper_admission")
        legacy_handoff=conditional_paper.harvest_legacy(db,now)
        report["paper_admission"]={**paper_admission.summary(db),"recovered_nominations":recovered_nominations,
                                   "new_nominations":new_nominations,"archive_recovery":archive_recovery,"this_run":admissions}
        native_new=conditional_paper.harvest(db)
        if os.getenv("GITHUB_ACTIONS") == "true":
            db.execute("SAVEPOINT native_archive")
            try:
                native_archive_recovery=conditional_paper.recover_archives(db,now)
            except Exception as exc:
                db.execute("ROLLBACK TO native_archive")
                native_archive_recovery=[{"status":"error","error":str(exc)[:220]}]
            finally:
                db.execute("RELEASE native_archive")
        db.execute("SAVEPOINT conditional_native_cycle")
        try:
            native_result=process_native_backlog(db,market_names,aligned_panel,aligned_markets,event_dates,calendar_cutoffs,now)
        except Exception as exc:
            db.execute("ROLLBACK TO conditional_native_cycle")
            native_result={"status":"error","error":str(exc)[:220],"checked_this_run":0}
        finally:
            db.execute("RELEASE conditional_native_cycle")
        report["conditional_paper"]={**conditional_paper.summary(db),"old_candidates_harvested":native_harvested,
            "new_candidates_harvested":native_new,"legacy_handoff":legacy_handoff,"risk_policy_migrated":native_migrated,"archive_recovery":native_archive_recovery,"this_run":native_result}
    report["new_coverage_debt_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=? AND rowid>?", (coverage.VERSION, first_technical)).fetchone()[0]
    report["new_combination_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=? AND rowid>?", (combination_research.VERSION, first_technical)).fetchone()[0]
    report["combination_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=?", (combination_research.VERSION,)).fetchone()[0]
    report["combination_coverage"] = combination_research.summary(db)
    report["combination_external_sources"] = combination_research.external_scope()
    report["new_indicator_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=? AND rowid>?", (indicator_research.VERSION, first_technical)).fetchone()[0]
    report["indicator_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=?", (indicator_research.VERSION,)).fetchone()[0]
    report["indicator_counts"] = indicator_research.counts(db)
    report["statistical_model_trials"] = db.execute(
        "SELECT COUNT(*) FROM attempts WHERE version=?", (model_research.VERSION,)).fetchone()[0]
    report["new_foundry_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=? AND rowid>?", (foundry.VERSION, first_technical)).fetchone()[0]
    report["total_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version NOT LIKE 'coverage-%'").fetchone()[0]
    report["foundry_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=?", (foundry.VERSION,)).fetchone()[0]
    report["coverage_debt_trials"] = db.execute("SELECT COUNT(*) FROM attempts WHERE version=?", (coverage.VERSION,)).fetchone()[0]
    report["total_event_trials"] = db.execute("SELECT COUNT(*) FROM event_trials").fetchone()[0]
    report["paper"] = [
        dict(row) for row in db.execute("SELECT sequence,asset,start,cursor,status FROM paper ORDER BY sequence")
    ]
    report["pending_notifications"] = db.execute("SELECT COUNT(*) FROM outbox WHERE receipt IS NULL").fetchone()[0]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="runtime/research.sqlite")
    parser.add_argument("--report", default="runtime/report.json")
    parser.add_argument("--market", action="append", choices=[market.name for market in data.MARKETS])
    parser.add_argument("--budget", type=int, choices=range(1, 129), default=24, metavar="1..128")
    parser.add_argument("--event-budget", type=int, choices=range(25), default=6, metavar="0..24")
    parser.add_argument("--assets-per-market", type=int, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--daily-report", default="runtime/event-daily.jsonl")
    args = parser.parse_args()
    with connect(args.db) as db:
        report = run(db, args.market or [market.name for market in data.MARKETS], args.budget,
                     args.event_budget, args.assets_per_market, args.daily_report)
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"{report['total_trials']} technical trials, {report['total_event_trials']} event trials; "
          f"{report['pending_notifications']} pending alerts")


if __name__ == "__main__":
    main()
