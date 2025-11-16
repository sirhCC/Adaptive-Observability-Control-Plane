"""Rule evaluation engine for adaptive observability.

This module contains the core logic for:
- Evaluating policy rules against signals
- Calculating aggregate metrics
- Merging actions from multiple matching rules
- Applying merge strategies
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Callable
import time
import asyncio

from loguru import logger

from control_plane.schemas import (
    Policy, Rule, Condition, Action, Signal, EffectiveConfig, MergeStrategy
)
from control_plane import metrics as prom_metrics
from control_plane.feature_flags import get_feature_flag_service
from control_plane.pattern_matching import matches_service_pattern, matches_environment_pattern


# Comparison operators for condition evaluation
from typing import Callable
op_map: Dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# Log level ordering for strictest merge strategy (higher = more verbose)
LOG_LEVEL_ORDER = {
    "ERROR": 0,
    "WARN": 1,
    "INFO": 2,
    "DEBUG": 3
}


def _percentile(values: List[float], p: float) -> float:
    """Calculate percentile p (0-100) from sorted or unsorted list.
    
    Args:
        values: List of numeric values
        p: Percentile to calculate (0-100, e.g., 95 for p95)
    
    Returns:
        The percentile value, or 0.0 if values is empty
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def _calc_aggregates(buf: List[Signal], window_s: Optional[int] = None) -> Dict[str, float]:
    """Calculate advanced aggregates with optional time window filtering.
    
    Args:
        buf: List of Signal objects to aggregate
        window_s: Optional time window in seconds to filter recent signals
    
    Returns:
        Dictionary of aggregate metrics including:
        - Percentiles: latency_p50_ms, latency_p90_ms, latency_p95_ms, latency_p99_ms
        - Statistics: latency_avg_ms, latency_min_ms, latency_max_ms
        - Error metrics: error_rate, error_count
        - Request metrics: request_count, request_rate_per_sec
    """
    if not buf:
        return {
            "latency_p50_ms": 0.0,
            "latency_p90_ms": 0.0,
            "latency_p95_ms": 0.0,
            "latency_p99_ms": 0.0,
            "latency_avg_ms": 0.0,
            "latency_min_ms": 0.0,
            "latency_max_ms": 0.0,
            "error_rate": 0.0,
            "error_count": 0.0,
            "request_count": 0.0,
            "request_rate_per_sec": 0.0,
        }
    
    # Filter by time window if specified
    filtered = buf
    if window_s is not None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_s)
        filtered = [s for s in buf if s.ts >= cutoff]
    
    if not filtered:
        return {
            "latency_p50_ms": 0.0,
            "latency_p90_ms": 0.0,
            "latency_p95_ms": 0.0,
            "latency_p99_ms": 0.0,
            "latency_avg_ms": 0.0,
            "latency_min_ms": 0.0,
            "latency_max_ms": 0.0,
            "error_rate": 0.0,
            "error_count": 0.0,
            "request_count": 0.0,
            "request_rate_per_sec": 0.0,
        }
    
    # Calculate latency metrics
    latencies = [s.latency_ms for s in filtered if s.latency_ms is not None]
    
    # Calculate error metrics
    error_count = sum(1 for s in filtered if s.error)
    request_count = len(filtered)
    error_rate = error_count / request_count if request_count > 0 else 0.0
    
    # Calculate request rate (requests per second over the time window)
    if window_s is not None and window_s > 0:
        request_rate = request_count / window_s
    else:
        # Calculate from actual time span
        if len(filtered) > 1:
            time_span_seconds = (filtered[-1].ts - filtered[0].ts).total_seconds()
            request_rate = request_count / time_span_seconds if time_span_seconds > 0 else 0.0
        else:
            request_rate = 0.0
    
    return {
        # Percentiles
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p90_ms": _percentile(latencies, 90),
        "latency_p95_ms": _percentile(latencies, 95),
        "latency_p99_ms": _percentile(latencies, 99),
        
        # Statistics
        "latency_avg_ms": float(sum(latencies) / len(latencies)) if latencies else 0.0,
        "latency_min_ms": float(min(latencies)) if latencies else 0.0,
        "latency_max_ms": float(max(latencies)) if latencies else 0.0,
        
        # Error metrics
        "error_rate": float(error_rate),
        "error_count": float(error_count),
        
        # Request metrics
        "request_count": float(request_count),
        "request_rate_per_sec": float(request_rate),
    }


