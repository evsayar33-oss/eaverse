"""Enumerate bounded strategy hypotheses and simulate causal execution."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import defaultdict, deque

import numpy as np
import pandas as pd


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
STRATEGIC_FEATURES = set(FEATURES) - {"volume", "low_vol", "atr"}
MIN_VALIDATION_BARS = 180
PAPER_BARS = 90
PAPER_TRADES = 8
DRAWDOWN_LIMIT = 0.25
POLICY = {
    "history_version": VERSION,
    "history_folds": 3,
    "history_fold_min_bars": 60,
    "history_min_profitable_stress_folds": 2,
    "history_cost_multiplier": 2,
    "history_extra_delay_bars": 1,
    "history_total_min_trades": 6,
    "paper_bars": PAPER_BARS,
    "paper_min_round_trips": PAPER_TRADES,
    "paper_max_drawdown": DRAWDOWN_LIMIT,
    "paper_family_alpha": 0.05,
    "paper_hac_lags": [5, 10],
}


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


def choose(asset: str, prior: set, family_results: dict, budget: int):
    families = defaultdict(list)
    for spec in catalog():
        family = tuple(name for name, _ in spec["rules"])
        key = digest(spec)
        if key not in prior:
            families[family].append((digest([asset, key]), key, spec))
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
            groups[size] = deque(deque(sorted(families[family])) for family in group)
    selected = []
    active_sizes = deque(groups)
    while active_sizes and len(selected) < budget:
        size = active_sizes.popleft()
        queues = groups[size]
        queue = queues.popleft()
        _, key, spec = queue.popleft()
        selected.append((key, spec))
        if queue:
            queues.append(queue)
        if queues:
            active_sizes.append(size)
    return selected


def ranking_key(item):
    key, spec, metrics = item
    return (-metrics["net"], metrics["drawdown"], key, len(spec["rules"]))


def features(bars: pd.DataFrame):
    close = bars.close
    change = close.diff()
    returns = close.pct_change()
    tr = pd.concat([bars.high - bars.low, (bars.high - close.shift()).abs(), (bars.low - close.shift()).abs()], axis=1).max(axis=1)
    result = {}
    for family, windows in FEATURES.items():
        for n in windows:
            mean = close.rolling(n).mean()
            if family == "sma":
                flag = close > mean
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
                flag = (rsi > 50) if family == "rsi_trend" else ((rsi > 35) & (rsi.shift() < 45))
            elif family == "volume":
                flag = bars.volume > 1.15 * bars.volume.shift().rolling(n).mean()
            elif family == "low_vol":
                vol = returns.rolling(n).std()
                flag = vol <= vol.rolling(100).median()
            elif family == "atr":
                flag = (tr.rolling(n).mean() / close) < 0.05
            else:
                flag = close > mean + close.rolling(n).std()
            result[family, n] = flag.fillna(False).astype(float)
    return result


def target(bars, signals, spec, periods):
    votes = sum(signals[tuple(rule)] for rule in spec["rules"])
    result = (votes >= spec["vote"]).astype(float)
    if spec["confirmation"] == 2:
        result = result.rolling(2).min().fillna(0)
    if spec["sizing"] == "vol_target":
        vol = bars.close.pct_change().rolling(30).std() * math.sqrt(periods)
        scale = (0.25 / vol.where(vol > 0)).clip(upper=1).fillna(0)
        result *= (scale * 4).round() / 4
    return result


def simulation(bars, desired, start, end, cost_bps, delay=1):
    """Hold from an executable open to the following open, with terminal exit costs."""
    exposure = desired.shift(delay).loc[start:end].iloc[:-1].fillna(0)
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


def history_metrics(bars, desired, index, cost_bps):
    folds = np.array_split(np.arange(len(index)), POLICY["history_folds"])
    results = []
    for fold in folds:
        net, trades, _ = simulation(
            bars, desired, index[fold[0]], index[fold[-1]], 2 * cost_bps, delay=2,
        )
        results.append(stats(net, trades))
    total_net = math.prod(1 + result["net"] for result in results) - 1
    positive = sum(result["net"] > 0 for result in results)
    trades = sum(result["trades"] for result in results)
    drawdown = max(result["drawdown"] for result in results)
    return {
        "net": total_net, "profitable_folds": positive, "trades": trades, "drawdown": drawdown,
        "pass": total_net > 0 and positive >= 2 and trades >= 6 and drawdown <= DRAWDOWN_LIMIT,
        "folds": results,
    }


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


def paper_metrics(bars, desired, start, stop, cost_bps, sequence):
    returns, trades, _ = simulation(bars, desired, start, stop, 2 * cost_bps)
    benchmark, _, _ = simulation(bars, pd.Series(1.0, index=bars.index), start, stop, 2 * cost_bps)
    measurements = stats(returns, trades)
    benchmark_net = stats(benchmark, 0)["net"]
    # Test outperformance against both buy-and-hold and cash. Use the most
    # conservative estimate across two prespecified autocorrelation lags.
    p_value = max(hac_p_value(sample, lag)
                  for sample in (returns, returns - benchmark)
                  for lag in POLICY["paper_hac_lags"])
    alpha = POLICY["paper_family_alpha"] / (sequence * (sequence + 1))
    measurements.update({"benchmark_net": benchmark_net, "p_value": p_value, "alpha": alpha})
    measurements["pass"] = (
        measurements["net"] > max(0, benchmark_net) and trades >= PAPER_TRADES
        and measurements["drawdown"] <= DRAWDOWN_LIMIT and p_value <= alpha
    )
    return measurements, returns, benchmark
