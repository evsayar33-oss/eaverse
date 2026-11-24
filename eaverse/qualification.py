"""Run the actual pinned Superlab qualification contract on every new main-engine record."""
import json
import os
from . import research


def review_new(db, first_technical, first_event):
    try:
        from research.portfolio_qualification.eaverse_coverage import audit_record
    except ImportError:
        return {"status":"central_contract_unavailable","reviewed":0}
    reviewed=invalid=0
    for kind,table,column,first in (("technical","attempts","discovered",first_technical),
                                    ("event","event_trials","summary",first_event)):
        for row in list(db.execute(f"SELECT rowid,* FROM {table} WHERE rowid>?",(first,))):
            raw=json.loads(row[column])
            try:
                receipt=audit_record(row,kind)
                reviewed+=1
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                receipt={"status":"INVALID_EVIDENCE","error":str(exc),"paper_approved":False,"live_allowed":False}
                invalid+=1
            raw["superlab_receipt"]=receipt
            raw["superlab_contract_commit"]=os.getenv("EAVERSE_QS_CONTRACT_SHA","local")
            db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",(research.canonical(raw),row["rowid"]))
    return {"status":"reviewed" if not invalid else "invalid_evidence","reviewed":reviewed,"invalid":invalid}
