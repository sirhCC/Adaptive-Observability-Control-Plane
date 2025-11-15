"""
Tests for wildcard service/environment matching (Item #18).
"""
from control_plane.pattern_matching import (
    matches_pattern,
    matches_service_pattern,
    matches_environment_pattern,
    validate_pattern
)


class TestPatternMatching:
    """Test basic pattern matching functionality."""
    
    def test_none_pattern_matches_everything(self):
        """Test that None pattern matches any value."""
        assert matches_pattern("api-v1", None) is True
        assert matches_pattern("web-service", None) is True
        assert matches_pattern("", None) is True
    
    def test_wildcard_matches_everything(self):
        """Test that * wildcard matches any value."""
        assert matches_pattern("api-v1", "*") is True
        assert matches_pattern("web-service", "*") is True
        assert matches_pattern("production", "*") is True
        assert matches_pattern("", "*") is True
    
    def test_exact_match(self):
        """Test exact string matching."""
        assert matches_pattern("api-v1", "api-v1") is True
        assert matches_pattern("api-v1", "api-v2") is False
        assert matches_pattern("production", "production") is True
        assert matches_pattern("prod", "production") is False
    
    def test_glob_prefix_pattern(self):
        """Test glob patterns with prefix matching."""
        assert matches_pattern("api-v1", "api-*") is True
        assert matches_pattern("api-v2", "api-*") is True
        assert matches_pattern("api-service", "api-*") is True
        assert matches_pattern("web-v1", "api-*") is False
    
    def test_glob_suffix_pattern(self):
        """Test glob patterns with suffix matching."""
        assert matches_pattern("service-api", "*-api") is True
        assert matches_pattern("backend-api", "*-api") is True
        assert matches_pattern("api-v1", "*-api") is False
    
    def test_glob_middle_pattern(self):
        """Test glob patterns in the middle."""
        assert matches_pattern("api-v1-service", "api-*-service") is True
        assert matches_pattern("api-v2-service", "api-*-service") is True
        assert matches_pattern("web-v1-service", "api-*-service") is False
    
    def test_glob_question_mark(self):
        """Test glob patterns with ? (single character)."""
        assert matches_pattern("api-v1", "api-v?") is True
        assert matches_pattern("api-v2", "api-v?") is True
        assert matches_pattern("api-v10", "api-v?") is False
        assert matches_pattern("api-", "api-v?") is False
    
    def test_regex_pattern(self):
        """Test regex patterns (prefix with 'regex:')."""
        assert matches_pattern("api-v1", "regex:^api-v[0-9]+$") is True
        assert matches_pattern("api-v2", "regex:^api-v[0-9]+$") is True
        assert matches_pattern("api-v10", "regex:^api-v[0-9]+$") is True
        assert matches_pattern("api-vx", "regex:^api-v[0-9]+$") is False
        assert matches_pattern("web-v1", "regex:^api-v[0-9]+$") is False
    
    def test_regex_pattern_complex(self):
        """Test complex regex patterns."""
        # Match services ending with -prod or -staging
        pattern = "regex:^.*-(prod|staging)$"
        assert matches_pattern("api-prod", pattern) is True
        assert matches_pattern("web-staging", pattern) is True
        assert matches_pattern("api-dev", pattern) is False
        
        # Match versions like v1, v2, v10
        pattern = "regex:^v[0-9]+$"
        assert matches_pattern("v1", pattern) is True
        assert matches_pattern("v99", pattern) is True
        assert matches_pattern("v1.0", pattern) is False


class TestServiceEnvironmentMatching:
    """Test service and environment specific matching functions."""
    
    def test_matches_service_pattern(self):
        """Test service pattern matching."""
        assert matches_service_pattern("api-v1", None) is True
        assert matches_service_pattern("api-v1", "*") is True
        assert matches_service_pattern("api-v1", "api-*") is True
        assert matches_service_pattern("api-v1", "web-*") is False
    
    def test_matches_environment_pattern(self):
        """Test environment pattern matching."""
        assert matches_environment_pattern("production", None) is True
        assert matches_environment_pattern("production", "*") is True
        assert matches_environment_pattern("production", "prod*") is True
        assert matches_environment_pattern("production", "staging") is False
    
    def test_service_regex_patterns(self):
        """Test regex patterns for services."""
        # Match all API services with version numbers
        assert matches_service_pattern("api-v1", "regex:^api-v[0-9]+$") is True
        assert matches_service_pattern("api-v99", "regex:^api-v[0-9]+$") is True
        assert matches_service_pattern("web-v1", "regex:^api-v[0-9]+$") is False
    
    def test_environment_regex_patterns(self):
        """Test regex patterns for environments."""
        # Match prod, prod1, prod2, etc.
        pattern = "regex:^prod[0-9]*$"
        assert matches_environment_pattern("prod", pattern) is True
        assert matches_environment_pattern("prod1", pattern) is True
        assert matches_environment_pattern("production", pattern) is False


