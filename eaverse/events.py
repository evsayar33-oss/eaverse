"""Test calendar effects using dated releases and executable daily intervals."""

from __future__ import annotations

import itertools
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import research
from .universe import read_url


ARCHIVES = {
    "US_CPI": ("https://www.bls.gov/bls/news-release/cpi.htm", r"/archives/cpi_(\d{8})\.htm", "%m%d%Y"),
    "US_PAYROLLS": ("https://www.bls.gov/bls/news-release/empsit.htm", r"/archives/empsit_(\d{8})\.htm", "%m%d%Y"),
    "US_PPI": ("https://www.bls.gov/bls/news-release/ppi.htm", r"/archives/ppi_(\d{8})\.htm", "%m%d%Y"),
    "FOMC": ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
             r"/pressreleases/monetary(\d{8})a\.htm", "%Y%m%d"),
}
CALENDAR = tuple(f"WEEKDAY_{n}" for n in range(7)) + ("MONTH_FIRST_3", "MONTH_LAST_3")
STUDY_VERSION = "calendar-1"
MIN_EVENTS = 3
BASELINES = {
    "buy_hold": None,
    "sma_50": {"rules": [["sma", 50]], "vote": 1, "confirmation": 1, "sizing": "full"},
    "ema_26": {"rules": [["ema", 26]], "vote": 1, "confirmation": 1, "sizing": "full"},
    "momentum_30": {"rules": [["momentum", 30]], "vote": 1, "confirmation": 1, "sizing": "full"},
}


def parse_dates(raw, pattern, fmt, today):
    result = []
    for value in set(re.findall(pattern, raw)):
        try:
            day = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        if date(2017, 1, 1) <= day < today:
            result.append(day.isoformat())
    return sorted(result)


def refresh(db, today):
    rows = {row["family"]: row for row in db.execute("SELECT * FROM event_catalog")}
    event_dates, coverage = {}, {}

    def download(family):
        url, pattern, fmt = ARCHIVES[family]
        try:
            days = parse_dates(read_url(url), pattern, fmt, today)
            recent = [day for day in days if day >= f"{today.year - 3}-01-01"]
            if len(recent) < (15 if family == "FOMC" else 24):
                raise ValueError("archive coverage insufficient")
            return days, None
        except (OSError, ValueError) as exc:
            return None, type(exc).__name__

    pending = [family for family in ARCHIVES if family not in rows or rows[family]["checked"] != today.isoformat()]
    with ThreadPoolExecutor(max_workers=4) as workers:
        for family, (days, error) in zip(pending, workers.map(download, pending)):
            if days:
                db.execute("INSERT OR REPLACE INTO event_catalog VALUES (?,?,?,?,?)",
                           (family, today.isoformat(), today.isoformat(), json.dumps(days), None))
            elif family in rows:
                db.execute("UPDATE event_catalog SET checked=?, error=? WHERE family=?",
                           (today.isoformat(), error, family))

    for row in db.execute("SELECT * FROM event_catalog"):
        dates = json.loads(row["dates"])
        current = (today - date.fromisoformat(row["asof"])).days <= 3
        if current:
            event_dates[row["family"]] = dates
        coverage[row["family"]] = {"dates": len(dates), "fetched": row["asof"], "usable": current, "error": row["error"]}
    for family in ARCHIVES:
        if family not in coverage:
            coverage[family] = {"usable": False, "dates": 0}
    return event_dates, coverage


def masks(index, dates):
    output = {}
    for family, days in dates.items():
        published = pd.DatetimeIndex(days)
        # The open on the release date might precede publication. The first
        # eligible interval starts on the first session with a later date.
        after = index.searchsorted(published, side="right")
        for offset in (1, 2, 3):
            eligible = after + offset - 1
            values = np.zeros(len(index), dtype=bool)
            values[eligible[eligible < len(index)]] = True
            output[family, offset] = values

    for day in range(7):
        output[f"WEEKDAY_{day}", 1] = index.dayofweek == day
    # Calendar month ends are known in advance. Use calendar days so the
    # signal does not depend on the future contents of a partial dataset.
    output["MONTH_FIRST_3", 1] = index.day <= 3
    output["MONTH_LAST_3", 1] = index.day >= index.days_in_month - 2
    return output


