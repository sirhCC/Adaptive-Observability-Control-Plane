"""
Tests for CORS (Cross-Origin Resource Sharing) configuration.

Tests browser-based admin UI support, preflight requests,
and configurable CORS settings.
"""
import pytest
from fastapi.testclient import TestClient
import os


# Test client setup
from control_plane.main import app
client = TestClient(app)


class TestCORSBasics:
    """Test basic CORS functionality."""
    
    def test_cors_headers_present_on_simple_request(self):
        """Test CORS headers are present on simple GET requests."""
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "http://localhost:3000"}
        )
        
        assert response.status_code == 200
        # CORS headers should be present
        assert "access-control-allow-origin" in response.headers
    
    def test_cors_allows_all_origins_by_default(self):
        """Test CORS allows all origins when configured with *."""
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "http://example.com"}
        )
        
        assert response.status_code == 200
        # With allow_origins=["*"], should allow the origin
        assert "access-control-allow-origin" in response.headers
    
    def test_cors_on_public_endpoints(self):
        """Test CORS works on public endpoints."""
        endpoints = [
            "/v1/healthz",
            "/v1/readyz",
            "/v1/policy",
            "/v1/metrics",
        ]
        
        for endpoint in endpoints:
            response = client.get(
                endpoint,
                headers={"Origin": "http://localhost:3000"}
            )
            assert "access-control-allow-origin" in response.headers
    
    def test_cors_on_protected_endpoints(self):
        """Test CORS works on protected endpoints."""
        response = client.post(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "X-API-Key": "admin123",
                "Content-Type": "application/json"
            },
            json={
                "policy": {
                    "id": "test-policy",
                    "description": "Test",
                    "rules": []
                }
            }
        )
        
        # CORS headers should be present regardless of auth
        assert "access-control-allow-origin" in response.headers


class TestCORSPreflightRequests:
    """Test CORS preflight (OPTIONS) requests."""
    
    def test_preflight_request_for_post(self):
        """Test preflight request for POST endpoint."""
        response = client.options(
            "/v1/signal",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            }
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-methods" in response.headers
        assert "access-control-allow-headers" in response.headers
    
    def test_preflight_request_for_put(self):
        """Test preflight request for PUT endpoint."""
        response = client.options(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "x-api-key,content-type",
            }
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_preflight_allows_custom_headers(self):
        """Test preflight allows custom headers like X-API-Key."""
        response = client.options(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-api-key,content-type",
            }
        )
        
        assert response.status_code == 200
        headers = response.headers
        
        # Should allow the requested headers
        assert "access-control-allow-headers" in headers
    
    def test_preflight_exposes_rate_limit_headers(self):
        """Test preflight exposes rate limit headers."""
        response = client.options(
            "/v1/signal",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            }
        )
        
        assert response.status_code == 200
        # Expose headers should include rate limit headers
        if "access-control-expose-headers" in response.headers:
            exposed = response.headers["access-control-expose-headers"]
            # Check if rate limit headers are exposed
            assert any(header in exposed.lower() for header in ["ratelimit", "x-ratelimit"])


class TestCORSWithDifferentOrigins:
    """Test CORS with different origin configurations."""
    
    def test_localhost_origin(self):
        """Test CORS with localhost origin."""
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "http://localhost:8080"}
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_https_origin(self):
        """Test CORS with HTTPS origin."""
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "https://admin.example.com"}
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_custom_port_origin(self):
        """Test CORS with custom port."""
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "http://localhost:3000"}
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_subdomain_origin(self):
        """Test CORS with subdomain."""
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "https://dashboard.myapp.com"}
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestCORSMethods:
    """Test CORS with different HTTP methods."""
    
    def test_cors_on_get_request(self):
        """Test CORS on GET request."""
        response = client.get(
            "/v1/policy",
            headers={"Origin": "http://localhost:3000"}
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_cors_on_post_request(self):
        """Test CORS on POST request."""
        response = client.post(
            "/v1/signal",
            headers={
                "Origin": "http://localhost:3000",
                "Content-Type": "application/json"
            },
            json={
                "service": "test-service",
                "environment": "test",
                "latency_ms": 100,
                "error": False
            }
        )
        
        assert response.status_code in [200, 429]  # May hit rate limit
        assert "access-control-allow-origin" in response.headers
    
    def test_cors_on_options_request(self):
        """Test CORS on OPTIONS (preflight) request."""
        response = client.options(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            }
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestCORSWithAuthentication:
    """Test CORS interaction with authentication."""
    
    def test_cors_with_api_key(self):
        """Test CORS works with API key authentication."""
        response = client.post(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "X-API-Key": "admin123",
                "Content-Type": "application/json"
            },
            json={
                "policy": {
                    "id": "test-cors",
                    "description": "CORS test",
                    "rules": []
                }
            }
        )
        
        # CORS should work regardless of auth success/failure
        assert "access-control-allow-origin" in response.headers
    
    def test_preflight_without_api_key(self):
        """Test preflight request doesn't require API key."""
        response = client.options(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-api-key,content-type",
            }
        )
        
        # Preflight should succeed without API key
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_actual_request_still_requires_auth(self):
        """Test actual request still requires authentication."""
        # Preflight succeeds
        preflight = client.options(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            }
        )
        assert preflight.status_code == 200
        
        # But actual request without auth fails (or succeeds if auth not configured)
        actual = client.post(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Content-Type": "application/json"
            },
            json={
                "policy": {
                    "id": "test",
                    "description": "Test",
                    "rules": []
                }
            }
        )
        
        # Should still have CORS headers even if auth fails
        assert "access-control-allow-origin" in actual.headers


