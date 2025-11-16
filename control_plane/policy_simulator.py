"""Policy simulation and evaluation utilities.

Provides reusable policy evaluation logic for simulation, replay, and comparison
endpoints. Consolidates duplicate evaluation logic into a single source of truth.
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime


class EvaluationResult:
    """Result of evaluating a policy against a signal."""
    
    def __init__(
        self,
        signal: Any,  # Signal model
        matched_rules: List[Dict[str, Any]],
        effective_config: Any  # EffectiveConfig model
    ):
        self.signal = signal
        self.matched_rules = matched_rules
        self.effective_config = effective_config
        self.rule_count = len(matched_rules)
    
    def to_dict(self, include_index: Optional[int] = None) -> Dict[str, Any]:
        """Convert to dictionary for API responses.
        
        Args:
            include_index: Optional signal index to include in response
            
        Returns:
            Dictionary representation
        """
        result = {
            "service": self.signal.service,
            "environment": self.signal.environment,
            "latency_ms": self.signal.latency_ms,
            "error": self.signal.error,
            "matched_rules": self.matched_rules,
            "rule_count": self.rule_count,
            "effective_config": self.effective_config.model_dump()
        }
        
        if include_index is not None:
            result["signal_index"] = include_index
        
        return result


class PolicySimulator:
    """Evaluates policies against signals for simulation and analysis."""
    
    def __init__(
        self,
        calc_aggregates_fn,
        evaluate_rule_conditions_fn,
        match_rules_for_signal_fn
    ):
        """Initialize simulator with evaluation functions.
        
        Args:
            calc_aggregates_fn: Function to calculate signal aggregates
            evaluate_rule_conditions_fn: Function to evaluate rule conditions
            match_rules_for_signal_fn: Function to match rules for a signal
        """
        self._calc_aggregates = calc_aggregates_fn
        self._evaluate_rule_conditions = evaluate_rule_conditions_fn
        self._match_rules_for_signal = match_rules_for_signal_fn
    
    def evaluate_signal(
        self,
        policy: Any,  # Policy model
        signal: Any,  # Signal model
        buffer: Optional[List[Any]] = None  # List of Signal models
    ) -> EvaluationResult:
        """Evaluate a single signal against a policy.
        
        Args:
            policy: Policy to evaluate
            signal: Signal to evaluate
            buffer: Historical signals for aggregation (uses single signal if None)
            
        Returns:
            EvaluationResult with matched rules and effective config
        """
        # Create buffer for aggregates
        if buffer is None:
            buffer = [signal]
        
        # Calculate aggregates
        agg = self._calc_aggregates(buffer)
        
        # Match rules for this signal
        matched_rules, effective_config = self._match_rules_for_signal(policy, signal, agg)
        
        return EvaluationResult(signal, matched_rules, effective_config)
    
    def evaluate_batch(
        self,
        policy: Any,  # Policy model
        signals: List[Any]  # List of Signal models
    ) -> List[EvaluationResult]:
        """Evaluate multiple signals against a policy.
        
        Each signal is evaluated independently with its own aggregation context.
        
        Args:
            policy: Policy to evaluate
            signals: List of signals to evaluate
            
        Returns:
            List of EvaluationResult objects
        """
        results = []
        
        for signal in signals:
            result = self.evaluate_signal(policy, signal)
            results.append(result)
        
        return results
    
    def compare_evaluations(
        self,
        policies: List[Tuple[str, Any]],  # List of (label, Policy) tuples
        signals: List[Any]  # List of Signal models
    ) -> Dict[str, Any]:
        """Compare how different policies handle the same signals.
        
        Args:
            policies: List of (policy_id, Policy) tuples to compare
            signals: Signals to evaluate against each policy
            
        Returns:
            Comparison results showing differences between policies
        """
        comparison_results = []
        
        for signal in signals:
            signal_comparison = {
                "signal": {
                    "service": signal.service,
                    "environment": signal.environment,
                    "latency_ms": signal.latency_ms,
                    "error": signal.error
                },
                "policy_results": {}
            }
            
            # Evaluate signal with each policy
            for policy_id, policy in policies:
                result = self.evaluate_signal(policy, signal)
                signal_comparison["policy_results"][policy_id] = {
                    "matched_rules": result.rule_count,
                    "effective_config": result.effective_config.model_dump(),
                    "rule_details": result.matched_rules
                }
            
            comparison_results.append(signal_comparison)
        
        summary = self._build_comparison_summary(comparison_results, policies)
        
        return {
            "comparison_results": comparison_results,
            "total_signals": len(signals),
            "policies_compared": len(policies),
            "summary": summary
        }
    
    def _build_comparison_summary(
        self,
        comparison_results: List[Dict[str, Any]],
        policies: List[Tuple[str, Any]]  # List of (label, Policy) tuples
    ) -> Dict[str, Any]:
        """Build summary statistics for policy comparison.
        
        Args:
            comparison_results: Raw comparison results
            policies: Policies that were compared
            
        Returns:
            Summary statistics dictionary
        """
        summary = {
            "total_signals": len(comparison_results),
            "policies_compared": len(policies),
            "policy_ids": [pid for pid, _ in policies]
        }
        
        # Count differences
        signals_with_differences = 0
        for result in comparison_results:
            policy_results = result["policy_results"]
            configs = [pr["effective_config"] for pr in policy_results.values()]
            
            # Check if all configs are identical
            if len(set(str(c) for c in configs)) > 1:
                signals_with_differences += 1
        
        summary["signals_with_differences"] = signals_with_differences
        summary["signals_without_differences"] = len(comparison_results) - signals_with_differences
        
        return summary
    
    def replay_with_historical_policy(
        self,
        signals: List[Any],  # List of Signal models
        policy: Any,  # Policy model
        policy_timestamp: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Replay signals against a historical policy.
        
        Args:
            signals: Signals to replay
            policy: Historical policy to use
            policy_timestamp: Timestamp of the policy (for metadata)
            
        Returns:
            Replay results with effective configurations
        """
        results = self.evaluate_batch(policy, signals)
        
        replay_data = {
            "replay_results": [r.to_dict(include_index=i) for i, r in enumerate(results)],
            "total_signals": len(signals),
            "policy_id": policy.id,
            "policy_timestamp": policy_timestamp.isoformat() if policy_timestamp else None,
            "summary": {
                "signals_with_matches": sum(1 for r in results if r.rule_count > 0),
                "signals_without_matches": sum(1 for r in results if r.rule_count == 0),
                "total_rule_matches": sum(r.rule_count for r in results)
            }
        }
        
        return replay_data


def create_simulation_response(
    results: List[EvaluationResult],
    policy_id: str,
    total_signals: int
) -> Dict[str, Any]:
    """Create a complete simulation response.
    
    Args:
        results: List of evaluation results
        policy_id: ID of the simulated policy
        total_signals: Total number of signals simulated
        
    Returns:
        Complete simulation response dictionary
    """
    return {
        "simulation_results": [r.to_dict(include_index=i) for i, r in enumerate(results)],
        "total_signals": total_signals,
        "policy_id": policy_id,
        "summary": {
            "signals_with_matches": sum(1 for r in results if r.rule_count > 0),
            "signals_without_matches": sum(1 for r in results if r.rule_count == 0),
            "total_rule_matches": sum(r.rule_count for r in results)
        }
    }
