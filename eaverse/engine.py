"""Run one checkpointable discovery and forward-simulation cycle."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import data, research


SCHEMA = """
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
CREATE TABLE IF NOT EXISTS health (
    asset TEXT PRIMARY KEY, failures INTEGER NOT NULL, episode INTEGER NOT NULL
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
    desired = research.target(anchored, research.features(anchored), json.loads(row["spec"]), 365)
    metrics, returns, benchmark = research.paper_metrics(
        anchored, desired, row["start"], stop, row["cost"], row["sequence"],
    ) if len(observed) > 11 else (None, None, None)
    db.execute("UPDATE paper SET cursor=?, history_digest=? WHERE sequence=?",
               (date(stop), data.fingerprint(anchored.loc[:stop]), row["sequence"]))
    days = len(observed.loc[:stop]) - 1
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
            f"{os.environ.get('EAVERSE_EVIDENCE_URL', '')}",
        )
    return {"status": status, "sequence": row["sequence"], "bars": days, **metrics}


def discover(db, asset, market, bars, now, budget):
    records = list(db.execute("SELECT id,family,discovered FROM attempts WHERE asset=? AND version=?",
                              (asset, research.VERSION)))
    learning = {}
    for record in records:
        n, wins = learning.get(record["family"], (0, 0))
        learning[record["family"]] = (n + 1, wins + int(json.loads(record["discovered"])["pass"]))
    selected = research.choose(asset, {record["id"] for record in records}, learning, budget)
    snapshot = data.fingerprint(bars)
    code = os.getenv("GITHUB_SHA", "local")
    asof = date(bars.index[-1])
    periods = 365 if market.calendar is None else 252
    discovery_end = len(bars) - research.MIN_VALIDATION_BARS - 4
    validation_index = bars.index[-research.MIN_VALIDATION_BARS:]
    discovery_index = bars.index[200:discovery_end]
    signals = research.features(bars)
    candidates = []
    for key, spec in selected:
        desired = research.target(bars, signals, spec, periods)
        report = research.history_metrics(bars, desired, discovery_index, market.cost_bps)
        family = ",".join(name for name, _ in spec["rules"])
        db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                   (research.VERSION, asset, key, family, research.canonical(spec), market.source,
                    snapshot, asof, code, research.canonical(research.POLICY), research.canonical(report)))
        if report["pass"]:
            candidates.append((key, spec, report))
    # One candidate per batch/asset is chosen by discovery evidence before
    # validation. Reuse of historical data makes this a screen. PAPER is prospective.
    nominee = None
    if candidates:
        key, spec, discovery_report = min(candidates, key=research.ranking_key)
        desired = research.target(bars, signals, spec, periods)
        validation = research.history_metrics(bars, desired, validation_index, market.cost_bps)
        db.execute("UPDATE attempts SET validated=? WHERE version=? AND asset=? AND id=?",
                   (research.canonical(validation), research.VERSION, asset, key))
        nominee = {"id": key, "spec": spec, "validation": validation}
        if market.source == "coinbase" and validation["pass"]:
            start = (now.date() + timedelta(days=1)).isoformat()
            anchored = bars.iloc[-400:]
            origin = {"id": key, "source": market.source, "data_sha256": snapshot, "code": code,
                      "discovery": discovery_report, "validation": validation}
            db.execute(
                """INSERT OR IGNORE INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'active')""",
                (asset, research.canonical(spec), market.cost_bps, start, date(anchored.index[0]), asof,
                 data.fingerprint(anchored), research.VERSION, research.canonical(research.POLICY),
                 research.canonical(origin)),
            )
    return {"new_trials": len(selected), "total_trials": len(records) + len(selected), "candidate": nominee,
            "snapshot_sha256": snapshot, "from": date(bars.index[0]), "asof": asof, "paper_eligible": market.source == "coinbase"}


def run(db, market_names, budget):
    now = datetime.now(UTC)
    report = {"time": now.isoformat(), "policy": research.POLICY, "assets": {}}
    for market in data.MARKETS:
        if market.name not in market_names:
            continue
        for symbol in market.symbols:
            asset = f"{market.name}:{symbol}"
            try:
                bars = data.load(market, symbol, now)
                with db:
                    paper = advance_paper(db, asset, bars)
                    report["assets"][asset] = {**discover(db, asset, market, bars, now, budget), "paper": paper}
                    health(db, asset, None)
            except (OSError, ValueError, KeyError, IndexError) as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                with db:
                    health(db, asset, error)
                report["assets"][asset] = {"error": error}
            print(asset, "data error" if "error" in report["assets"][asset] else "screened", flush=True)
    report["total_trials"] = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
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
    args = parser.parse_args()
    with connect(args.db) as db:
        report = run(db, args.market or [market.name for market in data.MARKETS], args.budget)
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"{report['total_trials']} recorded hypotheses; {report['pending_notifications']} pending alerts")


if __name__ == "__main__":
    main()
