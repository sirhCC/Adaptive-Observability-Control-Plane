"""Policy Simulation and Replay Endpoints"""

from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
from typing import Optional
import time
from loguru import logger

from control_plane import constants
from control_plane.policy_simulator import PolicySimulator, create_simulation_response

# Import from main.py
from control_plane.main import (
    SimulateRequest, ReplayRequest, CompareRequest, Signal, limiter,
    POLICY, POLICY_HISTORY, _now,
    _calc_aggregates, _evaluate_rule_conditions, _match_rules_for_signal,
    _convert_signal_in_to_signal
)

router = APIRouter(tags=["simulation"])


@router.post("/policy/simulate")
@limiter.limit(constants.RATE_LIMIT_SIMULATE)
async def simulate_policy(request: Request, req: SimulateRequest):
    """Simulate policy evaluation with test signals."""
    simulator = PolicySimulator(
        calc_aggregates_fn=_calc_aggregates,
        evaluate_rule_conditions_fn=_evaluate_rule_conditions,
        match_rules_for_signal_fn=_match_rules_for_signal
    )
    
    test_signals = [_convert_signal_in_to_signal(sig_in) for sig_in in req.test_signals]
    
    start_time = time.time()
    results = simulator.evaluate_batch(req.policy, test_signals)
    eval_duration_ms = (time.time() - start_time) * 1000
    
    logger.info(
        "Policy simulation completed",
        policy_id=req.policy.id,
        num_signals=len(test_signals),
        num_rules=len(req.policy.rules),
        signals_with_matches=sum(1 for r in results if r.rule_count > 0),
        evaluation_time_ms=round(eval_duration_ms, 2)
    )
    
    return create_simulation_response(results, req.policy.id, len(test_signals))


@router.post("/replay")
@limiter.limit("20/minute")
async def replay_signals(request: Request, req: ReplayRequest):
    """Replay historical signals with time-travel policy evaluation."""
    for idx, sig in enumerate(req.signals):
        if sig.timestamp is None:
            raise HTTPException(
                status_code=400,
                detail=f"Signal at index {idx} missing timestamp. All signals must have timestamps for replay."
            )
    
    policy_to_use = POLICY
    policy_info = {
        "using": "current",
        "policy_id": POLICY.id
    }
    
    if req.policy_timestamp:
        policy_ts_fixed = req.policy_timestamp.replace(' ', '+').replace('Z', '+00:00')
        query_time = datetime.fromisoformat(policy_ts_fixed)
        
        applicable_versions = [
            v for v in POLICY_HISTORY 
            if v.applied_at <= query_time
        ]
        
        if applicable_versions:
            policy_version = sorted(applicable_versions, key=lambda v: v.applied_at, reverse=True)[0]
            policy_to_use = policy_version.policy
            policy_info = {
                "using": "historical",
                "policy_id": policy_to_use.id,
                "applied_at": policy_version.applied_at.isoformat(),
                "applied_by": policy_version.applied_by,
                "query_time": query_time.isoformat()
            }
        else:
            policy_info["note"] = f"No policy history before {query_time.isoformat()}, using current policy"
    
    simulator = PolicySimulator(
        calc_aggregates_fn=_calc_aggregates,
        evaluate_rule_conditions_fn=_evaluate_rule_conditions,
        match_rules_for_signal_fn=_match_rules_for_signal
    )
    
    replay_signals_list = []
    for idx, sig_in in enumerate(req.signals):
        signal_time = sig_in.timestamp
        assert signal_time is not None, "Signal timestamp must not be None"
        
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
            
        replay_signals_list.append(Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=signal_time,
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        ))
    
    policy_ts = datetime.fromisoformat(req.policy_timestamp.replace(' ', '+').replace('Z', '+00:00')) if req.policy_timestamp else None
    
    start_time = time.time()
    replay_results = simulator.replay_with_historical_policy(replay_signals_list, policy_to_use, policy_ts)
    replay_duration_ms = (time.time() - start_time) * 1000
    
    logger.info(
        "Signal replay completed",
        num_signals=len(replay_signals_list),
        policy_type=policy_info["using"],
        policy_id=policy_to_use.id,
        replay_time_ms=round(replay_duration_ms, 2)
    )
    
    replay_results["policy_info"] = policy_info
    
    return replay_results


@router.post("/compare")
@limiter.limit("20/minute")
async def compare_policies(request: Request, req: CompareRequest):
    """Compare how different policies would handle the same signals."""
    policies_to_compare = []
    
    for policy_ref in req.compare_policies:
        if policy_ref.lower() == "current":
            policies_to_compare.append({
                "policy": POLICY,
                "label": "current",
                "policy_id": POLICY.id,
                "applied_at": None
            })
        else:
            policy_ref_fixed = policy_ref.replace(' ', '+').replace('Z', '+00:00')
            query_time = datetime.fromisoformat(policy_ref_fixed)
            
            applicable_versions = [
                v for v in POLICY_HISTORY 
                if v.applied_at <= query_time
            ]
            
            if applicable_versions:
                policy_version = sorted(applicable_versions, key=lambda v: v.applied_at, reverse=True)[0]
                policies_to_compare.append({
                    "policy": policy_version.policy,
                    "label": policy_ref,
                    "policy_id": policy_version.policy.id,
                    "applied_at": policy_version.applied_at.isoformat()
                })
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"No policy history available before {query_time.isoformat()}"
                )
    
    simulator = PolicySimulator(
        calc_aggregates_fn=_calc_aggregates,
        evaluate_rule_conditions_fn=_evaluate_rule_conditions,
        match_rules_for_signal_fn=_match_rules_for_signal
    )
    
    test_signals = []
    for sig_in in req.signals:
        signal_time = sig_in.timestamp if sig_in.timestamp else _now()
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
            
        test_signals.append(Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=signal_time,
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        ))
    
    policies_with_labels = [
        (p["label"], p["policy"]) 
        for p in policies_to_compare
    ]
    
    start_time = time.time()
    comparison_results = simulator.compare_evaluations(policies_with_labels, test_signals)
    compare_duration_ms = (time.time() - start_time) * 1000
    
    logger.info(
        "Policy comparison completed",
        num_signals=len(test_signals),
        num_policies=len(policies_with_labels),
        signals_with_differences=comparison_results["summary"].get("signals_with_differences", 0),
        comparison_time_ms=round(compare_duration_ms, 2)
    )
    
    return comparison_results
