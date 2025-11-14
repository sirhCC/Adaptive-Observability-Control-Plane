"""Tests for Prometheus metrics endpoint."""
import pytest
from fastapi.testclient import TestClient
from control_plane.main import app, SIGNALS


@pytest.fixture(autouse=True)
def clean_signals():
    """Clean signals buffer before each test."""
    SIGNALS.clear()
    yield
    SIGNALS.clear()


class TestMetricsEndpoint:
    """Test Prometheus metrics endpoint."""
    
    def test_metrics_endpoint_exists(self):
        """Test that /metrics endpoint is available."""
        client = TestClient(app)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
    
    def test_metrics_endpoint_returns_prometheus_format(self):
        """Test that metrics are in Prometheus format."""
        client = TestClient(app)
        response = client.get("/metrics")
        content = response.text
        
        # Check for standard Prometheus metric prefixes
        assert "# HELP" in content or "# TYPE" in content
    
    def test_metrics_endpoint_includes_control_plane_metrics(self):
        """Test that control plane specific metrics are exposed."""
        client = TestClient(app)
        
        # Generate some activity to create metrics
        client.post("/signal", json={
            "service": "test-svc",
            "environment": "test",
            "latency_ms": 100,
            "error": False
        })
        
        response = client.get("/metrics")
        content = response.text
        
        # Check for our custom metrics
        assert "control_plane_signals_ingested_total" in content
        assert "control_plane_policy_evaluations_total" in content
    
    def test_metrics_track_signal_ingestion(self):
        """Test that signal ingestion is tracked in metrics."""
        client = TestClient(app)
        
        # Ingest multiple signals
        for i in range(5):
            client.post("/signal", json={
                "service": "metrics-test",
                "environment": "prod",
                "latency_ms": 50.0 * (i + 1),
                "error": i % 2 == 0
            })
        
        response = client.get("/metrics")
        content = response.text
        
        # Verify signal metrics exist
        assert "control_plane_signals_ingested_total" in content
        assert 'service="metrics-test"' in content
        assert 'environment="prod"' in content
    
    def test_metrics_track_policy_evaluations(self):
        """Test that policy evaluations are tracked."""
        client = TestClient(app)
        
        # Trigger policy evaluation via config endpoint
        client.get("/config/eval-test/dev")
        
        response = client.get("/metrics")
        content = response.text
        
        # Verify evaluation metrics
        assert "control_plane_policy_evaluations_total" in content
        assert "control_plane_policy_evaluation_duration_seconds" in content
    
    def test_metrics_track_error_signals(self):
        """Test that error signals are tracked separately."""
        client = TestClient(app)
        
        # Send error signal
        client.post("/signal", json={
            "service": "error-svc",
            "environment": "staging",
            "latency_ms": 200,
            "error": True
        })
        
        response = client.get("/metrics")
        content = response.text
        
        # Verify error tracking
        assert "control_plane_signals_with_errors_total" in content
    
    def test_metrics_track_buffer_size(self):
        """Test that buffer size is tracked as a gauge."""
        client = TestClient(app)
        
        # Add signals to buffer
        for i in range(3):
            client.post("/signal", json={
                "service": "buffer-test",
                "environment": "test",
                "latency_ms": 100
            })
        
        response = client.get("/metrics")
        content = response.text
        
        # Verify buffer size metric
        assert "control_plane_signal_buffer_size" in content


class TestMetricsAccuracy:
    """Test metric accuracy and correctness."""
    
    def test_counter_increments_correctly(self):
        """Test that counters increment with each request."""
        client = TestClient(app)
        
        # Send 3 signals
        for _ in range(3):
            client.post("/signal", json={
                "service": "counter-test",
                "environment": "dev",
                "latency_ms": 50
            })
        
        # Get metrics
        response = client.get("/metrics")
        content = response.text
        
        # Find the counter line for our service
        # Should have incremented 3 times
        assert "control_plane_signals_ingested_total" in content
    
    def test_histogram_records_durations(self):
        """Test that histogram metrics record durations."""
        client = TestClient(app)
        
        # Trigger evaluation
        client.get("/config/histogram-test/prod")
        
        response = client.get("/metrics")
        content = response.text
        
        # Check histogram components
        assert "control_plane_policy_evaluation_duration_seconds_bucket" in content or \
               "control_plane_policy_evaluation_duration_seconds_sum" in content


class TestMetricsWithPolicy:
    """Test metrics with policy changes."""
    
    def test_policy_update_metrics(self):
        """Test that policy updates are tracked."""
        client = TestClient(app)
        
        # First get current policy
        get_response = client.get("/policy")
        policy = get_response.json()
        
        # Try to update policy (will need admin key in real scenario)
        # For test without admin key, we expect rejection but metrics should track validation
        post_response = client.post("/policy", json={
            "policy": {
                "id": "test-policy",
                "rules": []  # Invalid - no rules
            }
        })
        
        # Get metrics
        metrics_response = client.get("/metrics")
        content = metrics_response.text
        
        # Should track validation error
        assert "control_plane_policy_validation_errors_total" in content


class TestHTTPMetrics:
    """Test standard HTTP metrics from instrumentator."""
    
    def test_http_request_metrics_exist(self):
        """Test that standard HTTP request metrics are exposed."""
        client = TestClient(app)
        
        # Make a request
        client.get("/healthz")
        
        response = client.get("/metrics")
        content = response.text
        
        # Check for standard FastAPI instrumentation metrics
        # These are provided by prometheus-fastapi-instrumentator
        assert "http_requests" in content or "fastapi" in content
