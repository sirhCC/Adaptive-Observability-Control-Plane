"""Tests for rule conflict detection and validation."""
import pytest
from fastapi.testclient import TestClient
from control_plane.main import app, POLICY, Rule, Condition, Action
from control_plane.rule_validator import RuleConflictDetector, validate_policy_rules


class TestRuleConflictDetector:
    """Test rule conflict detection logic."""
    
    def test_duplicate_rule_ids(self):
        """Test detection of duplicate rule IDs."""
        rules = [
            Rule(
                id="rule1",
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            ),
            Rule(
                id="rule1",  # Duplicate
                priority=20,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        duplicate_conflicts = [c for c in conflicts if c.type == "duplicate_id"]
        assert len(duplicate_conflicts) == 1
        assert duplicate_conflicts[0].severity == "error"
        assert "rule1" in duplicate_conflicts[0].message
    
    def test_scope_overlap_same_priority(self):
        """Test detection of overlapping scopes with same priority."""
        rules = [
            Rule(
                id="rule1",
                service="api",
                environment="prod",
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            ),
            Rule(
                id="rule2",
                service="api",
                environment="prod",
                priority=10,  # Same priority
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        overlap_conflicts = [c for c in conflicts if c.type == "scope_overlap"]
        assert len(overlap_conflicts) == 1
        assert overlap_conflicts[0].severity == "warning"
    
    def test_no_conflict_different_priorities(self):
        """Test that different priorities don't cause overlap warnings."""
        rules = [
            Rule(
                id="rule1",
                service="api",
                environment="prod",
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            ),
            Rule(
                id="rule2",
                service="api",
                environment="prod",
                priority=20,  # Different priority
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        overlap_conflicts = [c for c in conflicts if c.type == "scope_overlap"]
        assert len(overlap_conflicts) == 0
    
    def test_unreachable_rule_detection(self):
        """Test detection of unreachable rules after 'always' rules."""
        rules = [
            Rule(
                id="always-rule",
                service="api",
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            ),
            Rule(
                id="later-rule",
                service="api",
                priority=20,
                conditions=[Condition(kind="error_rate", op=">", value=0.1)],
                actions=Action(log_level="DEBUG")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        unreachable = [c for c in conflicts if c.type == "unreachable"]
        assert len(unreachable) >= 1
        assert "later-rule" in unreachable[0].rule_ids
    
    def test_always_with_other_conditions(self):
        """Test detection of 'always' combined with other conditions."""
        rules = [
            Rule(
                id="mixed-rule",
                priority=10,
                conditions=[
                    Condition(kind="always", op="always"),
                    Condition(kind="error_rate", op=">", value=0.1)
                ],
                actions=Action(log_level="INFO")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        always_conflicts = [c for c in conflicts if c.type == "always_with_conditions"]
        assert len(always_conflicts) == 1
        assert always_conflicts[0].severity == "warning"
    
    def test_wildcard_scope_overlap(self):
        """Test detection of wildcard scope overlaps."""
        rules = [
            Rule(
                id="wildcard-rule",
                service=None,  # Wildcard
                environment=None,  # Wildcard
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            ),
            Rule(
                id="specific-rule",
                service="api",
                environment="prod",
                priority=10,  # Same priority
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        overlap_conflicts = [c for c in conflicts if c.type == "scope_overlap"]
        assert len(overlap_conflicts) >= 1
    
    def test_no_conflicts_in_good_policy(self):
        """Test that a well-designed policy has no critical conflicts."""
        rules = [
            Rule(
                id="prod-defaults",
                environment="prod",
                priority=0,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            ),
            Rule(
                id="high-errors",
                priority=10,
                conditions=[Condition(kind="error_rate", op=">", value=0.05)],
                actions=Action(log_level="WARN")
            ),
            Rule(
                id="slow-requests",
                priority=20,
                conditions=[Condition(kind="metric", op=">", key="latency_p95_ms", value=500)],
                actions=Action(log_level="DEBUG")
            )
        ]
        
        detector = RuleConflictDetector(rules)
        conflicts = detector.analyze()
        
        errors = [c for c in conflicts if c.severity == "error"]
        assert len(errors) == 0


class TestValidatePolicyRules:
    """Test the validate_policy_rules function."""
    
    def test_valid_policy(self):
        """Test validation of a valid policy."""
        rules = [
            Rule(
                id="rule1",
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            )
        ]
        
        result = validate_policy_rules(rules)
        
        assert result["valid"] is True
        assert result["summary"]["errors"] == 0
    
    def test_invalid_policy_duplicate_ids(self):
        """Test validation fails on duplicate IDs."""
        rules = [
            Rule(id="dup", priority=10, conditions=[Condition(kind="always", op="always")], actions=Action(log_level="INFO")),
            Rule(id="dup", priority=20, conditions=[Condition(kind="always", op="always")], actions=Action(log_level="DEBUG"))
        ]
        
        result = validate_policy_rules(rules)
        
        assert result["valid"] is False
        assert result["summary"]["errors"] > 0
    
    def test_policy_with_warnings(self):
        """Test policy with warnings but no errors."""
        rules = [
            Rule(
                id="rule1",
                priority=10,
                conditions=[
                    Condition(kind="always", op="always"),
                    Condition(kind="error_rate", op=">", value=0.1)
                ],
                actions=Action(log_level="INFO")
            )
        ]
        
        result = validate_policy_rules(rules)
        
        assert result["valid"] is True  # No errors
        assert result["summary"]["warnings"] > 0


class TestValidationAPI:
    """Test the validation API endpoint."""
    
    def test_validate_endpoint_accepts_valid_policy(self):
        """Test /policy/validate endpoint with valid policy."""
        client = TestClient(app)
        
        response = client.post("/v1/policy/validate", json={
            "policy": {
                "id": "test-policy",
                "rules": [
                    {
                        "id": "rule1",
                        "priority": 10,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {"log_level": "INFO"}
                    }
                ]
            }
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "summary" in data
        assert "conflicts" in data
    
    def test_validate_endpoint_detects_duplicate_ids(self):
        """Test validation detects duplicate rule IDs."""
        client = TestClient(app)
        
        response = client.post("/v1/policy/validate", json={
            "policy": {
                "id": "test-policy",
                "rules": [
                    {
                        "id": "dup",
                        "priority": 10,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {"log_level": "INFO"}
                    },
                    {
                        "id": "dup",
                        "priority": 20,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {"log_level": "DEBUG"}
                    }
                ]
            }
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["summary"]["errors"] > 0
    
    def test_validate_endpoint_shows_warnings(self):
        """Test validation shows warnings."""
        client = TestClient(app)
        
        response = client.post("/v1/policy/validate", json={
            "policy": {
                "id": "test-policy",
                "rules": [
                    {
                        "id": "mixed",
                        "priority": 10,
                        "conditions": [
                            {"kind": "always", "op": "always"},
                            {"kind": "error_rate", "op": ">", "value": 0.1}
                        ],
                        "actions": {"log_level": "INFO"}
                    }
                ]
            }
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["warnings"] > 0


class TestPolicyUpdateWithValidation:
    """Test policy update with integrated validation."""
    
    def test_policy_update_rejects_duplicate_ids(self):
        """Test that policy update rejects duplicate IDs."""
        client = TestClient(app)
        
        response = client.post("/v1/policy", json={
            "policy": {
                "id": "bad-policy",
                "rules": [
                    {
                        "id": "dup",
                        "priority": 10,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {"log_level": "INFO"}
                    },
                    {
                        "id": "dup",
                        "priority": 20,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {"log_level": "DEBUG"}
                    }
                ]
            }
        })
        
        # Should fail validation
        assert response.status_code == 400
        assert "validation failed" in response.json()["message"].lower() or "duplicate" in response.json()["message"].lower()
    
    def test_policy_update_allows_warnings(self):
        """Test that policy update allows warnings but logs them."""
        client = TestClient(app)
        
        response = client.post("/v1/policy", json={
            "policy": {
                "id": "warning-policy",
                "rules": [
                    {
                        "id": "rule-with-warning",
                        "priority": 10,
                        "conditions": [
                            {"kind": "always", "op": "always"},
                            {"kind": "error_rate", "op": ">", "value": 0.1}
                        ],
                        "actions": {"log_level": "INFO"}
                    }
                ]
            }
        })
        
        # Should succeed despite warnings
        assert response.status_code == 200


class TestConflictDetectionEdgeCases:
    """Test edge cases in conflict detection."""
    
    def test_empty_rules_list(self):
        """Test conflict detection with empty rules."""
        rules = []
        result = validate_policy_rules(rules)
        
        assert result["valid"] is True
        assert result["summary"]["total_rules"] == 0
    
    def test_single_rule_no_conflicts(self):
        """Test single rule has no conflicts."""
        rules = [
            Rule(
                id="only-rule",
                priority=10,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO")
            )
        ]
        
        result = validate_policy_rules(rules)
        assert result["valid"] is True
        assert len(result["conflicts"]) == 0
