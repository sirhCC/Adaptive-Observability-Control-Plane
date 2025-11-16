"""Unit tests for validation helper functions."""

import pytest
from datetime import datetime, timedelta, timezone

from control_plane import validators


class TestNameValidation:
    """Test name pattern validation."""
    
    def test_valid_service_name(self):
        """Valid service names should pass."""
        assert validators.validate_service_name("my-service") == "my-service"
        assert validators.validate_service_name("service_123") == "service_123"
        assert validators.validate_service_name("Service-Name-123") == "Service-Name-123"
    
    def test_invalid_service_name_with_spaces(self):
        """Service names with spaces should fail."""
        with pytest.raises(ValueError, match="must contain only alphanumeric"):
            validators.validate_service_name("my service")
    
    def test_invalid_service_name_with_special_chars(self):
        """Service names with special characters should fail."""
        with pytest.raises(ValueError, match="must contain only alphanumeric"):
            validators.validate_service_name("my@service")
    
    def test_valid_environment_name(self):
        """Valid environment names should pass."""
        assert validators.validate_environment_name("prod") == "prod"
        assert validators.validate_environment_name("staging-1") == "staging-1"
        assert validators.validate_environment_name("dev_test") == "dev_test"
    
    def test_valid_rule_id(self):
        """Valid rule IDs should pass."""
        assert validators.validate_rule_id("rule-123") == "rule-123"
        assert validators.validate_rule_id("my_rule") == "my_rule"


class TestAttributeValidation:
    """Test attribute dictionary validation."""
    
    def test_valid_attributes(self):
        """Valid attributes should pass."""
        attrs = {"region": "us-west-2", "version": "1.0.0"}
        assert validators.validate_attributes(attrs) == attrs
    
    def test_attribute_key_too_long(self):
        """Attribute keys exceeding max length should fail."""
        attrs = {"x" * 129: "value"}
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validators.validate_attributes(attrs)
    
    def test_attribute_value_too_long(self):
        """Attribute values exceeding max length should fail."""
        attrs = {"key": "x" * 1025}
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validators.validate_attributes(attrs)
    
    def test_empty_attributes(self):
        """Empty attribute dict should pass."""
        assert validators.validate_attributes({}) == {}


class TestTimestampValidation:
    """Test timestamp validation."""
    
    def test_current_timestamp(self):
        """Current timestamp should pass."""
        now = datetime.now(timezone.utc)
        result = validators.validate_timestamp(now)
        assert result == now
    
    def test_none_timestamp(self):
        """None timestamp should pass."""
        assert validators.validate_timestamp(None) is None
    
    def test_recent_past_timestamp(self):
        """Recent past timestamps should pass."""
        past = datetime.now(timezone.utc) - timedelta(days=3)
        result = validators.validate_timestamp(past)
        assert result == past
    
    def test_timestamp_too_far_in_past(self):
        """Timestamps more than 7 days old should fail."""
        old = datetime.now(timezone.utc) - timedelta(days=8)
        with pytest.raises(ValueError, match="cannot be more than 7 days in the past"):
            validators.validate_timestamp(old)
    
    def test_timestamp_too_far_in_future(self):
        """Timestamps more than 1 day in future should fail."""
        future = datetime.now(timezone.utc) + timedelta(days=2)
        with pytest.raises(ValueError, match="cannot be more than 1 day"):
            validators.validate_timestamp(future)
    
    def test_timezone_naive_timestamp(self):
        """Timezone-naive timestamps should be converted to UTC."""
        naive = datetime.now()
        result = validators.validate_timestamp(naive)
        assert result.tzinfo == timezone.utc


class TestLatencyValidation:
    """Test latency validation."""
    
    def test_valid_latency(self):
        """Valid latency values should pass."""
        assert validators.validate_latency(100.5) == 100.5
        assert validators.validate_latency(0.0) == 0.0
    
    def test_none_latency(self):
        """None latency should pass."""
        assert validators.validate_latency(None) is None
    
    def test_negative_latency(self):
        """Negative latency should fail."""
        with pytest.raises(ValueError, match="cannot be negative"):
            validators.validate_latency(-10.0)
    
    def test_unreasonably_high_latency(self):
        """Extremely high latency should fail."""
        with pytest.raises(ValueError, match="suspiciously high"):
            validators.validate_latency(500_000.0)


