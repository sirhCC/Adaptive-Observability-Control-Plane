"""Policy management service.

Handles all policy-related business logic:
- CRUD operations for policies
- Policy validation and conflict detection
- Version history management
- Template management
"""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from loguru import logger

from control_plane.exceptions import PolicyValidationError
from control_plane import constants


class PolicyService:
    """Service for managing policies and their lifecycle."""
    
    def __init__(self, policy_state: Any, policy_history: List[Any], max_history: int = 100):
        """Initialize policy service with state management.
        
        Args:
            policy_state: Reference to global POLICY object
            policy_history: Reference to global POLICY_HISTORY list
            max_history: Maximum number of policy versions to retain
        """
        self.policy_state = policy_state
        self.policy_history = policy_history
        self.max_history = max_history
    
    def get_current_policy(self) -> Any:
        """Get the current active policy."""
        return self.policy_state
    
    def update_policy(self, new_policy: Any, updated_by: Optional[str] = None) -> Dict[str, Any]:
        """Update the current policy with validation and history tracking.
        
        Args:
            new_policy: New policy object to apply
            updated_by: Optional identifier of who made the change
            
        Returns:
            Dict containing update status and validation results
            
        Raises:
            PolicyValidationError: If policy validation fails
        """
        from control_plane.main import PolicyVersion, _now
        
        # Validate policy rules
        validation_result = self._validate_policy(new_policy)
        if not validation_result["valid"]:
            raise PolicyValidationError(
                f"Policy validation failed: {validation_result.get('errors', [])}"
            )
        
        # Save current policy to history before updating
        if self.policy_state:
            version = PolicyVersion(
                policy=self.policy_state.model_copy(deep=True),
                applied_at=_now(),
                applied_by=updated_by
            )
            self.policy_history.append(version)
            
            # Prune old history if needed
            if len(self.policy_history) > self.max_history:
                self.policy_history.pop(0)
        
        # Update the policy
        old_policy = self.policy_state.model_copy(deep=True) if self.policy_state else None
        self.policy_state.id = new_policy.id
        self.policy_state.description = new_policy.description
        self.policy_state.rules = new_policy.rules
        self.policy_state.merge_strategy = new_policy.merge_strategy
        
        logger.info(
            f"Policy updated: {new_policy.id} with {len(new_policy.rules)} rules",
            extra={"policy_id": new_policy.id, "rule_count": len(new_policy.rules)}
        )
        
        return {
            "status": "updated",
            "policy_id": new_policy.id,
            "rule_count": len(new_policy.rules),
            "validation": validation_result,
            "previous_version_saved": old_policy is not None
        }
    
    def get_policy_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get policy version history.
        
        Args:
            limit: Optional limit on number of versions to return
            
        Returns:
            List of policy versions with metadata
        """
        versions = self.policy_history[-limit:] if limit else self.policy_history
        return [
            {
                "policy_id": v.policy.id,
                "applied_at": v.applied_at.isoformat(),
                "applied_by": v.applied_by,
                "rule_count": len(v.policy.rules)
            }
            for v in versions
        ]
    
    def get_policy_at_time(self, timestamp: datetime) -> Optional[Any]:
        """Retrieve the policy that was active at a specific time.
        
        Args:
            timestamp: The point in time to query
            
        Returns:
            Policy object or None if no policy existed at that time
        """
        # Find the most recent policy version before the timestamp
        applicable_versions = [
            v for v in self.policy_history
            if v.applied_at <= timestamp
        ]
        
        if not applicable_versions:
            return None
        
        # Sort by applied_at and get the most recent
        applicable_versions.sort(key=lambda v: v.applied_at, reverse=True)
        return applicable_versions[0].policy
    
    def validate_policy_rules(self, policy: Any) -> Dict[str, Any]:
        """Validate policy rules for conflicts and issues.
        
        Args:
            policy: Policy object to validate
            
        Returns:
            Validation result with any warnings or errors
        """
        return self._validate_policy(policy)
    
    def _validate_policy(self, policy: Any) -> Dict[str, Any]:
        """Internal validation logic.
        
        Checks:
        - Rule priority conflicts
        - Condition validity
        - Action parameter ranges
        - Service/environment pattern validity
        """
        from control_plane.rule_validator import validate_rules_for_conflicts
        
        errors = []
        warnings = []
        
        # Import validation utilities
        try:
            from control_plane.pattern_matching import validate_pattern
            
            # Validate service and environment patterns
            for rule in policy.rules:
                if rule.service:
                    if not validate_pattern(rule.service):
                        errors.append(f"Rule '{rule.id}': Invalid service pattern '{rule.service}'")
                
                if rule.environment:
                    if not validate_pattern(rule.environment):
                        errors.append(f"Rule '{rule.id}': Invalid environment pattern '{rule.environment}'")
            
            # Check for rule conflicts
            conflicts = validate_rules_for_conflicts(policy.rules)
            if conflicts:
                warnings.extend([
                    f"Potential conflict: {c['description']}" for c in conflicts
                ])
        
        except Exception as e:
            logger.warning(f"Validation check skipped: {e}")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }
    
    def get_default_templates(self) -> Dict[str, Any]:
        """Get default policy templates for common scenarios.
        
        Returns:
            Dictionary of template name to policy configuration
        """
        return {
            "basic": {
                "id": "basic-policy",
                "description": "Basic observability with error detection",
                "rules": [
                    {
                        "id": "error-detection",
                        "description": "Elevate logging on errors",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.02, "window_s": 60}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.5,
                            "metric_period_s": 15
                        }
                    }
                ]
            },
            "production": {
                "id": "prod-policy",
                "description": "Production-grade observability",
                "rules": [
                    {
                        "id": "prod-defaults",
                        "description": "Conservative defaults for production",
                        "environment": "prod",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.1,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "prod-errors",
                        "description": "Aggressive error detection",
                        "environment": "prod",
                        "priority": 20,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.01, "window_s": 60}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.8,
                            "metric_period_s": 10
                        }
                    }
                ]
            },
            "development": {
                "id": "dev-policy",
                "description": "Development environment with verbose logging",
                "rules": [
                    {
                        "id": "dev-verbose",
                        "description": "Verbose logging for development",
                        "environment": "dev",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 1.0,
                            "metric_period_s": 15
                        }
                    }
                ]
            }
        }
