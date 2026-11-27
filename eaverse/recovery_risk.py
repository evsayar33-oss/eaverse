"""Capital survival and calendar-time recovery, independent of drawdown magnitude."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

VERSION = "capital-survival-recovery-v1"
MAX_RECOVERY_DAYS = 180

def measure(returns, dates, start):
    """Measure every completed and open underwater spell from the last equity peak.

    Log equity avoids numerical overflow over long histories; returns are net of the
    native executor's costs. No arbitrary percent-loss threshold is used.
    """
    values=np.asarray(returns,dtype=float)
    dates=pd.DatetimeIndex(dates)
    if len(values)!=len(dates) or not len(values) or not dates.is_monotonic_increasing or dates.has_duplicates:
        raise ValueError("unaligned recovery path")
    if pd.Timestamp(start)>dates[0] or not np.isfinite(values).all():
        raise ValueError("invalid recovery path")
    if np.any(values<=-1.):
        return {"ruin":True,"max_recovery_days":None,"completed_recovery_days":None,
                "current_underwater_days":None,"recovery_limit_days":MAX_RECOVERY_DAYS}
    log_equity=np.cumsum(np.log1p(values))
    peaks=np.maximum.accumulate(np.r_[0.,log_equity])[1:]
    at_peak=log_equity>=peaks-1e-10
    days=dates.to_numpy(dtype="datetime64[D]").astype(np.int64)
    start_day=np.datetime64(pd.Timestamp(start).date(),"D").astype(np.int64)
    last_peak=np.maximum.accumulate(np.where(at_peak,days,start_day))
    ages=days-np.r_[start_day,last_peak[:-1]]
    recovered=at_peak & np.r_[False,~at_peak[:-1]]
    longest_completed=int(ages[recovered].max(initial=0))
    longest_open=int(ages[~at_peak].max(initial=0))
    return {"ruin":False,"max_recovery_days":max(longest_completed,longest_open),
            "completed_recovery_days":longest_completed,
            "current_underwater_days":int(ages[-1]) if not at_peak[-1] else 0,
            "recovery_limit_days":MAX_RECOVERY_DAYS}

def reason(metrics):
    if metrics.get("status") in ("ruin","ruin_stress") or metrics.get("ruin") is True or metrics.get("net",0)<=-1:
        return "capital_ruin"
    days=metrics.get("max_recovery_days")
    if metrics.get("ruin") is not False or not isinstance(days,int) or days<0:
        return "missing_recovery_evidence"
    if days>MAX_RECOVERY_DAYS:
        return "recovery_time_exceeded"
    return None
