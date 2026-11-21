"""Reference next-open fills with backtesting.py, using one share per position."""
import numpy as np
from backtesting import Backtest, Strategy


def replay_positions(ohlc, orders, fee):
    """orders[i] is the signed unit position requested at close i for open i+1."""
    class Replay(Strategy):
        def init(self):
            pass

        def next(self):
            target = orders[len(self.data) - 1]
            if self.position.size == target:
                return
            self.position.close()
            if target > 0:
                self.buy(size=1)
            elif target < 0:
                self.sell(size=1)

    stats = Backtest(ohlc, Replay, cash=max(1e6, 10 * ohlc.High.max()),
                     commission=fee, trade_on_close=False).run()
    return stats["_trades"]


def audit_validation(bars, desired, cost_bps, report):
    """Compare the full-allocation model, fold by fold, including forced final exits."""
    if not desired.isin([0., 1.]).all():
        return {"status": "unsupported_fractional_sizing"}
    fee = 2 * cost_bps / 10000
    checked = 0
    for fold in report["folds"]:
        start, end = bars.index.get_indexer([fold["start"], fold["end"]])
        frame = bars.iloc[start-2:end+1].rename(columns=str.title)
        weights = desired.shift(2).iloc[start:end].to_numpy()
        orders = np.r_[0, weights, 0, 0]
        fills = replay_positions(frame, orders, fee)
        changes = np.diff(np.r_[0, weights, 0])
        entries = np.flatnonzero(changes == 1) + 2
        exits = np.flatnonzero(changes == -1) + 2
        if not (np.array_equal(fills.EntryBar, entries) and np.array_equal(fills.ExitBar, exits)):
            return {"status": "mismatch", "reason": "fill_dates"}
        net = 1.
        for trade in fills.itertuples():
            if trade.Size != 1 or trade.EntryPrice != frame.Open.iloc[trade.EntryBar] or trade.ExitPrice != frame.Open.iloc[trade.ExitBar]:
                return {"status": "mismatch", "reason": "fill_size_or_price"}
            # Native fractional-equity convention: costs multiply equity on both sides.
            net *= (trade.ExitPrice / trade.EntryPrice) * (1 - fee) ** 2
        if not np.isclose(net - 1, fold["net"], atol=1e-9, rtol=1e-9):
            return {"status": "mismatch", "reason": "net_return"}
        checked += len(fills)
    return {"status": "matched" if checked else "no_trades", "round_trips": checked,
            "engine": "backtesting.py", "unit_size": 1}
