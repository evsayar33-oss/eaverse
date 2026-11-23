"""Research-only adapters from real publisher records to Eaverse PIT event rows.

Adapters do not fetch, redistribute, infer historical first_seen or approve data rights.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .pit_research import utc

SEC_FORMS = {"8-K": "SEC_8K", "10-K": "SEC_10K", "10-Q": "SEC_10Q", "4": "SEC_FORM4"}
BINANCE_FLOW_CUTOFF = 0.20  # frozen, exploratory; this is NOT L2/CVD


def sec_events(payload: dict, *, asset: str, retrieved_utc: str) -> list[dict]:
    """SEC company submissions. Delay acceptance +24h: accepted != disseminated.

    The associated historical issuer identity must be reconciled separately.
    Do not use current ticker mapping for past delisted US stocks.
    """
    retrieved = utc(retrieved_utc)
    cik = int(payload["cik"])
    if cik <= 0 or asset != f"US:CIK{cik:010d}":
        raise ValueError("SEC CIK/permanent asset identity mismatch")
    recent = payload["filings"]["recent"]
    fields = ("accessionNumber", "form", "acceptanceDateTime")
    if any(field not in recent for field in fields):
        raise ValueError("SEC acceptance timestamp is required; filingDate is not PIT publication")
    if len({len(recent[field]) for field in fields}) != 1:
        raise ValueError("SEC submissions arrays misaligned")
    events = []
    for accession, form, accepted in zip(*(recent[field] for field in fields)):
        if form not in SEC_FORMS or not accepted:
            continue
        timestamp = datetime.fromisoformat(str(accepted).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))
        usable = timestamp.astimezone(timezone.utc) + timedelta(days=1)
        if usable > retrieved:
            continue
        events.append({
            "id": accession, "kind": SEC_FORMS[form],
            "market": "US", "asset": asset, "clock_basis": "ACCEPTANCE_PLUS_24H",
            "public_at_utc": usable.isoformat(),
            "first_seen_utc": retrieved.isoformat(),
            "source_acceptance_at_utc": timestamp.astimezone(timezone.utc).isoformat(),
            "issuer_cik": cik,
        })
    return sorted(events, key=lambda value: (value["public_at_utc"], value["id"]))


def _binance_stamp(raw: str) -> datetime:
    stamp = int(raw)
    # Binance post-2025 archives may use microseconds; disallow guessed seconds.
    if not (10**12 <= stamp < 10**17):
        raise ValueError("unsupported Binance epoch unit")
    seconds = stamp / (1_000_000 if stamp >= 10**14 else 1_000)
    return datetime.fromtimestamp(seconds, timezone.utc)


def binance_daily_klines(raw: bytes, *, symbol: str, retrieved_utc: str):
    """Parse Binance Vision 1-day spot kline CSV with actual taker-buy base volume.

    Output 24h net taker BUY minus SELL volume proxy, NOT trade-by-trade CVD,
    full-depth order-book imbalance, or Coinbase venue prices.
    """
    retrieved = utc(retrieved_utc)
    if not symbol or not symbol.isalnum() or not symbol.endswith("USDT"):
        raise ValueError("explicit Binance USDT pair required")
    asset = f"CRYPTO:BINANCE_SPOT:{symbol}"
    bars, events = [], []
    for line_number, row in enumerate(csv.reader(io.StringIO(raw.decode("utf-8"))), 1):
        if not row:
            continue
        if len(row) != 12:
            raise ValueError(f"Binance kline row {line_number}: expected 12 columns")
        start, end = _binance_stamp(row[0]), _binance_stamp(row[6])
        if start.hour or start.minute or start.second or (end - start).total_seconds() < 86398:
            raise ValueError("Binance source is not a completed UTC daily candle")
        if end + timedelta(seconds=1) > retrieved:
            raise ValueError("unfinished or future Binance candle")
        opn, high, low, close, volume = map(float, (row[1], row[2], row[3], row[4], row[5]))
        taker_buy = float(row[9])
        if volume < 0 or not (0 <= taker_buy <= volume * (1 + 1e-10)):
            raise ValueError("invalid reported Binance taker-buy base volume")
        if volume == 0:
            imbalance = 0.0
        else:
            imbalance = (2 * taker_buy - volume) / volume
        bars.append({"date": start.date().isoformat(), "open": opn, "high": high,
                     "low": low, "close": close, "volume": volume})
        kind = ("BINANCE_TAKER_BUY_DOMINANCE" if imbalance >= BINANCE_FLOW_CUTOFF
                else "BINANCE_TAKER_SELL_DOMINANCE" if imbalance <= -BINANCE_FLOW_CUTOFF else None)
        if kind:
            events.append({
                "id": f"{symbol}:{row[0]}", "kind": kind,
                "market": "CRYPTO", "asset": asset,
                "clock_basis": "EXCHANGE_BAR_CLOSE", "public_at_utc": (end + timedelta(seconds=1)).isoformat(),
                "first_seen_utc": retrieved.isoformat(),
                "taker_base_imbalance": imbalance, "source_venue": "BINANCE_SPOT",
            })
    if len({entry["date"] for entry in bars}) != len(bars):
        raise ValueError("duplicate daily Binance kline")
    if [entry["date"] for entry in bars] != sorted(entry["date"] for entry in bars):
        raise ValueError("Binance daily kline order invalid")
    return bars, events


def official_events(rows: list[dict], *, market: str, asset: str, retrieved_utc: str):
    """Normalize individually verified BIST SPK/KAP or CFTC events.

    Input must include the ORIGINAL document's publication timestamp and
    evidence URL. An observation date or CFTC Tuesday position date is not
    a publication timestamp and is rejected as a substitute.
    """
    retrieved = utc(retrieved_utc)
    if market not in {"BIST", "US", "CRYPTO"}:
        raise ValueError("unsupported market")
    events = []
    for item in rows:
        if not item.get("publication_evidence_url", "").startswith("https://"):
            raise ValueError("official publication evidence URL missing")
        public = utc(item["official_publication_utc"])
        if public > retrieved:
            raise ValueError("publication later than retrieval")
        if not item.get("event_id") or not item.get("event_kind"):
            raise ValueError("event identity or type missing")
        events.append({
            "id": str(item["event_id"]), "kind": str(item["event_kind"]),
            "market": market, "asset": asset, "clock_basis": "OFFICIAL_PUBLICATION",
            "public_at_utc": public.isoformat(), "first_seen_utc": retrieved.isoformat(),
            "publication_evidence_url": item["publication_evidence_url"],
        })
    return events


def make_manifest(*, market: str, asset: str, price_source_id: str,
                  source_id: str, bars_csv: bytes, events_jsonl: bytes,
                  retrieved_utc: str, rights_status: str, cost_bps: float) -> dict:
    """Build a SHA-pinned replay manifest; publishing rights remain caller's duty."""
    return {
        "schema": "eaverse.pit_replay.v1", "market": market, "asset": asset,
        "price_source_id": price_source_id, "source_id": source_id,
        "signal_frequency": "1d", "bars_file": "bars.csv", "events_file": "events.jsonl",
        "bars_sha256": hashlib.sha256(bars_csv).hexdigest(),
        "events_sha256": hashlib.sha256(events_jsonl).hexdigest(),
        "retrieved_utc": utc(retrieved_utc).isoformat(), "rights_status": rights_status,
        "one_way_cost_bps": cost_bps, "round_trip_stress_multiplier": 2,
        "price_pit_universe_verified": False,
        "automatic_paper_promotion": False,
    }


def event_jsonl(events):
    return "".join(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n" for entry in events).encode()
