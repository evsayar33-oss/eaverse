"""Causal OHLCV-only indicators for the independent indicator research queue.

Rules are immutable named parameters. Every result is calculated using only
completed bars, including the displaced Ichimoku cloud (never a negative shift).
Undefined values are NaN, not bearish or bullish signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FAMILIES = (
    "ichimoku", "supertrend", "wavetrend", "bollinger", "cci", "rsi", "stochastic_rsi",
    "williams_r", "mfi", "aroon", "dmi", "keltner", "donchian", "ppo", "trix",
    "tsi", "cmf", "vortex", "kama", "chaikin_oscillator", "ultimate_oscillator",
    "stochastic", "macd", "adx",
)
REQUESTED = FAMILIES[:7]
OPS = ("above", "below", "cross_up", "cross_down", "rising", "falling")
SOURCES = ("close", "open", "hl2", "hlc3", "ohlc4")
WINDOWS = (3, 5, 7, 9, 10, 12, 14, 18, 20, 21, 26, 30, 40, 50, 60, 75, 90, 120, 150, 200)
SMOOTH = (1, 2, 3, 4, 5, 7, 9, 12, 14)
AVERAGES = ("sma", "ema", "wilder", "wma")


def div(a, b):
    return a / b.where(b.abs() > 1e-12)


def average(x, n, kind):
    if kind == "sma":
        return x.rolling(n).mean()
    if kind == "ema":
        return x.ewm(span=n, min_periods=n, adjust=False).mean()
    if kind == "wilder":
        # Seed with the first complete SMA, then use Wilder's recurrence.
        # Restart only after a gap followed by n consecutive finite observations.
        values=x.to_numpy(dtype=float)
        seed=x.rolling(n).mean().to_numpy(dtype=float)
        out=np.full(len(x),np.nan)
        for i,v in enumerate(values):
            if not np.isfinite(v):
                continue
            if i>0 and np.isfinite(out[i-1]):
                out[i]=out[i-1]+(v-out[i-1])/n
            elif np.isfinite(seed[i]):
                out[i]=seed[i]
        return pd.Series(out,index=x.index,name=x.name)
    if kind == "wma":
        weights = np.arange(1, n+1, dtype=float)
        return x.rolling(n).apply(lambda v: np.dot(v, weights)/weights.sum(), raw=True)
    raise ValueError("unknown smoothing method")


def price(b, source):
    return {"close": b.close, "open": b.open, "hl2": (b.high+b.low)/2,
            "hlc3": (b.high+b.low+b.close)/3,
            "ohlc4": (b.open+b.high+b.low+b.close)/4}[source]


def tr(b):
    return pd.concat([b.high-b.low, (b.high-b.close.shift()).abs(),
                      (b.low-b.close.shift()).abs()], axis=1).max(axis=1)


def rsi(x, n, method):
    delta = x.diff()
    gains = average(delta.clip(lower=0), n, method)
    losses = average(-delta.clip(upper=0), n, method)
    total = gains+losses
    return (100*div(gains, total)).mask(total.eq(0), 50.)


def range_position(x, low, high):
    return 100*div(x-low, high-low)


def supertrend(b, n, multiplier, method):
    """Final bands trail until prior close crosses; direction flips at current close."""
    atr = average(tr(b), n, method).to_numpy()
    mid = ((b.high+b.low)/2).to_numpy()
    close = b.close.to_numpy()
    out = np.full(len(b), np.nan)
    upper = lower = np.nan
    direction = 0
    for i, a in enumerate(atr):
        if not np.isfinite(a) or a <= 0:
            upper = lower = np.nan
            direction = 0
            continue
        u, l = mid[i]+multiplier*a, mid[i]-multiplier*a
        if direction == 0:
            upper, lower, direction = u, l, 1 if close[i] >= mid[i] else -1
        else:
            upper = u if u < upper or close[i-1] > upper else upper
            lower = l if l > lower or close[i-1] < lower else lower
            if direction < 0 and close[i] > upper:
                direction = 1
            elif direction > 0 and close[i] < lower:
                direction = -1
        out[i] = lower if direction > 0 else upper
    return pd.Series(out, index=b.index)


def kama(x, n, fast, slow):
    efficiency = div((x-x.shift(n)).abs(), x.diff().abs().rolling(n).sum())
    efficiency = efficiency.mask(x.diff().abs().rolling(n).sum().eq(0), 0.)
    alpha = (efficiency*(2/(fast+1)-2/(slow+1))+2/(slow+1))**2
    out = np.full(len(x), np.nan)
    prev = np.nan
    for i, (v, a) in enumerate(zip(x.to_numpy(), alpha.to_numpy())):
        if np.isfinite(v) and np.isfinite(a):
            prev = v if not np.isfinite(prev) else prev+a*(v-prev)
            out[i] = prev
        else:
            prev = np.nan
    return pd.Series(out, index=x.index)


def choose(name, rng, default=False, frame="1d"):
    """Choose only parameters which affect the selected formula or comparator."""
    def pick(values, base):
        return base if default else rng.choice(values)

    windows=WINDOWS if frame=="1d" else tuple(n for n in WINDOWS if n<=60)

    def window(base=14):
        return pick(windows, base)

    def smooth(base=3):
        return pick(SMOOTH, base)

    def ordered(base=(12, 26)):
        return base if default else tuple(sorted(rng.sample(windows, len(base))))

    p = {}
    metric = "value"
    levels, base = (-2, -1, 0, 1, 2), 0
    if name == "ichimoku":
        p["tenkan"], p["kijun"], p["span_b"] = ordered((9, 26, 52))
        metric = pick(("cloud", "tenkan_kijun", "span_cross", "chikou"), "cloud")
        if metric in ("cloud", "span_cross", "chikou"):
            p["displacement"] = pick((5, 9, 13, 20, 26, 30, 52), 26)
        # Unused windows are omitted from each hypothesis.
        if metric == "tenkan_kijun":
            p.pop("span_b")
        if metric == "chikou":
            for k in ("tenkan", "kijun", "span_b"):
                p.pop(k)
    elif name == "supertrend":
        p.update(n=window(10), multiplier=pick((.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6), 3),
                 average=pick(AVERAGES, "wilder"))
    elif name in ("wavetrend", "tsi", "ppo", "macd", "trix", "chaikin_oscillator"):
        if name == "wavetrend":
            p.update(channel=window(10), average=window(21), source=pick(SOURCES, "hlc3"))
            metric = pick(("line", "histogram"), "line")
            levels = (-80, -60, -53, -30, -10, 0, 10, 30, 53, 60, 80) if metric == "line" else (-20, -10, -5, 0, 5, 10, 20)
        elif name == "trix":
            p.update(n=window(15), source=pick(SOURCES, "close"))
            metric = pick(("line", "histogram"), "line")
            levels = (-1, -.5, -.2, 0, .2, .5, 1)
        elif name == "tsi":
            p["fast"], p["slow"] = ordered((13, 25))
            p["source"] = pick(SOURCES, "close")
            metric = pick(("line", "histogram"), "line")
            levels = (-50, -30, -15, 0, 15, 30, 50)
        else:
            p["fast"], p["slow"] = ordered((3, 10) if name == "chaikin_oscillator" else (12, 26))
            if name != "chaikin_oscillator":
                p["source"] = pick(SOURCES, "close")
            metric = pick(("line", "histogram"), "line")
            levels = (-5, -2, -1, -.5, 0, .5, 1, 2, 5)
        if metric == "histogram":
            p["signal"] = smooth(9 if name in ("ppo", "macd") else 4)
    elif name == "bollinger":
        p.update(n=window(20), multiplier=pick((.5, .75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 3, 3.5, 4), 2),
                 average=pick(AVERAGES, "sma"), source=pick(SOURCES, "close"))
        metric = pick(("percent_b", "bandwidth"), "percent_b")
        levels, base = ((-.5, -.25, 0, .1, .25, .5, .75, .9, 1, 1.25, 1.5), .5) if metric == "percent_b" else ((.5, 1, 2, 3, 5, 8, 10, 15, 20, 30), 5)
    elif name == "cci":
        p.update(n=window(20), source=pick(SOURCES, "hlc3"), constant=pick((.01, .015, .02, .025), .015))
        levels = (-250, -200, -150, -100, -75, -50, 0, 50, 75, 100, 150, 200, 250)
    elif name in ("rsi", "stochastic_rsi", "stochastic"):
        p["n"] = window()
        p["source"] = pick(SOURCES, "close")
        if name != "stochastic":
            p["average"] = pick(AVERAGES, "wilder")
        if name == "rsi":
            metric = pick(("line", "signal_spread"), "line")
            if metric == "signal_spread":
                p["signal"] = smooth(5)
                levels = (-20, -10, -5, 0, 5, 10, 20)
            else:
                levels, base = tuple(range(10, 91, 5)), 50
        else:
            if name == "stochastic_rsi":
                p["stoch_window"] = window()
            p["k"] = smooth()
            metric = pick(("k", "d", "cross"), "k")
            if metric != "k":
                p["d"] = smooth()
            levels, base = ((-20, -10, -5, 0, 5, 10, 20), 0) if metric == "cross" else (tuple(range(5, 96, 5)), 50)
    elif name in ("williams_r", "mfi", "aroon", "dmi", "adx", "cmf", "vortex", "donchian", "keltner"):
        p["n"] = window(20 if name in ("cmf", "donchian", "keltner") else 14)
        if name == "williams_r":
            levels, base = (-95, -90, -85, -80, -70, -60, -50, -40, -30, -20, -10, -5), -50
        elif name == "mfi":
            levels, base = tuple(range(10, 91, 5)), 50
        elif name == "aroon":
            levels = (-80, -60, -40, -20, 0, 20, 40, 60, 80)
        elif name in ("dmi", "adx"):
            p["average"] = pick(AVERAGES, "wilder")
            if name == "adx":
                p["smooth"] = window(14)
                levels, base = (10, 15, 18, 20, 22, 25, 30, 35, 40, 50), 25
            else:
                levels = (-40, -25, -15, -5, 0, 5, 15, 25, 40)
        elif name == "cmf":
            levels = (-.5, -.3, -.2, -.1, -.05, 0, .05, .1, .2, .3, .5)
        elif name == "vortex":
            levels = (-.5, -.3, -.2, -.1, 0, .1, .2, .3, .5)
        elif name == "donchian":
            levels, base = (-25, 0, 10, 20, 40, 50, 60, 80, 90, 100, 125), 50
        elif name == "keltner":
            p.update(atr=window(10), multiplier=pick((.5, 1, 1.5, 2, 2.5, 3, 4), 2),
                     average=pick(AVERAGES, "ema"), source=pick(SOURCES, "close"))
            levels = (-2, -1.5, -1, -.5, 0, .5, 1, 1.5, 2)
    elif name == "kama":
        p.update(n=window(10), fast=pick((2, 3, 4, 5, 6), 2), slow=pick((15, 20, 25, 30, 40, 60, 90), 30),
                 source=pick(SOURCES, "close"))
    elif name == "ultimate_oscillator":
        p["fast"], p["middle"], p["slow"] = ordered((7, 14, 28))
        levels, base = tuple(range(10, 91, 5)), 50
    else:
        raise ValueError("unknown indicator: "+name)
    op = pick(OPS, "above")
    # Slope comparisons have no threshold; do not create redundant settings.
    return {"name": name, "metric": metric, "params": p, "op": op,
            "level": None if op in ("rising", "falling") else pick(levels, base)}


def warmup(rule):
    p, name = rule["params"], rule["name"]
    # Conservative upper bound for serial smoothing and displaced windows.
    if name == "ichimoku":
        return max(p.get("span_b", 0), p.get("kijun", 0), p.get("tenkan", 0))+p.get("displacement", 0)+3
    if name == "trix":
        return 3*p["n"]+p.get("signal", 0)+3
    if name == "tsi":
        return p["fast"]+p["slow"]+p.get("signal", 0)+3
    if name == "wavetrend":
        return 2*p["channel"]+p["average"]+p.get("signal", 0)+3
    if name == "stochastic_rsi":
        return p["n"]+p["stoch_window"]+p["k"]+p.get("d", 0)+3
    if name == "adx":
        return p["n"]+p["smooth"]+3
    if name in ("rsi", "stochastic"):
        return p["n"]+p.get("signal", 0)+p.get("k", 0)+p.get("d", 0)+3
    return max((v for k,v in p.items() if k in ("n", "fast", "slow", "atr", "middle")), default=1)+p.get("signal", 0)+3


def measure(b, rule):
    name, mode, p = rule["name"], rule["metric"], rule["params"]
    x = price(b, p.get("source", "close"))
    c, h, l, v = b.close, b.high, b.low, b.volume
    n = p.get("n")
    if name == "ichimoku":
        def mid(k):
            return (h.rolling(k).max()+l.rolling(k).min())/2
        if mode == "chikou":
            return 100*(div(c, c.shift(p["displacement"]))-1)
        t, k = mid(p["tenkan"]), mid(p["kijun"])
        if mode == "tenkan_kijun":
            return 100*div(t-k,c)
        a = ((t+k)/2).shift(p["displacement"])
        z = mid(p["span_b"]).shift(p["displacement"])
        if mode == "span_cross":
            return 100*div(a-z,c)
        ceiling = pd.concat([a,z],axis=1).max(axis=1,skipna=False)
        floor = pd.concat([a,z],axis=1).min(axis=1,skipna=False)
        return 100*div((c-ceiling).clip(lower=0)+(c-floor).clip(upper=0),c)
    if name == "supertrend":
        return 100*div(c-supertrend(b,n,p["multiplier"],p["average"]),c)
    if name == "bollinger":
        center = average(x,n,p["average"])
        distance = p["multiplier"]*x.rolling(n).std(ddof=0)
        return div(x-center+distance,2*distance) if mode == "percent_b" else 100*div(2*distance,center)
    if name == "cci":
        center = x.rolling(n).mean()
        deviation = x.rolling(n).apply(lambda a: np.mean(np.abs(a-a.mean())), raw=True)
        return div(x-center,p["constant"]*deviation)
    if name == "rsi":
        line = rsi(x,n,p["average"])
        return line-average(line,p["signal"],"ema") if mode == "signal_spread" else line
    if name in ("stochastic", "stochastic_rsi"):
        if name == "stochastic_rsi":
            r = rsi(x,n,p["average"])
            w = p["stoch_window"]
            raw = range_position(r,r.rolling(w).min(),r.rolling(w).max())
        else:
            raw = range_position(x,l.rolling(n).min(),h.rolling(n).max())
        k = raw.rolling(p["k"]).mean()
        if mode == "k":
            return k
        d = k.rolling(p["d"]).mean()
        return d if mode == "d" else k-d
    if name == "williams_r":
        return range_position(c,l.rolling(n).min(),h.rolling(n).max())-100
    if name == "mfi":
        typical = (h+l+c)/3
        flow = typical*v
        change = typical.diff()
        pos = flow.where(change>0,0).where(change.notna()).rolling(n).sum()
        neg = flow.where(change<0,0).where(change.notna()).rolling(n).sum()
        return 100*div(pos,pos+neg)
    if name == "aroon":
        # Last occurrence handles tied highs/lows consistently.
        up = h.rolling(n+1).apply(lambda a: n-np.argmax(a[::-1]),raw=True)
        down = l.rolling(n+1).apply(lambda a: n-np.argmin(a[::-1]),raw=True)
        return 100*(up-down)/n
    if name in ("adx","dmi"):
        up, down = h.diff(), -l.diff()
        atr = average(tr(b),n,p["average"])
        pos = 100*div(average(up.where((up>down)&(up>0),0),n,p["average"]),atr)
        neg = 100*div(average(down.where((down>up)&(down>0),0),n,p["average"]),atr)
        return average(100*div((pos-neg).abs(),pos+neg),p["smooth"],p["average"]) if name == "adx" else pos-neg
    if name == "keltner":
        center = average(x,n,p["average"])
        return div(x-center,p["multiplier"]*average(tr(b),p["atr"],p["average"]))
    if name == "donchian":
        # Past channel: breakouts can exceed 100 or fall below zero.
        return range_position(c,l.shift().rolling(n).min(),h.shift().rolling(n).max())
    if name in ("cmf","chaikin_oscillator"):
        # Zero-range bars contribute zero signed flow, not a division artifact.
        money = div(2*c-h-l,h-l).mask(h.eq(l),0)*v
        if name == "cmf":
            return div(money.rolling(n).sum(),v.rolling(n).sum())
        adl = money.cumsum()
        line = average(adl,p["fast"],"ema")-average(adl,p["slow"],"ema")
        # Scale-independent oscillator to make thresholds comparable across symbols.
        line = 100*div(line, v.rolling(p["slow"]).sum())
    elif name == "vortex":
        atr = tr(b).rolling(n).sum()
        return div((h-l.shift()).abs().rolling(n).sum()-(l-h.shift()).abs().rolling(n).sum(),atr)
    elif name == "kama":
        return 100*(div(x,kama(x,n,p["fast"],p["slow"]))-1)
    elif name == "ultimate_oscillator":
        low = pd.concat([l,c.shift()],axis=1).min(axis=1,skipna=False)
        high = pd.concat([h,c.shift()],axis=1).max(axis=1,skipna=False)
        terms = [div((c-low).rolling(p[k]).sum(),(high-low).rolling(p[k]).sum()) for k in ("fast","middle","slow")]
        return 100*(4*terms[0]+2*terms[1]+terms[2])/7
    elif name == "wavetrend":
        esa = average(x,p["channel"],"ema")
        dev = average((x-esa).abs(),p["channel"],"ema")
        line = average(div(x-esa,.015*dev),p["average"],"ema")
    elif name == "trix":
        line = x
        for _ in range(3):
            line = average(line,n,"ema")
        line = 100*line.pct_change(fill_method=None)
    elif name == "tsi":
        change = x.diff()
        def double(a):
            return average(average(a,p["slow"],"ema"),p["fast"],"ema")
        line = 100*div(double(change),double(change.abs()))
    elif name in ("ppo","macd"):
        fast, slow = average(x,p["fast"],"ema"),average(x,p["slow"],"ema")
        # MACD scaled by current price, PPO by slow EMA.
        line = 100*div(fast-slow,slow if name == "ppo" else x)
    else:
        raise ValueError("unknown indicator: "+name)
    if mode == "histogram":
        smooth = "sma" if name == "wavetrend" else "ema"
        return line-average(line,p["signal"],smooth)
    return line


def signal(bars, rule):
    x = measure(bars,rule)
    valid = pd.Series(np.isfinite(x),index=x.index)
    level, op = rule["level"], rule["op"]
    if op in ("rising","falling","cross_up","cross_down"):
        valid &= valid.shift(fill_value=False)
    if op == "rising":
        flag = x > x.shift()
    elif op == "falling":
        flag = x < x.shift()
    elif op == "above":
        flag = x > level
    elif op == "below":
        flag = x < level
    elif op == "cross_up":
        flag = (x > level)&(x.shift() <= level)
    elif op == "cross_down":
        flag = (x < level)&(x.shift() >= level)
    else:
        raise ValueError("unknown indicator comparator")
    return flag&valid, valid
