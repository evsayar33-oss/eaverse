"""Offline realistic history source assurance, not trading strategy backtest."""
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd

from eaverse import equity_source_intelligence as eql


def vix_sample():
    dates=pd.bdate_range("1990-01-02","2026-11-19")
    output=io.StringIO()
    w=csv.writer(output)
    w.writerow(["DATE","OPEN","HIGH","LOW","CLOSE"])
    for i,date in enumerate(dates):
        level=18.5+7*(i%47==0)+15*(i%270==0)
        w.writerow([date.strftime("%m/%d/%Y"),level,level+1,level-1,level])
    return output.getvalue().encode()


def bist_sample():
    out=io.StringIO()
    writer=csv.writer(out)
    writer.writerow(["year","snapshot_date","ticker","name","index","delisted_in_year"])
    for quarter in range(110):
        yyyy=2000+quarter//4
        mo=1+(quarter%4)*3
        date=f"{yyyy}-{mo:02d}-01"
        for i in range(202):
            tier="XU030" if i<30 else "XU050" if i<50 else "XU100" if i<100 else ""
            writer.writerow([yyyy,date,f"T{(quarter*202+i)%700:04d}",
                             "Placeholder",tier,"False"])
    return out.getvalue().encode()


class EquitySourceAssuranceTests(unittest.TestCase):
    def test_genuine_vix_schema_coverage_and_no_options_chain_inference(self):
        result=eql.vix_coverage(vix_sample())
        self.assertGreater(result["rows"],6000)
        self.assertEqual(result["first_date"],"1990-01-02")
        self.assertTrue(result["fixed_regime_crossings"])
        self.assertFalse(result["publication_clock_exactly_verified"])
        self.assertFalse(result["full_options_chain_data"])
        self.assertFalse(result["pit_factor_approved_for_paper"])

    def test_vix_missing_high_low_is_not_repaired(self):
        f=vix_sample().replace(b"DATE,OPEN,HIGH,LOW,CLOSE",b"DATE,OPEN,LOW,CLOSE")
        with self.assertRaisesRegex(ValueError,"VIX_COLUMN_SCHEMA_MISSING"):
            eql.vix_coverage(f)
        rows=vix_sample().splitlines()
        rows[1]=rows[1].replace(b",",b",",1)
        segments=rows[1].decode().split(",")
        segments[2]="0"
        rows[1]=",".join(segments).encode()
        with self.assertRaisesRegex(ValueError,"INCONSISTENT"):
            eql.vix_coverage(b"\n".join(rows)+b"\n")

    def test_bist_membership_exclusive_tier_and_unpublished_snapshot(self):
        raw=bist_sample()
        result=eql.bist_coverage(raw)
        self.assertEqual(result["complete_tier_snapshots"],110)
        self.assertEqual(result["review_snapshots"],110)
        self.assertEqual(result["unique_issuer_symbols"],700)
        self.assertFalse(result["historical_delisted_issuer_prices_provided"])
        self.assertFalse(result["official_original_publication_timestamps_verified"])
        self.assertTrue(result["membership_clock_is_effective_date_not_publication"])

    def test_bist_duplicate_issuer_in_date_is_explicit_ambiguity(self):
        blob=bist_sample()
        lines=blob.splitlines()
        # Never silently count a duplicate as an extra unique issuer.
        lines.append(lines[1])
        result=eql.bist_coverage(b"\n".join(lines)+b"\n")
        self.assertEqual(result["duplicate_symbol_date_rows"],1)
        self.assertEqual(result["conflicting_tier_duplicate_rows"],0)
        self.assertEqual(result["distinct_snapshot_symbol_keys"],110*202)
        self.assertIn("AMBIGUITIES",result["status"])
        self.assertFalse(result["universe_pit_trade_eligible"])

    def test_bist_conflicting_tier_for_duplicate_is_not_hidden(self):
        lines=bist_sample().splitlines()
        first=lines[1].decode().split(",")
        first[-2]="XU100"
        lines.append(",".join(first).encode())
        result=eql.bist_coverage(b"\n".join(lines)+b"\n")
        self.assertEqual(result["duplicate_symbol_date_rows"],1)
        self.assertEqual(result["conflicting_tier_duplicate_rows"],1)
        self.assertFalse(result["universe_pit_trade_eligible"])

    def test_source_schema_failure_records_specific_safe_reason(self):
        source=eql._attempt("CBOE",eql.VIX_URL,eql.vix_coverage,
                            lambda u,l,a:b"nonsense")
        self.assertEqual(source["validation_reason_code"],"VIX_COLUMN_SCHEMA_MISSING")
        self.assertEqual(source["bytes_received"],len(b"nonsense"))
        self.assertEqual(len(source["raw_source_sha256"]),64)
        self.assertNotIn("nonsense",json.dumps(source))

    def test_sec_requires_real_identifying_contact_and_keeps_CIK(self):
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        fake={cik:{"cik":int(cik),"filings":{"recent":{
            "accessionNumber":[f"{cik}-20240101","{cik}-20240102"],
            "form":["8-K","10-K"],
            "acceptanceDateTime":["2024-01-03T16:00:00","2024-01-04T16:00:00"],
        }}} for cik in eql.SEC_CIKS}
        downloads={}
        def mock_get(url,limit,agent):
            downloads[url]=True
            if url==eql.VIX_URL:
                return vix_sample()
            if url==eql.BIST_URL:
                return bist_sample()
            cik=url.split("CIK")[-1].removesuffix(".json")
            return json.dumps(fake[cik]).encode()
        blocked=eql.audit(now,mock_get)
        self.assertEqual(len(downloads),2)
        self.assertEqual(blocked["sources"]["SEC_CIK0000320193_SUBMISSIONS"]["status"],
                         "SEC_REQUIRED_CONTACT_USER_AGENT_NOT_CONFIGURED")
        allowed=eql.audit(now,mock_get,sec_agent="Eaverse Research contact@example.org")
        self.assertEqual(len(downloads),5)
        sec=allowed["sources"]["SEC_CIK0000320193_SUBMISSIONS"]
        self.assertEqual(sec["events_with_conservative_acceptance_plus_24h_clock"],2)
        self.assertFalse(sec["historical_archive_additional_files_fetched"])
        self.assertFalse(sec["historical_delisted_prices_verified"])
        self.assertFalse(allowed["eligible_for_strategy_qualification"])
        self.assertFalse(allowed["paper_eligible"])

    def test_foreign_issuer_and_fake_time_do_not_get_pit_credit(self):
        payload={"cik":9876,"filings":{"recent":{
            "accessionNumber":["1"],"form":["8-K"],
            "acceptanceDateTime":["2026-11-22T12:00:00"]}}}
        with self.assertRaisesRegex(ValueError,"WRONG_ISSUER"):
            eql.sec_coverage(json.dumps(payload).encode(),"0000320193",
                             "2026-11-23T12:00:00Z")

    def test_source_allowlist_and_durable_evidence_sha(self):
        with self.assertRaisesRegex(ValueError,"UNREGISTERED_REMOTE_DATA_SOURCE"):
            eql.get("https://untrusted.invalid/private")
        now=datetime(2026,11,23,12,tzinfo=timezone.utc)
        result=eql.audit(now,lambda url,limit,agent:
                         vix_sample() if url==eql.VIX_URL else bist_sample())
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"ledger.jsonl"
            self.assertEqual(eql.append(p,result)["records"],1)
            self.assertIn("UNCHANGED",eql.append(p,result)["status"])
            altered=json.loads(json.dumps(result))
            altered["sources"]["CBOE_VIX_1990_HISTORY"]["raw_source_sha256"]="different"
            self.assertEqual(eql.append(p,altered)["records"],2)
            entries=p.read_text().splitlines()
            tampered=json.loads(entries[0])
            tampered["market_coverage"]["US"]="FABRICATED"
            p.write_text(json.dumps(tampered)+"\n"+entries[1]+"\n")
            with self.assertRaisesRegex(ValueError,"SHA_CHAIN_INVALID"):
                eql.append(p,altered)


if __name__=="__main__":
    unittest.main()
