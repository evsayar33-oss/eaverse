"""Read-only empirical path-risk sensitivity on genuinely completed PAPER bars.

Do not interpret stationary resampling as a future P&L prediction or use
this diagnostic in any acceptance, position sizing or Telegram decision.
Pure stdlib implementation so no new provider, package or paid API is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path

SCHEMA = "eaverse.prospective.paper_path_risk.v1"
BLOCK_SIZES = (5, 10, 20)
SAMPLES = 600
COMPLETED = {"verified_paper", "paper_gate_failed"}


def _read_returns(value):
    if not isinstance(value, list) or not value:
        raise ValueError("MISSING_DAILY_NET_RETURN_VECTOR")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("NON_NUMERIC_PROSPECTIVE_RETURN")
        x = float(item)
        if not math.isfinite(x) or x <= -1:
            raise ValueError("NONFINITE_OR_INSOLVENT_RETURN")
        out.append(x)
    return out


def _path(returns):
    capital = peak = 1.0
    drawdown = 0.0
    for value in returns:
        capital *= 1 + value
        peak = max(peak, capital)
        drawdown = min(drawdown, capital / peak - 1)
    return capital - 1, drawdown


def _percentiles(values):
    if not values:
        raise ValueError("MISSING_PATH_SIMULATION")
    ordered = sorted(values)
    def point(q):
        t = q * (len(ordered) - 1)
        lo = int(t)
        frac = t - lo
        return round(ordered[lo] * (1 - frac) +
                     ordered[min(lo + 1, len(ordered) - 1)] * frac, 6)
    return {"p05": point(0.05), "median": point(0.50), "p95": point(0.95)}


def _stationary_resample_joint(strategy, benchmark, *, mean_block, samples, seed):
    """Resample strategy and benchmark using SAME indices, preserving pairing."""
    if not 1 <= mean_block or not 1 <= samples:
        raise ValueError("INVALID_BOOTSTRAP_CONFIGURATION")
    n = len(strategy)
    generator = random.Random(seed)
    returns, drawdowns, relative = [], [], []
    for _ in range(samples):
        indices = [generator.randrange(n)]
        for _j in range(1, n):
            indices.append(generator.randrange(n)
                           if generator.random() < 1 / mean_block
                           else (indices[-1] + 1) % n)
        s, d = _path(strategy[i] for i in indices)
        b, _ = _path(benchmark[i] for i in indices)
        returns.append(s)
        drawdowns.append(d)
        relative.append(s - b)
    return {
        "n_resamples": samples, "mean_block_bars": mean_block,
        "compounded_return": _percentiles(returns),
        "max_drawdown": _percentiles(drawdowns),
        "compounded_return_minus_paired_benchmark": _percentiles(relative),
        "p_resampled_positive": round(sum(r > 0 for r in returns) / samples, 4),
        "p_resampled_excess_positive": round(sum(r > 0 for r in relative) / samples, 4),
        "future_profit_probability": None,
        "broker_execution_verified": False,
    }


def evaluate_one(entry):
    sequence = entry.get("sequence")
    base = {
        "sequence": sequence, "asset": entry.get("asset"),
        "original_status": entry.get("status"),
        "read_only": True, "alpha_verified": False,
        "paper_approval_recomputed": False, "live_allowed": False,
    }
    if entry.get("status") not in COMPLETED:
        return {**base, "status": "NOT_COMPLETED_PAPER"}
    result = entry.get("result")
    if not isinstance(result, dict):
        return {**base, "status": "MISSING_FROZEN_PAPER_RESULT"}
    try:
        a = _read_returns(result.get("day_net"))
        b = _read_returns(result.get("day_benchmark"))
        if len(a) != len(b) or len(a) < 30:
            raise ValueError("INSUFFICIENT_OR_UNPAIRED_PROSPECTIVE_BARS")
        if result.get("data_sha256") is None:
            raise ValueError("MISSING_PAPER_DATA_FINGERPRINT")
    except (ValueError, TypeError) as exc:
        return {**base, "status": "INVALID_FORWARD_LEDGER",
                "blocker": str(exc)}
    evidence = json.dumps({"strategy": a, "benchmark": b,
                           "data_sha256": result["data_sha256"],
                           "start": result.get("start"), "end": result.get("end")},
                          sort_keys=True, allow_nan=False, separators=(",", ":"))
    digest = hashlib.sha256(evidence.encode()).hexdigest()
    observed, drawdown = _path(a)
    bench, _ = _path(b)
    scenarios = {
        str(block): _stationary_resample_joint(
            a, b, mean_block=block, samples=SAMPLES,
            seed=int(hashlib.sha256((digest + ":" + str(block)).encode()).hexdigest()[:16], 16))
        for block in BLOCK_SIZES
    }
    return {
        **base,
        "status": "EMPIRICAL_BOOTSTRAP_RESEARCH_ONLY",
        "source_data_fingerprint": result["data_sha256"],
        "frozen_pair_sha256": digest,
        "observed_bars": len(a),
        "observed_compounded_return": round(observed, 6),
        "observed_benchmark_return": round(bench, 6),
        "observed_max_drawdown": round(drawdown, 6),
        "stationary_block_sensitivity": scenarios,
        "limit": ("Only resamples observed, dependent completed PAPER bar returns; "
                  "not an independent future path, not an estimate of real fills, "
                  "not a Monte Carlo forecast or a changed original PAPER verdict."),
        "paper_verdict_preserved": entry["status"],
    }


def evaluate_panel(panel):
    if not isinstance(panel, dict) or panel.get("schema") != 1:
        raise ValueError("UNKNOWN_OR_NON_NATIVE_EAVERSE_PANEL")
    entries = panel.get("entries")
    if not isinstance(entries, list):
        raise ValueError("PAPER_PANEL_ENTRIES_MISSING")
    results = [evaluate_one(entry) for entry in entries if isinstance(entry, dict)]
    return {"schema": SCHEMA, "published_at": panel.get("published_at"),
            "source_panel_run_url": panel.get("run_url"),
            "paper_entries_observed": len(results),
            "completed_risk_diagnostics": sum(
                r["status"] == "EMPIRICAL_BOOTSTRAP_RESEARCH_ONLY" for r in results),
            "descriptive_only": True,
            "signals_or_decisions_changed": False,
            "portfolio_or_bot_execution": False,
            "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_panel(json.loads(args.panel.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                   allow_nan=False) + "\n", encoding="utf-8")
    print("EAVERSE_FORWARD_PATH_RISK", result["completed_risk_diagnostics"],
          "completed ledgers, RESEARCH_ONLY_NO_NEW_ACCEPT")


if __name__ == "__main__":
    main()