class TestPatternValidation:
    """Test pattern validation."""
    
    def test_validate_none_pattern(self):
        """Test that None pattern is valid."""
        is_valid, error = validate_pattern(None)
        assert is_valid is True
        assert error is None
    
    def test_validate_wildcard(self):
        """Test that * wildcard is valid."""
        is_valid, error = validate_pattern("*")
        assert is_valid is True
        assert error is None
    
    def test_validate_exact_match(self):
        """Test that exact strings are valid."""
        is_valid, error = validate_pattern("api-v1")
        assert is_valid is True
        assert error is None
    
    def test_validate_glob_pattern(self):
        """Test that glob patterns are valid."""
        is_valid, error = validate_pattern("api-*")
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_pattern("*-api")
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_pattern("api-?-service")
        assert is_valid is True
        assert error is None
    
    def test_validate_valid_regex(self):
        """Test that valid regex patterns are accepted."""
        is_valid, error = validate_pattern("regex:^api-v[0-9]+$")
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_pattern("regex:^.*-(prod|staging)$")
        assert is_valid is True
        assert error is None
    
    def test_validate_invalid_regex(self):
        """Test that invalid regex patterns are rejected."""
        is_valid, error = validate_pattern("regex:[invalid")
        assert is_valid is False
        assert error is not None
        assert "Invalid regex pattern" in error
        
        is_valid, error = validate_pattern("regex:(?P<incomplete")
        assert is_valid is False
        assert error is not None
        assert "Invalid regex pattern" in error


class TestRealWorldScenarios:
    """Test real-world usage scenarios."""
    
    def test_multi_region_services(self):
        """Test matching services across multiple regions."""
        pattern = "api-*-us-*"
        
        assert matches_pattern("api-v1-us-east", pattern) is True
        assert matches_pattern("api-v2-us-west", pattern) is True
        assert matches_pattern("api-v1-eu-west", pattern) is False
    
    def test_versioned_environments(self):
        """Test matching versioned environments."""
        pattern = "regex:^prod-v[0-9]+$"
        
        assert matches_pattern("prod-v1", pattern) is True
        assert matches_pattern("prod-v2", pattern) is True
        assert matches_pattern("prod", pattern) is False
        assert matches_pattern("staging-v1", pattern) is False
    
    def test_microservice_families(self):
        """Test matching microservice families."""
        # Match all payment-related services
        pattern = "payment-*"
        
        assert matches_pattern("payment-api", pattern) is True
        assert matches_pattern("payment-processor", pattern) is True
        assert matches_pattern("payment-gateway", pattern) is True
        assert matches_pattern("order-api", pattern) is False
    
    def test_environment_tiers(self):
        """Test matching environment tiers."""
        # Match all non-production environments
        pattern = "regex:^(dev|test|staging).*$"
        
        assert matches_pattern("dev", pattern) is True
        assert matches_pattern("test", pattern) is True
        assert matches_pattern("staging", pattern) is True
        assert matches_pattern("staging-v2", pattern) is True
        assert matches_pattern("production", pattern) is False
        assert matches_pattern("prod", pattern) is False
    
    def test_legacy_vs_modern_services(self):
        """Test separating legacy from modern services."""
        # Match only v2 and higher
        pattern = "regex:^.*-v([2-9]|[1-9][0-9]+)$"
        
        assert matches_pattern("api-v1", pattern) is False
        assert matches_pattern("api-v2", pattern) is True
        assert matches_pattern("api-v3", pattern) is True
        assert matches_pattern("api-v10", pattern) is True
