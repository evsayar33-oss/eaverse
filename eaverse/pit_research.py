"""Eaverse-native, research-only point-in-time historical event replay.

Consumes separately verified, immutable daily OHLCV and source-publication events.
Never writes production/PAPER state and never qualifies a strategy for trading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import research

SCHEMA = "eaverse.pit_replay.v1"
CLOCKS = {"OFFICIAL_PUBLICATION", "ACCEPTANCE_PLUS_24H", "EXCHANGE_BAR_CLOSE"}
RESEARCH_RIGHTS = {"OFFICIAL_PUBLIC_RESEARCH", "PERMITTED_PERSONAL_NONCOMMERCIAL"}
TIMEZONES = {"BIST": "Europe/Istanbul", "US": "America/New_York", "CRYPTO": "UTC"}
HORIZONS = (1, 3)
MODES = ("event_only", "event_sma50", "event_momentum30")
EMBARGO_BARS = 5
MIN_EVENTS_PER_FOLD = (4, 3, 3)


def utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string with UTC offset")
    value = value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamp without timezone; refusing ambiguous PIT clock")
    return stamp.astimezone(timezone.utc)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _artifact(root: Path, name: str, expected: str) -> bytes:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("unsafe dataset path")
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError("dataset path is outside manifest directory or missing")
    raw = candidate.read_bytes()
    if _digest(raw) != expected:
        raise ValueError("dataset SHA256 does not match manifest")
    return raw


def _manifest(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError("unsupported PIT input schema")
    if payload.get("rights_status") not in RESEARCH_RIGHTS:
        raise ValueError("source access rights unverified; no research replay")
    if payload.get("market") not in TIMEZONES or not payload.get("asset"):
        raise ValueError("unknown market or asset")
    if not payload.get("source_id") or not payload.get("price_source_id"):
        raise ValueError("source identifiers required")
    if payload.get("signal_frequency") != "1d":
        raise ValueError("only completed daily bars are supported")
    if not isinstance(payload.get("round_trip_stress_multiplier"), (int, float)) or payload["round_trip_stress_multiplier"] < 2:
        raise ValueError("cost stress multiplier must be at least 2")
    if not isinstance(payload.get("one_way_cost_bps"), (int, float)) or not (0 < payload["one_way_cost_bps"] <= 500):
        raise ValueError("explicit strictly positive one-way costs required")
    retrieved = utc(payload["retrieved_utc"])
    if retrieved > datetime.now(timezone.utc):
        raise ValueError("retrieval timestamp is in the future")
    for key in ("bars_sha256", "events_sha256"):
        if not isinstance(payload.get(key), str) or len(payload[key]) != 64:
            raise ValueError("complete artifact SHA256 required")
    return payload, retrieved


def _bars(raw: bytes, market: str):
    from io import BytesIO
    import exchange_calendars as xcals

    bars = pd.read_csv(BytesIO(raw))
    if list(bars.columns) != ["date", "open", "high", "low", "close", "volume"]:
        raise ValueError("OHLCV must have date,open,high,low,close,volume columns")
    index = pd.DatetimeIndex(pd.to_datetime(bars.pop("date"), format="%Y-%m-%d", errors="raise"))
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("daily bar order/identity invalid")
    bars.index = pd.DatetimeIndex(index)
    if len(bars) < 600:
        raise ValueError("fewer than 600 completed bars; no backtest")
    bars = bars.astype(float)
    if not np.isfinite(bars.to_numpy()).all() or (bars[["open", "high", "low", "close"]] <= 0).any().any() or (bars.volume < 0).any():
        raise ValueError("nonfinite, negative volume or nonpositive OHLC")
    if (bars.high < bars[["open", "low", "close"]].max(axis=1) * (1 - 1e-12)).any():
        raise ValueError("inconsistent OHLC high")
    if (bars.low > bars[["open", "high", "close"]].min(axis=1) * (1 + 1e-12)).any():
        raise ValueError("inconsistent OHLC low")
    if market == "CRYPTO":
        expected = pd.date_range(bars.index[0], bars.index[-1], freq="D")
    else:
        calendar = xcals.get_calendar("XNYS" if market == "US" else "XIST")
        expected = calendar.sessions_in_range(bars.index[0], bars.index[-1]).tz_localize(None)
    if not bars.index.equals(expected):
        raise ValueError("missing/extra daily sessions; no forward fill or silent repairs")
    return bars


def _events(raw: bytes, manifest: dict, retrieved: datetime):
    market, asset = manifest["market"], manifest["asset"]
    event_map = {}
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        for key in ("id", "kind", "market", "asset", "public_at_utc", "clock_basis"):
            if key not in record:
                raise ValueError(f"event line {number}: missing {key}")
        if record["market"] != market or record["asset"] != asset:
            raise ValueError("cross-asset or cross-market event contamination")
        if record["clock_basis"] not in CLOCKS:
            raise ValueError("unverified publication time source")
        if not str(record["kind"]).replace("_", "").isalnum():
            raise ValueError("unsafe event family")
        public = utc(record["public_at_utc"])
        if public > retrieved:
            raise ValueError("event not yet published when archive was retrieved")
        if "first_seen_utc" in record:
            first_seen = utc(record["first_seen_utc"])
            if first_seen < public or first_seen > retrieved:
                raise ValueError("first_seen was backdated or exceeds retrieval")
        identity = str(record["id"])
        if not identity:
            raise ValueError("event identity missing")
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        if identity in event_map and event_map[identity][0] != canonical:
            raise ValueError("conflicting revisions of the same event in one snapshot")
        event_map[identity] = (canonical, record, public)
    return [value[1:] for _, value in sorted(event_map.items())]


def _availability_index(index: pd.DatetimeIndex, public: datetime, market: str) -> int:
    # Never assume an event published on a weekend was known on Friday.
    local = public.astimezone(ZoneInfo(TIMEZONES[market]))
    date = pd.Timestamp(local.date())
    return int(index.searchsorted(date, side="left"))


def _event_desired(index: pd.DatetimeIndex, events, market, kind, horizon):
    desired = pd.Series(0.0, index=index)
    anchor = set()
    for record, public in events:
        if record["kind"] != kind:
            continue
        point = _availability_index(index, public, market)
        if point >= len(index):
            continue
        anchor.add(point)
        desired.iloc[point:min(len(index), point + horizon)] = 1.0
    return desired, sorted(anchor)


def _folds(index: pd.DatetimeIndex):
    size = len(index)
    first, second = int(size * .50), int(size * .75)
    # Explicit embargo prevents event treatment spanning two evaluation folds.
    folds = (
        ("discovery", 0, first - EMBARGO_BARS),
        ("validation", first + EMBARGO_BARS, second - EMBARGO_BARS),
        ("untouched_holdout", second + EMBARGO_BARS, size - 1),
    )
    if any(stop <= start + 60 for _, start, stop in folds):
        raise ValueError("insufficient folds after embargo")
    return folds


def replay(manifest_path: Path) -> dict:
    manifest_path = Path(manifest_path).resolve()
    manifest, retrieved = _manifest(manifest_path)
    root = manifest_path.parent
    bars = _bars(_artifact(root, manifest["bars_file"], manifest["bars_sha256"]), manifest["market"])
    events = _events(_artifact(root, manifest["events_file"], manifest["events_sha256"]), manifest, retrieved)
    folds = _folds(bars.index)
    baseline_specs = {
        "event_sma50": {"rules": [["sma", 50]], "vote": 1, "sizing": "full", "confirmation": 1},
        "event_momentum30": {"rules": [["momentum", 30]], "vote": 1, "sizing": "full", "confirmation": 1},
    }
    indicators = research.features(bars, list(baseline_specs.values()))
    targets = {mode: research.target(bars, indicators, spec, 365 if manifest["market"] == "CRYPTO" else 252)
               for mode, spec in baseline_specs.items()}
    cost = float(manifest["one_way_cost_bps"]) * float(manifest["round_trip_stress_multiplier"])
    experiments = []
    for kind in sorted({record["kind"] for record, _ in events}):
        for horizon in HORIZONS:
            pulse, anchors = _event_desired(bars.index, events, manifest["market"], kind, horizon)
            for mode in MODES:
                # Variants are fixed before any returns are evaluated.
                signal = pulse if mode == "event_only" else pulse * targets[mode]
                spec = {"kind": kind, "horizon": horizon, "mode": mode, "delay_bars": 1,
                        "cost_bps": cost, "folds": [(n, a, b) for n, a, b in folds],
                        "embargo_bars": EMBARGO_BARS}
                family = {"id": research.digest([SCHEMA, manifest["asset"], spec]), "spec": spec,
                          "folds": {}, "paper_eligible": False, "alpha_verified": False}
                qualified_count = True
                for (name, start, end), threshold in zip(folds, MIN_EVENTS_PER_FOLD):
                    event_count = sum(start <= n <= end for n in anchors)
                    sample = {"events": event_count, "min_required": threshold,
                              "first": bars.index[start].date().isoformat(),
                              "last": bars.index[end].date().isoformat()}
                    if event_count < threshold:
                        sample["status"] = "INSUFFICIENT_EVENTS"
                        qualified_count = False
                    else:
                        returns, trades, turnover = research.simulation(
                            bars, signal, bars.index[start], bars.index[end], cost, delay=1
                        )
                        sample.update(research.stats(returns, trades))
                        sample.update({"status": "RETROSPECTIVE_ONLY", "turnover": turnover,
                                       "observed_return_intervals": len(returns)})
                    family["folds"][name] = sample
                family["status"] = ("RETROSPECTIVE_ONLY_NO_PROMOTION" if qualified_count
                                    else "DATA_INSUFFICIENT_NO_PROMOTION")
                experiments.append(family)
    return {
        "schema": "eaverse.pit_replay_result.v1", "market": manifest["market"],
        "asset": manifest["asset"], "source_id": manifest["source_id"],
        "price_source_id": manifest["price_source_id"], "retrieved_utc": manifest["retrieved_utc"],
        "manifest_sha256": _digest(manifest_path.read_bytes()),
        "bars_sha256": manifest["bars_sha256"], "events_sha256": manifest["events_sha256"],
        "bars": len(bars), "events": len(events), "variants": len(experiments),
        "split": "chronological_50_25_25_embargo5", "execution": "signal_after_publication_next_daily_open",
        "price_pit_universe_verified": manifest.get("price_pit_universe_verified") is True,
        "production_changes": False, "paper_eligible": False, "alpha_verified": False,
        "forward_evidence": False, "discoveries": experiments,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("out/eaverse_pit_replay.json"))
    args = parser.parse_args()
    result = replay(args.manifest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{result['variants']} retrospective variants; PAPER eligible = false")


if __name__ == "__main__":
    main()
