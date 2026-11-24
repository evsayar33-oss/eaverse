"""Independent parameter surface for every original OHLCV family.

Every spec is explicit and immutable. This is bounded exploration, not a claim
that every real-valued parameter or indicator definition has been exhausted.
"""
import numpy as np
import pandas as pd
from . import coverage

FAMILIES=tuple(dict.fromkeys(rule[0] for rule in coverage.rule_grid("1d")))
OPS=("above","below","cross_up","cross_down")

def choose(frame,name,rng):
    windows=(5,7,10,14,20,26,40,52) if frame=="1w" else (5,7,10,14,20,30,40,60,90,120,200)
    n=rng.choice(windows)
    aux=rng.choice((5,10,20,30,52) if frame=="1w" else (5,10,20,30,60,100,200))
    smooth=rng.choice(("sma","ema","wilder","median"))
    source=rng.choice(("close","open","hl2","hlc3","ohlc4"))
    levels={
      "rsi_recovery":(20,30,40,50),"rsi_trend":(40,50,60,70),
      "stoch_recovery":(15,20,30,50,70,80),"adx":(15,20,25,30,40),
      "low_vol":(.5,.75,1,1.25,1.5),"volume":(.8,1,1.2,1.5,2),
      "atr":(1,2,3,5,8),"bollinger":(-2,-1,0,1,2),"mean_reversion":(-2,-1,0,1,2),
      "macd":(-1,-.25,0,.25,1),"obv":(-.5,-.2,0,.2,.5),
      "vwma":(-5,-2,0,2,5),"sma":(-5,-2,0,2,5),"ema":(-5,-2,0,2,5),
      "momentum":(-5,-2,0,2,5),"breakout":(-2,0,1,2)}
    level=rng.choice(levels.get(name,coverage.NORMALIZED.get(name,(0,))))
    return ["surface",name,n,aux,smooth,source,level,rng.choice(OPS)]

def warmup(rule):
    _,name,n,aux,*_=rule
    if name=="macd": return max(n+1,aux)+min(n,aux)+5
    if name in ("adx","cost_z","low_vol"): return n+aux+5
    if name=="stoch_recovery": return n+min(aux,10)+5
    return n+5

def average(x,n,kind):
    if kind=="sma": return x.rolling(n).mean()
    if kind=="median": return x.rolling(n).median()
    if kind=="ema": return x.ewm(span=n,min_periods=n,adjust=False).mean()
    return x.ewm(alpha=1/n,min_periods=n,adjust=False).mean()

def div(a,b):
    return a/b.where(b.abs()>1e-12)

def z(x,n,kind):
    past=x.shift()
    return div(x-average(past,n,kind),past.rolling(n).std(ddof=0))

def measure(b,rule):
    _,name,n,aux,kind,source,_,_=rule
    c={"close":b.close,"open":b.open,"hl2":(b.high+b.low)/2,
       "hlc3":(b.high+b.low+b.close)/3,"ohlc4":(b.open+b.high+b.low+b.close)/4}[source]
    v=b.volume
    if name in ("sma","ema"):
        basis=average(c,n,kind)
        return 100*(div(c,basis)-1)
    if name in ("momentum","return_pct"):
        return 100*c.pct_change(n,fill_method=None)
    if name=="breakout":
        return 100*(div(c,b.high.shift().rolling(n).max())-1)
    if name in ("rsi_recovery","rsi_trend"):
        delta=c.diff()
        gain=average(delta.clip(lower=0),n,kind)
        loss=average(-delta.clip(upper=0),n,kind)
        return 100*div(gain,gain+loss)
    if name=="volume":
        return div(v,average(v.shift(),n,kind))
    if name=="volume_pct":
        return 100*(div(v,average(v.shift(),n,kind))-1)
    if name=="volume_z": return z(v,n,kind)
    if name=="price_z": return z(c,n,kind)
    if name=="low_vol":
        vol=c.pct_change(fill_method=None).rolling(n).std()
        return div(vol,average(vol.shift(),aux,kind))
    if name in ("atr","atr_pct","adx"):
        tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
        atr=average(tr,n,kind)
        if name!="adx": return 100*div(atr,c)
        up,down=b.high.diff(),-b.low.diff()
        p=div(average(up.where((up>down)&(up>0),0),n,kind),atr)
        m=div(average(down.where((down>up)&(down>0),0),n,kind),atr)
        return average(100*div((p-m).abs(),p+m),aux,kind)
    if name in ("bollinger","mean_reversion"):
        return div(c-average(c,n,kind),c.rolling(n).std())
    if name=="macd":
        slow=max(n+1,aux)
        x=average(c,n,kind)-average(c,slow,kind)
        return 100*div(x-average(x,max(3,min(n,aux)),kind),c)
    if name=="stoch_recovery":
        low=b.low.rolling(n).min()
        return average(100*div(c-low,b.high.rolling(n).max()-low),max(1,min(aux,10)),kind)
    if name in ("obv","flow_ratio"):
        return div(average(np.sign(c.diff())*v,n,kind),average(v,n,kind))
    if name in ("vwma","cost_pct","cost_z"):
        basis=div(average(c*v,n,kind),average(v,n,kind))
        distance=100*(div(c,basis)-1)
        return z(distance,aux,kind) if name=="cost_z" else distance
    if name=="range_pct": return 100*average(div(b.high-b.low,c),n,kind)
    if name=="realized_vol_pct": return 100*c.pct_change(fill_method=None).rolling(n).std()
    raise ValueError("UNREGISTERED_SURFACE_FAMILY")

def signal(bars,rule):
    x=measure(bars,rule)
    level,op=rule[-2:]
    if op=="above": flag=x>level
    elif op=="below": flag=x<level
    elif op=="cross_up": flag=(x>level)&(x.shift()<=level)
    elif op=="cross_down": flag=(x<level)&(x.shift()>=level)
    else: raise ValueError("UNREGISTERED_OPERATOR")
    valid=x.notna()&np.isfinite(x)
    return flag&valid,valid
