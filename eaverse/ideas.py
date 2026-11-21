"""Map supported indicator mentions in public upstream READMEs to testable hypotheses."""

import base64
import itertools
import json
import re
import subprocess

from . import research
from .delivery import gh


SOURCES = ("evsayar33-oss/quant_superlab", "evsayar33-oss/h054_spot_bot",
           "evsayar33-oss/bist_orderflow_quant", "evsayar33-oss/early_accumulation_radar",
           "evsayar33-oss/federal-incentive-radar", "evsayar33-oss/islem_onerisi_alarmi",
           "evsayar33-oss/tier1_quant_terminal", "bukosabino/ta")


def refresh(db):
    windows = {**research.FEATURES, **research.EXTENDED}
    for repo in SOURCES:
        try:
            doc = json.loads(gh("api", f"repos/{repo}/readme"))
            if db.execute("SELECT 1 FROM idea_documents WHERE repo=? AND sha=?", (repo, doc["sha"])).fetchone():
                continue
            body = base64.b64decode(doc["content"]).decode().lower()
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
            print(f"Idea source unavailable: {repo} ({type(exc).__name__})")
            continue
        found = [name for name in windows if re.search(r"\b" + name.replace("_", "[ _-]") + r"\b", body)]
        if "rsi" in body:
            found = sorted(set(found + ["rsi_trend"]))
        for size in (1, 2):
            for names in itertools.combinations(found, size):
                spec = {"rules": [[name, windows[name][0]] for name in names],
                        "vote": size, "confirmation": 1, "sizing": "full"}
                db.execute("INSERT OR IGNORE INTO source_ideas VALUES (?,?,?,?)",
                           (repo, doc["sha"], research.digest(spec), research.canonical(spec)))
        db.execute("INSERT OR IGNORE INTO idea_documents VALUES (?,?)", (repo, doc["sha"]))


def pending(db, asset, limit):
    rows = db.execute("""
        SELECT i.id,min(i.spec) FROM source_ideas i
        WHERE NOT EXISTS (SELECT 1 FROM attempts a WHERE a.asset=? AND a.version=? AND a.id=i.id)
        GROUP BY i.id ORDER BY i.id LIMIT ?
    """, (asset, research.VERSION, limit))
    return [(key, json.loads(spec)) for key, spec in rows]
