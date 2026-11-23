"""Audit V8 proposals against local PIT evidence bundles, without promotion.

Example: python -m eaverse.integrity_audit --evidence-root /private/archive
Only source fingerprints/decision reasons go into report, never raw prices.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from . import causal_discovery, research_integrity


def audit(root=None):
    registry = causal_discovery.designs()
    results = []
    evidence_root = Path(root).resolve() if root else None
    for design in registry:
        bundle = None
        if evidence_root:
            candidate = evidence_root / "bundles" / (design["id"] + ".json")
            if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
                if candidate.stat().st_size > 1_000_000:
                    results.append({"hypothesis_id": design["id"],
                                    "status": "BLOCKED_OVERSIZED_BUNDLE",
                                    "research_ready": False,
                                    "paper_eligible": False, "live_allowed": False})
                    continue
                try:
                    bundle = json.loads(candidate.read_text(encoding="utf-8"))
                except (ValueError, OSError, UnicodeError):
                    results.append({"hypothesis_id": design["id"],
                                    "status": "BLOCKED_INVALID_BUNDLE_JSON",
                                    "research_ready": False,
                                    "paper_eligible": False, "live_allowed": False})
                    continue
        results.append(research_integrity.preflight(design, bundle, root=evidence_root))
    return {
        "schema": "eaverse.pit_integrity_audit.v9",
        "hypothesis_count": len(results),
        "structural_research_ready": sum(x["research_ready"] for x in results),
        "blocked": sum(not x["research_ready"] for x in results),
        "paper_eligible": 0, "live_authorized": False,
        "real_profit_not_inferred_from_source_readiness": True,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("out/pit_integrity/audit.json"))
    args = parser.parse_args()
    report = audit(args.evidence_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("EAVERSE_V9_EVIDENCE", report["hypothesis_count"],
          "BLOCKED", report["blocked"],
          "STRUCTURAL_READY", report["structural_research_ready"],
          "NO_AUTO_PAPER_LIVE")


if __name__ == "__main__":
    main()
