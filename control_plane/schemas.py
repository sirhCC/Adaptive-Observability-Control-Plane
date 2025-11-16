"""Pydantic schemas for API requests and responses.

This module contains all Pydantic models used for:
- API request/response validation
- Policy configuration
- Signal ingestion
- Configuration output
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional
from enum import Enum
import re

from pydantic import BaseModel, Field, field_validator


# Constants for validation
MAX_SERVICE_NAME_LEN = 64
MAX_ENV_NAME_LEN = 32
VALID_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


class MergeStrategy(str, Enum):
    """Strategy for merging actions when multiple rules match."""
    LAST_WINS = "last_wins"  # Last matching rule wins (default, current behavior)
    MIN = "min"  # Choose minimum value (for sampling rates)
    MAX = "max"  # Choose maximum value (for sampling rates)
    STRICTEST = "strictest"  # Most verbose log level (DEBUG > INFO > WARN > ERROR)
    ADDITIVE = "additive"  # Combine all non-conflicting actions


class Condition(BaseModel):
    """Condition for rule evaluation.
    
    Supports multiple condition types:
    - metric: Compare aggregated metrics (latency, error rate, etc.)
    - error_rate: Compare error rate over time window
    - feature_flag: Evaluate feature flag value
    - always: Always match (unconditional rule)
    """
    kind: Literal["metric", "error_rate", "feature_flag", "time", "always"] = Field(
        description="Type of condition to evaluate"
    )
    op: Literal[">", ">=", "<", "<=", "==", "!=", "in", "contains", "always"] = Field(
        description="Comparison operator"
    )
    key: Optional[str] = None
    value: Optional[float] = None
    window_s: Optional[int] = Field(default=None, description="Rolling window seconds for aggregations")


class Action(BaseModel):
    """Configuration actions to apply when rule matches.
    
    Defines observability settings that should be applied:
    - log_level: Logging verbosity (DEBUG, INFO, WARN, ERROR)
    - trace_sample_rate: Distributed tracing sampling rate (0.0-1.0)
    - metric_period_s: Metrics collection interval in seconds
    """
    log_level: Optional[str] = None  # DEBUG|INFO|WARN|ERROR
    trace_sample_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    metric_period_s: Optional[int] = Field(default=None, ge=1)


class Rule(BaseModel):
    """Policy rule with conditions and actions.
    
    Rules are evaluated in priority order (lower priority number = evaluated first).
    When conditions match, actions are applied using the specified merge strategy.
    """
    id: str
    description: Optional[str] = None
    service: Optional[str] = None  # target service or pattern (* for all, api-* for glob, regex:^api.*$ for regex)
    environment: Optional[str] = None  # environment or pattern (prod, staging, *, prod-*, regex:^prod.*$)
    priority: int = 100  # lower runs first
    conditions: List[Condition] = Field(default_factory=list)
    actions: Action
    enabled: bool = True
    merge_strategy: Optional[MergeStrategy] = Field(
        default=None,
        description="Strategy for merging this rule's actions with others. If None, uses policy-level strategy."
    )


class Policy(BaseModel):
    """Complete policy configuration with multiple rules.
    
    A policy contains:
    - Unique identifier and description
    - List of rules evaluated in priority order
    - Default merge strategy for handling overlapping rules
    """
    id: str
    description: Optional[str] = None
    rules: List[Rule] = Field(default_factory=list)
    merge_strategy: MergeStrategy = Field(
        default=MergeStrategy.LAST_WINS,
        description="Default merge strategy for all rules unless overridden at rule level"
    )


class Signal(BaseModel):
    """Telemetry signal from an observability agent.
    
    Contains metrics and metadata about a service's behavior:
    - Latency measurements
    - Error status
    - Custom attributes
    - Timestamp (server or client-provided)
    """
    service: str
    environment: str
    ts: datetime
    latency_ms: Optional[float] = None
    error: Optional[bool] = None
    attrs: Dict[str, str] = Field(default_factory=dict)


class SignalIn(BaseModel):
    """Input model for signal ingestion with validation.
    
    Validates incoming signals from agents with strict limits:
    - Service/environment name length and character restrictions
    - Latency bounds (0-1M ms)
    - Attribute limits (max 50 pairs, key/value size limits)
    - Optional timestamp for replay/debugging
    """
    service: str = Field(..., min_length=1, max_length=MAX_SERVICE_NAME_LEN)
    environment: str = Field(..., min_length=1, max_length=MAX_ENV_NAME_LEN)
    latency_ms: Optional[float] = Field(None, ge=0.0, le=1_000_000.0)
    error: Optional[bool] = None
    attrs: Dict[str, str] = Field(default_factory=dict, max_length=50)
    timestamp: Optional[datetime] = Field(
        None,
        description="Client-provided timestamp. If not provided, server time is used. Useful for replay/debugging."
    )
    
    @field_validator('service', 'environment')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate service and environment names contain only allowed characters."""
        if not VALID_NAME_PATTERN.match(v):
            raise ValueError(f"Name must contain only alphanumeric, underscore, and hyphen characters: {v}")
        return v
    
    @field_validator('attrs')
    @classmethod
    def validate_attrs(cls, v: Dict[str, str]) -> Dict[str, str]:
        """Validate attribute key/value sizes."""
        for key, value in v.items():
            if len(key) > 128 or len(value) > 1024:
                raise ValueError("Attribute keys must be ≤128 chars, values ≤1024 chars")
        return v
    
    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Validate timestamp is within reasonable bounds (7 days past, 1 day future)."""
        if v is not None:
            now = datetime.now(timezone.utc)
            max_past = now - timedelta(days=7)
            max_future = now + timedelta(days=1)
            
            # Handle timezone-naive timestamps
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            
            if v < max_past:
                raise ValueError("Timestamp cannot be more than 7 days in the past")
            if v > max_future:
                raise ValueError("Timestamp cannot be more than 1 day in the future")
        
        return v


class EffectiveConfig(BaseModel):
    """Effective observability configuration after rule evaluation.
    
    This is what gets returned to agents after evaluating all matching rules.
    Contains the merged configuration from all applicable rules:
    - log_level: Current logging level to use
    - trace_sample_rate: Sampling rate for distributed traces
    - metric_period_s: How often to collect/send metrics
    """
    service: str
    environment: str
    log_level: str = "INFO"
    trace_sample_rate: float = 0.1
    metric_period_s: int = 60


class PolicyVersion(BaseModel):
    """Historical policy version for time-travel debugging.
    
    Tracks policy changes over time with:
    - Complete policy configuration snapshot
    - When it was applied
    - Who applied it (admin identifier)
    
    Used for:
    - Auditing policy changes
    - Replaying signals with historical policies
    - Understanding "what would have happened" scenarios
    """
    policy: Policy
    applied_at: datetime
    applied_by: Optional[str] = None


# Request/Response Models

class UpsertPolicy(BaseModel):
    """Request model for updating policy configuration."""
    policy: Policy


class SimulateRequest(BaseModel):
    """Request to simulate policy evaluation with test signals."""
    policy: Policy
    test_signals: List[SignalIn] = Field(
        ..., 
        min_length=1, 
        max_length=100,
        description="Test signals to evaluate (1-100)"
    )


class ReplayRequest(BaseModel):
    """Request to replay historical signals with a policy."""
    signals: List[SignalIn] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Signals to replay (1-100). Must include timestamps."
    )
    policy_timestamp: Optional[str] = Field(
        None,
        description="ISO 8601 timestamp. Use policy that was active at this time. If not provided, uses current policy."
    )


class CompareRequest(BaseModel):
    """Request to compare how different policies would handle the same signals."""
    signals: List[SignalIn] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Signals to analyze (1-50)"
    )
    compare_policies: List[str] = Field(
        ...,
        min_length=2,
        max_length=5,
        description="ISO 8601 timestamps of policies to compare (2-5). Use 'current' for current policy."
    )
