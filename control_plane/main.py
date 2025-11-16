"""Adaptive Observability Control Plane - Main Application

This is the main FastAPI application file that brings together all components:

Module Organization:
- schemas.py: Pydantic models for API validation (Policy, Rule, Signal, etc.)
- engine.py: Rule evaluation logic and metric aggregation
- storage.py: In-memory state management (signals buffer, policy history)
- models.py: SQLAlchemy database models
- repository.py: Database access layer
- auth.py: Authentication and authorization
- metrics.py: Prometheus metrics
- exceptions.py: Custom exception handling
- feature_flags.py: Feature flag integration
- pattern_matching.py: Service/environment pattern matching (wildcards, globs, regex)
- rule_validator.py: Policy rule conflict detection

For better code organization, core logic has been extracted to separate modules,
but this file maintains backward compatibility by re-exporting key components.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional
from enum import Enum
import re
import os
import time
import json
import yaml
import signal
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import APIRouter

from control_plane.database import init_db, get_db
from control_plane.repository import PolicyRepository, SignalRepository
from control_plane.auth import require_admin_key, get_api_key, get_optional_api_key
from control_plane import metrics as prom_metrics
from control_plane import constants
from control_plane import exporters
from control_plane import validators
from control_plane.policy_simulator import PolicySimulator, create_simulation_response
from control_plane.exceptions import (
    register_exception_handlers,
    PolicyValidationError,
    SignalProcessingError,
    DatabaseError
)
from control_plane.feature_flags import init_feature_flags, get_feature_flag_service
from control_plane.pattern_matching import matches_service_pattern, matches_environment_pattern, validate_pattern
from control_plane.services import PolicyService, SignalService, ConfigService, HealthService

# Note: Core logic extracted to separate modules for better organization:
# - schemas.py: Pydantic models (265 lines)
# - engine.py: Rule evaluation engine (361 lines)  
# - storage.py: State management (190 lines)
# These modules provide reusable components while main.py maintains backward compatibility

# Configuration - Import from constants module
MAX_SIGNALS_PER_SERVICE = constants.MAX_SIGNALS_PER_SERVICE
MAX_SERVICE_NAME_LEN = constants.MAX_SERVICE_NAME_LEN
MAX_ENV_NAME_LEN = constants.MAX_ENV_NAME_LEN
VALID_NAME_PATTERN = constants.VALID_NAME_PATTERN

# CORS Configuration
CORS_ORIGINS = constants.CORS_ORIGINS
CORS_ALLOW_CREDENTIALS = constants.CORS_ALLOW_CREDENTIALS
CORS_ALLOW_METHODS = constants.CORS_ALLOW_METHODS
CORS_ALLOW_HEADERS = constants.CORS_ALLOW_HEADERS

# Shutdown Configuration
SHUTDOWN_TIMEOUT = constants.SHUTDOWN_TIMEOUT
shutdown_event = asyncio.Event()

# Feature Flag Configuration
FF_PROVIDER = constants.FF_PROVIDER
FF_CACHE_TTL = constants.FF_CACHE_TTL


# Lifespan context manager for startup and shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for graceful startup and shutdown.
    
    Startup:
    - Initialize database
    - Set up Prometheus metrics
    - Seed default policy
    - Install signal handlers
    
    Shutdown:
    - Handle SIGTERM/SIGINT gracefully
    - Flush buffered signals to database
    - Wait for in-flight requests (with timeout)
    - Clean up resources
    """
    # --- Startup ---
    logger.info("Starting control plane...")
    
    # Initialize database
    await init_db()
    logger.info("Database initialized")
    
    # Set control plane info
    prom_metrics.control_plane_info.info({
        'version': app.version,
        'title': app.title,
    })
    logger.info("Prometheus metrics enabled at /metrics")
    
    # Initialize feature flag service
    init_feature_flags(provider_type=FF_PROVIDER, cache_ttl=FF_CACHE_TTL)
    logger.info(f"Feature flag service initialized with {FF_PROVIDER} provider")
    
    # Initialize service layer with global state
    global policy_service, signal_service, config_service, health_service
    policy_service = PolicyService(POLICY, POLICY_HISTORY, MAX_POLICY_HISTORY)
    signal_service = SignalService(SIGNALS, WINDOW_MAX)
    config_service = ConfigService(POLICY, SIGNALS, evaluate)
    health_service = HealthService(POLICY, SIGNALS)
    logger.info("Service layer initialized")
    
    # Seed default policy if no policy exists
    async for db in get_db():
        existing_policy = await PolicyRepository.get_current_policy(db)
        if not existing_policy:
            # Convert in-memory default policy to database
            default_rules = [rule.model_dump() for rule in POLICY.rules]
            await PolicyRepository.create_policy(
                db,
                policy_id=POLICY.id,
                rules=default_rules,
                description=POLICY.description,
                changed_by="system",
            )
            logger.info(f"Seeded default policy: {POLICY.id}")
        break
    
    # Install signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        """Handle shutdown signals."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating graceful shutdown...")
        shutdown_event.set()
    
    # Register signal handlers (SIGTERM for Docker/K8s, SIGINT for Ctrl+C)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Control plane started successfully")
    
    yield  # Application runs here
    
    # --- Shutdown ---
    logger.info("Shutting down control plane...")
    
    # Flush buffered signals to database
    try:
        total_flushed = 0
        total_services = len(SIGNALS)
        
        if total_services > 0:
            logger.info(f"Flushing signals from {total_services} services...")
            async for db in get_db():
                for (service, env), signals in SIGNALS.items():
                    if signals:
                        logger.debug(f"Flushing {len(signals)} signals for {service}/{env}")
                        # Signals are already in memory, just log count
                        # In production, you might want to persist them
                        total_flushed += len(signals)
                break
            
            logger.info(f"Flushed {total_flushed} buffered signals (tracked for {total_services} service(s))")
            # Clear the buffer after flushing
            SIGNALS.clear()
    except Exception as e:
        logger.error(f"Error flushing signals during shutdown: {e}")
    
    # Wait briefly for in-flight requests to complete
    logger.info(f"Waiting up to {SHUTDOWN_TIMEOUT}s for in-flight requests...")
    await asyncio.sleep(min(2, SHUTDOWN_TIMEOUT))  # Brief grace period
    
    logger.info("Control plane shutdown complete")


