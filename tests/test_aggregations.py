"""Tests for advanced aggregation functions."""
import pytest
from datetime import datetime, timezone, timedelta
from control_plane.main import _calc_aggregates, Signal


class TestAdvancedAggregations:
    """Test advanced aggregation functions."""
    
    def test_percentiles_calculation(self):
        """Test p50, p90, p95, p99 percentile calculations."""
        # Create 100 signals with latencies from 1-100ms
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=float(i),
                error=False,
                attrs={}
            )
            for i in range(1, 101)
        ]
        
        aggs = _calc_aggregates(signals)
        
        # Verify percentiles are in expected ranges
        assert 49 <= aggs["latency_p50_ms"] <= 51  # ~50th
        assert 89 <= aggs["latency_p90_ms"] <= 91  # ~90th
        assert 94 <= aggs["latency_p95_ms"] <= 96  # ~95th
        assert 98 <= aggs["latency_p99_ms"] <= 100  # ~99th
    
    def test_average_calculation(self):
        """Test average latency calculation."""
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=100.0,
                error=False,
                attrs={}
            )
            for _ in range(5)
        ]
        
        aggs = _calc_aggregates(signals)
        assert aggs["latency_avg_ms"] == 100.0
    
    def test_min_max_calculation(self):
        """Test min and max latency calculation."""
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=float(i * 10),
                error=False,
                attrs={}
            )
            for i in range(1, 11)
        ]
        
        aggs = _calc_aggregates(signals)
        assert aggs["latency_min_ms"] == 10.0
        assert aggs["latency_max_ms"] == 100.0
    
    def test_error_count_and_rate(self):
        """Test error count and rate calculation."""
        signals = []
        # 20 total signals, 3 with errors
        for i in range(20):
            signals.append(Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=50.0,
                error=(i < 3),  # First 3 are errors
                attrs={}
            ))
        
        aggs = _calc_aggregates(signals)
        assert aggs["error_count"] == 3.0
        assert aggs["error_rate"] == 0.15  # 3/20 = 0.15
    
    def test_request_count(self):
        """Test request count calculation."""
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=50.0,
                error=False,
                attrs={}
            )
            for _ in range(42)
        ]
        
        aggs = _calc_aggregates(signals)
        assert aggs["request_count"] == 42.0
    
    def test_request_rate_per_second(self):
        """Test request rate calculation."""
        now = datetime.now(timezone.utc)
        signals = []
        
        # 10 requests over 5 seconds = 2 req/sec
        for i in range(10):
            signals.append(Signal(
                service="test",
                environment="dev",
                ts=now + timedelta(seconds=i * 0.5),  # Every 0.5 seconds
                latency_ms=50.0,
                error=False,
                attrs={}
            ))
        
        aggs = _calc_aggregates(signals)
        # 10 requests over ~4.5 seconds
        assert 1.8 <= aggs["request_rate_per_sec"] <= 2.5
    
    def test_empty_buffer_returns_zeros(self):
        """Test that empty buffer returns zero values."""
        aggs = _calc_aggregates([])
        
        assert aggs["latency_p50_ms"] == 0.0
        assert aggs["latency_p90_ms"] == 0.0
        assert aggs["latency_p95_ms"] == 0.0
        assert aggs["latency_p99_ms"] == 0.0
        assert aggs["latency_avg_ms"] == 0.0
        assert aggs["latency_min_ms"] == 0.0
        assert aggs["latency_max_ms"] == 0.0
        assert aggs["error_rate"] == 0.0
        assert aggs["error_count"] == 0.0
        assert aggs["request_count"] == 0.0
        assert aggs["request_rate_per_sec"] == 0.0
    
    def test_single_signal(self):
        """Test aggregates with a single signal."""
        signal = Signal(
            service="test",
            environment="dev",
            ts=datetime.now(timezone.utc),
            latency_ms=123.45,
            error=True,
            attrs={}
        )
        
        aggs = _calc_aggregates([signal])
        
        # All percentiles should equal the single value
        assert aggs["latency_p50_ms"] == 123.45
        assert aggs["latency_p90_ms"] == 123.45
        assert aggs["latency_p95_ms"] == 123.45
        assert aggs["latency_p99_ms"] == 123.45
        assert aggs["latency_avg_ms"] == 123.45
        assert aggs["latency_min_ms"] == 123.45
        assert aggs["latency_max_ms"] == 123.45
        assert aggs["error_rate"] == 1.0
        assert aggs["error_count"] == 1.0
        assert aggs["request_count"] == 1.0
    
    def test_signals_without_latency(self):
        """Test aggregates when some signals lack latency data."""
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=None,
                error=False,
                attrs={}
            )
            for _ in range(5)
        ]
        
        aggs = _calc_aggregates(signals)
        
        # Latency metrics should be zero
        assert aggs["latency_p50_ms"] == 0.0
        assert aggs["latency_p95_ms"] == 0.0
        # But request count should still work
        assert aggs["request_count"] == 5.0
        assert aggs["error_rate"] == 0.0
    
    def test_window_filtering_with_aggregates(self):
        """Test that window filtering works with new aggregates."""
        now = datetime.now(timezone.utc)
        signals = []
        
        # Old signals (60+ seconds ago)
        for i in range(5):
            signals.append(Signal(
                service="test",
                environment="dev",
                ts=now - timedelta(seconds=70 + i),
                latency_ms=1000.0,  # High latency
                error=True,
                attrs={}
            ))
        
        # Recent signals (within window)
        for i in range(10):
            signals.append(Signal(
                service="test",
                environment="dev",
                ts=now - timedelta(seconds=i),
                latency_ms=50.0,  # Low latency
                error=False,
                attrs={}
            ))
        
        # Calculate with 60-second window
        aggs = _calc_aggregates(signals, window_s=60)
        
        # Should only include recent signals
        assert aggs["request_count"] == 10.0
        assert aggs["error_count"] == 0.0
        assert aggs["latency_avg_ms"] == 50.0
        assert aggs["latency_max_ms"] == 50.0


