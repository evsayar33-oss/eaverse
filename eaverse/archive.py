"""Archive detailed research evidence, retaining exact dedup keys and screen outcomes."""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .checkpoint import file_digest
from .delivery import gh
from .engine import connect
from . import research, foundry
from .research import canonical


CHUNK = 30_000
FAILED_BATCH = 256
DETAIL_BYTES = 16 * 1024 * 1024
MAX_BATCHES = 4
NEGATIVE = ("discovery_gate_failed", "validation_gate_failed", "historical_loss", "historical_break_even", "historical_ruin")
INDEX = "CREATE TABLE IF NOT EXISTS archive_outcome_indexed(sha TEXT PRIMARY KEY)"


def metric_summary(report):
    return {k: report[k] for k in ("net", "drawdown", "entries", "trades", "status", "profitable_folds")
            if k in report}


def historical_outcome(train, check):
    """Describe the observed historical result, not a permanent family verdict."""
    if any(item.get("status") in ("ruin", "ruin_stress") for item in (train, check)):
        return "historical_ruin"
    if check.get("status") == "no_entries" or (
        check.get("net") == 0 and check.get("trades") == 0
    ):
        return "no_trade_evidence"
    if isinstance(check.get("net"), (int, float)):
        if check["net"] < 0:
            return "historical_loss"
        if check["net"] == 0 and check.get("entries", check.get("trades", 0)) > 0:
            return "historical_break_even"
        return "screened_research_only"
    return "unclassified"


def technical_summary(row):
    d = json.loads(row["discovered"])
    v = json.loads(row["validated"]) if row["validated"] else {}
    train, check = d.get("metrics", d), v.get("metrics", v)
    receipt = d.get("superlab_receipt") or {}
    audit = v.get("execution_audit") or {}
    if receipt.get("status") == "INVALID_EVIDENCE" or audit.get("status") == "mismatch":
        outcome = "invalid_evidence"
    elif row["version"] == research.VERSION:
        if d.get("pass") is False:
            outcome = "discovery_gate_failed"
        elif v.get("pass") is False:
            outcome = "validation_gate_failed"
        else:
            outcome = "screened_not_final"
    else:
        # Coverage/Foundry/combination engines use pass=False even when
        # profitable. Do NOT mistake research-only eligibility for failure.
        outcome = historical_outcome(train, check)
    result = {"pass": d["pass"], "outcome": outcome, "discovery_metrics": metric_summary(train),
              "validation_metrics": metric_summary(check)}
    if row["version"] == foundry.VERSION and "metrics" in d:
        # Adaptive discovery reads only these metrics, never the validation
        # outcome. Retain its input and spec so archiving does not prune parents.
        result["metrics"] = d["metrics"]
    if "return_evidence" in d and d["return_evidence"].get("sha256"):
        result["return_evidence_sha256"] = d["return_evidence"]["sha256"]
    return result


def event_summary(row):
    d = json.loads(row["summary"])
    train, check = d.get("discovery", {}), d.get("recent", {})
    outcome = ("invalid_evidence" if (d.get("superlab_receipt") or {}).get("status") == "INVALID_EVIDENCE"
               else historical_outcome(train, check))
    return {"outcome": outcome, "quarter": d.get("quarter"), "discovery_metrics": metric_summary(train),
            "recent_metrics": metric_summary(check)}


def ready_records(db, limit, byte_limit=DETAIL_BYTES):
    """Read at most one bounded batch, interleaving old technical/event rows."""
    cursors = [iter(db.execute("SELECT rowid,* FROM attempts WHERE policy<>'' ORDER BY rowid")),
               iter(db.execute("SELECT rowid,* FROM event_trials WHERE calendar<>'' ORDER BY rowid"))]
    records = [[], []]
    size = count = 0
    while count < limit and size < byte_limit:
        progressed = False
        for i, cursor in enumerate(cursors):
            row = next(cursor, None)
            if row is None:
                continue
            progressed = True
            records[i].append(row)
            size += sum(len(v.encode()) for v in row if isinstance(v, str))
            count += 1
            if count >= limit or size >= byte_limit:
                break
        if not progressed:
            break
    return records[0], records[1], size