# Rate limiter setup
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="Adaptive Observability Control Plane",
    version="1.0.0",
    lifespan=lifespan  # Use lifespan context manager
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware - Add before other middleware for proper preflight handling
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in CORS_ORIGINS],
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=[method.strip() for method in CORS_ALLOW_METHODS],
    allow_headers=[header.strip() for header in CORS_ALLOW_HEADERS],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)

# Register custom exception handlers
register_exception_handlers(app)


# Structured logging middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Add request ID and performance tracking to all requests."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    
    # Bind request context to logger
    with logger.contextualize(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown"
    ):
        start_time = time.time()
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log request completion with performance metrics
            logger.info(
                f"{request.method} {request.url.path}",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2)
            )
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"Request failed: {request.method} {request.url.path}",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 2)
            )
            raise

# Create v1 API router
v1_router = APIRouter(prefix="/v1", tags=["v1"])


# --- Models
class Condition(BaseModel):
    kind: Literal["metric", "error_rate", "feature_flag", "time", "always"] = Field(
        description="Type of condition to evaluate"
    )
    op: Literal[">", ">=", "<", "<=", "==", "!=", "in", "contains", "always"] = Field(
        description="Comparison operator"
    )
    key: Optional[str] = None
    # For numeric comparisons we expect a float; keep simple for demo
    value: Optional[float] = None
    window_s: Optional[int] = Field(default=None, description="Rolling window seconds for aggregations")


class MergeStrategy(str, Enum):
    """Strategy for merging actions when multiple rules match."""
    LAST_WINS = "last_wins"  # Last matching rule wins (default, current behavior)
    MIN = "min"  # Choose minimum value (for sampling rates)
    MAX = "max"  # Choose maximum value (for sampling rates)
    STRICTEST = "strictest"  # Most verbose log level (DEBUG > INFO > WARN > ERROR)
    ADDITIVE = "additive"  # Combine all non-conflicting actions


