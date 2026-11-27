"""Causal statistical/ML diagnostics for each asset; never a PAPER promoter.

The train/validation boundary is chronological.  Every feature is known at the
completed bar and every target starts on the following bar.  These diagnostics
measure whether GARCH+ML, an unsupervised regime model, or a direction model add
anything beyond simple baselines.  Results enter the normal immutable attempts
registry but cannot create a candidate or trading permission.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestClassifier
from sklearn.mixture import GaussianMixture

from . import data, research

VERSION = "causal-statistical-models-v1"
POLICY = {
    "version": VERSION,
    "split": "chronological_70_30",
    "features": "completed_bar_only",
    "targets": "next_bar_or_next_21bar_realized_volatility",
    "promotion": "disabled_research_only",
    "direction_test": "stationary_block_bootstrap_vs_always_up",
}


def _garch_path(r, omega, alpha, beta):
    values = np.asarray(r, dtype=float)
    out = np.full(len(values), np.nan)
    seed = float(np.var(values[:63])) if len(values) >= 63 else float(np.var(values))
    out[0] = max(seed, 1e-12)
    for i in range(1, len(values)):
        out[i] = max(omega + alpha * values[i - 1] ** 2 + beta * out[i - 1], 1e-12)
    return np.sqrt(out)


def _stationary_accuracy_p(diff, simulations=400, mean_block=10, seed=41):
    x = np.asarray(diff, dtype=float)
    if len(x) < 40:
        return None
    centered = x - x.mean()
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(simulations):
        idx = np.empty(len(x), dtype=int)
        idx[0] = rng.integers(len(x))
        for j in range(1, len(x)):
            idx[j] = (rng.integers(len(x)) if rng.random() < 1 / mean_block
                      else (idx[j - 1] + 1) % len(x))
        extreme += float(centered[idx].mean() >= x.mean())
    return round((extreme + 1) / (simulations + 1), 6)


def analyze(bars):
    close = bars.close.astype(float)
    r = np.log(close).diff()
    target_vol = r.rolling(21).std(ddof=0).shift(-21) * math.sqrt(252)
    features = pd.DataFrame({
        "ret1": r,
        "abs1": r.abs(),
        "vol5": r.rolling(5).std(ddof=0) * math.sqrt(252),
        "vol21": r.rolling(21).std(ddof=0) * math.sqrt(252),
        "vol63": r.rolling(63).std(ddof=0) * math.sqrt(252),
        "mom21": close.pct_change(21),
    })
    valid = features.notna().all(axis=1) & target_vol.notna()
    frame = features.loc[valid].copy()
    yv = target_vol.loc[valid]
    if len(frame) < 300:
        return {"status": "INSUFFICIENT_CAUSAL_HISTORY", "observations": len(frame),
                "paper_eligible": False, "trade_authorized": False}
    boundary = int(len(frame) * .70)
    train_index, test_index = frame.index[:boundary], frame.index[boundary:]
    raw = r.reindex(frame.index).fillna(0).to_numpy()
    # Fixed admissible grid, selected on train only.  Forecast is shifted so
    # today's prediction never sees today's target volatility.
    candidates = []
    variance = float(np.var(raw[:boundary]))
    for alpha, beta in ((.03, .90), (.05, .90), (.07, .88), (.10, .85), (.12, .80)):
        omega = max(variance * (1 - alpha - beta), 1e-12)
        path = _garch_path(raw, omega, alpha, beta) * math.sqrt(252)
        mae = float(np.mean(np.abs(path[:boundary] - yv.iloc[:boundary].to_numpy())))
        candidates.append((mae, alpha, beta, omega, path))
    _, alpha, beta, omega, garch = min(candidates, key=lambda x: x[0])
    frame["garch"] = garch
    Xtr, Xte = frame.loc[train_index], frame.loc[test_index]
    ytr, yte = yv.loc[train_index], yv.loc[test_index]
    ml = GradientBoostingRegressor(n_estimators=80, max_depth=2, learning_rate=.04,
                                   loss="huber", random_state=19)
    ml.fit(Xtr, ytr)
    mlp = np.maximum(ml.predict(Xte), 1e-8)
    gp = Xte.garch.to_numpy()
    naive = Xte.vol21.to_numpy()
    actual = yte.to_numpy()
    mae = lambda p: float(np.mean(np.abs(p - actual)))
    qlike = lambda p: float(np.mean(np.log(p * p) + actual * actual / (p * p)))

    regime_train = Xtr[["ret1", "vol21", "mom21"]].to_numpy()
    regime_test = Xte[["ret1", "vol21", "mom21"]].to_numpy()
    gmm = GaussianMixture(n_components=3, covariance_type="full", n_init=5, random_state=23)
    gmm.fit(regime_train)
    states = gmm.predict(regime_test)
    train_states = gmm.predict(regime_train)
    state_vol = {int(s): float(Xtr.vol21.to_numpy()[train_states == s].mean())
                 for s in np.unique(train_states)}
    ordered = sorted(state_vol, key=state_vol.get)
    labels = {state: label for state, label in zip(ordered, ("CALM", "NORMAL", "STRESS"))}

    direction_y = (r.shift(-1) > 0).reindex(frame.index).astype(int)
    rf = RandomForestClassifier(n_estimators=200, min_samples_leaf=30,
                                class_weight="balanced", random_state=29, n_jobs=1)
    rf.fit(Xtr, direction_y.loc[train_index])
    pred = rf.predict(Xte)
    truth = direction_y.loc[test_index].to_numpy()
    always = np.full(len(truth), int(direction_y.loc[train_index].mean() >= .5))
    diff = (pred == truth).astype(float) - (always == truth).astype(float)
    return {
        "status": "RETROSPECTIVE_CAUSAL_DIAGNOSTIC_COMPLETE",
        "split": {"train_end": str(train_index[-1].date()),
                  "validation_start": str(test_index[0].date()),
                  "train_n": len(train_index), "validation_n": len(test_index)},
        "volatility": {
            "garch": {"alpha": alpha, "beta": beta, "omega": omega,
                      "mae": round(mae(gp), 8), "qlike": round(qlike(gp), 8)},
            "garch_plus_gradient_boosting": {"mae": round(mae(mlp), 8),
                                               "qlike": round(qlike(mlp), 8)},
            "rolling_21_baseline": {"mae": round(mae(naive), 8),
                                     "qlike": round(qlike(naive), 8)},
            "ml_beats_both_baselines": bool(mae(mlp) < min(mae(gp), mae(naive))),
        },
        "regimes": {"model": "TRAIN_ONLY_GAUSSIAN_MIXTURE_3_STATE",
                    "training_state_volatility": state_vol,
                    "validation_counts": {labels[int(s)]: int(np.sum(states == s)) for s in ordered},
                    "labels_derived_from_training_only": True},
        "direction": {"random_forest_accuracy": round(float((pred == truth).mean()), 6),
                      "train_majority_baseline_accuracy": round(float((always == truth).mean()), 6),
                      "accuracy_difference": round(float(diff.mean()), 6),
                      "stationary_bootstrap_p": _stationary_accuracy_p(diff),
                      "edge_claimed": False},
        "paper_eligible": False, "trade_authorized": False,
        "requires_superlab_independent_validation": True,
    }


def run_asset(db, asset, market, bars):
    identity = research.digest([VERSION, asset, data.fingerprint(bars)])
    existing = db.execute("SELECT validated FROM attempts WHERE version=? AND asset=? AND id=?",
                          (VERSION, asset, identity)).fetchone()
    if existing:
        return {"status": "duplicate_not_counted", "id": identity}
    result = analyze(bars)
    spec = {"families": ["garch_ml_volatility", "gmm_regime", "rf_direction_negative_control"],
            "frame": "1d", "promotion": "disabled"}
    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (VERSION, asset, identity, "model_diagnostics", research.canonical(spec),
                market.source, data.fingerprint(bars), bars.index[-1].date().isoformat(),
                "local", research.canonical(POLICY),
                research.canonical({"pass": False, "paper_eligible": False}),
                research.canonical(result)))
    return {"status": result["status"], "id": identity,
            "ml_volatility_improved": result.get("volatility", {}).get("ml_beats_both_baselines", False),
            "paper_eligible": False}
