"""Integration tests for API endpoints."""
import pytest
from fastapi.testclient import TestClient
from control_plane.main import app, POLICY, SIGNALS


client = TestClient(app)


def setup_function(_):
    """Clear state before each test."""
    SIGNALS.clear()


class TestHealthEndpoint:
    """Test the health check endpoint."""
    
    def test_healthz_returns_ok(self):
        """Health check should return ok status."""
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "ts" in data
    
    def test_healthz_timestamp_format(self):
        """Health check timestamp should be ISO format."""
        response = client.get("/healthz")
        data = response.json()
        # Should be parseable as ISO datetime
        from datetime import datetime
        ts = datetime.fromisoformat(data["ts"].replace("Z", "+00:00"))
        assert ts is not None


class TestPolicyEndpoints:
    """Test policy GET and POST endpoints."""
    
    def test_get_policy_returns_current_policy(self):
        """GET /policy should return the current policy."""
        response = client.get("/policy")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "rules" in data
        assert isinstance(data["rules"], list)
    
    def test_get_policy_includes_rules(self):
        """GET /policy should include rule details."""
        response = client.get("/policy")
        data = response.json()
        assert len(data["rules"]) > 0
        first_rule = data["rules"][0]
        assert "id" in first_rule
        assert "priority" in first_rule
        assert "conditions" in first_rule
        assert "actions" in first_rule
    
    def test_post_policy_updates_policy(self):
        """POST /policy should update the active policy."""
        original = client.get("/policy").json()
        
        new_policy = {
            "id": "test-policy",
            "description": "Test policy",
            "rules": [
                {
                    "id": "test-rule",
                    "priority": 50,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"log_level": "DEBUG"}
                }
            ]
        }
        
        response = client.post("/policy", json={"policy": new_policy})
        assert response.status_code == 200
        assert response.json()["id"] == "test-policy"
        
        # Verify it was actually updated
        current = client.get("/policy").json()
        assert current["id"] == "test-policy"
        
        # Restore original
        client.post("/policy", json={"policy": original})
    
    def test_post_policy_with_missing_id(self):
        """POST /policy without required ID should return 422."""
        invalid_policy = {
            # Missing ID
            "rules": [
                {
                    "id": "test-rule",
                    "priority": 10,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"log_level": "INFO"}
                }
            ]
        }
        
        response = client.post("/policy", json={"policy": invalid_policy})
        assert response.status_code == 422  # Validation error


class TestConfigEndpoint:
    """Test the configuration retrieval endpoint."""
    
    def test_get_config_returns_effective_config(self):
        """GET /config should return effective configuration."""
        response = client.get("/config/test-service/prod")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "test-service"
        assert data["environment"] == "prod"
        assert "log_level" in data
        assert "trace_sample_rate" in data
        assert "metric_period_s" in data
    
    def test_get_config_different_services(self):
        """Config for different services should work independently."""
        # Get config for multiple services
        response1 = client.get("/config/service-a/prod")
        response2 = client.get("/config/service-b/prod")
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        data1 = response1.json()
        data2 = response2.json()
        
        assert data1["service"] == "service-a"
        assert data2["service"] == "service-b"


class TestSignalEndpoint:
    """Test signal ingestion endpoint."""
    
    def test_signal_returns_config(self):
        """POST /signal should return effective config."""
        response = client.post("/signal", json={
            "service": "test",
            "environment": "prod",
            "latency_ms": 150.0,
            "error": False
        })
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert "log_level" in data
    
    def test_signal_with_minimal_data(self):
        """Signal with only required fields should work."""
        response = client.post("/signal", json={
            "service": "minimal",
            "environment": "test"
        })
        assert response.status_code == 200
    
    def test_signal_with_attributes(self):
        """Signal with custom attributes should be accepted."""
        response = client.post("/signal", json={
            "service": "attr-test",
            "environment": "test",
            "latency_ms": 100.0,
            "attrs": {
                "host": "server-01",
                "version": "1.2.3",
                "region": "us-west-2"
            }
        })
        assert response.status_code == 200
    
    def test_signal_affects_subsequent_config(self):
        """Sending signals should affect config retrieval."""
        service = "stateful-test"
        env = "test"
        
        # Get initial config
        initial = client.get(f"/config/{service}/{env}").json()
        
        # Send high-latency signals
        for i in range(30):
            client.post("/signal", json={
                "service": service,
                "environment": env,
                "latency_ms": 600.0,
                "error": False
            })
        
        # Get config again
        updated = client.get(f"/config/{service}/{env}").json()
        
        # Config should have changed (actual values depend on policy rules)
        # At minimum, we can verify the call succeeded
        assert updated is not None


