"""V8 bounded causal discovery queue: hypotheses, not manufactured backtests.

An event's economic role, public-as-of clock, asset applicability and data
readiness precede parameter sweeps. No autonomous strategy promotion.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
from . import source_council, extended_flow, research_integrity

SCHEMA="eaverse.causal_discovery_queue.v8"
# Asset membership here is a frozen research universe, not a point-in-time
# listing history. An unlisted symbol cannot be retroactively invented.
ASSETS={
    "CRYPTO":tuple(extended_flow.SYMBOLS),
    "US":("SPY","QQQ","IWM","XLF","XLK","XLE","TLT","HYG"),
    "BIST":("XU100","XU030","XBANK","XUSIN"),
}
# Economic channels are deliberately distinct. The generator may combine
# a trigger with a context or independent confirmation, never two copies
# of the same price move or two vendor proxies for the same information.
CHANNELS={
    "CRYPTO":{
        "aggressor_imbalance":("trigger","spot_taker_base_volume","BINANCE_SPOT_VISION"),
        "absorption":("trigger","spot_taker_base_volume","BINANCE_SPOT_VISION"),
        "funding_dislocation":("trigger","funding_settlement","BINANCE_USDM_FUNDING"),
        "oi_liquidation_pressure":("trigger","open_interest_ratios","BINANCE_USDM_METRICS"),
        "cross_venue_dislocation":("confirm","venue_ohlcv","COINBASE_DAILY"),
        "orderbook_imbalance":("confirm","l2_orderbook_sequence","OKX_ORDERBOOK"),
        "stablecoin_liquidity":("context","stablecoin_liquidity_supply","DEFILLAMA"),
        "chain_activity":("context","chain_activity_metrics","COINMETRICS_COMMUNITY"),
    },
    "US":{
        "sec_filing_shock":("trigger","issuer_filing_acceptance","SEC_EDGAR_SUBMISSIONS"),
        "short_interest_change":("trigger","short_interest_position","FINRA_SHORT_INTEREST"),
        "cftc_position_shift":("trigger","weekly_futures_position","CFTC_COT"),
        "vix_risk_regime":("context","vix_daily_close","CBOE_VIX_CLOSE"),
        "macro_vintage_surprise":("context","macro_release_vintages","FRED_ALFRED"),
        "treasury_liquidity":("context","treasury_cash_auction","TREASURY_FISCAL_DATA"),
        "short_volume_confirmation":("confirm","daily_short_sale_volume","FINRA_SHORT_SALE_VOLUME"),
    },
    "BIST":{
        "kap_material_disclosure":("trigger","issuer_announcements","KAP_DISCLOSURES"),
        "spk_corporate_action":("trigger","capital_corporate_events","SPK_BULLETINS"),
        "credit_fx_pressure":("context","rates_fx_credit","TCMB_EVDS"),
        "membership_vintage":("context","constituent_universe","BIST_HISTORICAL_MEMBERSHIP"),
        "fund_nav_rotation":("confirm","fund_nav_allocations","TEFAS_HISTORY"),
    },
}
# Only economically meaningful, pre-registered combinations; no brute
# force Cartesian mining across every indicator and symbol.
ALLOWED={
    "CRYPTO":(
        ("aggressor_imbalance","cross_venue_dislocation"),
        ("absorption","orderbook_imbalance"),
        ("funding_dislocation","stablecoin_liquidity"),
        ("oi_liquidation_pressure","chain_activity"),
        ("aggressor_imbalance","stablecoin_liquidity"),
    ),
    "US":(
        ("sec_filing_shock","vix_risk_regime"),
        ("sec_filing_shock","short_volume_confirmation"),
        ("short_interest_change","macro_vintage_surprise"),
        ("cftc_position_shift","treasury_liquidity"),
    ),
    "BIST":(
        ("kap_material_disclosure","membership_vintage"),
        ("spk_corporate_action","credit_fx_pressure"),
        ("kap_material_disclosure","fund_nav_rotation"),
    ),
}
CLOCK_RULES={
    "spot_taker_base_volume":"closed_hour_after_exchange_publication",
    "funding_settlement":"settlement_time_plus_confirmed_publication_delay",
    "open_interest_ratios":"exchange_first_publication_time",
    "venue_ohlcv":"independent_venue_closed_bar_only",
    "l2_orderbook_sequence":"ordered_snapshot_plus_deltas_with_gap_detection",
    "stablecoin_liquidity_supply":"first_observed_vintage_not_today_revised_history",
    "chain_activity_metrics":"first_observed_block_finality_and_indexer_delay",
    "issuer_filing_acceptance":"sec_acceptance_plus_conservative_delay",
    "short_interest_position":"public_release_not_settlement_date",
    "weekly_futures_position":"friday_public_release_not_tuesday_position",
    "vix_daily_close":"next_session_or_later",
    "macro_release_vintages":"alfred_vintage_first_release",
    "treasury_cash_auction":"actual_publication_timestamp",
    "daily_short_sale_volume":"publication_after_session_not_intraday",
    "issuer_announcements":"kap_first_publication_time",
    "capital_corporate_events":"spk_first_bulletin_publication",
    "rates_fx_credit":"tcmb_publication_time_and_vintage",
    "constituent_universe":"original_membership_publication_not_effective_date",
    "fund_nav_allocations":"tefas_actual_publication_not_nav_valuation_date",
}
# Source council status is evidence of *adapter* availability, not an
# assertion that every asset, horizon or causal timestamp is backtestable.
READABLE_STATUS={"IMPLEMENTED_CHECKSUM_REPLAY","IMPLEMENTED_DAILY_ENGINE",
                 "IMPLEMENTED_DIAGNOSTIC","IMPLEMENTED_CONDITIONAL_ADAPTER",
                 "IMPLEMENTED_AMBIGUITY_AUDIT"}
MAX_DESIGNS=300


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),
                             allow_nan=False).encode()).hexdigest()


def validate():
    source_council.validate()
    sources={x["id"]:x for x in source_council.SOURCES}
    if set(ASSETS)!=set(CHANNELS) or set(ALLOWED)!=set(CHANNELS):
        raise ValueError("MARKET_RESEARCH_REGISTRY_MISMATCH")
    for market, channels in CHANNELS.items():
        if len(set(ASSETS[market]))!=len(ASSETS[market]):
            raise ValueError("DUPLICATE_RESEARCH_ASSET")
        for key,(role,family,source) in channels.items():
            if role not in {"trigger","confirm","context"} or family not in CLOCK_RULES:
                raise ValueError("UNREGISTERED_CAUSAL_ROLE_OR_CLOCK")
            if source not in sources or sources[source]["market"]!=market or sources[source]["feature_family"]!=family:
                raise ValueError("MISMATCHED_SOURCE_INFORMATION_FAMILY")
        for pair in ALLOWED[market]:
            if len(pair)!=2 or pair[0] not in channels or pair[1] not in channels:
                raise ValueError("UNREGISTERED_CAUSAL_COMBINATION")
            if channels[pair[0]][0]!="trigger" or channels[pair[1]][0]=="trigger":
                raise ValueError("CAUSAL_PAIR_MUST_BE_TRIGGER_PLUS_INDEPENDENT_CONTEXT")
            if channels[pair[0]][1]==channels[pair[1]][1]:
                raise ValueError("DUPLICATE_INFORMATION_IN_PAIR")
        if len(set(ALLOWED[market]))!=len(ALLOWED[market]):
            raise ValueError("DUPLICATE_PAIR")
    return True


def readiness(source_id):
    s=next(x for x in source_council.SOURCES if x["id"]==source_id)
    status=s["status"]
    # All outputs still require independent PIT and costs; implemented
    # diagnostic or partial adapter is NOT equivalent to validated history.
    return ("ADAPTER_AVAILABLE_NEEDS_PIT_PROOF" if status in READABLE_STATUS
            else "SOURCE_ADAPTER_OR_RIGHTS_UNVERIFIED")


def designs():
    validate()
    results=[]
    for market in ("CRYPTO","US","BIST"):
        candidates=[(name,) for name,(role,*_) in CHANNELS[market].items()
                    if role=="trigger"]
        candidates.extend(ALLOWED[market])
        for asset,features in itertools.product(ASSETS[market],candidates):
            required=[CHANNELS[market][x] for x in features]
            source_ids=[x[2] for x in required]
            clocks=[CLOCK_RULES[x[1]] for x in required]
            hypothesis={
                "schema":SCHEMA,"market":market,"asset":asset,
                "event_features":list(features),
                "economic_roles":[x[0] for x in required],
                "source_ids":source_ids,
                "asof_rules":clocks,
                "data_readiness":[readiness(x) for x in source_ids],
                "causal_question":(
                    f"Does {features[0]} precede a tradable response in {asset}"
                    + (f" conditional on {features[1]}" if len(features)==2 else "")
                    + ", beyond a same-market time-matched control?"),
                "controls":["matched_market_regime","same_time_randomized_entry",
                            "base_event_without_confirmation" if len(features)==2
                            else "asset_baseline"],
                "test_contract":{
                    "pre_register_before_outcome_inspection":True,
                    "time_split":"purged_walk_forward_then_untouched_holdout",
                    "minimum_validation":"independent_nonoverlapping_cases_and_multiple_regimes",
                    "execution":"venue_native_spread_fees_slippage_liquidity_capacity",
                    "multiplicity":"familywise_correction_or_deflated_sharpe",
                    "prospective":"new_future_first_seen_events_only",
                    "failure_is_valid_result":True,
                },
                "history_backtest_run":False,
                "pit_validated":False,
                "out_of_sample_validated":False,
                "prospective_validated":False,
                "paper_eligible":False,
                "live_allowed":False,
            }
            hypothesis["id"]=digest(hypothesis)[:24]
            # V9: candidate generation does NOT mean source, delist,
            # publication, revision, L2 or prospective proof exists.
            hypothesis["integrity_preflight"]=research_integrity.preflight(hypothesis)
            results.append(hypothesis)
    if len(results)>MAX_DESIGNS or len({x["id"] for x in results})!=len(results):
        raise ValueError("UNBOUNDED_OR_DUPLICATE_DISCOVERY_QUEUE")
    return results


def report():
    proposals=designs()
    return {
        "schema":SCHEMA,
        "registry_sha256":digest({"assets":ASSETS,"channels":CHANNELS,
                                  "allowed":ALLOWED,"clocks":CLOCK_RULES}),
        "source_catalog_sha256":source_council.report()["catalog_sha256"],
        "designs":proposals,
        "by_market":{m:sum(x["market"]==m for x in proposals) for m in ASSETS},
        "new_backtests_completed":0,
        "integrity_gate_schema":research_integrity.SCHEMA,
        "no_source_qualified_by_design_only":all(
            not item["integrity_preflight"]["research_ready"] for item in proposals),
        "integrity_blocked_hypotheses":sum(
            not item["integrity_preflight"]["research_ready"] for item in proposals),
        "new_paper_approvals":0,
        "live_authorized":False,
        "not_a_search_for_profitable_results":True,
        "next_action":"IMPLEMENT_REAL_PIT_REPLAY_PER_FAMILY_BEFORE_ANY_RANKING",
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=Path("out/causal_discovery"))
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    result=report()
    (args.out/"queue.json").write_text(json.dumps(result,indent=2,ensure_ascii=False,
                                                    allow_nan=False)+"\n")
    print("EAVERSE_CAUSAL_QUEUE",len(result["designs"]),result["by_market"],
          "DESIGN_ONLY_NO_PAPER")


if __name__=="__main__":
    main()