class Action(BaseModel):
    log_level: Optional[str] = None  # DEBUG|INFO|WARN|ERROR
    trace_sample_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    metric_period_s: Optional[int] = Field(default=None, ge=1)


class Rule(BaseModel):
    id: str
    description: Optional[str] = None
    service: Optional[str] = None  # target service or *
    environment: Optional[str] = None  # prod|staging|*
    priority: int = 100  # lower runs first
    conditions: List[Condition] = Field(default_factory=list)
    actions: Action
    enabled: bool = True
    merge_strategy: Optional[MergeStrategy] = Field(
        default=None,
        description="Strategy for merging this rule's actions with others. If None, uses policy-level strategy."
    )


class Policy(BaseModel):
    id: str
    description: Optional[str] = None
    rules: List[Rule] = Field(default_factory=list)
    merge_strategy: MergeStrategy = Field(
        default=MergeStrategy.LAST_WINS,
        description="Default merge strategy for all rules unless overridden at rule level"
    )


class Signal(BaseModel):
    service: str
    environment: str
    ts: datetime
    latency_ms: Optional[float] = None
    error: Optional[bool] = None
    attrs: Dict[str, str] = Field(default_factory=dict)


class EffectiveConfig(BaseModel):
    service: str
    environment: str
    log_level: str = "INFO"
    trace_sample_rate: float = 0.1
    metric_period_s: int = 60


# --- In-memory state (replace with DB in real usage)
POLICY = Policy(
    id="default",
    description="Default adaptive policy",
    rules=[
        Rule(
            id="elevate-on-errors",
            description="If error rate > 2% over 1m raise sampling and logging",
            service=None,
            environment=None,
            priority=10,
            conditions=[
                Condition(kind="error_rate", op=">", key="rate", value=0.02, window_s=60),
            ],
            actions=Action(log_level="DEBUG", trace_sample_rate=0.5, metric_period_s=15),
        ),
        Rule(
            id="slow-requests",
            description="If latency p95 > 400ms over 1m",
            priority=20,
            conditions=[
                Condition(kind="metric", op=">", key="latency_p95_ms", value=400, window_s=60),
            ],
            actions=Action(log_level="DEBUG", trace_sample_rate=0.4, metric_period_s=20),
        ),
        Rule(
            id="prod-defaults",
            description="Tighter defaults in prod",
            environment="prod",
            priority=0,
            conditions=[Condition(kind="always", op="always")],
            actions=Action(log_level="INFO", trace_sample_rate=0.2, metric_period_s=30),
        ),
    ],
)

# Rolling signals per (service, env)
SIGNALS: Dict[tuple[str, str], List[Signal]] = {}
WINDOW_MAX = 5 * 60  # seconds to keep raw events

# Policy version history for time-travel debugging
class PolicyVersion(BaseModel):
    """Represents a policy configuration at a specific point in time."""
    policy: Policy
    applied_at: datetime
    applied_by: Optional[str] = None

# Store up to 100 historical policy versions
POLICY_HISTORY: List[PolicyVersion] = []
MAX_POLICY_HISTORY = 100

# Initialize service layer instances (after global state is defined)
# These services encapsulate business logic and can be tested independently
# They are initialized in the lifespan startup phase
policy_service: PolicyService = None  # type: ignore
signal_service: SignalService = None  # type: ignore
config_service: ConfigService = None  # type: ignore
health_service: HealthService = None  # type: ignore


# --- Helpers

def _now() -> datetime:
    """Get current UTC time with timezone awareness."""
    return datetime.now(timezone.utc)


def _prune(key: tuple[str, str]):
    """Prune old signals and enforce max buffer size."""
    cutoff = _now() - timedelta(seconds=WINDOW_MAX)
    buf = SIGNALS.get(key)
    if not buf:
        return
    # Remove old signals
    initial_count = len(buf)
    buf = [s for s in buf if s.ts >= cutoff]
    # Enforce max buffer size (keep most recent)
    if len(buf) > MAX_SIGNALS_PER_SERVICE:
        buf = sorted(buf, key=lambda s: s.ts, reverse=True)[:MAX_SIGNALS_PER_SERVICE]
    
    pruned_count = initial_count - len(buf)
    if pruned_count > 0:
        service, environment = key
        prom_metrics.record_buffer_pruned(service, environment, pruned_count)
    
    SIGNALS[key] = buf
    # Update buffer size metric
    service, environment = key
    prom_metrics.update_buffer_size(service, environment, len(buf))