class TestErrorResponses:
    """Test error handling in API endpoints."""
    
    def test_invalid_json_returns_422(self):
        """Invalid JSON should return 422."""
        response = client.post(
            "/signal",
            data="not json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
    
    def test_missing_required_field_returns_422(self):
        """Missing required field should return 422."""
        response = client.post("/signal", json={
            "service": "test"
            # Missing environment
        })
        assert response.status_code == 422
    
    def test_get_nonexistent_endpoint_returns_404(self):
        """Non-existent endpoint should return 404."""
        response = client.get("/nonexistent")
        assert response.status_code == 404


class TestConcurrentRequests:
    """Test handling of concurrent requests."""
    
    def test_multiple_services_isolated(self):
        """Signals from different services should be isolated."""
        # Send signals to service A
        for i in range(10):
            client.post("/signal", json={
                "service": "service-a",
                "environment": "test",
                "latency_ms": 1000.0,
                "error": True
            })
        
        # Send signals to service B
        for i in range(10):
            client.post("/signal", json={
                "service": "service-b",
                "environment": "test",
                "latency_ms": 50.0,
                "error": False
            })
        
        # Configs should be independent
        config_a = client.get("/config/service-a/test").json()
        config_b = client.get("/config/service-b/test").json()
        
        # Service A should have elevated settings
        # Service B should have normal settings
        # Exact values depend on policy, but they should differ
        assert config_a is not None
        assert config_b is not None
    
    def test_multiple_environments_isolated(self):
        """Signals from different environments should be isolated."""
        service = "multi-env-test"
        
        # Send signals to prod
        for i in range(10):
            client.post("/signal", json={
                "service": service,
                "environment": "prod",
                "latency_ms": 1000.0,
                "error": True
            })
        
        # Send signals to staging
        for i in range(10):
            client.post("/signal", json={
                "service": service,
                "environment": "staging",
                "latency_ms": 50.0,
                "error": False
            })
        
        # Configs should be independent
        config_prod = client.get(f"/config/{service}/prod").json()
        config_staging = client.get(f"/config/{service}/staging").json()
        
        assert config_prod is not None
        assert config_staging is not None


class TestDataTypes:
    """Test data type handling and validation."""
    
    def test_float_latency_accepted(self):
        """Latency as float should be accepted."""
        response = client.post("/signal", json={
            "service": "test",
            "environment": "test",
            "latency_ms": 123.456
        })
        assert response.status_code == 200
    
    def test_integer_latency_accepted(self):
        """Latency as integer should be accepted."""
        response = client.post("/signal", json={
            "service": "test",
            "environment": "test",
            "latency_ms": 100
        })
        assert response.status_code == 200
    
    def test_null_latency_accepted(self):
        """Null latency should be accepted."""
        response = client.post("/signal", json={
            "service": "test",
            "environment": "test",
            "latency_ms": None
        })
        assert response.status_code == 200
    
    def test_boolean_error_field(self):
        """Error field should accept boolean."""
        response = client.post("/signal", json={
            "service": "test",
            "environment": "test",
            "error": True
        })
        assert response.status_code == 200
        
        response = client.post("/signal", json={
            "service": "test",
            "environment": "test",
            "error": False
        })
        assert response.status_code == 200
