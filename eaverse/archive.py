"""Archive full historical records while keeping compact deduplication keys."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .checkpoint import file_digest
from .delivery import gh
from .engine import connect
from .research import canonical


CHUNK = 30_000


def ready_records(db, limit):
    attempts = list(db.execute(
        "SELECT rowid,* FROM attempts WHERE policy<>'' ORDER BY rowid LIMIT ?", (limit,)))
    events = list(db.execute(
        "SELECT rowid,* FROM event_trials WHERE calendar<>'' ORDER BY rowid LIMIT ?", (limit - len(attempts),)))
    return attempts, events


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
    assets = json.loads(gh("api", assets_endpoint))
    if not any(item["name"] == path.name for item in assets):
        gh("release", "upload", tag, str(path), "-R", repo)
        assets = json.loads(gh("api", assets_endpoint))
    asset = next(item for item in assets if item["name"] == path.name)
    if asset.get("digest") != f"sha256:{sha}":
        if asset.get("digest") or asset["size"] != path.stat().st_size:
            raise ValueError("archive release checksum or size mismatch")
        with tempfile.TemporaryDirectory() as verify:
            gh("release", "download", tag, "-R", repo, "--pattern", path.name, "--dir", verify)
            if file_digest(Path(verify) / path.name) != sha:
                raise ValueError("downloaded archive failed verification")
    return asset["browser_download_url"]


def compact(db, limit=CHUNK):
    attempts, events = ready_records(db, limit)
    if len(attempts) + len(events) < limit:
        return None
    tag = "research-history-" + datetime.now(UTC).strftime("%Y-%m")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "records"
        with path.open("wb") as outer, gzip.GzipFile(fileobj=outer, mode="wb", mtime=0, filename="") as out:
            for kind, records in (("technical", attempts), ("event", events)):
                for row in records:
                    item = dict(row)
                    del item["rowid"]
                    out.write((canonical({"type": kind, "row": item}) + "\n").encode())
        sha = file_digest(path)
        path = path.rename(path.with_name(f"research-{sha}.jsonl.gz"))
        location = upload(path, sha, tag)
    with db:
        db.execute("INSERT OR IGNORE INTO history_archives VALUES (?,?,?)",
                   (sha, location, len(attempts) + len(events)))
        db.executemany(
            """UPDATE attempts SET spec='',source='',snapshot='',asof='',code='',policy='',discovered=?,validated=NULL
            WHERE rowid=?""",
            [(canonical({"pass": json.loads(row["discovered"])["pass"], "archive": sha}), row["rowid"])
             for row in attempts],
        )
        db.executemany("UPDATE event_trials SET snapshot='', asof='', calendar='', summary=? WHERE rowid=?",
                       [(canonical({"archive": sha}), row["rowid"]) for row in events])
    db.execute("VACUUM")
    return location


if __name__ == "__main__":
    with connect("runtime/research.sqlite") as state:
        archive = compact(state)
    if archive:
        print(archive)