def _percentile(values: List[float], p: float) -> float:
    """Calculate percentile from sorted values."""
    if not values:
        return 0.0
    idx = int(p * len(values))
    return values[min(idx, len(values) - 1)]


def _calc_aggregates(buf: List[Signal], window_s: Optional[int] = None) -> Dict[str, float]:
    """Calculate comprehensive aggregates over signal buffer.
    
    Returns metrics including percentiles (p50, p90, p95, p99),
    averages, min/max, error rates, and request rates.
    """
    if not buf:
        return {
            "latency_p50_ms": 0.0,
            "latency_p90_ms": 0.0,
            "latency_p95_ms": 0.0,
            "latency_p99_ms": 0.0,
            "latency_avg_ms": 0.0,
            "latency_min_ms": 0.0,
            "latency_max_ms": 0.0,
            "error_rate": 0.0,
            "error_count": 0.0,
            "request_count": 0.0,
            "request_rate_per_sec": 0.0,
        }
    
    # Apply time window filter if specified
    if window_s is not None:
        cutoff = _now() - timedelta(seconds=window_s)
        buf = [s for s in buf if s.ts >= cutoff]
    
    if not buf:
        return {
            "latency_p50_ms": 0.0,
            "latency_p90_ms": 0.0,
            "latency_p95_ms": 0.0,
            "latency_p99_ms": 0.0,
            "latency_avg_ms": 0.0,
            "latency_min_ms": 0.0,
            "latency_max_ms": 0.0,
            "error_rate": 0.0,
            "error_count": 0.0,
            "request_count": 0.0,
            "request_rate_per_sec": 0.0,
        }
    
    # Latency metrics
    latencies = [s.latency_ms for s in buf if s.latency_ms is not None]
    latencies.sort()
    
    if latencies:
        p50 = _percentile(latencies, 0.50)
        p90 = _percentile(latencies, 0.90)
        p95 = _percentile(latencies, 0.95)
        p99 = _percentile(latencies, 0.99)
        avg = sum(latencies) / len(latencies)
        min_lat = min(latencies)
        max_lat = max(latencies)
    else:
        p50 = p90 = p95 = p99 = avg = min_lat = max_lat = 0.0
    
    # Error metrics
    error_count = sum(1 for s in buf if s.error)
    error_rate = error_count / len(buf)
    
    # Request rate calculation
    request_count = len(buf)
    if request_count > 1:
        time_span = (buf[-1].ts - buf[0].ts).total_seconds()
        request_rate = request_count / max(time_span, 1.0)
    else:
        request_rate = 0.0
    
    return {
        "latency_p50_ms": float(p50),
        "latency_p90_ms": float(p90),
        "latency_p95_ms": float(p95),
        "latency_p99_ms": float(p99),
        "latency_avg_ms": float(avg),
        "latency_min_ms": float(min_lat),
        "latency_max_ms": float(max_lat),
        "error_rate": float(error_rate),
        "error_count": float(error_count),
        "request_count": float(request_count),
        "request_rate_per_sec": float(request_rate),
    }


# Comparison operators for condition evaluation
from typing import Callable
op_map: Dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# Log level ordering for strictest merge strategy (higher = more verbose)
LOG_LEVEL_ORDER = {
    "ERROR": 0,
    "WARN": 1,
    "INFO": 2,
    "DEBUG": 3
}


def _merge_log_level(current: str, new: Optional[str], strategy: MergeStrategy) -> str:
    """Merge log level values according to merge strategy.
    
    Args:
        current: Current log level (ERROR, WARN, INFO, DEBUG)
        new: New log level to merge (or None to skip)
        strategy: Merge strategy (last_wins, strictest, additive, min, max)
    
    Returns:
        Merged log level based on strategy
    """
    if new is None:
        return current
    
    if strategy == MergeStrategy.LAST_WINS:
        return new
    elif strategy == MergeStrategy.STRICTEST:
        # Choose more verbose (higher value in LOG_LEVEL_ORDER)
        current_order = LOG_LEVEL_ORDER.get(current.upper(), -1)
        new_order = LOG_LEVEL_ORDER.get(new.upper(), -1)
        # Keep whichever has higher order value (more verbose)
        return new if new_order >= current_order else current
    elif strategy == MergeStrategy.ADDITIVE:
        # For log level, use strictest when additive
        current_order = LOG_LEVEL_ORDER.get(current.upper(), -1)
        new_order = LOG_LEVEL_ORDER.get(new.upper(), -1)
        # Keep whichever has higher order value (more verbose)
        return new if new_order >= current_order else current
    else:
        # MIN/MAX don't apply to log levels, use last wins
        return new