class TestAggregateIntegration:
    """Test aggregates in rule evaluation context."""
    
    def test_rule_with_p50_metric(self):
        """Test rule evaluation using p50 metric."""
        from control_plane.main import SIGNALS, evaluate, POLICY
        from control_plane.main import Rule, Condition, Action
        
        # Clean up
        SIGNALS.clear()
        
        # Create a rule that triggers on p50 > 100ms
        POLICY.rules = [
            Rule(
                id="p50-threshold",
                description="Alert on median latency > 100ms",
                priority=10,
                conditions=[
                    Condition(kind="metric", op=">", key="latency_p50_ms", value=100.0, window_s=60)
                ],
                actions=Action(log_level="WARN", trace_sample_rate=0.8)
            )
        ]
        
        # Add signals with median > 100ms
        now = datetime.now(timezone.utc)
        for i in range(20):
            SIGNALS.setdefault(("test-svc", "prod"), []).append(
                Signal(
                    service="test-svc",
                    environment="prod",
                    ts=now,
                    latency_ms=float(100 + i * 10),  # 100, 110, 120, ... 290
                    error=False,
                    attrs={}
                )
            )
        
        config = evaluate("test-svc", "prod")
        
        # Rule should match
        assert config.log_level == "WARN"
        assert config.trace_sample_rate == 0.8
        
        # Cleanup
        SIGNALS.clear()
    
    def test_rule_with_request_rate(self):
        """Test rule evaluation using request rate metric."""
        from control_plane.main import SIGNALS, evaluate, POLICY
        from control_plane.main import Rule, Condition, Action
        
        # Clean up
        SIGNALS.clear()
        
        # Create a rule that triggers on high request rate
        POLICY.rules = [
            Rule(
                id="high-traffic",
                description="High request rate detected",
                priority=10,
                conditions=[
                    Condition(kind="metric", op=">", key="request_rate_per_sec", value=5.0)
                ],
                actions=Action(metric_period_s=10)
            )
        ]
        
        # Add signals simulating high traffic
        now = datetime.now(timezone.utc)
        for i in range(100):
            SIGNALS.setdefault(("busy-svc", "prod"), []).append(
                Signal(
                    service="busy-svc",
                    environment="prod",
                    ts=now + timedelta(milliseconds=i * 100),  # 10 req/sec
                    latency_ms=50.0,
                    error=False,
                    attrs={}
                )
            )
        
        config = evaluate("busy-svc", "prod")
        
        # Rule should match
        assert config.metric_period_s == 10
        
        # Cleanup
        SIGNALS.clear()


class TestPercentileEdgeCases:
    """Test percentile calculation edge cases."""
    
    def test_percentile_with_two_values(self):
        """Test percentile with exactly 2 values."""
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=10.0,
                error=False,
                attrs={}
            ),
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=20.0,
                error=False,
                attrs={}
            )
        ]
        
        aggs = _calc_aggregates(signals)
        
        # Should handle gracefully
        assert aggs["latency_p50_ms"] in [10.0, 20.0]
        assert aggs["latency_p95_ms"] in [10.0, 20.0]
        assert aggs["latency_min_ms"] == 10.0
        assert aggs["latency_max_ms"] == 20.0
        assert aggs["latency_avg_ms"] == 15.0
    
    def test_percentile_with_identical_values(self):
        """Test percentile when all values are identical."""
        signals = [
            Signal(
                service="test",
                environment="dev",
                ts=datetime.now(timezone.utc),
                latency_ms=50.0,
                error=False,
                attrs={}
            )
            for _ in range(100)
        ]
        
        aggs = _calc_aggregates(signals)
        
        # All percentiles should be the same
        assert aggs["latency_p50_ms"] == 50.0
        assert aggs["latency_p90_ms"] == 50.0
        assert aggs["latency_p95_ms"] == 50.0
        assert aggs["latency_p99_ms"] == 50.0
        assert aggs["latency_avg_ms"] == 50.0
        assert aggs["latency_min_ms"] == 50.0
        assert aggs["latency_max_ms"] == 50.0
