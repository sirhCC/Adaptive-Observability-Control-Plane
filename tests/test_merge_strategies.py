"""
Tests for action merge strategies when multiple rules match.

Tests cover:
- LAST_WINS: Last matching rule wins (default)
- MIN: Choose minimum value for numeric fields
- MAX: Choose maximum value for numeric fields
- STRICTEST: Most verbose log level (DEBUG > INFO > WARN > ERROR)
- ADDITIVE: Combine non-conflicting actions
- Policy-level and rule-level strategy configuration
"""

import pytest
from fastapi.testclient import TestClient
from control_plane import main
from control_plane.main import app, Policy, Rule, Action, Condition, MergeStrategy


@pytest.fixture(autouse=True)
def reset_policy():
    """Reset policy before each test."""
    # Save original policy
    original_policy = main.POLICY
    # Start with clean empty policy
    main.POLICY = Policy(
        id="test-policy",
        description="Test policy",
        rules=[],
        merge_strategy=MergeStrategy.LAST_WINS
    )
    yield
    # Restore original policy after test
    main.POLICY = original_policy


client = TestClient(app)


class TestLastWinsStrategy:
    """Test LAST_WINS merge strategy (default behavior)."""

    def test_last_wins_log_level(self):
        """Last matching rule should override log level."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="ERROR"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.LAST_WINS

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "ERROR"  # rule2 wins

    def test_last_wins_sampling_rate(self):
        """Last matching rule should override sampling rate."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.LAST_WINS

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["trace_sample_rate"] == 0.2  # rule2 wins

    def test_last_wins_metric_period(self):
        """Last matching rule should override metric period."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=30),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=120),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.LAST_WINS

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["metric_period_s"] == 120  # rule2 wins


class TestMinStrategy:
    """Test MIN merge strategy."""

    def test_min_sampling_rate(self):
        """MIN strategy should choose minimum sampling rate."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MIN

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        # MIN chooses minimum: min(0.1 default, 0.8, 0.2) = 0.1
        assert config["trace_sample_rate"] == 0.1

    def test_min_metric_period(self):
        """MIN strategy should choose minimum metric period."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=120),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=30),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MIN

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["metric_period_s"] == 30  # min of 120 and 30

    def test_min_log_level_uses_last_wins(self):
        """MIN strategy should use last wins for log level (not applicable)."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="ERROR"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MIN

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "ERROR"  # last wins for log level


class TestMaxStrategy:
    """Test MAX merge strategy."""

    def test_max_sampling_rate(self):
        """MAX strategy should choose maximum sampling rate."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MAX

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["trace_sample_rate"] == 0.8  # max of 0.2 and 0.8

    def test_max_metric_period(self):
        """MAX strategy should choose maximum metric period."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=30),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=120),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MAX

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["metric_period_s"] == 120  # max of 30 and 120


class TestStrictestStrategy:
    """Test STRICTEST merge strategy (most verbose log level)."""

    def test_strictest_debug_vs_info(self):
        """STRICTEST should choose DEBUG over INFO."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.STRICTEST

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "DEBUG"

    def test_strictest_info_vs_warn(self):
        """STRICTEST should choose INFO over WARN."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="WARN"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.STRICTEST

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "INFO"

    def test_strictest_warn_vs_error(self):
        """STRICTEST should choose most verbose including default."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="ERROR"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="WARN"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.STRICTEST

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        # STRICTEST keeps INFO (default) since it's more verbose than ERROR or WARN
        assert config["log_level"] == "INFO"

    def test_strictest_sampling_uses_last_wins(self):
        """STRICTEST should use last wins for sampling rate."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.STRICTEST

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["trace_sample_rate"] == 0.2  # last wins


