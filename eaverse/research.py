"""Enumerate bounded strategy hypotheses and simulate causal execution."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from functools import lru_cache
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from . import metrics, recovery_risk


VERSION = "1"
FEATURES = {
    "sma": (20, 50, 100, 200),
    "ema": (12, 26, 60),
    "momentum": (10, 30, 90, 180),
    "breakout": (20, 55, 100),
    "rsi_recovery": (10, 14, 21),
    "rsi_trend": (10, 14, 21),
    "volume": (15, 30, 60),
    "low_vol": (15, 30, 60),
    "atr": (10, 20, 40),
    "bollinger": (15, 20, 40),
}
EXTENDED = {"macd": (8, 12, 20), "stoch_recovery": (9, 14, 21),
            "adx": (10, 14, 21), "obv": (10, 30, 60), "vwma": (20, 50, 100),
            "mean_reversion": (10, 20, 50)}
LEVELS = {"rsi_recovery": (25, 30, 35, 40), "rsi_trend": (45, 50, 55, 60),
          "volume": (1.05, 1.15, 1.35, 1.5), "atr": (0.03, 0.05, 0.07),
          "bollinger": (0.75, 1.0, 1.5, 2.0), "adx": (18, 22, 27, 32),
          "stoch_recovery": (15, 20, 25, 35), "mean_reversion": (0.75, 1.0, 1.5, 2.0)}
STRATEGIC_FEATURES = set(FEATURES) - {"volume", "low_vol", "atr"}
MIN_VALIDATION_BARS = 180
PAPER_BARS = 90
PAPER_TRADES = 8
PAPER_EXECUTION_DELAY = 2
LEGACY_POLICY = {
    "history_version": VERSION,
    "history_folds": 3,
    "history_fold_min_bars": 60,
    "history_min_profitable_stress_folds": 2,
    "history_cost_multiplier": 2,
    "history_extra_delay_bars": 1,
    "history_total_min_trades": 6,
    "paper_bars": PAPER_BARS,
    "paper_execution_delay_bars": PAPER_EXECUTION_DELAY,
    "paper_min_round_trips": PAPER_TRADES,
    "paper_max_drawdown": 0.25,
    "paper_family_alpha": 0.05,
    "paper_hac_lags": [5, 10],
}
# Existing PAPER experiments retain their originally frozen contract.
# New research/admissions no longer use a percentage drawdown threshold.
POLICY = {k:v for k,v in LEGACY_POLICY.items() if k!="paper_max_drawdown"}
POLICY.update({"paper_risk_policy":recovery_risk.VERSION,
               "paper_max_recovery_days":recovery_risk.MAX_RECOVERY_DAYS})


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def catalog():
    for size in (1, 2, 3):
        for families in itertools.combinations(FEATURES, size):
            if not STRATEGIC_FEATURES.intersection(families):
                continue
            for windows in itertools.product(*(FEATURES[family] for family in families)):
                for vote in ({1: (1,), 2: (2, 1), 3: (3, 2)}[size]):
                    for sizing in ("full", "vol_target"):
                        for confirmation in (1, 2):
                            yield {"rules": list(zip(families, windows)), "vote": vote,
                                   "sizing": sizing, "confirmation": confirmation}


@lru_cache(maxsize=1)
def base_catalog():
    return tuple((tuple(name for name, _ in spec["rules"]), digest(spec), spec) for spec in catalog())


def choose(asset: str, prior: set, family_results: dict, budget: int):
    families = defaultdict(list)
    for family, key, spec in base_catalog():
        if key not in prior:
            families[family].append((key, spec))
    rng = random.Random(digest([asset, len(prior)]))
    for group in families.values():
        rng.shuffle(group)
    # A smoothed estimate guides some of the allocation. Under-explored families
    # stay in rotation and only discovery folds can inform the estimate.
    def priority(family):
        attempted, promising = family_results.get(",".join(family), (0, 0))
        estimate = (promising + 1) / (attempted + 4)
        return (-estimate - 0.4 / math.sqrt(1 + attempted), digest([asset, family]))

    groups = {}
    for size in (1, 2, 3):
        group = sorted((family for family in families if len(family) == size), key=priority)
        if group:
            groups[size] = deque(deque(families[family]) for family in group)
    selected = []
    active_sizes = deque(groups)
    while active_sizes and len(selected) < budget:
        size = active_sizes.popleft()
        queues = groups[size]
        queue = queues.popleft()
        key, spec = queue.popleft()
        selected.append((key, spec))
        if queue:
            queues.append(queue)
        if queues:
            active_sizes.append(size)
    return selected


def expand(asset, prior, family_results, parents, budget):
    """Allocate trials to known families, new settings and transferred winners."""
    fixed = choose(asset, prior, family_results, (budget + 2) // 3)
    chosen = dict(fixed)
    seen = prior | set(chosen)
    rng = random.Random(digest([asset, len(prior), "settings-v1"]))
    options = {**FEATURES, **EXTENDED}
    for turn in range(max(24, budget * 6)):
        if len(chosen) >= budget:
            break
        if parents and turn % 2 == 0:
            spec = json.loads(canonical(parents[(turn // 2) % len(parents)]))
            slot = rng.randrange(len(spec["rules"]))
            name, old = spec["rules"][slot]
            nearby = round(old * rng.choice((0.60, 0.80, 0.90, 1.10, 1.20, 1.40)))
            spec["rules"][slot] = [name, min(100 if name in {"macd", "adx", "low_vol"} else 200, max(5, nearby))]
        else:
            size = (turn // 2) % 3 + 1
            names = sorted(rng.sample(sorted(options), size))
            if not (STRATEGIC_FEATURES | set(EXTENDED)).intersection(names):
                continue
            spec = {"rules": [[name, rng.choice(options[name])] for name in names],
                    "vote": rng.choice(tuple(range(max(1, size - 1), size + 1))),
                    "sizing": rng.choice(("full", "vol_target")), "confirmation": rng.choice((1, 2, 3))}
            name = rng.choice(names)
        if name in LEVELS and rng.random() < 0.75:
            spec.setdefault("levels", {})[name] = rng.choice(LEVELS[name])
        identity = digest(spec)
        if identity not in seen:
            seen.add(identity)
            chosen[identity] = spec
    return list(chosen.items())


def signal_key(spec, name, window):
    level = spec.get("levels", {}).get(name)
    return (name, window) if level is None else (name, window, level)


def ranking_key(item):
    key, spec, metrics = item
    return (-metrics["net"], metrics["drawdown"], key, len(spec["rules"]))


def features(bars: pd.DataFrame, specs=None):
    close = bars.close
    change = close.diff()
    returns = close.pct_change()
    tr = pd.concat([bars.high - bars.low, (bars.high - close.shift()).abs(),
                    (bars.low - close.shift()).abs()], axis=1).max(axis=1)
    requested = (
        {(name, n) for name, windows in FEATURES.items() for n in windows}
        if specs is None else {signal_key(spec, name, n) for spec in specs for name, n in spec["rules"]}
    )
    result = {}
    for key in requested:
        family, n, *levels = key
        level = levels[0] if levels else None
        if family == "sma":
            flag = close > close.rolling(n).mean()
        elif family == "ema":
            flag = close > close.ewm(span=n, min_periods=n, adjust=False).mean()
        elif family == "momentum":
            flag = close > close.shift(n)
        elif family == "breakout":
            flag = close > bars.high.shift().rolling(n).max()
        elif family.startswith("rsi"):
            gain = change.clip(lower=0).rolling(n).mean()
            loss = -change.clip(upper=0).rolling(n).mean()
            rsi = 100 * gain / (gain + loss)
            floor = level or 35
            flag = (rsi > (level or 50)) if family == "rsi_trend" else ((rsi > floor) & (rsi.shift() < floor + 10))
        elif family == "volume":
            flag = bars.volume > (level or 1.15) * bars.volume.shift().rolling(n).mean()
        elif family == "low_vol":
            vol = returns.rolling(n).std()
            flag = vol <= vol.rolling(100).median()
        elif family == "atr":
            flag = (tr.rolling(n).mean() / close) < (level or 0.05)
        elif family == "bollinger":
            flag = close > close.rolling(n).mean() + (level or 1.0) * close.rolling(n).std()
        elif family == "macd":
            macd = close.ewm(span=n, min_periods=n, adjust=False).mean() - close.ewm(
                span=2*n, min_periods=2*n, adjust=False).mean()
            flag = macd > macd.ewm(span=max(3, n//2), adjust=False).mean()
        elif family == "stoch_recovery":
            low = bars.low.rolling(n).min()
            high = bars.high.rolling(n).max()
            position = 100 * (close - low) / (high - low)
            threshold = level or 20
            flag = (position >= threshold) & (position.shift() < threshold)
        elif family == "adx":
            up, down = bars.high.diff(), -bars.low.diff()
            plus = up.where((up > down) & (up > 0), 0).ewm(alpha=1/n, min_periods=n, adjust=False).mean()
            minus = down.where((down > up) & (down > 0), 0).ewm(alpha=1/n, min_periods=n, adjust=False).mean()
            dx = 100 * (plus - minus).abs() / (plus + minus).where(plus + minus > 0)
            flag = (plus > minus) & (dx.ewm(alpha=1/n, min_periods=n, adjust=False).mean() > (level or 22))
        elif family == "obv":
            flag = (np.sign(change) * bars.volume).rolling(n).sum() > 0
        elif family == "vwma":
            flag = close > (close * bars.volume).rolling(n).sum() / bars.volume.rolling(n).sum()
        elif family == "mean_reversion":
            flag = close < close.rolling(n).mean() - (level or 1.0) * close.rolling(n).std()
        else:
            raise ValueError(f"unknown feature {family}")
        result[key] = flag.fillna(False).astype(float)
    return result


def target(bars, signals, spec, periods):
    votes = sum(signals[signal_key(spec, *rule)] for rule in spec["rules"])
    result = (votes >= spec["vote"]).astype(float)
    if spec["confirmation"] > 1:
        result = result.rolling(spec["confirmation"]).min().fillna(0)
    if spec["sizing"] == "vol_target":
        vol = bars.close.pct_change().rolling(30).std() * math.sqrt(periods)
        scale = (0.25 / vol.where(vol > 0)).clip(upper=1).fillna(0)
        result *= (scale * 4).round() / 4
    return result


def direction_check(bars, long, spec, signals, discovery, validation, cost_bps, periods):
    """Exploratory short exposure from failed votes, including daily borrow and exit fees."""
    votes = sum(signals[signal_key(spec, *rule)] for rule in spec["rules"])
    bearish = (len(spec["rules"]) - votes >= spec["vote"]) & (votes < spec["vote"])
    size = bearish.astype(float).rolling(spec["confirmation"]).min().fillna(0)
    if spec["sizing"] == "vol_target":
        vol = bars.close.pct_change().rolling(30).std() * math.sqrt(periods)
        size *= ((0.25 / vol.where(vol > 0)).clip(upper=1).fillna(0) * 4).round() / 4
    result = {"borrow_apr_assumption": 0.05, "execution_delay_bars": 2}
    for label, target_weights in (("long", long), ("short", -size), ("long_short", long - size)):
        weights = target_weights.shift(2).fillna(0).to_numpy()
        stages = {}
        for name, dates in (("discovery", discovery), ("validation", validation)):
            first, last = bars.index.get_indexer([dates[0], dates[-1]])
            w = weights[first:last]
            opens = bars.open.to_numpy()[first:last+1]
            returns = opens[1:] / opens[:-1] - 1
            elapsed = np.diff(dates.to_numpy()) / np.timedelta64(1, "D")
            cost = 2 * cost_bps / 10000
            gross = 1 + w * returns - np.maximum(-w, 0) * 0.05 * elapsed / 365
            # Stress short positions against the daily high as well as the next open.
            highs = bars.high.to_numpy()[first:last]
            insolvent = np.any(1 + w[w < 0] * (highs[w < 0] / opens[:-1][w < 0] - 1) <= 0)
            net = -1.0
            if not insolvent and np.all(gross > 0):
                drift = w * (1 + returns) / gross
                factors = gross * (1 - cost * abs(w - np.r_[0., drift[:-1]]))
                factors[-1] *= 1 - cost * abs(drift[-1])
                if np.all(factors > 0):
                    net = float(np.prod(factors) - 1)
            stages[name] = {"net": net, "start": dates[0].date().isoformat(),
                            "end": dates[-1].date().isoformat()}
        result[label] = stages
    return result


def interval_exposure(desired, start, end, delay):
    return desired.shift(delay).loc[start:end].iloc[:-1].fillna(0)


def simulation(bars, desired, start, end, cost_bps, delay=1):
    """Hold from an executable open to the following open, with terminal exit costs."""
    exposure = interval_exposure(desired, start, end, delay)
    open_return = (bars.open.shift(-1) / bars.open - 1).reindex(exposure.index)
    prior = exposure.shift().fillna(0)
    turnover = (exposure - prior).abs()
    cost = cost_bps / 10_000
    gross = 1 + exposure * open_return
    # Mark the position before rebalancing so partial allocators pay for drift.
    drifted = (exposure * (1 + open_return) / gross).shift().fillna(0)
    fees = (exposure - drifted).abs() * cost
    net = (1 - fees) * gross - 1
    if len(net):
        terminal_weight = exposure.iloc[-1] * (1 + open_return.iloc[-1]) / gross.iloc[-1]
        net.iloc[-1] = (1 + net.iloc[-1]) * (1 - terminal_weight * cost) - 1
    complete_trades = int(((exposure == 0) & (prior > 0)).sum())
    return net, complete_trades, float(turnover.sum())


def stats(net, trades):
    equity = (1 + net).cumprod()
    if not len(equity):
        return {"net": 0.0, "drawdown": 0.0, "trades": 0}
    peak = equity.cummax().clip(lower=1.0)
    return {
        "net": float(equity.iloc[-1] - 1),
        "drawdown": float((1 - equity / peak).max()),
        "trades": trades,
    }


def history_metrics(bars, desired, index, cost_bps, periods=None):
    folds = np.array_split(np.arange(len(index)), POLICY["history_folds"])
    results = []
    for fold in folds:
        net, trades, _ = simulation(
            bars, desired, index[fold[0]], index[fold[-1]], 2 * cost_bps, delay=2,
        )
        result = stats(net, trades)
        if periods is not None:
            start, end = index[fold[0]], index[fold[-1]]
            benchmark, _, _ = simulation(bars, pd.Series(1.0, index=bars.index), start, end, 2 * cost_bps)
            result.update(metrics.describe(net, interval_exposure(desired, start, end, 2), periods))
            result.update({"start": start.date().isoformat(), "end": end.date().isoformat(),
                           "benchmark_net": stats(benchmark, 0)["net"]})
        results.append(result)
    total_net = math.prod(1 + result["net"] for result in results) - 1
    positive = sum(result["net"] > 0 for result in results)
    trades = sum(result["trades"] for result in results)
    drawdown = max(result["drawdown"] for result in results)
    continuous,_,_=simulation(bars,desired,index[0],index[-1],2*cost_bps,delay=2)
    continuous_dates=bars.loc[index[0]:index[-1]].index[1:]
    risk=recovery_risk.measure(continuous.to_numpy(),continuous_dates,index[0])
    reason=recovery_risk.reason(risk)
    if any(result["net"]<=-1 or result["drawdown"]>=1 for result in results):
        reason="capital_ruin"
    report = {
        "net": total_net, "profitable_folds": positive, "trades": trades, "drawdown": drawdown,
        "pass": total_net > 0 and positive >= 2 and trades >= 6 and reason is None,
        "folds": results,"risk_policy":recovery_risk.VERSION, "risk_reason":reason, **risk,
    }
    if periods is not None:
        report.update({
            "start": index[0].date().isoformat(), "end": index[-1].date().isoformat(),
            "benchmark_net": math.prod(1 + result["benchmark_net"] for result in results) - 1,
        })
    return report


def hac_p_value(excess, lag):
    sample = np.asarray(excess, dtype=float)
    centered = sample - sample.mean()
    variance = (centered @ centered) / len(sample)
    for k in range(1, lag + 1):
        covariance = (centered[k:] @ centered[:-k]) / len(sample)
        variance += 2 * (1 - k / (lag + 1)) * covariance
    if variance <= 0:
        return 1.0
    z = sample.mean() / math.sqrt(variance / len(sample))
    return 0.5 * math.erfc(z / math.sqrt(2))


def paper_metrics(bars, desired, start, stop, cost_bps, sequence, *, final=True, periods=365, frozen_policy=None):
    returns, trades, _ = simulation(bars, desired, start, stop, 2 * cost_bps, delay=PAPER_EXECUTION_DELAY)
    benchmark, _, _ = simulation(bars, pd.Series(1.0, index=bars.index), start, stop, 2 * cost_bps)
    measurements = stats(returns, trades)
    measurements.update(metrics.describe(
        returns, interval_exposure(desired, start, stop, PAPER_EXECUTION_DELAY), periods,
    ))
    benchmark_net = stats(benchmark, 0)["net"]
    measurements["benchmark_net"] = benchmark_net
    risk=recovery_risk.measure(returns.to_numpy(),bars.loc[start:stop].index[1:],start)
    measurements.update(risk)
    selected=POLICY if frozen_policy is None else frozen_policy
    measurements["risk_policy"]=selected.get("paper_risk_policy","legacy_frozen")
    reason=recovery_risk.reason(risk)
    measurements["risk_reason"]=reason
    if not final:
        return measurements, returns, benchmark
    # Test outperformance against both buy-and-hold and cash. Use the most
    # conservative estimate across two prespecified autocorrelation lags.
    p_value = max(hac_p_value(sample, lag)
                  for sample in (returns, returns - benchmark)
                  for lag in POLICY["paper_hac_lags"])
    alpha = POLICY["paper_family_alpha"] / (sequence * (sequence + 1))
    measurements.update({"benchmark_net": benchmark_net, "p_value": p_value, "alpha": alpha})
    measurements["pass"] = (
        measurements["net"] > max(0, benchmark_net) and trades >= PAPER_TRADES
        and (measurements["drawdown"]<=selected["paper_max_drawdown"]
             if "paper_max_drawdown" in selected else reason is None) and p_value <= alpha
    )
    return measurements, returns, benchmark
