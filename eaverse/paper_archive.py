"""Append-only exact candle evidence for verified PAPER prefixes.

Archive timestamps describe when this archive was recorded, not historical
first-seen times. No alternate provider or archived price bypasses PAPER gates.
"""
from datetime import UTC, datetime
import hashlib
import json
import zlib

import pandas as pd

from . import data


def encode(frame):
    return json.dumps([[day.strftime("%Y-%m-%d"), *[float(v).hex() for v in values]]
                       for day, values in zip(frame.index, frame[data.COLUMNS].to_numpy(), strict=True)],
                      separators=(",", ":")).encode()


def read_prefix(db, sequence):
    chunks = db.execute("SELECT * FROM paper_candle_archive WHERE sequence=? ORDER BY through_day",
                        (sequence,)).fetchall()
    items = []
    source = None
    for chunk in chunks:
        raw = zlib.decompress(chunk["payload"])
        if hashlib.sha256(raw).hexdigest() != chunk["content_sha256"]:
            raise ValueError("PAPER archive content digest mismatch")
        rows = json.loads(raw)
        if not rows or rows[0][0] != chunk["first_day"] or rows[-1][0] != chunk["through_day"]:
            raise ValueError("PAPER archive date bounds mismatch")
        if source is not None and source != chunk["source"]:
            raise ValueError("PAPER archive source changed")
        source = chunk["source"]
        items.extend(rows)
        frame = pd.DataFrame([[float.fromhex(v) for v in r[1:]] for r in items],
                             index=pd.DatetimeIndex([r[0] for r in items]), columns=data.COLUMNS)
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError("PAPER archive date overlap")
        if data.fingerprint(frame) != chunk["prefix_sha256"]:
            raise ValueError("PAPER archive prefix digest mismatch")
    return (frame, source) if chunks else (None, None)


def capture(db, row, frame):
    if frame.empty or frame.index[0].strftime("%Y-%m-%d") != row["anchor"]:
        raise ValueError("PAPER archive missing origin")
    origin = frame.loc[:row["cutoff"]]
    if data.fingerprint(origin) != row["digest"]:
        raise ValueError("PAPER archive cannot verify origin")
    if row["cursor"] and data.fingerprint(frame.loc[:row["cursor"]]) != row["history_digest"]:
        raise ValueError("PAPER archive cannot verify prior observations")
    source = json.loads(row["origin"]).get("source", "legacy-unspecified")
    archived, old_source = read_prefix(db, row["sequence"])
    if archived is not None:
        if source != old_source or not archived.index.equals(frame.loc[:archived.index[-1]].index):
            raise ValueError("PAPER archive source or prefix changed")
        if data.fingerprint(archived) != data.fingerprint(frame.loc[:archived.index[-1]]):
            raise ValueError("PAPER archive conflicts with verified prefix")
        frame_new = frame.loc[frame.index > archived.index[-1]]
    else:
        frame_new = frame
    if frame_new.empty:
        return
    raw = encode(frame_new)
    db.execute("INSERT INTO paper_candle_archive VALUES (?,?,?,?,?,?,?,?)",
               (row["sequence"], frame_new.index[-1].strftime("%Y-%m-%d"),
                frame_new.index[0].strftime("%Y-%m-%d"), source, datetime.now(UTC).isoformat(),
                data.fingerprint(frame), hashlib.sha256(raw).hexdigest(), zlib.compress(raw, 6)))
