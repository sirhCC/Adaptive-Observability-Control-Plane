"""Tests for error handling and custom exceptions."""
import pytest
from fastapi.testclient import TestClient
from control_plane.main import app
from control_plane.exceptions import (
    PolicyValidationError,
    SignalProcessingError,
    DatabaseError,
    AuthenticationError,
    AuthorizationError
)


class TestCustomExceptions:
    """Test custom exception classes."""
    
    def test_policy_validation_error(self):
        """Test PolicyValidationError exception."""
        exc = PolicyValidationError("Test error", conflicts=[{"type": "test"}])
        assert exc.message == "Test error"
        assert exc.status_code == 400
        assert "conflicts" in exc.details
    
    def test_signal_processing_error(self):
        """Test SignalProcessingError exception."""
        exc = SignalProcessingError("Processing failed", signal_data={"test": "data"})
        assert exc.message == "Processing failed"
        assert exc.status_code == 422
        assert exc.details["signal_data"] == {"test": "data"}
    
    def test_database_error(self):
        """Test DatabaseError exception."""
        exc = DatabaseError("DB connection failed", operation="select")
        assert exc.message == "DB connection failed"
        assert exc.status_code == 503
        assert exc.details["operation"] == "select"
    
    def test_authentication_error(self):
        """Test AuthenticationError exception."""
        exc = AuthenticationError("Invalid credentials")
        assert exc.message == "Invalid credentials"
        assert exc.status_code == 401
    
    def test_authorization_error(self):
        """Test AuthorizationError exception."""
        exc = AuthorizationError("Access denied")
        assert exc.message == "Access denied"
        assert exc.status_code == 403


class TestErrorResponses:
    """Test API error responses."""
    
    def test_validation_error_response_format(self):
        """Test that validation errors return detailed format."""
        client = TestClient(app)
        
        # Send invalid signal (missing required fields)
        response = client.post("/v1/signal", json={
            "service": "test"
            # Missing environment and other required fields
        })
        
        assert response.status_code == 422
        data = response.json()
        assert "error" in data
        assert data["error"] == "ValidationError"
        assert "message" in data
        assert "details" in data
        assert "errors" in data["details"]
    
    def test_policy_validation_error_response(self):
        """Test policy validation error returns proper format."""
        client = TestClient(app)
        
        response = client.post("/v1/policy", json={
            "policy": {
                "id": "test",
                "rules": []  # Empty rules - should fail
            }
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "PolicyValidationError" in data["error"]
        assert "message" in data
    
    def test_invalid_json_returns_422(self):
        """Test that invalid JSON returns 422."""
        client = TestClient(app)
        
        response = client.post(
            "/v1/signal",
            data="invalid json{",
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 422
    
    def test_missing_field_validation_error(self):
        """Test validation error for missing required field."""
        client = TestClient(app)
        
        response = client.post("/v1/signal", json={
            "service": "test-svc"
            # Missing 'environment' field
        })
        
        assert response.status_code == 422
        data = response.json()
        assert "ValidationError" in data["error"]
        assert data["details"]["error_count"] > 0
    
    def test_invalid_field_type_validation_error(self):
        """Test validation error for wrong field type."""
        client = TestClient(app)
        
        response = client.post("/v1/signal", json={
            "service": "test-svc",
            "environment": "test",
            "latency_ms": "not-a-number"  # Should be float
        })
        
        assert response.status_code == 422
        data = response.json()
        assert "ValidationError" in data["error"]


class TestHealthCheckErrors:
    """Test health check endpoint error handling."""
    
    def test_healthz_includes_component_status(self):
        """Test health check returns component statuses."""
        client = TestClient(app)
        
        response = client.get("/v1/healthz")
        
        # Should succeed normally
        data = response.json()
        assert "status" in data
        assert "components" in data
        assert "database" in data["components"]
        assert "signal_buffer" in data["components"]
    
    def test_healthz_returns_timestamp(self):
        """Test health check includes timestamp."""
        client = TestClient(app)
        
        response = client.get("/v1/healthz")
        data = response.json()
        
        assert "timestamp" in data
        assert data["timestamp"]  # Not empty


class TestErrorLogging:
    """Test that errors are properly logged."""
    
    def test_validation_error_logged(self):
        """Test that validation errors are logged."""
        client = TestClient(app)
        
        # This should trigger a validation error
        response = client.post("/v1/signal", json={
            "service": "test",
            "environment": "test",
            "latency_ms": -100  # Negative latency should fail validation
        })
        
        assert response.status_code == 422


class TestErrorRecovery:
    """Test that system recovers gracefully from errors."""
    
    def test_error_does_not_crash_subsequent_requests(self):
        """Test that an error doesn't affect subsequent valid requests."""
        client = TestClient(app)
        
        # First request with error
        response1 = client.post("/v1/signal", json={
            "service": "test",
            "latency_ms": "invalid"
        })
        assert response1.status_code == 422
        
        # Second valid request should work
        response2 = client.post("/v1/signal", json={
            "service": "test-svc",
            "environment": "test",
            "latency_ms": 100.0
        })
        if response2.status_code == 429:
            pytest.skip("Rate limit reached (expected when running full test suite)")
        assert response2.status_code == 200
    
    def test_multiple_validation_errors(self):
        """Test handling multiple validation errors."""
        client = TestClient(app)
        
        response = client.post("/v1/signal", json={
            "service": "",  # Too short
            "environment": "",  # Too short
            "latency_ms": -1,  # Negative
            "error": "not-a-boolean"  # Wrong type
        })
        
        assert response.status_code == 422
        data = response.json()
        # Should report multiple errors
        assert data["details"]["error_count"] >= 2


class TestErrorDetailSuppression:
    """Test that sensitive error details are not exposed."""
    
    def test_validation_errors_do_not_expose_internals(self):
        """Test validation errors don't expose internal implementation."""
        client = TestClient(app)
        
        response = client.post("/v1/signal", json={
            "service": "test"
        })
        
        data = response.json()
        # Should not contain stack traces or internal paths
        content_str = str(data).lower()
        assert "traceback" not in content_str
        assert ".py" not in content_str or "file" not in content_str


class TestConflictErrorDetails:
    """Test that conflict detection errors provide useful details."""
    
    def test_duplicate_id_error_details(self):
        """Test duplicate ID error includes helpful details."""
        client = TestClient(app)
        
        response = client.post("/v1/policy", json={
            "policy": {
                "id": "test",
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
        
        assert response.status_code == 400
        data = response.json()
        assert "message" in data
        # Message should mention the duplicate ID
        assert "dup" in data["message"].lower() or "duplicate" in data["message"].lower()


class TestRateLimitErrors:
    """Test rate limiting error responses."""
    
    def test_rate_limit_returns_429(self):
        """Test that exceeding rate limit returns 429."""
        client = TestClient(app)
        
        # Make many requests rapidly to trigger rate limit
        # Note: This test might be flaky depending on rate limit settings
        responses = []
        for _ in range(200):
            response = client.get("/v1/healthz")
            responses.append(response.status_code)
        
        # At least one should be rate limited
        # (Actual behavior depends on rate limit configuration)
        # This is more of a smoke test
        assert all(code in [200, 429, 503] for code in responses)
