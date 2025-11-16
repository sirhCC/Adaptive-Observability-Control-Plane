"""Configuration computation service.

Handles dynamic configuration computation based on:
- Current policy rules
- Signal data and metrics
- Service/environment matching
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from loguru import logger


class ConfigService:
    """Service for computing effective configuration from policy and signals."""
    
    def __init__(self, policy_state: Any, signals_buffer: Dict, evaluate_fn: Any):
        """Initialize config service.
        
        Args:
            policy_state: Reference to global POLICY object
            signals_buffer: Reference to global SIGNALS buffer
            evaluate_fn: Reference to evaluate() function from engine
        """
        self.policy_state = policy_state
        self.signals_buffer = signals_buffer
        self.evaluate_fn = evaluate_fn
    
    def get_effective_config(
        self,
        service: str,
        environment: str
    ) -> Dict[str, Any]:
        """Compute effective configuration for a service/environment.
        
        Args:
            service: Service name
            environment: Environment name
            
        Returns:
            Dict containing computed configuration values
        """
        from control_plane.main import _now
        
        # Get signals for this service/environment
        key = (service, environment)
        signals = self.signals_buffer.get(key, [])
        
        # Evaluate policy to get configuration
        config = self.evaluate_fn(
            service=service,
            environment=environment,
            policy=self.policy_state,
            signals=signals
        )
        
        logger.info(
            f"Config computed for {service}/{environment}",
            extra={
                "service": service,
                "environment": environment,
                "log_level": config.log_level,
                "trace_sample_rate": config.trace_sample_rate,
                "signal_count": len(signals)
            }
        )
        
        return {
            "service": service,
            "environment": environment,
            "log_level": config.log_level,
            "trace_sample_rate": config.trace_sample_rate,
            "metric_period_s": config.metric_period_s,
            "computed_at": _now().isoformat(),
            "signal_count": len(signals)
        }
    
    def get_all_configs(self) -> List[Dict[str, Any]]:
        """Get effective configuration for all known service/environment pairs.
        
        Returns:
            List of configuration dicts
        """
        configs = []
        
        # Get all unique service/environment pairs
        for (service, environment) in self.signals_buffer.keys():
            config = self.get_effective_config(service, environment)
            configs.append(config)
        
        return configs
    
    def explain_config(
        self,
        service: str,
        environment: str
    ) -> Dict[str, Any]:
        """Explain why a particular configuration was computed.
        
        Args:
            service: Service name
            environment: Environment name
            
        Returns:
            Dict with configuration and explanation of which rules applied
        """
        from control_plane.pattern_matching import matches_service_pattern, matches_environment_pattern
        
        key = (service, environment)
        signals = self.signals_buffer.get(key, [])
        
        # Find matching rules
        matching_rules = []
        for rule in self.policy_state.rules:
            # Check service pattern match
            service_match = (
                rule.service is None or
                matches_service_pattern(service, rule.service)
            )
            
            # Check environment pattern match
            env_match = (
                rule.environment is None or
                matches_environment_pattern(environment, rule.environment)
            )
            
            if service_match and env_match:
                # Check if conditions are met (simplified)
                # Full evaluation would require calling evaluate_conditions
                matching_rules.append({
                    "rule_id": rule.id,
                    "description": rule.description,
                    "priority": rule.priority,
                    "actions": {
                        "log_level": rule.actions.log_level,
                        "trace_sample_rate": rule.actions.trace_sample_rate,
                        "metric_period_s": rule.actions.metric_period_s
                    }
                })
        
        # Sort by priority
        matching_rules.sort(key=lambda r: r["priority"], reverse=True)
        
        # Get the actual computed config
        config = self.get_effective_config(service, environment)
        
        return {
            "config": config,
            "matching_rules": matching_rules,
            "explanation": (
                f"Configuration computed from {len(matching_rules)} matching rules. "
                f"Based on {len(signals)} signals."
            )
        }