def upload(path, sha, tag):
    repo = os.environ["GITHUB_REPOSITORY"]
    endpoint = f"repos/{repo}/releases/tags/{tag}"
    try:
        release = json.loads(gh("api", endpoint))
    except subprocess.CalledProcessError as exc:
        if "(HTTP 404)" not in exc.stderr:
            raise
        gh("release", "create", tag, "-R", repo, "--target", os.environ["GITHUB_SHA"],
           "--title", f"Eaverse research history {tag.removeprefix('research-history-')}",
           "--notes", "Immutable JSONL archives of technical and event research records.", "--latest=false")
        release = json.loads(gh("api", endpoint))
    assets_endpoint = f"repos/{repo}/releases/{release['id']}/assets?per_page=100"

    def find_asset():
        page = 1
        while True:
            assets = json.loads(gh("api", f"{assets_endpoint}&page={page}"))
            asset = next((item for item in assets if item["name"] == path.name), None)
            if asset is not None or len(assets) < 100:
                return asset
            page += 1

    asset = find_asset()
    if asset is None:
        gh("release", "upload", tag, str(path), "-R", repo)
        asset = find_asset()
    if asset is None or asset.get("state", "uploaded") != "uploaded" or asset["size"] != path.stat().st_size:
        raise ValueError("archive asset missing, incomplete or size mismatch")
    if asset.get("digest") != f"sha256:{sha}":
        if asset.get("digest"):
            raise ValueError("archive release checksum mismatch")
        with tempfile.TemporaryDirectory() as verify:
            gh("release", "download", tag, "-R", repo, "--pattern", path.name, "--dir", verify)
            if file_digest(Path(verify) / path.name) != sha:
                raise ValueError("downloaded archive failed verification")
    return asset["browser_download_url"]


def compact(db, limit=CHUNK, *, byte_limit=DETAIL_BYTES, failed_batch=FAILED_BATCH, vacuum=True):
    attempts, events, size = ready_records(db, limit, byte_limit)
    technical = [(row, technical_summary(row)) for row in attempts]
    calendar = [(row, event_summary(row)) for row in events]
    if len(attempts) + len(events) < limit and size < byte_limit:
        # Archive clearly negative and no-trade records earlier. All eligible
        # historical details remain in the verified immutable release.
        early = NEGATIVE + ("no_trade_evidence",)
        technical = [(r, s) for r, s in technical if s["outcome"] in early]
        calendar = [(r, s) for r, s in calendar if s["outcome"] in early]
        if len(technical) + len(calendar) < min(failed_batch, limit):
            return None
    if not technical and not calendar:
        return None
    tag = "research-history-" + datetime.now(UTC).strftime("%Y-%m")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "records"
        with path.open("wb") as outer, gzip.GzipFile(fileobj=outer, mode="wb", mtime=0, filename="") as out:
            for kind, records in (("technical", technical), ("event", calendar)):
                for row, _ in records:
                    item = dict(row)
                    del item["rowid"]
                    out.write((canonical({"type": kind, "row": item}) + "\n").encode())
        sha = file_digest(path)
        path = path.rename(path.with_name(f"research-{sha}.jsonl.gz"))
        location = upload(path, sha, tag)
    # No active detail is removed before a verified upload. Retain source,
    # snapshot hash, date, code, version, asset, id and per-fold outcome.
    # Existing uniqueness constraints remain the exact no-repeat registry.
    with db:
        db.execute(INDEX)
        db.execute("INSERT OR IGNORE INTO history_archives VALUES (?,?,?)",
                   (sha, location, len(technical) + len(calendar)))
        db.executemany(
            """UPDATE attempts SET spec=?,policy='',discovered=?,validated=NULL WHERE rowid=?""",
            [(row["spec"] if row["version"] == foundry.VERSION else "",
              canonical({**summary, "archive": sha}), row["rowid"]) for row, summary in technical],
        )
        db.executemany("UPDATE event_trials SET calendar='', summary=? WHERE rowid=?",
                       [(canonical({**summary, "archive": sha, "calendar_sha256": row["calendar"]}), row["rowid"])
                        for row, summary in calendar])
        db.execute("INSERT OR IGNORE INTO archive_outcome_indexed VALUES (?)", (sha,))
    if vacuum:
        db.execute("VACUUM")
    return location


