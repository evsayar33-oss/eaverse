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

from . import cohort, coverage, data, events, foundry, qualification, research, universe


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
    previous = {key: value for key, value in research.POLICY.items() if key != "paper_execution_delay_bars"}
    db.execute(
        "UPDATE paper SET policy=? WHERE status='active' AND cursor IS NULL AND start > ? AND policy=?",
        (research.canonical(research.POLICY), now.date().isoformat(), research.canonical(previous)),
    )


def recheck_pending(db, asset, market, bars, now):
    """Replace a revised pre-opening proposal only after testing its frozen rules again."""
    row = db.execute("SELECT * FROM paper WHERE asset=? AND status='active' AND cursor IS NULL AND start>?",
                     (asset, now.date().isoformat())).fetchone()
    if not row or row["version"] != research.VERSION or row["policy"] != research.canonical(research.POLICY):
        return
    window = bars.loc[row["anchor"]:row["cutoff"]]
    if data.fingerprint(window) == row["digest"]:
        return
    origin = json.loads(row["origin"])
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
    db.execute("UPDATE paper SET status='revised_before_start' WHERE sequence=?", (row["sequence"],))
    if all(block["pass"] for block in revised.values()):
        anchor = history.iloc[-400:]
        origin.update(revised, replaces=row["sequence"], data_start=date(history.index[0]),
                      data_sha256=data.fingerprint(history), code=os.getenv("GITHUB_SHA", "local"))
        db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                      VALUES(?,?,?,?,?,?,?,?,?,?,'active')""",
                   (asset,row["spec"],row["cost"],row["start"],date(anchor.index[0]),row["cutoff"],
                    data.fingerprint(anchor),row["version"],row["policy"],research.canonical(origin)))


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
    if row["version"] != research.VERSION or row["policy"] != research.canonical(research.POLICY):
        raise ValueError("PAPER engine version changed; frozen experiment needs its original engine")
    anchored = bars.loc[row["anchor"]:]
    if data.fingerprint(anchored.loc[:row["cutoff"]]) != row["digest"]:
        raise ValueError("PAPER origin candles were revised or are missing")
    if row["cursor"] and data.fingerprint(anchored.loc[:row["cursor"]]) != row["history_digest"]:
        raise ValueError("previously observed PAPER candles were revised or are missing")
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
            final=days >= research.PAPER_BARS, periods=periods,
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
        active_count = db.execute("SELECT count(*) FROM paper WHERE status='active'").fetchone()[0]
        if validation["pass"] and validation["execution_audit"]["status"] != "mismatch" and active_count < 12:
            start = (now.date() + timedelta(days=1)).isoformat()
            if market.calendar:
                import exchange_calendars as xcals
                start = date(xcals.get_calendar(market.calendar).date_to_session(start, direction="next"))
            anchored = bars.iloc[-400:]
            origin = {"id": key, "source": market.source, "data_sha256": snapshot, "code": code,
                      "periods": periods, "calendar": market.calendar,
                      "data_start": date(bars.index[0]), "data_end": asof,
                      "history_cost_bps": 2 * market.cost_bps,
                      "discovery": discovery_report, "validation": validation}
            db.execute(
                """INSERT OR IGNORE INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'active')""",
                (asset, research.canonical(spec), market.cost_bps, start, date(anchored.index[0]), asof,
                 data.fingerprint(anchored), research.VERSION, research.canonical(research.POLICY),
                 research.canonical(origin)),
            )
    return {"new_trials": len(selected), "total_trials": len(records) + len(selected), "candidate": nominee,
            "snapshot_sha256": snapshot, "from": date(bars.index[0]), "asof": asof, "paper_eligible": True}


def fetch_bars(market, symbol, now):
    try:
        return data.load(market, symbol, now), None
    except Exception as exc:
        # The provider can raise transport and parsing errors from several libraries.
        # Strategy/evidence errors are handled separately and never hidden here.
        return None, f"{type(exc).__name__}: {exc}"[:300]


def run(db, market_names, budget, event_budget=6, asset_limit=None, daily_report=None):
    now = datetime.now(UTC)
    deadline = time.monotonic() + 14 * 60
    first_technical = db.execute('SELECT coalesce(max(rowid),0) FROM attempts').fetchone()[0]
    first_event = db.execute('SELECT coalesce(max(rowid),0) FROM event_trials').fetchone()[0]
    with db:
        update_pending_paper_policy(db, now)
        if os.getenv("GITHUB_ACTIONS") == "true":
            from . import ideas
            ideas.refresh(db)
        event_dates, coverage = events.refresh(db, now.date()) if event_budget else ({}, {})
        calendar_cutoffs = {}
        if event_budget:
            db.execute("INSERT OR IGNORE INTO event_calendar_versions VALUES (?,?)",
                       (research.digest(event_dates), research.canonical(event_dates)))
            calendar_cutoffs = {name: item["fetched"] for name, item in coverage.items()}
            stale_calendars = [name for name, limit in calendar_cutoffs.items()
                               if limit < (now.date() - timedelta(days=3)).isoformat()]
            health(db, "macro-calendar", "stale " + ", ".join(stale_calendars) if stale_calendars else None)
        queues = []
        market_reports = {}
        for market in data.MARKETS:
            if market.name not in market_names:
                continue
            symbols, listing = universe.refresh(db, market, now.date())
            listing_asof = listing["listing_date"]
            stale_listing = listing_asof is None or listing_asof < (now.date() - timedelta(days=3)).isoformat()
            health(db, f"listings:{market.name}", "live listing catalog unavailable or stale" if stale_listing else None)
            selected = universe.queue(db, market, symbols, now.date(), asset_limit or universe.LIMITS[market.name])
            for symbol in reversed(cohort.active_assets(db, market)):
                if symbol not in selected:
                    # At most one extra research asset, never displace existing PAPER.
                    selected.insert(0, symbol)
            queues.append((market, iter(selected)))
            market_reports[market.name] = {**listing, "universe": len(symbols), "scheduled": len(selected),
                                          "screened": 0, "unavailable": 0}
    report = {"time": now.isoformat(), "policy": research.POLICY, "assets": {}, "markets": market_reports,
              "event_sources": coverage}
    daily_records = []
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
    for batch_start in range(0, len(scheduled), 6):
        if time.monotonic() > deadline:
            report["deferred_for_runtime"] = len(scheduled) - batch_start
            break
        batch = scheduled[batch_start:batch_start+6]
        with ThreadPoolExecutor(max_workers=4) as executor:
            prices = list(executor.map(lambda item: fetch_bars(*item, now), batch))
        for (market, symbol), (bars, failure) in zip(batch, prices):
            asset = f"{market.name}:{symbol}"
            tracked = db.execute("SELECT 1 FROM paper WHERE asset=? AND status='active'", (asset,)).fetchone()
            if failure is None:
                try:
                    with db:
                        recheck_pending(db, asset, market, bars, now)
                        paper = advance_paper(db, asset, bars)
                        extend_active_history(db, asset, bars, market)
                        result = discover(db, asset, market, bars, now, budget)
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
                                db, asset, market, bars, event_dates, calendar_cutoffs, deadline=deadline)
                        except Exception as exc:
                            db.execute("ROLLBACK TO coverage_research")
                            result["coverage_debt"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
                            print(f"Coverage research error for {asset}: {exc}", flush=True)
                        finally:
                            db.execute("RELEASE coverage_research")
                        db.execute("SAVEPOINT foundry_research")
                        try:
                            result["foundry"] = foundry.run_asset(db, asset, market, bars, deadline)
                        except Exception as exc:
                            db.execute("ROLLBACK TO foundry_research")
                            result["foundry"] = {"status":"error","error":f"{type(exc).__name__}: {exc}"[:300]}
                            print(f"Foundry research error for {asset}: {exc}",flush=True)
                        finally:
                            db.execute("RELEASE foundry_research")
                        if time.monotonic() < deadline - 30:
                            db.execute("SAVEPOINT cohort_research")
                            try:
                                result["foundry_cohort"] = cohort.run_asset(db, asset, market, bars, now)
                            except Exception as exc:
                                db.execute("ROLLBACK TO cohort_research")
                                result["foundry_cohort"] = {"status":"error","error":f"{type(exc).__name__}: {exc}"[:300]}
                                print(f"Cohort research error for {asset}: {exc}",flush=True)
                            finally:
                                db.execute("RELEASE cohort_research")
                        report["assets"][asset] = {**result, "paper": paper}
                        universe.record(db, market.name, symbol, now, None)
                        if tracked or symbol in market.symbols:
                            health(db, asset, None)
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
                    if tracked or symbol in market.symbols:
                        health(db, asset, failure)
                market_reports[market.name]["unavailable"] += 1
            print(asset, "coverage error" if failure else "screened", flush=True)
    with db:
        for name, outcome in market_reports.items():
            market_error = "no symbols could be screened" if not outcome["screened"] and outcome["unavailable"] else None
            health(db, f"market:{name}", market_error)
    if daily_report:
        events.write_daily(daily_report, daily_records)
    with db:
        report["superlab_qualification"] = qualification.review_new(db, first_technical, first_event)
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