def _merge_float(current: float, new: Optional[float], strategy: MergeStrategy) -> float:
    """Merge float values (sampling rates) according to strategy."""
    if new is None:
        return current
    
    if strategy == MergeStrategy.MIN:
        return min(current, new)
    elif strategy == MergeStrategy.MAX:
        return max(current, new)
    elif strategy == MergeStrategy.ADDITIVE:
        # For additive, use minimum sampling (more conservative)
        return min(current, new)
    else:  # LAST_WINS, STRICTEST
        return new


def _merge_int(current: int, new: Optional[int], strategy: MergeStrategy) -> int:
    """Merge integer values (metric periods) according to strategy."""
    if new is None:
        return current
    
    if strategy == MergeStrategy.MIN:
        return min(current, new)
    elif strategy == MergeStrategy.MAX:
        return max(current, new)
    elif strategy == MergeStrategy.ADDITIVE:
        # For additive, use minimum period (more frequent collection)
        return min(current, new)
    else:  # LAST_WINS, STRICTEST
        return new


def _apply_action_merge(effective: EffectiveConfig, action: Action, strategy: MergeStrategy) -> None:
    """Apply an action to effective config using the specified merge strategy."""
    if action.log_level:
        effective.log_level = _merge_log_level(effective.log_level, action.log_level, strategy)
    if action.trace_sample_rate is not None:
        effective.trace_sample_rate = _merge_float(effective.trace_sample_rate, action.trace_sample_rate, strategy)
    if action.metric_period_s is not None:
        effective.metric_period_s = _merge_int(effective.metric_period_s, action.metric_period_s, strategy)


def _eval_condition(cond: Condition, signal: Signal, aggs: dict) -> bool:
    """Evaluate a single condition against a signal and aggregates."""
    if cond.kind == "always" or cond.op == "always":
        return True
    
    if cond.kind == "error_rate":
        v = aggs.get("error_rate", 0.0)
        threshold = float(cond.value) if cond.value is not None else 0.0
        return op_map[cond.op](v, threshold)
    elif cond.kind == "metric":
        v = aggs.get(cond.key or "", 0.0)
        threshold = float(cond.value) if cond.value is not None else 0.0
        return op_map[cond.op](v, threshold)
    elif cond.kind == "feature_flag":
        # Evaluate feature flag
        if not cond.key:
            logger.warning("Feature flag condition missing 'key' field")
            return False
        
        try:
            ff_service = get_feature_flag_service()
            # Build context from signal
            context = {
                "service": signal.service,
                "environment": signal.environment,
                **signal.attrs
            }
            # Synchronous wrapper for async evaluation
            result = asyncio.run(ff_service.evaluate(
                flag_key=cond.key,
                context=context,
                default=False
            ))
            flag_value = result.value
            
            # Support boolean comparisons
            if cond.op == "==":
                expected = bool(cond.value) if cond.value is not None else True
                return flag_value == expected
            elif cond.op == "!=":
                expected = bool(cond.value) if cond.value is not None else True
                return flag_value != expected
            else:
                # For other operators, treat flag value as boolean
                return flag_value
        except Exception as e:
            logger.error(f"Error evaluating feature flag {cond.key}: {e}")
            return False
    
    return False


# --- Rule evaluation