def _merge_log_level(current: str, new: Optional[str], strategy: MergeStrategy) -> str:
    """Merge log level values according to merge strategy.
    
    Args:
        current: Current log level (ERROR, WARN, INFO, DEBUG)
        new: New log level to merge (or None to skip)
        strategy: Merge strategy (last_wins, strictest, additive, min, max)
    
    Returns:
        Merged log level based on strategy
    """
    if new is None:
        return current
    
    if strategy == MergeStrategy.LAST_WINS:
        return new
    elif strategy == MergeStrategy.STRICTEST or strategy == MergeStrategy.ADDITIVE:
        # Most verbose (DEBUG > INFO > WARN > ERROR)
        current_order = LOG_LEVEL_ORDER.get(current, 0)
        new_order = LOG_LEVEL_ORDER.get(new, 0)
        return new if new_order > current_order else current
    else:
        # For MIN/MAX, treat log levels as ordered (ERROR=0, WARN=1, INFO=2, DEBUG=3)
        # MIN = less verbose (ERROR), MAX = more verbose (DEBUG)
        current_order = LOG_LEVEL_ORDER.get(current, 0)
        new_order = LOG_LEVEL_ORDER.get(new, 0)
        if strategy == MergeStrategy.MIN:
            return new if new_order < current_order else current
        else:  # MAX
            return new if new_order > current_order else current


def _merge_float(current: float, new: Optional[float], strategy: MergeStrategy) -> float:
    """Merge float values according to merge strategy.
    
    Args:
        current: Current float value
        new: New float value to merge (or None to skip)
        strategy: Merge strategy (last_wins, min, max, strictest, additive)
    
    Returns:
        Merged float value based on strategy
    """
    if new is None:
        return current
    
    if strategy == MergeStrategy.LAST_WINS:
        return new
    elif strategy == MergeStrategy.MIN or strategy == MergeStrategy.ADDITIVE:
        return min(current, new)
    elif strategy == MergeStrategy.MAX or strategy == MergeStrategy.STRICTEST:
        return max(current, new)
    else:
        return new


def _merge_int(current: int, new: Optional[int], strategy: MergeStrategy) -> int:
    """Merge integer values according to merge strategy.
    
    Args:
        current: Current integer value
        new: New integer value to merge (or None to skip)
        strategy: Merge strategy (last_wins, min, max, strictest, additive)
    
    Returns:
        Merged integer value based on strategy
    """
    if new is None:
        return current
    
    if strategy == MergeStrategy.LAST_WINS:
        return new
    elif strategy == MergeStrategy.MIN or strategy == MergeStrategy.ADDITIVE:
        return min(current, new)
    elif strategy == MergeStrategy.MAX or strategy == MergeStrategy.STRICTEST:
        return max(current, new)
    else:
        return new


def _apply_action_merge(effective: EffectiveConfig, action: Action, strategy: MergeStrategy) -> None:
    """Apply action to effective config using merge strategy.
    
    Args:
        effective: Current effective configuration to modify
        action: Action with new configuration values
        strategy: Merge strategy for resolving conflicts
    
    Note:
        Modifies effective config in-place
    """
    if action.log_level:
        effective.log_level = _merge_log_level(effective.log_level, action.log_level, strategy)
    if action.trace_sample_rate is not None:
        effective.trace_sample_rate = _merge_float(effective.trace_sample_rate, action.trace_sample_rate, strategy)
    if action.metric_period_s is not None:
        effective.metric_period_s = _merge_int(effective.metric_period_s, action.metric_period_s, strategy)


