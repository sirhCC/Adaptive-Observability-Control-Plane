"""Constants and configuration values for the Control Plane.

Centralizes all magic numbers, rate limits, validation bounds, and other
configuration values for easy maintenance and documentation.
"""

import re
import os


# ============================================================================
# Buffer Limits
# ============================================================================

MAX_SIGNALS_PER_SERVICE = 10000
"""Maximum number of signals to keep per (service, environment) pair."""

MAX_POLICY_HISTORY = 100
"""Maximum number of policy versions to retain in history."""

WINDOW_MAX = 300
"""Maximum window size in seconds for signal aggregation."""


# ============================================================================
# Validation Limits
# ============================================================================

MAX_SERVICE_NAME_LEN = 64
"""Maximum length for service names."""

MAX_ENV_NAME_LEN = 32
"""Maximum length for environment names."""

VALID_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
"""Regex pattern for valid service/environment names."""

MAX_SIGNAL_ATTRS = 20
"""Maximum number of attributes allowed per signal."""

MAX_ATTR_KEY_LEN = 128
"""Maximum length for attribute keys."""

MAX_ATTR_VALUE_LEN = 1024
"""Maximum length for attribute values."""


# ============================================================================
# Request Validation Limits
# ============================================================================

# Signal ingestion limits
MIN_SIGNALS_BATCH = 1
"""Minimum signals in a batch request."""

MAX_SIGNALS_BATCH = 100
"""Maximum signals in a batch request."""

# Simulation limits
MIN_TEST_SIGNALS = 1
"""Minimum test signals for simulation."""

MAX_TEST_SIGNALS = 100
"""Maximum test signals for simulation."""

# Replay limits
MIN_REPLAY_SIGNALS = 1
"""Minimum signals for replay."""

MAX_REPLAY_SIGNALS = 100
"""Maximum signals for replay."""

# Comparison limits
MIN_COMPARE_POLICIES = 2
"""Minimum policies to compare."""

MAX_COMPARE_POLICIES = 5
"""Maximum policies to compare."""

MIN_COMPARE_SIGNALS = 1
"""Minimum signals for policy comparison."""

MAX_COMPARE_SIGNALS = 50
"""Maximum signals for policy comparison."""


# ============================================================================
# Rate Limits (format: "count/period")
# ============================================================================

RATE_LIMIT_GENERATE_KEY = "5/minute"
"""Rate limit for API key generation (very restrictive)."""

RATE_LIMIT_GET_POLICY = "20/minute"
"""Rate limit for retrieving policy."""

RATE_LIMIT_SET_POLICY = "10/minute"
"""Rate limit for updating policy (strict - critical operation)."""

RATE_LIMIT_VALIDATE_POLICY = "20/minute"
"""Rate limit for policy validation."""

RATE_LIMIT_EXPORT_POLICY = "20/minute"
"""Rate limit for policy export."""

RATE_LIMIT_IMPORT_POLICY = "10/minute"
"""Rate limit for policy import."""

RATE_LIMIT_TEMPLATES = "20/minute"
"""Rate limit for policy templates."""

RATE_LIMIT_SIMULATE = "20/minute"
"""Rate limit for policy simulation."""

RATE_LIMIT_REPLAY = "20/minute"
"""Rate limit for signal replay."""

RATE_LIMIT_COMPARE = "20/minute"
"""Rate limit for policy comparison."""

RATE_LIMIT_EXPORT_SIGNALS = "20/minute"
"""Rate limit for exporting signals."""

RATE_LIMIT_INGEST_SIGNAL = "100/minute"
"""Rate limit for signal ingestion (higher - frequent operation)."""

RATE_LIMIT_GET_CONFIG = "200/minute"
"""Rate limit for retrieving configuration (higher - frequent operation)."""


# ============================================================================
# CORS Configuration
# ============================================================================

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
"""Allowed CORS origins (comma-separated in env var)."""

CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"
"""Whether to allow credentials in CORS requests."""

CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "*").split(",")
"""Allowed HTTP methods for CORS."""

CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "*").split(",")
"""Allowed headers for CORS."""


# ============================================================================
# Shutdown Configuration
# ============================================================================

SHUTDOWN_TIMEOUT = int(os.getenv("SHUTDOWN_TIMEOUT", "30"))
"""Maximum time in seconds to wait for graceful shutdown."""


# ============================================================================
# Feature Flag Configuration
# ============================================================================

FF_PROVIDER = os.getenv("FF_PROVIDER", "static")
"""Feature flag provider: static, launchdarkly, splitio, custom."""

FF_CACHE_TTL = int(os.getenv("FF_CACHE_TTL", "60"))
"""Feature flag cache TTL in seconds."""


# ============================================================================
# Time/Date Formats
# ============================================================================

ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
"""Standard ISO 8601 timestamp format."""


# ============================================================================
# Log Levels
# ============================================================================

LOG_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
"""Log levels in order of severity for 'strictest' merge strategy."""


# ============================================================================
# Default Values
# ============================================================================

DEFAULT_LOG_LEVEL = "INFO"
"""Default log level when not specified."""

DEFAULT_TRACE_SAMPLE_RATE = 0.1
"""Default trace sampling rate (10%)."""

DEFAULT_METRICS_INTERVAL = 60
"""Default metrics collection interval in seconds."""

DEFAULT_AGGREGATION_WINDOW = 60
"""Default aggregation window in seconds."""