def designs(available, baselines):
    for family, offset in sorted(available):
        for base in baselines:
            for mode in ("only", "avoid"):
                yield ([(family, offset)], "single", base, mode)
    for first, second in itertools.combinations(sorted({name for name, _ in available if name in ARCHIVES}), 2):
        for combine in ("and", "or"):
            for base in baselines:
                yield ([(first, 1), (second, 1)], combine, base, "only")
    for first, second in itertools.combinations(sorted({name for name, _ in available if name in ARCHIVES}), 2):
        for lag in (2, 3):
            for base in baselines:
                yield ([(first, r) for r in range(1, lag + 1)] + [(second, 1)], "recent_overlap", base, "only")
    for family in sorted({name for name, _ in available if name in ARCHIVES}):
        for weekday in range(7):
            yield ([(family, 1), (f"WEEKDAY_{weekday}", 1)], "and", "sma_50", "only")


def select_mask(mask_map, conditions, combine):
    parts = [mask_map[tuple(key)] for key in conditions]
    if combine == "recent_overlap":
        return np.logical_or.reduce(parts[:-1]) & parts[-1]
    if combine == "or":
        return np.logical_or.reduce(parts)
    return np.logical_and.reduce(parts)


def evaluate(db, asset, bars, dates, asof, snapshot, cost_bps, budget, technical=None, periods=252):
    rules = masks(bars.index, dates)
    prior = {r["id"] for r in db.execute("SELECT id FROM event_trials WHERE asset=? AND version=?",
                                       (asset, STUDY_VERSION))}
    eligible = {}
    season = f"{bars.index[-1].year}-Q{bars.index[-1].quarter}"
    baselines = dict(BASELINES)
    if technical:
        baselines["discovery_finalist"] = technical
    boundary = len(bars) - research.MIN_VALIDATION_BARS
    for design in designs(rules, baselines):
        identity = research.digest([season, design, baselines[design[2]]])
        if identity in prior:
            continue
        treatment = select_mask(rules, design[0], design[1])
        if treatment[200:boundary].sum() >= MIN_EVENTS and treatment[boundary:-1].sum() >= MIN_EVENTS:
            category = design[1] if design[0][0][0] in ARCHIVES else "calendar"
            eligible.setdefault(category, []).append((identity, design, treatment))
    # Interleave stand-alone macro dates, calendar effects and joint events.
    groups = [iter(sorted(items, key=lambda item: research.digest([asset, item[0]])))
              for _, items in sorted(eligible.items())]
    ordered = []
    while groups and len(ordered) < budget:
        active = []
        for group in groups:
            item = next(group, None)
            if item and len(ordered) < budget:
                ordered.append(item)
                active.append(group)
        groups = active
    specs = [spec for spec in baselines.values() if spec]
    features = research.features(bars, specs)
    baseline = {
        name: research.target(bars, features, spec, periods) if spec else pd.Series(1.0, index=bars.index)
        for name, spec in baselines.items()
    }
    daily = []
    summaries = []
    for identity, design, treatment in ordered:
        conditions, combine, base, mode = design
        base_exposure = baseline[base].shift(2).fillna(0)
        target = base_exposure * (treatment if mode == "only" else ~treatment)
        summary = {"design": design, "baseline_spec": baselines[base], "quarter": season,
                   "discovery": {}, "recent": {}}
        for label, start, stop in (("discovery", 200, boundary - 1), ("recent", boundary, len(bars) - 1)):
            options = (bars.index[start], bars.index[stop], 2 * cost_bps)
            tested, trades, _ = research.simulation(bars, target, *options, delay=0)
            reference, ref_trades, _ = research.simulation(bars, base_exposure, *options, delay=0)
            tagged = treatment[start:stop]
            summary[label] = {**research.stats(tested, trades), "baseline": research.stats(reference, ref_trades),
                              "tagged_bars": int(tagged.sum()),
                              "baseline_tagged_mean": float(reference.to_numpy()[tagged].mean()),
                              "mean_daily_net_delta": float((tested - reference).mean())}
            if label == "recent":
                daily.extend({"asset": asset, "experiment": identity, "date": day.date().isoformat(),
                              "event": bool(tagged[i]), "net": float(net), "baseline_net": float(reference.iloc[i])}
                             for i, (day, net) in enumerate(tested.items()))
        db.execute("INSERT INTO event_trials VALUES (?,?,?,?,?,?,?)",
                   (STUDY_VERSION, asset, identity, snapshot, asof, research.digest(dates), research.canonical(summary)))
        summaries.append({"id": identity, **summary})
    return {"new_trials": len(summaries), "total_trials": len(prior) + len(summaries), "trials": summaries}, daily


def write_daily(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as out:
        for record in records:
            out.write(research.canonical(record) + "\n")