def backfill_one(db):
    """Re-index one legacy archive per cycle; never invent missing evidence."""
    db.execute(INDEX)
    old = db.execute("""SELECT sha,url,records FROM history_archives h
        WHERE NOT EXISTS (SELECT 1 FROM archive_outcome_indexed i WHERE i.sha=h.sha)
        ORDER BY sha LIMIT 1""").fetchone()
    if old is None:
        return None
    sha, url, expected = old
    repo = os.environ["GITHUB_REPOSITORY"]
    name = f"research-{sha}.jsonl.gz"
    match = re.fullmatch(r"https://github\.com/" + re.escape(repo) +
                         r"/releases/download/(research-history-[0-9]{4}-[0-9]{2})/" +
                         re.escape(name), url)
    if not match or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("unexpected legacy archive source")
    with tempfile.TemporaryDirectory() as temp:
        gh("release", "download", match[1], "-R", repo, "--pattern", name, "--dir", temp)
        path = Path(temp) / name
        if file_digest(path) != sha:
            raise ValueError("legacy archive checksum mismatch")
        seen = set()
        with db, gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                row = item["row"]
                kind = item["type"]
                key = (row["version"], row["asset"], row["id"])
                if (kind, key) in seen or len(seen) >= expected:
                    raise ValueError("legacy archive duplicate or excess records")
                seen.add((kind, key))
                if kind == "technical":
                    summary = technical_summary(row)
                    table, column, marker = "attempts", "discovered", "policy"
                elif kind == "event":
                    summary = {**event_summary(row), "calendar_sha256": row["calendar"]}
                    table, column, marker = "event_trials", "summary", "calendar"
                else:
                    raise ValueError("unknown legacy archive record type")
                existing = db.execute(f"""SELECT {column} FROM {table} WHERE
                    version=? AND asset=? AND id=? AND {marker}=''""", key).fetchone()
                if existing is None or json.loads(existing[0]).get("archive") != sha:
                    raise ValueError("legacy archive does not match permanent test key")
                # Preserve the generator's existing spec availability; restore
                # provenance, result summary and reference, not full payload.
                db.execute(f"""UPDATE {table} SET {column}=?,source=?,snapshot=?,asof=?,code=?
                    WHERE version=? AND asset=? AND id=?""" if kind == "technical" else
                    f"""UPDATE {table} SET {column}=?,snapshot=?,asof=?
                    WHERE version=? AND asset=? AND id=?""",
                    (canonical({**summary, "archive": sha}), row["source"], row["snapshot"],
                     row["asof"], row["code"], *key) if kind == "technical" else
                    (canonical({**summary, "archive": sha}), row["snapshot"], row["asof"], *key))
            if len(seen) != expected:
                raise ValueError("legacy archive record count mismatch")
            db.execute("INSERT INTO archive_outcome_indexed VALUES (?)", (sha,))
    return sha


def compact_cycle(db):
    """Keep catch-up bounded; compact physical pages once even after partial failure."""
    locations = []
    try:
        for _ in range(MAX_BATCHES):
            location = compact(db, vacuum=False)
            if location is None:
                break
            locations.append(location)
    finally:
        if locations:
            db.execute("VACUUM")
    return locations


def outcome_counts(db):
    """Count verified compact rows, never infer failure from a coverage pass flag."""
    groups = {}
    for table, column, marker in (("attempts", "discovered", "policy"),
                                  ("event_trials", "summary", "calendar")):
        for asset, outcome, n in db.execute(f"""SELECT asset,coalesce(json_extract({column},'$.outcome'),
                 'legacy_unknown'),count(*) FROM {table}
                 WHERE {marker}='' AND json_valid({column}) AND json_extract({column},'$.archive') IS NOT NULL
                 GROUP BY asset,2"""):
            group = groups.setdefault(asset, {"asset": asset, "archived": 0, "negative": 0,
                                             "no_trade_evidence": 0, "other": 0, "unknown": 0})
            group["archived"] += n
            key = ("negative" if outcome in NEGATIVE else "no_trade_evidence" if outcome == "no_trade_evidence"
                   else "unknown" if outcome in ("legacy_unknown", "unclassified", "invalid_evidence") else "other")
            group[key] += n
    rows = sorted(groups.values(), key=lambda r: r["asset"])
    totals = {k: sum(r[k] for r in rows) for k in ("archived", "negative", "no_trade_evidence", "other", "unknown")}
    return {**totals, "asset_rows": len(rows), "assets": rows[:2000]}


if __name__ == "__main__":
    with connect("runtime/research.sqlite") as state:
        for location in compact_cycle(state):
            print(location)
        indexed = backfill_one(state)
        if indexed:
            print(f"indexed legacy archive {indexed}")
