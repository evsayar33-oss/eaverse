"""Historical SEC segments and BIST delisted flags are NOT stock-PIT pricing."""
import json
import unittest
from datetime import datetime, timezone

from eaverse import equity_source_intelligence as eql
from test_equity_source_intelligence import bist_sample


class SECArchiveAndBistTests(unittest.TestCase):
    def test_historical_archive_by_permanent_cik_with_bounded_sha(self):
        cik=eql.SEC_CIKS[0]
        files=[{"name":f"CIK{cik}-submissions-001.json",
                "filingFrom":"2019-01-01","filingTo":"2022-12-31"},
               {"name":f"CIK{cik}-submissions-002.json",
                "filingFrom":"2015-01-01","filingTo":"2018-12-31"}]
        root={"cik":int(cik),"filings":{"recent":{
            "accessionNumber":["000001-26-000001"],
            "form":["8-K"],"acceptanceDateTime":["2026-01-01T16:00:00"],
        },"files":files}}
        observations={}
        for index,row in enumerate(files):
            observations["https://data.sec.gov/submissions/"+row["name"]]=json.dumps({
                "accessionNumber":[f"000001-{index+20}-000002"],
                "form":["10-K"],"acceptanceDateTime":[
                    f"{2018+index}-01-01T14:00:00"],
            }).encode()
        calls=[]
        def getter(url,limit,agent):
            calls.append((url,agent))
            return observations[url]
        out=eql.sec_archive_coverage(json.dumps(root).encode(),cik,
                                      "2026-11-23T00:00:00Z",
                                      getter,"research test@example.com")
        self.assertEqual(out["historical_archive_files_declared"],2)
        self.assertEqual(out["historical_archive_files_scanned"],2)
        self.assertEqual(out["extra_distinct_historical_events"],2)
        self.assertTrue(all(len(f["source_sha256"])==64
                            for f in out["historical_archive_segments"]))
        self.assertEqual(len(calls),2)
        self.assertFalse(out["current_security_id_backdated"])
        self.assertFalse(out["historical_delisted_prices_verified"])

    def test_untrusted_filename_is_rejected_before_external_request(self):
        cik=eql.SEC_CIKS[1]
        payload={"cik":int(cik),"filings":{"recent":{
            "accessionNumber":[],"form":[],"acceptanceDateTime":[],
        },"files":[{"name":"../../untrusted.txt","filingFrom":"2014-01-01"}]}}
        with self.assertRaisesRegex(ValueError,"FILENAME_CIK_MISMATCH"):
            eql.sec_archive_coverage(json.dumps(payload).encode(),cik,
                                      "2026-11-23T00:00:00Z",
                                      lambda *_:self.fail("untrusted fetch"),
                                      "research test@example.com")
        with self.assertRaisesRegex(ValueError,"UNREGISTERED_REMOTE_DATA_SOURCE"):
            eql.get("https://data.sec.gov/submissions/CIK0000000000-submissions-001.json")

    def test_unavailable_archive_never_becomes_complete(self):
        cik=eql.SEC_CIKS[2]
        payload={"cik":int(cik),"filings":{"recent":{
            "accessionNumber":[],"form":[],"acceptanceDateTime":[],
        },"files":[{"name":f"CIK{cik}-submissions-001.json",
                     "filingFrom":"2020-01-01"}]}}
        def blocked(*_):
            raise OSError("HTTP blocked")
        out=eql.sec_archive_coverage(json.dumps(payload).encode(),cik,
                                     "2026-11-23T00:00:00Z",
                                     blocked,"research test@example.com")
        self.assertEqual(out["historical_archive_files_scanned"],0)
        self.assertFalse(out["historical_archive_complete_for_issuer"])
        self.assertFalse(out["historical_archive_additional_files_fetched"])
        self.assertIn("BLOCKED",out["status"])

    def test_community_delisted_metadata_is_not_delisted_price_history(self):
        original=bist_sample().decode().splitlines()
        parts=original[2].split(",")
        parts[-1]="True"
        original[2]=",".join(parts)
        result=eql.bist_coverage(("\n".join(original)+"\n").encode())
        self.assertEqual(result["symbols_flagged_delisted_in_reconstructed_rows"],1)
        self.assertFalse(result["historical_delisted_issuer_prices_provided"])
        self.assertFalse(result["universe_pit_trade_eligible"])


if __name__=="__main__":
    unittest.main()