class TestAdditiveStrategy:
    """Test ADDITIVE merge strategy."""

    def test_additive_log_level_uses_strictest(self):
        """ADDITIVE should use strictest (most verbose) log level."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="ERROR"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.ADDITIVE

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "DEBUG"

    def test_additive_sampling_uses_min(self):
        """ADDITIVE should use minimum sampling rate (most conservative)."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.ADDITIVE

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        # ADDITIVE uses min, including default: min(0.1, 0.8, 0.2) = 0.1
        assert config["trace_sample_rate"] == 0.1

    def test_additive_metric_period_uses_min(self):
        """ADDITIVE should use minimum period (most frequent)."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=120),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(metric_period_s=30),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.ADDITIVE

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["metric_period_s"] == 30  # min


class TestRuleLevelStrategy:
    """Test rule-level strategy overrides."""

    def test_rule_strategy_overrides_policy(self):
        """Rule-level strategy should override policy-level strategy."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True,
                merge_strategy=None  # Use policy strategy
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=True,
                merge_strategy=MergeStrategy.MAX  # Override to MAX
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MIN  # Policy default is MIN

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        # Rule1 applies 0.2 with MIN strategy (0.2)
        # Rule2 applies 0.8 with MAX strategy (max(0.2, 0.8) = 0.8)
        assert config["trace_sample_rate"] == 0.8

    def test_mixed_strategies(self):
        """Test multiple rules with different strategies."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="ERROR", trace_sample_rate=0.5),
                enabled=True,
                merge_strategy=MergeStrategy.LAST_WINS
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG", trace_sample_rate=0.8),
                enabled=True,
                merge_strategy=MergeStrategy.STRICTEST
            ),
            Rule(
                id="rule3",
                priority=3,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True,
                merge_strategy=MergeStrategy.MIN
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.LAST_WINS

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        # Log level: rule1 (ERROR) -> rule2 strictest (DEBUG)
        assert config["log_level"] == "DEBUG"
        # Sampling: rule1 (0.5) -> rule2 strictest/last_wins (0.8) -> rule3 min (0.2)
        assert config["trace_sample_rate"] == 0.2


class TestPartialActions:
    """Test merge strategies with partial actions."""

    def test_partial_actions_min_strategy(self):
        """Test MIN strategy with rules setting different fields."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="INFO"),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.5),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MIN

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "INFO"
        # MIN includes default: min(0.1, 0.5) = 0.1
        assert config["trace_sample_rate"] == 0.1
        assert config["metric_period_s"] == 60  # default

    def test_none_values_dont_override(self):
        """Test that None values don't override existing values."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.7),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MAX

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "DEBUG"
        assert config["trace_sample_rate"] == 0.7


class TestSimulationWithStrategies:
    """Test policy simulation with merge strategies."""

    def test_simulate_min_strategy(self):
        """Test simulation endpoint respects MIN strategy."""
        policy_data = {
            "id": "test-policy",
            "description": "Test MIN strategy",
            "merge_strategy": "min",
            "rules": [
                {
                    "id": "rule1",
                    "priority": 1,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"trace_sample_rate": 0.8},
                    "enabled": True
                },
                {
                    "id": "rule2",
                    "priority": 2,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"trace_sample_rate": 0.3},
                    "enabled": True
                }
            ]
        }
        
        signals = [
            {
                "service": "test-svc",
                "environment": "prod",
                "ts": "2024-01-01T00:00:00Z",
                "latency_ms": 100,
                "error": False
            }
        ]

        response = client.post(
            "/v1/policy/simulate",
            json={"policy": policy_data, "test_signals": signals}
        )
        assert response.status_code == 200
        result = response.json()
        assert len(result["simulation_results"]) == 1
        sim = result["simulation_results"][0]
        # MIN includes default: min(0.1, 0.8, 0.3) = 0.1
        assert sim["effective_config"]["trace_sample_rate"] == 0.1

    def test_simulate_strictest_strategy(self):
        """Test simulation endpoint respects STRICTEST strategy."""
        policy_data = {
            "id": "test-policy",
            "description": "Test STRICTEST strategy",
            "merge_strategy": "strictest",
            "rules": [
                {
                    "id": "rule1",
                    "priority": 1,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"log_level": "ERROR"},
                    "enabled": True
                },
                {
                    "id": "rule2",
                    "priority": 2,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"log_level": "DEBUG"},
                    "enabled": True
                }
            ]
        }
        
        signals = [
            {
                "service": "test-svc",
                "environment": "prod",
                "ts": "2024-01-01T00:00:00Z",
                "latency_ms": 100,
                "error": False
            }
        ]

        response = client.post(
            "/v1/policy/simulate",
            json={"policy": policy_data, "test_signals": signals}
        )
        assert response.status_code == 200
        result = response.json()
        sim = result["simulation_results"][0]
        assert sim["effective_config"]["log_level"] == "DEBUG"  # strictest


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_single_rule_any_strategy(self):
        """Single rule should work with any strategy."""
        for strategy in MergeStrategy:
            main.POLICY.rules = [
                Rule(
                    id="rule1",
                    priority=1,
                    conditions=[Condition(kind="always", op="always")],
                    actions=Action(log_level="DEBUG", trace_sample_rate=0.5),
                    enabled=True
                )
            ]
            main.POLICY.merge_strategy = strategy

            response = client.get("/v1/config/test/prod")
            assert response.status_code == 200
            config = response.json()
            # For all strategies, DEBUG wins (most verbose than default INFO)
            assert config["log_level"] == "DEBUG"
            # For MIN/ADDITIVE, default 0.1 < 0.5, so 0.1 wins
            # For MAX/LAST_WINS/STRICTEST, 0.5 wins
            if strategy in (MergeStrategy.MIN, MergeStrategy.ADDITIVE):
                assert config["trace_sample_rate"] == 0.1
            else:
                assert config["trace_sample_rate"] == 0.5

    def test_no_matching_rules_returns_defaults(self):
        """No matching rules should return default config."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                service="other-service",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(log_level="DEBUG"),
                enabled=True
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.STRICTEST

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["log_level"] == "INFO"  # default
        assert config["trace_sample_rate"] == 0.1  # default

    def test_disabled_rules_not_merged(self):
        """Disabled rules should not participate in merging."""
        main.POLICY.rules = [
            Rule(
                id="rule1",
                priority=1,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.2),
                enabled=True
            ),
            Rule(
                id="rule2",
                priority=2,
                conditions=[Condition(kind="always", op="always")],
                actions=Action(trace_sample_rate=0.8),
                enabled=False  # disabled
            )
        ]
        main.POLICY.merge_strategy = MergeStrategy.MAX

        response = client.get("/v1/config/test/prod")
        assert response.status_code == 200
        config = response.json()
        assert config["trace_sample_rate"] == 0.2  # only rule1 applied
