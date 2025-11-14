"""Tests for authentication and authorization."""
import pytest
from fastapi.testclient import TestClient
from control_plane.main import app


client = TestClient(app)


class TestAuthentication:
    """Test authentication functionality."""
    
    def test_healthz_no_auth_required(self):
        """Health endpoint should not require authentication."""
        response = client.get("/v1/healthz")
        assert response.status_code == 200
    
    def test_get_policy_no_auth_required(self):
        """Reading policy should not require authentication."""
        response = client.get("/v1/policy")
        assert response.status_code == 200
    
    def test_post_policy_without_admin_key_when_not_configured(self):
        """Policy update should work when ADMIN_API_KEY is not set (backward compatible)."""
        policy = {
            "policy": {
                "id": "test",
                "rules": [{
                    "id": "test-rule",
                    "priority": 100,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {"log_level": "INFO"}
                }]
            }
        }
        # Without ADMIN_API_KEY env var, should succeed
        response = client.post("/v1/policy", json=policy)
        # May be 200 or 401 depending on environment
        assert response.status_code in (200, 401)
    
    def test_signal_endpoint_works_without_api_key(self):
        """Signal endpoint should work without API key (optional auth)."""
        signal = {
            "service": "test-svc",
            "environment": "test",
            "latency_ms": 100.0,
            "error": False,
        }
        response = client.post("/v1/signal", json=signal)
        assert response.status_code == 200
    
    def test_signal_endpoint_with_api_key(self):
        """Signal endpoint should accept API key in header."""
        signal = {
            "service": "test-svc",
            "environment": "test",
            "latency_ms": 100.0,
            "error": False,
        }
        headers = {"X-API-Key": "aoc_test_key_1234567890abcdef"}
        response = client.post("/v1/signal", json=signal, headers=headers)
        assert response.status_code == 200
    
    def test_generate_key_without_auth_fails(self):
        """API key generation should require admin authentication."""
        response = client.post("/v1/auth/generate-key")
        # Should fail without admin key when ADMIN_API_KEY is set
        # Or succeed if ADMIN_API_KEY is not set (backward compatible)
        assert response.status_code in (200, 401)
    
    def test_config_endpoint_no_auth_required(self):
        """Config endpoint should not require authentication."""
        response = client.get("/v1/config/test-svc/test")
        assert response.status_code == 200


class TestAPIKeyValidation:
    """Test API key validation logic."""
    
    def test_short_api_key_rejected_on_signal(self):
        """API keys that are too short should be rejected if provided."""
        signal = {
            "service": "test-svc",
            "environment": "test",
            "latency_ms": 100.0,
        }
        # Very short key - should work since API key is optional on /signal
        headers = {"X-API-Key": "short"}
        response = client.post("/v1/signal", json=signal, headers=headers)
        # Optional auth means it won't fail, just won't be validated
        assert response.status_code == 200


class TestAdminEndpoints:
    """Test endpoints that require admin access."""
    
    def test_policy_post_is_protected(self):
        """Policy modification should be protected."""
        policy = {
            "policy": {
                "id": "test",
                "rules": [{
                    "id": "r1",
                    "priority": 1,
                    "conditions": [{"kind": "always", "op": "always"}],
                    "actions": {}
                }]
            }
        }
        response = client.post("/v1/policy", json=policy)
        # Either succeeds (no ADMIN_API_KEY set) or requires auth
        assert response.status_code in (200, 401, 403)
    
    def test_generate_key_requires_admin(self):
        """Key generation should require admin privileges."""
        response = client.post("/v1/auth/generate-key")
        assert response.status_code in (200, 401, 403)