class TestSampleRateValidation:
    """Test trace sample rate validation."""
    
    def test_valid_sample_rates(self):
        """Valid sample rates should pass."""
        assert validators.validate_sample_rate(0.0) == 0.0
        assert validators.validate_sample_rate(0.5) == 0.5
        assert validators.validate_sample_rate(1.0) == 1.0
    
    def test_sample_rate_below_zero(self):
        """Sample rates below 0 should fail."""
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            validators.validate_sample_rate(-0.1)
    
    def test_sample_rate_above_one(self):
        """Sample rates above 1 should fail."""
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            validators.validate_sample_rate(1.5)


class TestMetricPeriodValidation:
    """Test metric period validation."""
    
    def test_valid_metric_periods(self):
        """Valid metric periods should pass."""
        assert validators.validate_metric_period(1) == 1
        assert validators.validate_metric_period(60) == 60
        assert validators.validate_metric_period(3600) == 3600
    
    def test_metric_period_too_small(self):
        """Metric period less than 1 second should fail."""
        with pytest.raises(ValueError, match="must be at least 1 second"):
            validators.validate_metric_period(0)
    
    def test_metric_period_too_large(self):
        """Metric period over 1 hour should fail."""
        with pytest.raises(ValueError, match="cannot exceed 1 hour"):
            validators.validate_metric_period(7200)


class TestLogLevelValidation:
    """Test log level validation."""
    
    def test_valid_log_levels(self):
        """Valid log levels should pass and be normalized."""
        assert validators.validate_log_level("INFO") == "INFO"
        assert validators.validate_log_level("info") == "INFO"
        assert validators.validate_log_level("DEBUG") == "DEBUG"
        assert validators.validate_log_level("ERROR") == "ERROR"
    
    def test_warning_normalized_to_warn(self):
        """WARNING should be normalized to WARN."""
        assert validators.validate_log_level("WARNING") == "WARN"
        assert validators.validate_log_level("warning") == "WARN"
    
    def test_invalid_log_level(self):
        """Invalid log levels should fail."""
        with pytest.raises(ValueError, match="Invalid log level"):
            validators.validate_log_level("INVALID")


class TestPriorityValidation:
    """Test rule priority validation."""
    
    def test_valid_priorities(self):
        """Valid priorities should pass."""
        assert validators.validate_priority(0) == 0
        assert validators.validate_priority(100) == 100
        assert validators.validate_priority(-100) == -100
        assert validators.validate_priority(1000) == 1000
        assert validators.validate_priority(-1000) == -1000
    
    def test_priority_too_high(self):
        """Priority above 1000 should fail."""
        with pytest.raises(ValueError, match="must be between -1000 and 1000"):
            validators.validate_priority(1001)
    
    def test_priority_too_low(self):
        """Priority below -1000 should fail."""
        with pytest.raises(ValueError, match="must be between -1000 and 1000"):
            validators.validate_priority(-1001)


class TestValidatorIntegration:
    """Test validators work together correctly."""
    
    def test_multiple_validations_on_signal_data(self):
        """Validate multiple fields like a real signal."""
        # Valid signal data
        service = validators.validate_service_name("api-gateway")
        env = validators.validate_environment_name("production")
        latency = validators.validate_latency(45.2)
        attrs = validators.validate_attributes({"region": "us-east-1"})
        timestamp = validators.validate_timestamp(datetime.now(timezone.utc))
        
        assert service == "api-gateway"
        assert env == "production"
        assert latency == 45.2
        assert "region" in attrs
        assert timestamp is not None
    
    def test_validation_chain_stops_on_first_error(self):
        """Invalid data should raise error immediately."""
        with pytest.raises(ValueError):
            validators.validate_service_name("invalid name")
