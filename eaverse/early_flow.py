"""Eaverse V5: causal, as-of hourly spot-flow research with explicit controls.

A historically downloaded trade-hour is not a historically first-seen
research decision. This is fixed, research-only, retrospective hypothesis
comparison; never a SHORT fill, approved PAPER strategy or LIVE signal.

All signals only use CLOSED Binance spot hourly klines, official ZIP SHA256
checks, venue-native taker BUY base volume, and past rolling baselines.
The result labels prospective evidence FALSE, including for holdout folds.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import pit_adapters, pit_binance_pilot

SCHEMA = "eaverse.early_flow_hourly.v1"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
MONTHS = pit_binance_pilot.MONTHS
HORIZONS = (1, 4, 12, 24)
FAMILIES = (
    "flow_pressure",          # signed taker flow with expanding turnover
    "flow_divergence",        # taker flow leads a flat/opposing candle
    "flow_acceleration",      # signed taker flow surprises 24h own baseline
    "flow_tail",              # prior-168h as-of 85/15 percentile
)
DIRECTIONS = (1, -1)
EMBARGO = 25  # hours, larger than maximal studied 24-hour hold
MIN_CASES = {"discovery": 30, "validation": 15, "untouched_holdout": 15}
ONE_WAY_BPS = 12.0  # explicit Binance spot fee+slippage assumption, not live fills
STRESS_MULTIPLIER = 2.0
ROUND_TRIP_BPS = 2 * ONE_WAY_BPS * STRESS_MULTIPLIER
MAX_MATCH_LOOKBACK = 24 * 28
MAX_SOURCE_RECORDS = 24 * 366 * 2 + 24 * 7


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _hourly_csv(blob: bytes, symbol: str, month: str):
    """Read one original Binance 12-column spot 1h kline file, no interpolation."""
    try:
        decoded = blob.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("hourly source encoding invalid") from exc
    parsed = []
    for line_no, row in enumerate(csv.reader(io.StringIO(decoded)), 1):
        if not row:
            continue
        if len(row) != 12:
            raise ValueError("hourly kline requires 12 columns")
        try:
            start_raw = int(row[0])
            end_raw = int(row[6])
            unit = 1_000_000 if start_raw >= 10**15 else 1_000
            opened = datetime.fromtimestamp(start_raw / unit, timezone.utc)
            if (opened.strftime("%Y-%m") != month or
                opened.minute != 0 or opened.second != 0 or
                opened.microsecond != 0 or
                end_raw != start_raw + 3600 * unit - 1):
                raise ValueError("wrong month, timeframe or incomplete hourly candle")
            opn, high, low, close, vol = [float(row[i]) for i in (1, 2, 3, 4, 5)]
            quote = float(row[7])
            taker = float(row[9])
        except (OSError, OverflowError, TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"bad hourly kline at source row {line_no}") from exc
        if (not all(math.isfinite(n) for n in (opn, high, low, close, vol, quote, taker))
            or min(opn, high, low, close) <= 0 or
            high < max(opn, low, close) or low > min(opn, high, close) or
            vol < 0 or quote < 0 or not 0 <= taker <= vol * (1+1e-10)):
            raise ValueError(f"impossible OHLC/turnover/taker quantity at row {line_no}")
        # Both quote and base volumes are observed exchange aggregates.
        if vol > 0 and not low * vol * .98 <= quote <= high * vol * 1.02:
            raise ValueError("base/quote volume units inconsistent")
        parsed.append((opened, opn, high, low, close, vol, quote, taker))
    return parsed


def parse_months(month_blobs: dict, symbol: str):
    if symbol not in SYMBOLS:
        raise ValueError("unexpected or hindsight-selected ticker")
    if tuple(sorted(month_blobs)) != MONTHS:
        raise ValueError("expected immutable complete 24-month hypothesis period")
    rows = []
    for month in MONTHS:
        rows.extend(_hourly_csv(month_blobs[month], symbol, month))
    if not 24*700 <= len(rows) <= MAX_SOURCE_RECORDS:
        raise ValueError("insufficient historical hourly source coverage")
    index = pd.DatetimeIndex([r[0] for r in rows], tz="UTC")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("duplicate or unsorted hourly source")
    expected = pd.date_range("2024-01-01T00:00:00Z",
                             "2025-12-31T23:00:00Z", freq="h")
    if not index.equals(expected):
        raise ValueError("missing/extra closed hourly bars; no interpolation")
    bars = pd.DataFrame(
        np.array([r[1:] for r in rows], dtype=float),
        index=index, columns=("open","high","low","close","volume","quote_volume","taker_buy_base"))
    return bars


def download_verified(symbol: str, fetcher=pit_binance_pilot.fetch_bytes):
    """All original files must pass publisher SHA checksum before research."""
    if symbol not in SYMBOLS:
        raise ValueError("symbol not pre-registered")
    blobs, provenance = {}, []
    for month in MONTHS:
        zip_name = f"{symbol}-1h-{month}.zip"
        url = f"{pit_binance_pilot.BASE_URL}/{symbol}/1h/{zip_name}"
        zipped = fetcher(url, pit_binance_pilot.MAX_ZIP_BYTES)
        checksum = fetcher(url+".CHECKSUM", pit_binance_pilot.MAX_CHECKSUM_BYTES)
        raw = pit_binance_pilot.unpack_verified(zipped, checksum, zip_name)
        blobs[month] = raw
        provenance.append({
            "month": month, "exchange": "BINANCE_SPOT",
            "timeframe": "1h", "zip_sha256": hashlib.sha256(zipped).hexdigest(),
            "csv_sha256": hashlib.sha256(raw).hexdigest(),
            "archive_url": url, "checksum_verified": True,
        })
    return parse_months(blobs, symbol), provenance


def asof_features(bars: pd.DataFrame):
    """Every moving reference is shifted(1): it excludes the signal candle."""
    imbalance = (2*bars.taker_buy_base.div(bars.volume.replace(0, np.nan)) - 1)
    imbalance = imbalance.fillna(0.0)
    ratio = bars.quote_volume.div(
        bars.quote_volume.shift(1).rolling(168, min_periods=168).median()
        .replace(0, np.nan))
    quiet = (bars.close.div(bars.open).sub(1).abs() <= .004)
    prior_24h = imbalance.shift(1).rolling(24, min_periods=24).mean()
    q15 = imbalance.shift(1).rolling(168, min_periods=168).quantile(.15)
    q85 = imbalance.shift(1).rolling(168, min_periods=168).quantile(.85)
    return pd.DataFrame({
        "imbalance": imbalance, "turnover_ratio": ratio,
        "quiet": quiet.fillna(False),
        "flow_24h_prior_mean": prior_24h,
        "q15_prior168": q15, "q85_prior168": q85,
        "bar_return": bars.close.div(bars.open).sub(1),
    }, index=bars.index)


def flags(feat, family, direction):
    """Pre-registered economic hypotheses, no return-informed optimization."""
    imbal, volume, quiet = feat.imbalance, feat.turnover_ratio, feat.quiet
    if family == "flow_pressure":
        mask = (direction*imbal >= .08) & (volume >= 1.25) & quiet
    elif family == "flow_divergence":
        mask = (direction*imbal >= .07) & (direction*feat.bar_return <= 0) & quiet
    elif family == "flow_acceleration":
        mask = ((direction*imbal >= .06) &
                (direction*(imbal - feat.flow_24h_prior_mean) >= .10) &
                (volume >= 1.10) & quiet)
    elif family == "flow_tail":
        mask = ((imbal >= feat.q85_prior168) if direction > 0
                else (imbal <= feat.q15_prior168)) & quiet & (volume >= .8)
    else:
        raise ValueError("unknown/future-learned family")
    # Required prior lookback not yet available: no signal.
    return mask.fillna(False).to_numpy(dtype=bool)


def folds(count):
    half, third = count//2, (count*3)//4
    split = {
        "discovery": (168, half-EMBARGO),
        "validation": (half+EMBARGO, third-EMBARGO),
        "untouched_holdout": (third+EMBARGO, count-HORIZONS[-1]-2),
    }
    if min(end-start for start,end in split.values()) < 120:
        raise ValueError("cannot form independent time splits")
    return split


def _outcome(opens: np.ndarray, i: int, horizon: int, direction: int):
    entry = opens[i+1]
    exit_price = opens[i+1+horizon]
    if not np.isfinite(entry) or not np.isfinite(exit_price) or min(entry, exit_price) <= 0:
        raise ValueError("bad future open in research outcome")
    return direction * (exit_price/entry - 1) * 10000


def _matched_prior_control(idx, features, active, event_i, horizon, first):
    """Find a *previous* same-UTC-hour quiet-bar control, not a shifted future.
    Controls must be mature BEFORE the event, and have roughly similar turnover.
    """
    ratio = features.turnover_ratio.to_numpy()
    quiet = features.quiet.to_numpy(dtype=bool)
    for j in range(event_i-24, max(first, event_i-MAX_MATCH_LOOKBACK)-1, -24):
        if (j < first or j+1+horizon >= event_i or active[j] or
            not quiet[j] or not np.isfinite(ratio[j]) or
            not np.isfinite(ratio[event_i])):
            continue
        if .5 <= ratio[j]/ratio[event_i] <= 2:
            return j
    return None


def _evaluate_one(bars, feat, family, direction, horizon, split):
    opens = bars.open.to_numpy()
    active = flags(feat, family, direction)
    result = {}
    for name,(first,last) in split.items():
        selected, controls = [], []
        previous = first-horizon-1
        for i in range(first,last-horizon-1):
            if not active[i] or i <= previous+horizon:
                continue
            selected.append((i,_outcome(opens,i,horizon,direction)))
            matched = _matched_prior_control(opens,feat,active,i,horizon,first)
            if matched is not None:
                controls.append((i,matched,_outcome(opens,matched,horizon,direction)))
            previous=i
        cases=np.array([v for _,v in selected],dtype=float)
        comp=np.array([v for _,_,v in controls],dtype=float)
        n=len(cases)
        enough=(n>=MIN_CASES[name])
        result[name]={
            "cases_nonoverlapping":n,
            "required_cases":MIN_CASES[name],
            "prior_same_hour_controls":len(comp),
            "case_rate_per_1000h":round(1000*n/max(1,last-first),3),
            "first_utc":bars.index[first].isoformat(),
            "last_utc":bars.index[last].isoformat(),
            "directional_hit_rate":round(float(np.mean(cases>0)),5) if n else None,
            "mean_signed_gross_bps":round(float(np.mean(cases)),3) if n else None,
            "mean_matched_control_bps":round(float(np.mean(comp)),3) if len(comp) else None,
            "mean_case_after_stressed_fees_bps":(
                round(float(np.mean(cases))-ROUND_TRIP_BPS,3) if n and direction==1 else None),
            "mean_matched_lift_bps":(
                round(float(np.mean(cases))-float(np.mean(comp)),3)
                if len(comp)>=max(8,n//2) and n else None),
            "minimum_sample_satisfied":enough,
            "cost_bps_round_trip_assumption":ROUND_TRIP_BPS,
            "downside_case_is_executable_spot_short":False,
            "data_status":"DESCRIPTIVE_ONLY" if enough else "INSUFFICIENT_INDEPENDENT_CASES",
        }
    return result


def analyze(bars: pd.DataFrame, provenance: list, symbol: str, *, captured_utc: str|None=None):
    if symbol not in SYMBOLS:
        raise ValueError("unknown symbol")
    if len(provenance)!=len(MONTHS) or not all(item.get("checksum_verified") for item in provenance):
        raise ValueError("missing official monthly source checksums")
    feat=asof_features(bars)
    split=folds(len(bars))
    cases=[]
    for family in FAMILIES:
        for direction in DIRECTIONS:
            for horizon in HORIZONS:
                study=_evaluate_one(bars,feat,family,direction,horizon,split)
                adequate=all(item["minimum_sample_satisfied"] for item in study.values())
                # Even robust retrospective folds are not forward/PAPER admission.
                if not adequate:
                    decision="DEFER_MORE_INDEPENDENT_CASES"
                elif (direction>0 and
                      all(study[k]["mean_case_after_stressed_fees_bps"]>0
                          for k in ("validation","untouched_holdout")) and
                      all((study[k]["mean_matched_lift_bps"] or float("-inf"))>0
                          for k in ("validation","untouched_holdout"))):
                    decision="EXPLORATORY_FORWARD_RESEARCH_CANDIDATE_NOT_PAPER"
                else:
                    decision="NO_REPLICATED_AFTER_COST_EDGE_OR_DOWNSIDE_DIAGNOSTIC"
                cases.append({
                    "id":digest([SCHEMA,symbol,family,direction,horizon]),
                    "family":family,"direction":"LONG_TIMING" if direction==1 else "RISK_AVOIDANCE_NOT_SHORT",
                    "horizon_hours":horizon,
                    "folds":study,"decision":decision,
                    "forward_validated":False,"paper_eligible":False,"trade_authorized":False,
                })
    policy={"schema":SCHEMA,"source":"BINANCE_SPOT_1H", "symbol":symbol,
        "preregistered_families":list(FAMILIES),"directions":list(DIRECTIONS),
        "horizons_hours":list(HORIZONS),"start":"2024-01-01","end":"2025-12-31",
        "one_way_bps_fee_slippage_assumption":ONE_WAY_BPS,
        "stress_multiplier":STRESS_MULTIPLIER,
        "pre_price_definition":"signal at fully closed 1h bar with abs bar return <=0.4pct; NO PRICE BREAKOUT REQUIRED",
        "entry":"NEXT_HOURLY_OPEN","exit":"h_hours_later_open",
        "no_hindsight_threshold_search":True,"time_split":"50pct_25pct_25pct_with_25h_embargo",
        "source_archive_revisions_reconstruct_past_first_seen":False,
        "total_multiple_trials":len(cases),"multiple_testing_adjusted_alpha_proven":False}
    source_summary=[{"month":p["month"],"zip_sha256":p["zip_sha256"],
                     "csv_sha256":p["csv_sha256"]} for p in provenance]
    return {"schema":SCHEMA, "captured_utc":captured_utc or datetime.now(timezone.utc).isoformat(),
        "asset":symbol,"market":"CRYPTO","venue":"BINANCE_SPOT",
        "observed_hourly_bars":len(bars),"source_months":len(provenance),
        "source_archive_digest":digest(source_summary),"source_zip_provenance":source_summary,
        "strategy_policy_hash":digest(policy),"policy":policy,
        "experiment_count":len(cases),"results":cases,
        "counts_by_decision":dict(Counter(x["decision"] for x in cases)),
        "original_source_revisions_pit_verified":False,
        "independent_venue_validated":False,
        "forward_validated":False,"paper_eligible":False,"live_allowed":False,
        "raw_market_data_exported":False}


def append_ledger(path, report):
    """Append-only SHA chain of summary-only decisions, not historical market ticks."""
    path=Path(path)
    previous="GENESIS"
    prior=[]
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            item=json.loads(line)
            signed={k:v for k,v in item.items() if k!="sha256"}
            if item.get("prev_sha256")!=previous or digest(signed)!=item.get("sha256"):
                raise ValueError("V5_RESEARCH_EVIDENCE_SHA_CHAIN_INVALID")
            previous=item["sha256"]
            prior.append(item)
    # A run with the same source and policy is not a new observation,
    # even if clock/capture changed. Never call retrospective replay forward.
    if prior and (prior[-1]["source_archive_digest"]==report["source_archive_digest"]
                  and prior[-1]["strategy_policy_hash"]==report["strategy_policy_hash"]):
        return {"status":"UNCHANGED_SOURCE_NO_SYNTHETIC_NEW_OBSERVATION","records":len(prior)}
    row={"schema":"eaverse.early_flow_ledger.v1",
         "captured_utc":report["captured_utc"],
         "source_archive_digest":report["source_archive_digest"],
         "strategy_policy_hash":report["strategy_policy_hash"],
         "asset":report["asset"],"experiment_count":report["experiment_count"],
         "counts_by_decision":report["counts_by_decision"],
         "source_revision": bool(prior and
              prior[-1]["source_archive_digest"]!=report["source_archive_digest"]),
         "forward_validated":False,"paper_eligible":False,
         "prev_sha256":previous}
    row["sha256"]=digest(row)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a",encoding="utf-8") as stream:
        stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+"\n")
    return {"status":"APPENDED_RESEARCH_RETROSPECTIVE_ONLY",
            "records":len(prior)+1,"sha256":row["sha256"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol",choices=SYMBOLS,default="BTCUSDT")
    parser.add_argument("--out",type=Path,default=Path("out/eaverse_hourly"))
    parser.add_argument("--ledger",type=Path)
    args=parser.parse_args()
    bars, provenance=download_verified(args.symbol)
    report=analyze(bars,provenance,args.symbol)
    args.out.mkdir(parents=True,exist_ok=True)
    if args.ledger:
        report["local_summary_ledger"]=append_ledger(args.ledger,report)
    (args.out/"report.json").write_text(
        json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print("EAVERSE_HOURLY_REAL_ARCHIVES",
          args.symbol,report["source_months"],"HOURS",report["observed_hourly_bars"],
          "EXPERIMENTS",report["experiment_count"],"DECISIONS",
          report["counts_by_decision"],"PAPER",report["paper_eligible"])


if __name__=="__main__":
    main()
