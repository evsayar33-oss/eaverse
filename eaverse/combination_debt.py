"""Lossless compact blocked-rank ledger for the deterministic combination catalog.

Ranks may exceed SQLite's signed integers. Store them in fixed-width chunk
keys and keep a 1024-bit bitmap per chunk/reason. Retry sweeps have a frozen
upper bound so new work cannot indefinitely move the retry frontier.
"""
from __future__ import annotations

CHUNK = 1024
WIDTH = 40  # Much larger than every finite catalog rank; checked, never truncated.
DEBT = """CREATE TABLE IF NOT EXISTS combination_debt_bits(
 asset TEXT NOT NULL,frame TEXT NOT NULL,lane TEXT NOT NULL,version TEXT NOT NULL,
 chunk TEXT NOT NULL,reason TEXT NOT NULL,bits BLOB NOT NULL,count INTEGER NOT NULL,
 PRIMARY KEY(asset,frame,lane,version,chunk,reason))"""
SWEEPS = """CREATE TABLE IF NOT EXISTS combination_debt_sweep(
 asset TEXT NOT NULL,frame TEXT NOT NULL,lane TEXT NOT NULL,version TEXT NOT NULL,
 next_rank TEXT NOT NULL,stop_rank TEXT NOT NULL,token TEXT NOT NULL,
 PRIMARY KEY(asset,frame,lane,version))"""
KEY = "asset=? AND frame=? AND lane=? AND version=?"


def initialize(db):
    db.execute(DEBT)
    db.execute(SWEEPS)


def chunk_key(rank):
    if rank < 0:
        raise ValueError("negative debt rank")
    chunk, offset = divmod(rank, CHUNK)
    key = str(chunk).zfill(WIDTH)
    if len(key) != WIDTH:
        raise OverflowError("combination debt exceeds chunk key width")
    return key, offset


def decode(bits):
    return int.from_bytes(bits, "little")


def save(db,args,chunk,reason,bits):
    if bits:
        data=bits.to_bytes((bits.bit_length()+7)//8,"little")
        db.execute("INSERT OR REPLACE INTO combination_debt_bits VALUES (?,?,?,?,?,?,?,?)",
                   (*args,chunk,reason,data,bits.bit_count()))
    else:
        db.execute(f"DELETE FROM combination_debt_bits WHERE {KEY} AND chunk=? AND reason=?",
                   (*args,chunk,reason))


def set_reason(db,args,rank,reason):
    """Move a rank between blockers or clear it; never double-count a rank."""
    chunk, offset=chunk_key(rank)
    point=1<<offset
    rows=list(db.execute(f"SELECT reason,bits FROM combination_debt_bits WHERE {KEY} AND chunk=?",
                         (*args,chunk)))
    previous={r:decode(b) for r,b in rows}
    for name,bits in previous.items():
        if name!=reason and bits&point:
            save(db,args,chunk,name,bits&~point)
    if reason is not None:
        current=previous.get(reason,0)
        if not current&point:
            save(db,args,chunk,reason,current|point)


def count(db,args):
    return int(db.execute(f"SELECT coalesce(sum(count),0) FROM combination_debt_bits WHERE {KEY}",
                          args).fetchone()[0])


def migrate_lane(db,args):
    """Losslessly migrate previous per-rank rows once, inside the caller savepoint."""
    rows=list(db.execute(f"SELECT rank,reason FROM combination_pending WHERE {KEY}",args))
    if not rows:
        return
    for rank,reason in rows:
        set_reason(db,args,int(rank),reason)
    # Verify every old entry's exact bit and reason before removing old storage.
    for rank,reason in rows:
        chunk,offset=chunk_key(int(rank))
        row=db.execute(f"SELECT bits FROM combination_debt_bits WHERE {KEY} AND chunk=? AND reason=?",
                       (*args,chunk,reason)).fetchone()
        if row is None or not decode(row[0])&(1<<offset):
            raise ValueError("combination debt migration verification failed")
    db.execute(f"DELETE FROM combination_pending WHERE {KEY}",args)


def next_blocked(db,args,start,stop):
    """Lowest blocked rank in [start,stop), using indexed sparse chunks."""
    if start>=stop:
        return None
    first,offset=chunk_key(start)
    last,_=chunk_key(stop-1)
    # Query only the first nonempty candidate chunk, then the next if its
    # lower bits precede the current retry cursor. No whole-catalog scans.
    for lower,inclusive in ((first,True),(first,False)):
        op=">=" if inclusive else ">"
        row=db.execute(f"""SELECT chunk FROM combination_debt_bits WHERE {KEY}
                         AND chunk {op} ? AND chunk<=? ORDER BY chunk LIMIT 1""",
                       (*args,lower,last)).fetchone()
        if row is None:
            return None
        key=row[0]
        value=0
        for (bits,) in db.execute(f"SELECT bits FROM combination_debt_bits WHERE {KEY} AND chunk=?",
                                   (*args,key)):
            value |= decode(bits)
        if key==first:
            value &= ~((1<<offset)-1)
        if not value:
            continue
        bit=(value & -value).bit_length()-1
        rank=int(key)*CHUNK+bit
        return rank if rank<stop else None
    return None


def set_sweep(db,args,start,stop,token):
    db.execute("INSERT OR REPLACE INTO combination_debt_sweep VALUES (?,?,?,?,?,?,?)",
               (*args,str(start),str(stop),token))


def pick_retry(db,args,frontier,token):
    """At most one blocked rank, without starvation from a moving frontier.

    Start a new bounded sweep only on a changed source token. A sweep in
    progress keeps its original endpoint even if new data arrives. New ranks
    are not eligible for the current sweep; all old ranks are visited first.
    """
    row=db.execute(f"SELECT next_rank,stop_rank,token FROM combination_debt_sweep WHERE {KEY}",
                   args).fetchone()
    if row:
        start,stop,old_token=int(row[0]),int(row[1]),row[2]
    else:
        start=stop=0
        old_token=None
    if start>=stop:
        if old_token==token:
            return None
        start,stop,old_token=0,frontier,token
    rank=next_blocked(db,args,start,stop)
    set_sweep(db,args,stop if rank is None else rank+1,stop,old_token)
    return rank


def counts(db,version):
    """Aggregate both storage versions, including untouched pre-upgrade lanes."""
    result={}
    for a,f,l,n in db.execute("""SELECT asset,frame,lane,count(*) FROM combination_pending
                               WHERE version=? GROUP BY asset,frame,lane""",(version,)):
        result[a,f,l]=n
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='combination_debt_bits'").fetchone():
        for a,f,l,n in db.execute("""SELECT asset,frame,lane,sum(count) FROM combination_debt_bits
                                  WHERE version=? GROUP BY asset,frame,lane""",(version,)):
            result[a,f,l]=result.get((a,f,l),0)+n
    return result