def _eval_condition(cond: Condition, signal: Signal, aggs: dict) -> bool:
    """Evaluate a single condition against a signal and aggregates."""
    if cond.kind == "always" or cond.op == "always":
        return True
    
    if cond.kind == "error_rate":
        v = aggs.get("error_rate", 0.0)
        threshold = float(cond.value) if cond.value is not None else 0.0
        return op_map[cond.op](v, threshold)
    elif cond.kind == "metric":
        v = aggs.get(cond.key or "", 0.0)
        threshold = float(cond.value) if cond.value is not None else 0.0
        return op_map[cond.op](v, threshold)
    elif cond.kind == "feature_flag":
        # Evaluate feature flag
        if not cond.key:
            logger.warning("Feature flag condition missing 'key' field")
            return False
        
        try:
            ff_service = get_feature_flag_service()
            # Build context from signal
            context = {
                "service": signal.service,
                "environment": signal.environment,
                **signal.attrs
            }
            # Synchronous wrapper for async evaluation
            result = asyncio.run(ff_service.evaluate(
                flag_key=cond.key,
                context=context,
                default=False
            ))
            flag_value = result.value
            
            # Support boolean comparisons
            if cond.op == "==":
                expected = bool(cond.value) if cond.value is not None else True
                return flag_value == expected
            elif cond.op == "!=":
                expected = bool(cond.value) if cond.value is not None else True
                return flag_value != expected
            else:
                # For other operators, treat flag value as boolean
                return flag_value
        except Exception as e:
            logger.error(f"Error evaluating feature flag {cond.key}: {e}")
            return False
    
    return False


def evaluate(
    service: str,
    env: str,
    policy: Policy,
    signals_buffer: Dict[tuple[str, str], List[Signal]]
) -> EffectiveConfig:
    """Evaluate policy rules for a service and environment.
    
    Args:
        service: Service name
        env: Environment name
        policy: Policy to evaluate
        signals_buffer: Buffer of historical signals for aggregation
    
    Returns:
        EffectiveConfig with merged actions from all matching rules
    
    Note:
        - Evaluates rules in priority order (highest first)
        - Applies merge strategies for overlapping rules
        - Records metrics for policy evaluations and rule matches
    """
    start_time = time.time()
    key = (service, env)
    buf = signals_buffer.get(key, [])
    agg = _calc_aggregates(buf)
    
    # Start with defaults
    effective = EffectiveConfig(service=service, environment=env)
    
    # Sort rules by priority (lower number = higher priority)
    sorted_rules = sorted(policy.rules, key=lambda r: r.priority)
    
    for rule in sorted_rules:
        if not rule.enabled:
            continue
        
        # Check if rule scope matches with pattern support
        if not matches_service_pattern(service, rule.service):
            continue
        if not matches_environment_pattern(env, rule.environment):
            continue
        
        # Evaluate conditions
        matched = True
        for cond in rule.conditions:
            # Apply window filtering for conditions with window_s
            cond_agg = _calc_aggregates(buf, cond.window_s) if cond.window_s else agg
            if not _eval_condition(cond, Signal(service=service, environment=env, ts=datetime.now(timezone.utc)), cond_agg):
                matched = False
                break
        
        if not matched:
            continue

        # Record rule match
        prom_metrics.record_rule_match(rule.id, service, env)
        logger.info(f"Rule '{rule.id}' matched for {service}/{env}")

        # Apply actions using merge strategy (rule-level overrides policy-level)
        strategy = rule.merge_strategy if rule.merge_strategy is not None else policy.merge_strategy
        _apply_action_merge(effective, rule.actions, strategy)

    # Record evaluation metrics
    duration = time.time() - start_time
    prom_metrics.policy_evaluations_total.labels(service=service, environment=env).inc()
    prom_metrics.policy_evaluation_duration_seconds.labels(service=service, environment=env).observe(duration)

    return effective
