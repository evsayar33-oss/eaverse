"""Train-only PCA and residual-pair diagnostics; never a PAPER promoter."""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from . import research
VERSION="cross-asset-pca-pairs-v1"

def analyze(panel):
    closes={k:v.close.astype(float) for k,v in panel.items() if len(v)>=300}
    prices=pd.concat(closes,axis=1,join="inner").dropna() if closes else pd.DataFrame()
    returns=np.log(prices).diff().dropna()
    if returns.shape[0]<250 or returns.shape[1]<3:
        return {"status":"INSUFFICIENT_ALIGNED_PANEL","assets":returns.shape[1],"observations":returns.shape[0],"paper_eligible":False,"trade_authorized":False}
    cut=int(len(returns)*.70);train,test=returns.iloc[:cut],returns.iloc[cut:]
    mu,sigma=train.mean(),train.std(ddof=0).replace(0,np.nan)
    ztr=((train-mu)/sigma).dropna(axis=1);zte=((test[ztr.columns]-mu[ztr.columns])/sigma[ztr.columns]).fillna(0)
    _,singular,vt=np.linalg.svd(ztr.to_numpy(),full_matrices=False);loading=vt[0]
    tr=pd.DataFrame(ztr.to_numpy()-np.outer(ztr.to_numpy()@loading,loading),index=ztr.index,columns=ztr.columns)
    te=pd.DataFrame(zte.to_numpy()-np.outer(zte.to_numpy()@loading,loading),index=zte.index,columns=zte.columns)
    candidates=[]
    for left,right in itertools.combinations(ztr.columns,2):
        corr=float(tr[left].corr(tr[right]))
        if np.isfinite(corr): candidates.append((abs(corr),left,right,corr))
    pairs=[]
    for _,left,right,corr in sorted(candidates,reverse=True)[:10]:
        spread_tr=tr[left]-np.sign(corr)*tr[right];scale=float(spread_tr.std(ddof=0))
        if scale<=1e-12: continue
        spread=te[left]-np.sign(corr)*te[right]
        hit=(-np.sign(spread/scale)*(spread.shift(-1)-spread)).iloc[:-1]>0
        pairs.append({"left":left,"right":right,"train_residual_correlation":round(corr,6),"validation_n":len(hit),"validation_mean_reversion_hit":round(float(hit.mean()),6)})
    explained=float(singular[0]**2/np.sum(singular**2))
    return {"status":"RETROSPECTIVE_CAUSAL_DIAGNOSTIC_COMPLETE","split":{"train_end":str(train.index[-1]),"validation_start":str(test.index[0]),"train_n":len(train),"validation_n":len(test)},"pca":{"fit":"DISCOVERY_ONLY","components":1,"explained_variance_ratio":round(explained,6),"loadings":{k:round(float(v),6) for k,v in zip(ztr.columns,loading)}},"pairs":pairs,"selection":"TOP10_TRAIN_RESIDUAL_CORRELATION","paper_eligible":False,"trade_authorized":False,"requires_superlab_independent_validation":True}

def run(db,panel):
    result=analyze(panel);identity=research.digest([VERSION,sorted(panel),result.get("split")])
    if db.execute("SELECT 1 FROM attempts WHERE version=? AND asset=? AND id=?",(VERSION,"CROSS_ASSET_PANEL",identity)).fetchone(): return {"status":"duplicate_not_counted","id":identity}
    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(VERSION,"CROSS_ASSET_PANEL",identity,"pca_residual_pairs",research.canonical({"assets":sorted(panel),"promotion":"disabled"}),"aligned_completed_bars",identity,result.get("split",{}).get("train_end","insufficient"),"local",research.canonical({"split":"chronological_70_30","fit":"train_only"}),research.canonical({"pass":False,"paper_eligible":False}),research.canonical(result)))
    return {"status":result["status"],"id":identity,"assets":len(panel),"paper_eligible":False}