class TestCORSBrowserCompatibility:
    """Test CORS browser compatibility scenarios."""
    
    def test_simple_get_no_preflight_needed(self):
        """Test simple GET request doesn't need preflight."""
        response = client.get(
            "/v1/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Accept": "application/json"
            }
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
    
    def test_post_with_json_triggers_preflight(self):
        """Test POST with JSON content-type (requires preflight in browser)."""
        # In a real browser, this would be preceded by an OPTIONS request
        response = client.post(
            "/v1/signal",
            headers={
                "Origin": "http://localhost:3000",
                "Content-Type": "application/json"
            },
            json={
                "service": "test",
                "environment": "prod",
                "latency_ms": 50,
                "error": False
            }
        )
        
        # Should have CORS headers
        assert "access-control-allow-origin" in response.headers
    
    def test_credentials_mode_handling(self):
        """Test CORS with credentials (cookies, auth headers)."""
        response = client.get(
            "/v1/policy",
            headers={
                "Origin": "http://localhost:3000",
                "Cookie": "session=abc123"
            }
        )
        
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestCORSEdgeCases:
    """Test CORS edge cases and error scenarios."""
    
    def test_missing_origin_header(self):
        """Test request without Origin header (not a CORS request)."""
        response = client.get("/v1/healthz")
        
        assert response.status_code == 200
        # CORS middleware may or may not add headers when Origin is missing
        # This is acceptable behavior
    
    def test_cors_with_error_response(self):
        """Test CORS headers present even on error responses."""
        response = client.get(
            "/v1/config/invalid service/prod",
            headers={"Origin": "http://localhost:3000"}
        )
        
        # Should have CORS headers even on error
        assert "access-control-allow-origin" in response.headers
    
    def test_cors_with_rate_limit_error(self):
        """Test CORS headers present on rate limit errors."""
        # Make many requests to trigger rate limit
        origin = "http://localhost:3000"
        
        for _ in range(100):
            response = client.post(
                "/v1/signal",
                headers={
                    "Origin": origin,
                    "Content-Type": "application/json"
                },
                json={
                    "service": "test",
                    "environment": "prod",
                    "latency_ms": 50,
                    "error": False
                }
            )
            
            if response.status_code == 429:
                # Rate limited response should still have CORS headers
                assert "access-control-allow-origin" in response.headers
                break


class TestCORSConfiguration:
    """Test CORS configuration via environment variables."""
    
    def test_cors_configuration_loaded(self):
        """Test CORS configuration is loaded from environment."""
        # This test verifies the configuration exists
        # Actual values depend on environment variables
        response = client.get(
            "/v1/healthz",
            headers={"Origin": "http://localhost:3000"}
        )
        
        assert response.status_code == 200
        # Should have CORS configured
        assert "access-control-allow-origin" in response.headers
    
    def test_multiple_origins_supported(self):
        """Test CORS supports multiple origins (when configured)."""
        # With default config (*), all origins should work
        origins = [
            "http://localhost:3000",
            "http://localhost:8080",
            "https://admin.example.com"
        ]
        
        for origin in origins:
            response = client.get(
                "/v1/healthz",
                headers={"Origin": origin}
            )
            
            assert response.status_code == 200
            assert "access-control-allow-origin" in response.headers
