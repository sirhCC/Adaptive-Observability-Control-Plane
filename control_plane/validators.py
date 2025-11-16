"""Validation helper functions for Pydantic models.

This module provides reusable validation functions that can be used across
multiple Pydantic models to ensure consistent validation logic.

Common validations:
- Name validation (service, environment names)
- Attribute validation (key/value size limits)
- Timestamp validation (reasonable time bounds)
- Pattern validation (regex patterns for IDs, names)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from control_plane import constants


def validate_name_pattern(name: str, field_name: str = "name") -> str:
    """Validate that a name contains only allowed characters.
    
    Allowed characters: alphanumeric, underscore, hyphen
    Used for service names, environment names, rule IDs, etc.
    
    Args:
        name: The name to validate
        field_name: Name of the field being validated (for error messages)
        
    Returns:
        The validated name (unchanged if valid)
        
    Raises:
        ValueError: If name contains invalid characters
        
    Example:
        >>> validate_name_pattern("my-service_123")
        "my-service_123"
        >>> validate_name_pattern("my service")  # raises ValueError
    """
    if not constants.VALID_NAME_PATTERN.match(name):
        raise ValueError(
            f"{field_name} must contain only alphanumeric, underscore, "
            f"and hyphen characters: {name}"
        )
    return name


def validate_service_name(service: str) -> str:
    """Validate a service name.
    
    Args:
        service: Service name to validate
        
    Returns:
        The validated service name
        
    Raises:
        ValueError: If service name is invalid
    """
    return validate_name_pattern(service, "Service name")


def validate_environment_name(environment: str) -> str:
    """Validate an environment name.
    
    Args:
        environment: Environment name to validate
        
    Returns:
        The validated environment name
        
    Raises:
        ValueError: If environment name is invalid
    """
    return validate_name_pattern(environment, "Environment name")


def validate_rule_id(rule_id: str) -> str:
    """Validate a rule ID.
    
    Args:
        rule_id: Rule ID to validate
        
    Returns:
        The validated rule ID
        
    Raises:
        ValueError: If rule ID is invalid
    """
    return validate_name_pattern(rule_id, "Rule ID")


def validate_attributes(attrs: Dict[str, str]) -> Dict[str, str]:
    """Validate attribute dictionary key/value sizes.
    
    Enforces limits:
    - Keys: Maximum 128 characters
    - Values: Maximum 1024 characters
    
    Args:
        attrs: Dictionary of attributes to validate
        
    Returns:
        The validated attributes dictionary (unchanged if valid)
        
    Raises:
        ValueError: If any key or value exceeds size limits
        
    Example:
        >>> validate_attributes({"region": "us-west-2"})
        {"region": "us-west-2"}
        >>> validate_attributes({"key": "x" * 2000})  # raises ValueError
    """
    for key, value in attrs.items():
        if len(key) > constants.MAX_ATTR_KEY_LEN:
            raise ValueError(
                f"Attribute key '{key}' exceeds maximum length "
                f"({constants.MAX_ATTR_KEY_LEN} chars)"
            )
        if len(value) > constants.MAX_ATTR_VALUE_LEN:
            raise ValueError(
                f"Attribute value for '{key}' exceeds maximum length "
                f"({constants.MAX_ATTR_VALUE_LEN} chars)"
            )
    return attrs


def validate_timestamp(
    timestamp: Optional[datetime],
    max_past_days: int = 7,
    max_future_days: int = 1
) -> Optional[datetime]:
    """Validate timestamp is within reasonable time bounds.
    
    Ensures timestamps are:
    - Not too far in the past (prevents replay attacks, stale data)
    - Not too far in the future (prevents clock skew issues)
    - Timezone-aware (converts timezone-naive to UTC)
    
    Args:
        timestamp: The timestamp to validate (None is allowed)
        max_past_days: Maximum days in the past (default: 7)
        max_future_days: Maximum days in the future (default: 1)
        
    Returns:
        The validated timestamp (with timezone if needed), or None
        
    Raises:
        ValueError: If timestamp is outside acceptable bounds
        
    Example:
        >>> from datetime import datetime, timezone
        >>> now = datetime.now(timezone.utc)
        >>> validate_timestamp(now)  # Returns now
        >>> validate_timestamp(None)  # Returns None
    """
    if timestamp is None:
        return None
    
    now = datetime.now(timezone.utc)
    max_past = now - timedelta(days=max_past_days)
    max_future = now + timedelta(days=max_future_days)
    
    # Handle timezone-naive timestamps by assuming UTC
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    if timestamp < max_past:
        raise ValueError(
            f"Timestamp cannot be more than {max_past_days} days in the past"
        )
    if timestamp > max_future:
        raise ValueError(
            f"Timestamp cannot be more than {max_future_days} day(s) in the future"
        )
    
    return timestamp


def validate_latency(latency_ms: Optional[float]) -> Optional[float]:
    """Validate latency value is reasonable.
    
    Args:
        latency_ms: Latency in milliseconds (None is allowed)
        
    Returns:
        The validated latency value
        
    Raises:
        ValueError: If latency is negative or unreasonably high
    """
    if latency_ms is None:
        return None
    
    if latency_ms < 0:
        raise ValueError("Latency cannot be negative")
    
    # 5 minutes = 300,000 ms seems like a reasonable upper bound
    if latency_ms > 300_000:
        raise ValueError(
            f"Latency value suspiciously high: {latency_ms}ms (max: 300000ms)"
        )
    
    return latency_ms


def validate_sample_rate(rate: float) -> float:
    """Validate trace sample rate is between 0.0 and 1.0.
    
    Args:
        rate: Sample rate to validate
        
    Returns:
        The validated sample rate
        
    Raises:
        ValueError: If rate is outside [0.0, 1.0] range
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"Sample rate must be between 0.0 and 1.0, got: {rate}")
    return rate


def validate_metric_period(period_s: int) -> int:
    """Validate metric collection period is reasonable.
    
    Args:
        period_s: Period in seconds
        
    Returns:
        The validated period
        
    Raises:
        ValueError: If period is too small or too large
    """
    if period_s < 1:
        raise ValueError("Metric period must be at least 1 second")
    if period_s > 3600:
        raise ValueError("Metric period cannot exceed 1 hour (3600 seconds)")
    return period_s


def validate_log_level(level: str) -> str:
    """Validate log level is one of the allowed values.
    
    Args:
        level: Log level to validate
        
    Returns:
        The validated log level (uppercase)
        
    Raises:
        ValueError: If log level is not recognized
    """
    allowed_levels = {"TRACE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}
    level_upper = level.upper()
    
    if level_upper not in allowed_levels:
        raise ValueError(
            f"Invalid log level '{level}'. "
            f"Must be one of: {', '.join(sorted(allowed_levels))}"
        )
    
    # Normalize WARNING to WARN
    if level_upper == "WARNING":
        return "WARN"
    
    return level_upper


def validate_priority(priority: int) -> int:
    """Validate rule priority is within reasonable bounds.
    
    Args:
        priority: Rule priority to validate
        
    Returns:
        The validated priority
        
    Raises:
        ValueError: If priority is outside reasonable range
    """
    if not -1000 <= priority <= 1000:
        raise ValueError("Rule priority must be between -1000 and 1000")
    return priority
