"""Deterministic coverage-debt research; never a PAPER or order approval.

Each asset/timeframe/lane has an append-only trial identity and durable cursor.
The finite registered grid is a lower bound, not the universe of all strategies.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from functools import lru_cache

import numpy as np
import pandas as pd

from . import data, events, research

VERSION = "coverage-1"
FRAMES = ("1d", "1w")
LANES = ("single", "pair", "synthesis", "calendar", "macro")
DIRECTIONS = ("long", "short", "long_short")
STYLES = ("state", "confirmation2", "pulse3", "pulse10")
SIZES = ("full", "half", "vol_target")
POLICY = {"version": VERSION, "execution": "next_open", "cost_multiplier": 2,
          "short_borrow_apr_assumption": .05, "paper_eligible": False,
          "live_allowed": False, "selection": "none",
          "weekly": "aggregate completed interior calendar weeks",
          "validation": "chronological retrospective screen, not untouched holdout"}
NORMALIZED = {
    "return_pct": (-5., -2., 0., 2., 5.),
    "price_z": (-2., -1., 0., 1., 2.),
    "volume_z": (-2., -1., 0., 1., 2.),
    "volume_pct": (-30., 0., 30., 100.),
    "cost_pct": (-5., -2., 0., 2., 5.),
    "cost_z": (-2., -1., 0., 1., 2.),
    "atr_pct": (1., 2., 4., 8.),
    "range_pct": (1., 2., 4., 8.),
    "flow_ratio": (-.5, -.2, 0., .2, .5),
    "realized_vol_pct": (1., 2., 4., 8.),
}
SCHEMA = """CREATE TABLE IF NOT EXISTS coverage_cursor (
    asset TEXT NOT NULL, timeframe TEXT NOT NULL, lane TEXT NOT NULL,
    version TEXT NOT NULL, next_trial INTEGER NOT NULL,
    PRIMARY KEY(asset,timeframe,lane,version)
)"""
PENDING = """CREATE TABLE IF NOT EXISTS coverage_pending (
    asset TEXT NOT NULL, timeframe TEXT NOT NULL, lane TEXT NOT NULL,
    version TEXT NOT NULL, position INTEGER NOT NULL, reason TEXT NOT NULL,
    checked TEXT NOT NULL,
    PRIMARY KEY(asset,timeframe,lane,version,position)
)"""


@lru_cache(maxsize=2)
def rule_grid(frame):
    rules = []
    for name, windows in {**research.FEATURES, **research.EXTENDED}.items():
        for n in sorted(set(windows) | ({5, 10, 20, 40, 52} if frame == "1w" else {5, 10, 40, 120, 200})):
            if frame == "1w" and n > 52:
                continue
            # Preserve the legacy signal calculation; its inverse is a separate hypothesis.
            for level in (None, *research.LEVELS.get(name, ())):
                for op in ("above", "below"):
                    rules.append((name, n, level, op))
    for name, levels in NORMALIZED.items():
        for n in ((5, 10, 20, 26, 52) if frame == "1w" else (5, 10, 20, 40, 60, 120)):
            for level, op in itertools.product(levels, ("above", "below", "cross_up", "cross_down")):
                rules.append((name, n, level, op))
    # Interleave families, then settings, so a new asset does not spend its first
    # hundred visits exclusively on SMA. Every registered rule remains reachable.
    groups = {}
    for rule in rules:
        groups.setdefault(rule[0], []).append(rule)
    return tuple(rule for row in itertools.zip_longest(*groups.values()) for rule in row if rule is not None)


@lru_cache(maxsize=2)
def representatives(frame):
    return tuple((name, 20, NORMALIZED[name][len(NORMALIZED[name])//2] if name in NORMALIZED else None, "above")
                 for name in dict.fromkeys(rule[0] for rule in rule_grid(frame)))


@lru_cache(maxsize=16)
def templates(frame, lane):
    reps = representatives(frame)
    if lane == "single":
        return tuple(((rule,), 1, None) for rule in rule_grid(frame))
    if lane == "pair":
        return tuple((rules, vote, None) for rules in itertools.combinations(reps, 2) for vote in (1, 2))
    if lane == "synthesis":
        # Deterministic rotating groups across ALL families; no profitability gate.
        # This is a registered sparse design, not every possible higher-order combination.
        groups = {}
        for size in (3, 4, 5):
            for offset in range(len(reps)):
                for step in (1, 3, 5, 7):
                    rules = tuple(sorted({reps[(offset + i * step) % len(reps)] for i in range(size)}))
                    if len(rules) == size:
                        for vote in (1, math.ceil(size / 2), size):
                            groups[(rules, vote)] = (rules, vote, None)
        return tuple(groups.values())
    names = (tuple(events.CALENDAR) if lane == "calendar" else tuple(sorted(events.FAMILIES)))
    if frame == "1w" and lane == "calendar":
        names = tuple(name for name in names if not name.startswith("WEEKDAY_"))
    return tuple(((rule,), 1, (name, mode)) for rule in reps for name in names for mode in ("only", "avoid"))


def total(frame, lane):
    return len(templates(frame, lane)) * len(DIRECTIONS) * len(STYLES) * len(SIZES)


def plan(frame, lane, position):
    group = templates(frame, lane)
    if not 0 <= position < total(frame, lane):
        return None
    cycle, offset = divmod(position, len(group))
    rules, vote, event = group[offset]
    configs = tuple(itertools.product(DIRECTIONS, STYLES, SIZES))
    direction, style, sizing = configs[(cycle + offset) % len(configs)]
    return {"frame": frame, "lane": lane, "rules": rules, "vote": vote,
            "event": event, "direction": direction, "style": style, "sizing": sizing}


def weekly(bars):
    # Drop both boundary weeks; the input has already passed source calendar checks.
    group = bars.groupby(bars.index.to_period("W-SUN"))
    result = group.agg({"open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"})
    result.index = pd.DatetimeIndex(group.apply(lambda part: part.index[-1]).values)
    return result.iloc[1:-1]


def _z(series, n):
    past = series.shift()
    sd = past.rolling(n).std(ddof=0)
    return (series - past.rolling(n).mean()) / sd.where(sd > 0)


def measurement(bars, name, n):
    c, v = bars.close, bars.volume
    if name == "return_pct":
        return 100 * c.pct_change(n, fill_method=None)
    if name == "price_z":
        return _z(c, n)
    if name == "volume_z":
        return _z(v, n)
    if name == "volume_pct":
        mean = v.shift().rolling(n).mean()
        return 100 * (v / mean.where(mean > 0) - 1)
    if name in ("cost_pct", "cost_z"):
        # OHLCV volume-weighted average PRICE proxy, not holder cost basis or order flow.
        denom = v.rolling(n).sum()
        cost = (c * v).rolling(n).sum() / denom.where(denom > 0)
        distance = 100 * (c / cost - 1)
        return distance if name == "cost_pct" else _z(distance, n)
    if name == "atr_pct":
        tr = pd.concat([bars.high - bars.low, (bars.high - c.shift()).abs(),
                        (bars.low - c.shift()).abs()], axis=1).max(axis=1)
        return 100 * tr.rolling(n).mean() / c
    if name == "range_pct":
        return 100 * ((bars.high - bars.low) / c).rolling(n).mean()
    if name == "flow_ratio":
        volume = v.rolling(n).sum()
        return (np.sign(c.diff()) * v).rolling(n).sum() / volume.where(volume > 0)
    if name == "realized_vol_pct":
        return 100 * c.pct_change(fill_method=None).rolling(n).std()
    raise ValueError("unknown coverage measurement")


def flag(bars, rule):
    name, n, level, op = rule
    if name not in NORMALIZED:
        spec = {"rules": [(name, n)], "levels": {} if level is None else {name: level}}
        x = research.features(bars, [spec])[research.signal_key(spec, name, n)]
        return x.astype(bool) if op == "above" else ~x.astype(bool)
    x = measurement(bars, name, n)
    if op == "above":
        return x > level
    if op == "below":
        return x < level
    if op == "cross_up":
        return (x > level) & (x.shift() <= level)
    if op == "cross_down":
        return (x < level) & (x.shift() >= level)
    raise ValueError("unknown comparison")


def event_mask(index, name, dates):
    if name in events.CALENDAR:
        # These are observed calendar conditions at the completed bar.
        return pd.Series(events.masks(index, {})[name, 1], index=index)
    observed = pd.DatetimeIndex(dates[name])
    pos = index.searchsorted(observed, side="right")
    result = np.zeros(len(index), dtype=bool)
    result[pos[pos < len(index)]] = True
    return pd.Series(result, index=index)


def desired(bars, spec, dates, daily_periods=252):
    flags = [flag(bars, rule) for rule in spec["rules"]]
    votes = sum(f.astype(int) for f in flags)
    signal = votes >= spec["vote"]
    valid = pd.Series(True, index=bars.index)
    for name, n, _, _ in spec["rules"]:
        if name in NORMALIZED:
            valid &= measurement(bars, name, n).notna()
        elif name == "vwma":
            valid &= bars.volume.rolling(n).sum() > 0
    gate = pd.Series(True, index=bars.index)
    if spec["event"]:
        name, mode = spec["event"]
        mask = event_mask(bars.index, name, dates)
        gate = mask if mode == "only" else ~mask
    direction = spec["direction"]
    signed = signal.astype(float) * (-1 if direction == "short" else 1)
    if direction == "long_short":
        signed = 2 * signed - 1
    signed = signed.where(valid, 0.)
    if spec["style"] == "confirmation2":
        signed = signed.where(signed == signed.shift(), 0.)
    elif spec["style"].startswith("pulse"):
        horizon = int(spec["style"][5:])
        out = np.zeros(len(signed))
        remaining, position = 0, 0.
        previous, previous_gate = 0., False
        for i, value in enumerate(signed.to_numpy()):
            if not remaining and value and (value != previous or not previous_gate) and gate.iloc[i]:
                remaining, position = horizon, value
            if not valid.iloc[i]:
                remaining = 0
            if remaining:
                out[i] = position
                remaining -= 1
            previous, previous_gate = value, bool(gate.iloc[i])
        signed = pd.Series(out, index=bars.index)
    else:
        signed = signed.where(gate, 0.)
    # Confirmation also requires the event gate. Pulse uses it at entry only.
    if spec["style"] == "confirmation2":
        signed = signed.where(gate, 0.)
    if spec["sizing"] == "half":
        signed *= .5
    elif spec["sizing"] == "vol_target":
        periods = 52 if spec["frame"] == "1w" else daily_periods
        vol = bars.close.pct_change(fill_method=None).rolling(20).std() * math.sqrt(periods)
        signed *= (.25 / vol.where(vol > 0)).clip(upper=1).fillna(0)
    return signed


def evaluate_fold(bars, weights, start, stop, cost_bps):
    # [start, stop) uses only opens inside the fold. Reset at fold boundaries.
    w = weights.shift(1).fillna(0).to_numpy()[start:stop-1]
    o = bars.open.to_numpy()[start:stop]
    high = bars.high.to_numpy()[start:stop-1]
    days = np.diff(bars.index[start:stop].to_numpy()) / np.timedelta64(1, "D")
    r = o[1:] / o[:-1] - 1
    cost = 2 * cost_bps / 10000
    gross = 1 + w * r - np.maximum(-w, 0) * .05 * days / 365
    prior = np.r_[0., w[:-1]]
    trades = int(np.count_nonzero((w != 0) & ((prior == 0) | (np.sign(w) != np.sign(prior)))))
    if np.any(gross <= 0) or np.any(1 + w * (high / o[:-1] - 1) <= 0):
        return {"net": -1., "drawdown": 1., "entries": trades, "status": "ruin_stress"}
    marked = w * (1 + r) / gross
    fees = cost * np.abs(w - np.r_[0., marked[:-1]])
    net = gross * (1 - fees)
    net[-1] *= 1 - cost * abs(marked[-1])
    if np.any(net <= 0):
        return {"net": -1., "drawdown": 1., "entries": trades, "status": "ruin_stress"}
    equity = np.cumprod(net)
    peak = np.maximum.accumulate(np.r_[1., equity])[1:]
    return {"net": float(equity[-1] - 1), "drawdown": float(np.max(1 - equity / peak)),
            "entries": trades, "status": "evaluated" if trades else "no_entries"}


def warmup_for(spec):
    needed = [22]
    for name, n, _, _ in spec["rules"]:
        extra = (100 if name == "low_vol" else n if name in ("cost_z", "macd", "adx") else 0)
        needed.append(n + extra + 3)
    return max(needed)


def eligible(current, spec, event_dates, cutoffs):
    sample = current
    name = spec["event"][0] if spec["event"] else None
    if spec["lane"] == "macro":
        if not event_dates.get(name) or not cutoffs.get(name):
            return None, "blocked_event_data"
        sample = current.loc[current.index <= pd.Timestamp(cutoffs[name])]
    warmup = warmup_for(spec)
    if len(sample) < warmup + 120:
        return None, "blocked_history"
    if spec["lane"] == "macro":
        mask = event_mask(sample.index, name, event_dates)
        boundary = warmup + (len(sample) - warmup) * 3 // 4
        if not mask.iloc[warmup:boundary].any() or not mask.iloc[boundary:].any():
            return None, "blocked_event_occurrences"
    return sample, None


def run_asset(db, asset, market, bars, event_dates, cutoffs, *, deadline=None):
    import time
    from datetime import UTC, datetime
    db.execute(SCHEMA)
    db.execute(PENDING)
    result = {}
    for frame in FRAMES:
        current = bars if frame == "1d" else weekly(bars)
        for lane in LANES:
            key = frame + ":" + lane
            args = (asset, frame, lane, VERSION)
            row = db.execute("SELECT next_trial FROM coverage_cursor WHERE asset=? AND timeframe=? AND lane=? AND version=?", args).fetchone()
            position = row[0] if row else 0
            count = total(frame, lane)
            pending_query = "FROM coverage_pending WHERE asset=? AND timeframe=? AND lane=? AND version=?"
            pending = db.execute("SELECT count(*) " + pending_query, args).fetchone()[0]
            status = {"completed": position-pending, "registered": count, "pending": pending,
                      "scheduled": position, "status": "grid_complete" if not pending and position >= count else "blocked_pending"}
            result[key] = status
            if deadline is not None and time.monotonic() >= deadline:
                status["status"] = "deferred_runtime"
                continue
            retry = db.execute("SELECT position " + pending_query + " ORDER BY checked,position LIMIT 1", args).fetchone()
            # At most one old blocked item and one new item are inspected. A
            # missing long-window or macro input cannot starve other settings.
            candidates = ([(retry[0], False)] if retry else []) + ([(position, True)] if position < count else [])
            for offset, is_new in candidates:
                spec = plan(frame, lane, offset)
                identity = research.digest([VERSION, spec])
                # A durable key survives compaction. Check it before any
                # eligibility or backtest work, including a recovered cursor.
                if db.execute("SELECT 1 FROM attempts WHERE version=? AND asset=? AND id=?",
                              (VERSION, asset, identity)).fetchone():
                    if is_new:
                        position += 1
                        db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)", (*args, position))
                    else:
                        db.execute("DELETE " + pending_query + " AND position=?", (*args, offset))
                        pending -= 1
                    status.update(status="duplicate_not_counted", completed=position-pending,
                                  pending=pending, scheduled=position, id=identity)
                    break
                sample, blocked = eligible(current, spec, event_dates, cutoffs)
                if blocked:
                    db.execute("INSERT OR REPLACE INTO coverage_pending VALUES (?,?,?,?,?,?,?)",
                               (*args, offset, blocked, datetime.now(UTC).isoformat()))
                    if is_new:
                        position += 1
                        pending += 1
                        db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)", (*args, position))
                    status.update(status=blocked, pending=pending, scheduled=position, completed=position-pending)
                    continue
                warmup = warmup_for(spec)
                boundary = warmup + (len(sample) - warmup) * 3 // 4
                weights = desired(sample, spec, event_dates, 365 if market.calendar is None else 252)
                discovery = evaluate_fold(sample, weights, warmup, boundary, market.cost_bps)
                validation = evaluate_fold(sample, weights, boundary, len(sample), market.cost_bps)
                event = spec["event"][0] if lane == "macro" else None
                record = {"pass": False, "coverage_only": True, "status": discovery["status"],
                          "metrics": discovery, "paper_eligible": False,
                          "calendar_sha256": research.digest(event_dates[event]) if event else None,
                          "calendar_asof": cutoffs[event] if event else None}
                validated = {"metrics": validation, "retrospective_only": True, "paper_eligible": False}
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (VERSION, asset, identity, "coverage:" + key, research.canonical(spec),
                            market.source, data.fingerprint(sample), sample.index[-1].date().isoformat(),
                            os.getenv("GITHUB_SHA", "local"), research.canonical(POLICY),
                            research.canonical(record), research.canonical(validated)))
                if is_new:
                    position += 1
                    db.execute("INSERT OR REPLACE INTO coverage_cursor VALUES (?,?,?,?,?)", (*args, position))
                else:
                    db.execute("DELETE " + pending_query + " AND position=?", (*args, offset))
                    pending -= 1
                status.update(status="evaluated", completed=position-pending, pending=pending, scheduled=position,
                              id=identity, discovery_status=discovery["status"], validation_status=validation["status"])
                break
    return result