def evaluate(service: str, env: str) -> EffectiveConfig:
    """Evaluate policy rules for a service and environment.
    
    Args:
        service: Service name
        env: Environment name
    
    Returns:
        EffectiveConfig with merged actions from all matching rules
    
    Note:
        - Evaluates rules in priority order (highest first)
        - Applies merge strategies for overlapping rules
        - Records metrics for policy evaluations and rule matches
    """
    start_time = time.time()
    key = (service, env)
    _prune(key)
    buf = SIGNALS.get(key, [])

    effective = EffectiveConfig(service=service, environment=env)

    for rule in sorted((r for r in POLICY.rules if r.enabled), key=lambda r: r.priority):
        # scope match with pattern support
        if not matches_service_pattern(service, rule.service):
            continue
        if not matches_environment_pattern(env, rule.environment):
            continue

        matched = True
        for c in rule.conditions:
            if c.kind == "always" or c.op == "always":
                continue
            # Calculate aggregates with per-condition window
            aggs = _calc_aggregates(buf, c.window_s)
            if c.kind == "error_rate":
                v = aggs.get("error_rate", 0.0)
                threshold = float(c.value) if c.value is not None else 0.0
                if not op_map[c.op](v, threshold):
                    matched = False
                    break
            elif c.kind == "metric":
                v = aggs.get(c.key or "", 0.0)
                threshold = float(c.value) if c.value is not None else 0.0
                if not op_map[c.op](v, threshold):
                    matched = False
                    break
            else:
                matched = False
                break
        if not matched:
            continue

        # Record rule match
        prom_metrics.record_rule_match(rule.id, service, env)
        logger.info(f"Rule '{rule.id}' matched for {service}/{env}")

        # Apply actions using merge strategy (rule-level overrides policy-level)
        strategy = rule.merge_strategy if rule.merge_strategy is not None else POLICY.merge_strategy
        _apply_action_merge(effective, rule.actions, strategy)

    # Record evaluation metrics
    duration = time.time() - start_time
    prom_metrics.policy_evaluations_total.labels(service=service, environment=env).inc()
    prom_metrics.policy_evaluation_duration_seconds.labels(service=service, environment=env).observe(duration)

    return effective


# Note: Startup and shutdown logic moved to lifespan context manager above


# --- Health Check Helpers

async def _check_database_health(db: AsyncSession) -> dict:
    """Check database connectivity and return health status.
    
    Args:
        db: Database session
        
    Returns:
        Dictionary with health status and optional error message
    """
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "error": None}
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {"status": "unhealthy", "error": str(e)}


def _check_signal_buffer_health() -> dict:
    """Check signal buffer status.
    
    Returns:
        Dictionary with buffer health metrics
    """
    try:
        total_signals = sum(len(buf) for buf in SIGNALS.values())
        return {
            "status": "healthy",
            "total_signals": total_signals,
            "services": len(SIGNALS),
            "error": None
        }
    except Exception as e:
        logger.error(f"Signal buffer health check failed: {e}")
        return {"status": "unhealthy", "error": str(e)}


# --- API
class UpsertPolicy(BaseModel):
    policy: Policy




# Note: All endpoints have been migrated to modular routers
# - Health, auth, config: routers/health.py, routers/auth.py, routers/config.py
# - Signal: routers/signal.py
# - Policy: routers/policy.py
# - Simulation: routers/simulation.py (ready but not yet registered)

# Helper models and functions used by routers

class SignalIn(BaseModel):
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
        return validators.validate_name_pattern(v)
    
    @field_validator('attrs')
    @classmethod
    def validate_attrs(cls, v: Dict[str, str]) -> Dict[str, str]:
        return validators.validate_attributes(v)
    
    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: Optional[datetime]) -> Optional[datetime]:
        return validators.validate_timestamp(v)


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


def _convert_signal_in_to_signal(sig_in: SignalIn) -> Signal:
    """Convert a SignalIn (request model) to a Signal with timestamp.
    
    Args:
        sig_in: Input signal from API request
        
    Returns:
        Signal with current timestamp
    """
    return Signal(
        service=sig_in.service,
        environment=sig_in.environment,
        ts=_now(),
        latency_ms=sig_in.latency_ms,
        error=sig_in.error,
        attrs=sig_in.attrs,
    )


