"""
Service and environment pattern matching utilities.

Supports wildcards, glob patterns, and regex for flexible service/environment matching.
"""
import re
import fnmatch
from typing import Optional
from loguru import logger


def matches_pattern(value: str, pattern: Optional[str]) -> bool:
    """
    Check if a value matches a pattern.
    
    Supports:
    - None/empty pattern: Matches everything
    - "*": Wildcard matches everything
    - "prefix-*": Glob pattern (e.g., "api-*" matches "api-v1", "api-v2")
    - "regex:^api-.*$": Regular expression (prefix with "regex:")
    - Exact match: Simple string comparison
    
    Args:
        value: The actual service or environment name
        pattern: The pattern to match against (from rule)
        
    Returns:
        True if the value matches the pattern, False otherwise
        
    Examples:
        >>> matches_pattern("api-v1", None)
        True
        >>> matches_pattern("api-v1", "*")
        True
        >>> matches_pattern("api-v1", "api-*")
        True
        >>> matches_pattern("api-v1", "regex:^api-v[0-9]+$")
        True
        >>> matches_pattern("api-v1", "web-*")
        False
    """
    # No pattern specified - matches everything
    if not pattern:
        return True
    
    # Wildcard - matches everything
    if pattern == "*":
        return True
    
    # Regex pattern (prefix with "regex:")
    if pattern.startswith("regex:"):
        regex_pattern = pattern[6:]  # Remove "regex:" prefix
        try:
            return bool(re.match(regex_pattern, value))
        except re.error as e:
            logger.warning(f"Invalid regex pattern '{regex_pattern}': {e}")
            return False
    
    # Glob pattern (contains * or ?)
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(value, pattern)
    
    # Exact match
    return value == pattern


def matches_service_pattern(service: str, rule_service: Optional[str]) -> bool:
    """
    Check if a service name matches a rule's service pattern.
    
    Args:
        service: The actual service name from the signal
        rule_service: The service pattern from the rule
        
    Returns:
        True if the service matches the rule pattern
    """
    return matches_pattern(service, rule_service)


def matches_environment_pattern(environment: str, rule_environment: Optional[str]) -> bool:
    """
    Check if an environment name matches a rule's environment pattern.
    
    Args:
        environment: The actual environment name from the signal
        rule_environment: The environment pattern from the rule
        
    Returns:
        True if the environment matches the rule pattern
    """
    return matches_pattern(environment, rule_environment)


def validate_pattern(pattern: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Validate a service or environment pattern.
    
    Args:
        pattern: The pattern to validate
        
    Returns:
        (is_valid, error_message) tuple
        
    Examples:
        >>> validate_pattern("api-*")
        (True, None)
        >>> validate_pattern("regex:^api-[0-9]+$")
        (True, None)
        >>> validate_pattern("regex:[invalid")
        (False, "Invalid regex pattern: ...")
    """
    if not pattern:
        return True, None
    
    # Wildcard is always valid
    if pattern == "*":
        return True, None
    
    # Validate regex patterns
    if pattern.startswith("regex:"):
        regex_pattern = pattern[6:]
        try:
            re.compile(regex_pattern)
            return True, None
        except re.error as e:
            return False, f"Invalid regex pattern: {e}"
    
    # Glob patterns are always valid (fnmatch doesn't raise errors)
    return True, None
