"""Descriptive statistics for the existing net return and position paths."""

from __future__ import annotations

import math

import numpy as np


def describe(returns, exposure, periods):
    net = returns.to_numpy(dtype=float)
    weights = exposure.reindex(returns.index).to_numpy(dtype=float)
    annualized_volatility = sharpe = sortino = None
    if len(net) >= 20:
        volatility = float(net.std(ddof=1))
        downside = float(np.sqrt(np.mean(np.minimum(net, 0.0) ** 2)))
        annualized_volatility = volatility * math.sqrt(periods)
        sharpe = float(net.mean()) * math.sqrt(periods) / volatility if volatility else None
        sortino = float(net.mean()) * math.sqrt(periods) / downside if downside else None

    closed = []
    trade_equity = None
    for weight, change in zip(weights, net, strict=True):
        if weight > 0 and trade_equity is None:
            trade_equity = 1.0
        if trade_equity is not None:
            trade_equity *= 1.0 + change
            if weight == 0:
                closed.append(trade_equity - 1.0)
                trade_equity = None
    gross_profit = sum(value for value in closed if value > 0)
    gross_loss = -sum(value for value in closed if value < 0)
    return {
        "sharpe": sharpe, "sortino": sortino, "volatility": annualized_volatility,
        "exposure": float(weights.mean()) if len(weights) else None,
        "trade_win_rate": sum(value > 0 for value in closed) / len(closed) if closed else None,
        "trade_expectancy": sum(closed) / len(closed) if closed else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "worst_bar": float(net.min()) if len(net) else None,
    }