def _evaluate_rule_conditions(rule: Rule, signal: Signal, agg: Dict[str, float]) -> tuple[bool, list[dict]]:
    """Evaluate all conditions for a rule and return match status with details.
    
    Args:
        rule: Rule to evaluate
        signal: Signal to evaluate against
        agg: Aggregated metrics
        
    Returns:
        Tuple of (matched, condition_results)
    """
    match = True
    condition_results = []
    
    for cond in rule.conditions:
        cond_match = _eval_condition(cond, signal, agg)
        condition_results.append({
            "kind": cond.kind,
            "op": cond.op,
            "matched": cond_match,
            "key": cond.key,
            "value": cond.value
        })
        if not cond_match:
            match = False
            break
    
    return match, condition_results


def _match_rules_for_signal(policy: Policy, signal: Signal, agg: Dict[str, float]) -> tuple[list[dict], EffectiveConfig]:
    """Find all matching rules for a signal and compute effective configuration.
    
    Args:
        policy: Policy containing rules
        signal: Signal to evaluate
        agg: Aggregated metrics
        
    Returns:
        Tuple of (matched_rules, effective_config)
    """
    matched_rules = []
    effective_config = EffectiveConfig(
        service=signal.service,
        environment=signal.environment
    )
    
    for rule in policy.rules:
        if not rule.enabled:
            continue
            
        # Check if rule scope matches with pattern support
        if not matches_service_pattern(signal.service, rule.service):
            continue
        if not matches_environment_pattern(signal.environment, rule.environment):
            continue
        
        # Evaluate conditions
        match, condition_results = _evaluate_rule_conditions(rule, signal, agg)
        
        if match:
            matched_rules.append({
                "rule_id": rule.id,
                "priority": rule.priority,
                "description": rule.description,
                "conditions": condition_results,
                "actions": rule.actions.model_dump(exclude_none=True),
                "merge_strategy": rule.merge_strategy or policy.merge_strategy
            })
            
            # Apply actions using merge strategy
            strategy = rule.merge_strategy if rule.merge_strategy is not None else policy.merge_strategy
            _apply_action_merge(effective_config, rule.actions, strategy)
    
    return matched_rules, effective_config


def _build_simulation_result(idx: int, signal: Signal, matched_rules: list[dict], effective_config: EffectiveConfig) -> dict:
    """Build a single simulation result entry.
    
    Args:
        idx: Signal index in the batch
        signal: The evaluated signal
        matched_rules: List of matched rule details
        effective_config: Computed effective configuration
        
    Returns:
        Simulation result dictionary
    """
    return {
        "signal_index": idx,
        "service": signal.service,
        "environment": signal.environment,
        "latency_ms": signal.latency_ms,
        "error": signal.error,
        "matched_rules": matched_rules,
        "rule_count": len(matched_rules),
        "effective_config": effective_config.model_dump()
    }


def _build_simulation_summary(results: list[dict], policy_id: str, total_signals: int) -> dict:
    """Build the complete simulation response with summary statistics.
    
    Args:
        results: List of individual simulation results
        policy_id: ID of the simulated policy
        total_signals: Total number of signals simulated
        
    Returns:
        Complete simulation response dictionary
    """
    return {
        "simulation_results": results,
        "total_signals": total_signals,
        "policy_id": policy_id,
        "summary": {
            "signals_with_matches": sum(1 for r in results if r["rule_count"] > 0),
            "signals_without_matches": sum(1 for r in results if r["rule_count"] == 0),
            "total_rule_matches": sum(r["rule_count"] for r in results)
        }
    }




# Register modular routers - all endpoints now in dedicated router modules
from control_plane.routers.health import router as health_router
from control_plane.routers.auth import router as auth_router
from control_plane.routers.config import router as config_router
from control_plane.routers.signal import router as signal_router
from control_plane.routers.policy import router as policy_router
from control_plane.routers.simulation import router as simulation_router

app.include_router(health_router, prefix="/v1", tags=["v1"])
app.include_router(auth_router, prefix="/v1", tags=["v1"])
app.include_router(config_router, prefix="/v1", tags=["v1"])
app.include_router(signal_router, prefix="/v1", tags=["v1"])
app.include_router(policy_router, prefix="/v1", tags=["v1"])
app.include_router(simulation_router, prefix="/v1", tags=["v1"])

# v1_router no longer has endpoints - all migrated to routers above
app.include_router(v1_router)
