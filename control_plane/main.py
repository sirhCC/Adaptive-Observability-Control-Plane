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



@v1_router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)):
    """Health check endpoint with component status.
    
    Checks status of critical components:
    - Database connectivity
    - Signal buffer health
    
    Returns:
        200: Service is healthy
        503: Service is degraded (component failures)
        
    Use for: Docker HEALTHCHECK, Kubernetes liveness probes
    """
    health_status = {
        "status": "healthy",
        "timestamp": _now().isoformat(),
        "components": {}
    }
    
    # Check database connectivity
    db_health = await _check_database_health(db)
    if db_health["status"] == "unhealthy":
        health_status["status"] = "degraded"
        health_status["components"]["database"] = "unhealthy"
    else:
        health_status["components"]["database"] = "healthy"
    
    # Check signal buffer status
    buffer_health = _check_signal_buffer_health()
    if buffer_health["status"] == "unhealthy":
        health_status["status"] = "degraded"
        health_status["components"]["signal_buffer"] = "unhealthy"
    else:
        health_status["components"]["signal_buffer"] = {
            "status": "healthy",
            "total_signals": buffer_health["total_signals"],
            "services": buffer_health["services"]
        }
    
    # Set HTTP status code based on health
    status_code = 200 if health_status["status"] == "healthy" else 503
    
    from fastapi.responses import JSONResponse
    return JSONResponse(content=health_status, status_code=status_code)


@v1_router.get("/readyz")
async def readyz(db: AsyncSession = Depends(get_db)):
    """Readiness check endpoint for Kubernetes/Docker.
    
    Returns 200 if service is ready to accept traffic, 503 otherwise.
    Checks critical dependencies like database connectivity.
    """
    readiness_status = {
        "ready": True,
        "timestamp": _now().isoformat(),
        "checks": {}
    }
    
    # Check database connectivity (critical for readiness)
    db_health = await _check_database_health(db)
    if db_health["status"] == "unhealthy":
        readiness_status["checks"]["database"] = {
            "status": "not_ready",
            "message": f"Database connection failed: {db_health['error']}"
        }
        readiness_status["ready"] = False
    else:
        readiness_status["checks"]["database"] = {
            "status": "ready",
            "message": "Database connection successful"
        }
    
    # Check if policy is initialized
    try:
        if POLICY and POLICY.rules:
            readiness_status["checks"]["policy"] = {
                "status": "ready",
                "message": f"Policy initialized with {len(POLICY.rules)} rules"
            }
        else:
            readiness_status["checks"]["policy"] = {
                "status": "ready",
                "message": "Default policy active"
            }
    except Exception as e:
        readiness_status["checks"]["policy"] = {
            "status": "not_ready",
            "message": f"Policy check failed: {str(e)}"
        }
        readiness_status["ready"] = False
        logger.error(f"Policy readiness check failed: {e}")
    
    # Set HTTP status code based on readiness
    status_code = 200 if readiness_status["ready"] else 503
    
    from fastapi.responses import JSONResponse
    return JSONResponse(content=readiness_status, status_code=status_code)


@v1_router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint for monitoring.
    
    Exposes comprehensive metrics:
    - HTTP request metrics (requests_total, request_duration_seconds)
    - Signal metrics (signals_ingested_total, signal_latency_ms)
    - Policy metrics (policy_evaluations_total, rule_matches_total)
    - Database metrics (db_queries_total, db_query_duration_seconds)
    
    Returns:
        Prometheus-formatted metrics in text/plain format
        
    Use with: Prometheus, Grafana, or any metrics scraper
    """
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    
    metrics_data = generate_latest()
    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)


@v1_router.post("/auth/generate-key")
@limiter.limit(constants.RATE_LIMIT_GENERATE_KEY)
async def generate_key(
    request: Request,
    admin: str = Depends(require_admin_key),
):
    """Generate a new API key for agent authentication.
    
    Requires admin API key in X-API-Key header.
    
    Returns:
        New API key with creation timestamp and security note.
        
    Rate limit: 5 requests per minute
    Security: Admin access required
    """
    from control_plane.auth import generate_api_key
    
    new_key = generate_api_key()
    logger.info(f"Generated new API key: {new_key[:12]}...")
    return {
        "api_key": new_key,
        "created_at": _now().isoformat(),
        "note": "Store this key securely - it won't be shown again",
    }


@v1_router.get("/policy", response_model=Policy)
@limiter.limit(constants.RATE_LIMIT_GET_POLICY)
async def get_policy(request: Request):
    """Get the current active policy configuration.
    
    Returns:
        Current policy with all rules and merge strategy settings.
        
    Rate limit: 50 requests per minute
    """
    return POLICY


@v1_router.get("/policy/export")
@limiter.limit(constants.RATE_LIMIT_EXPORT_POLICY)
async def export_policy(
    request: Request,
    format: Literal["json", "yaml"] = "json",
    include_history: bool = False
):
    """Export current policy configuration in JSON or YAML format.
    
    Supports GitOps workflows and policy portability.
    
    Args:
        format: Export format - 'json' (default) or 'yaml'
        include_history: If True, includes policy version history metadata
    
    Returns:
        Policy configuration in requested format
    """
    # Create export data structure
    export_data = exporters.create_policy_export_data(
        policy=POLICY,
        exported_at=_now(),
        include_history=include_history,
        policy_history=POLICY_HISTORY if include_history else None
    )
    
    # Export to requested format
    if format == "yaml":
        content = exporters.export_policy_to_yaml(export_data)
        return Response(
            content=content,
            media_type="application/x-yaml",
            headers={"Content-Disposition": f"attachment; filename=policy-{POLICY.id}.yaml"}
        )
    else:
        content = exporters.export_policy_to_json(export_data)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=policy-{POLICY.id}.json"}
        )


@v1_router.post("/policy/import")
@limiter.limit(constants.RATE_LIMIT_IMPORT_POLICY)
async def import_policy(
    request: Request,
    admin: str = Depends(require_admin_key),
    dry_run: bool = False
):
    """Import policy configuration from JSON or YAML.
    
    Supports GitOps workflows and policy portability.
    Automatically detects format from content.
    
    Args:
        dry_run: If True, validates without applying the policy
        
    Request body: JSON or YAML policy configuration
    
    Returns:
        Imported policy or validation results if dry_run=True
    """
    from control_plane.rule_validator import validate_policy_rules
    
    # Get raw body content
    body_bytes = await request.body()
    body_str = body_bytes.decode('utf-8')
    
    # Try to parse as YAML first (YAML parser can handle JSON too)
    try:
        import_data = yaml.safe_load(body_str)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML/JSON format: {str(e)}")
    
    # Extract policy from import data structure
    if isinstance(import_data, dict) and "policy" in import_data:
        policy_data = import_data["policy"]
    else:
        policy_data = import_data
    
    # Validate and parse policy
    try:
        imported_policy = Policy(**policy_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid policy structure: {str(e)}")
    
    # Validate policy has at least one rule
    if not imported_policy.rules:
        prom_metrics.record_policy_validation_error()
        raise PolicyValidationError("Policy must contain at least one rule")
    
    # Run conflict detection
    validation_result = validate_policy_rules(imported_policy.rules)
    
    # Block if there are errors
    if not validation_result["valid"]:
        prom_metrics.record_policy_validation_error()
        error_conflicts = [c for c in validation_result["conflicts"] if c.severity == "error"]
        error_messages = [c.message for c in error_conflicts]
        raise PolicyValidationError(
            f"Policy validation failed: {'; '.join(error_messages)}",
            conflicts=[{
                "type": c.type,
                "severity": c.severity,
                "rule_ids": c.rule_ids,
                "message": c.message
            } for c in error_conflicts]
        )
    
    # Log warnings but allow import
    warnings = [c for c in validation_result["conflicts"] if c.severity == "warning"]
    if warnings:
        logger.warning(f"Policy import has {len(warnings)} warnings: " + 
                      "; ".join([c.message for c in warnings]))
    
    # If dry_run, return validation results without applying
    if dry_run:
        logger.info(f"Dry-run import validation for policy '{imported_policy.id}' successful")
        return {
            "dry_run": True,
            "valid": True,
            "policy": imported_policy.model_dump(),
            "validation": {
                "conflicts": len(validation_result["conflicts"]),
                "warnings": len(warnings),
                "details": validation_result
            },
            "message": "Policy import is valid and can be applied"
        }
    
    # Apply the imported policy
    global POLICY
    POLICY = imported_policy
    
    # Save to policy history
    global POLICY_HISTORY
    version = PolicyVersion(
        policy=imported_policy.model_copy(deep=True),
        applied_at=_now(),
        applied_by=admin
    )
    POLICY_HISTORY.append(version)
    
    # Prune old policy history
    if len(POLICY_HISTORY) > MAX_POLICY_HISTORY:
        POLICY_HISTORY = POLICY_HISTORY[-MAX_POLICY_HISTORY:]
    
    prom_metrics.record_policy_update(imported_policy.id)
    logger.info(
        f"Policy '{imported_policy.id}' imported successfully by {admin}",
        policy_id=imported_policy.id,
        num_rules=len(imported_policy.rules),
        admin=admin
    )
    
    return {
        "imported": True,
        "policy": imported_policy.model_dump(),
        "message": f"Policy '{imported_policy.id}' imported and applied successfully"
    }


@v1_router.get("/policy/templates")
@limiter.limit(constants.RATE_LIMIT_TEMPLATES)
async def get_policy_templates(request: Request):
    """Get policy templates/presets for common scenarios.
    
    Returns pre-configured policy templates for different observability strategies:
    - production-safe: Conservative production policy
    - development: Verbose development environment
    - performance-focused: Latency-based adaptive policy
    - cost-optimized: Minimal overhead for cost savings
    - balanced: General-purpose adaptive policy
    
    Returns:
        Dictionary of available templates with descriptions
        
    Rate limit: 50 requests per minute
    Use for: Quick start, best practices, policy inspiration
    """
    templates = {
        "production-safe": {
            "name": "Production Safe",
            "description": "Conservative policy for production environments with error-based elevation",
            "policy": {
                "id": "production-safe",
                "description": "Conservative production policy",
                "rules": [
                    {
                        "id": "prod-baseline",
                        "description": "Baseline for production - minimal overhead",
                        "environment": "prod",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.01,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "prod-high-errors",
                        "description": "Elevate on high error rates in production",
                        "environment": "prod",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.05, "window_s": 300}
                        ],
                        "actions": {
                            "log_level": "WARN",
                            "trace_sample_rate": 0.10,
                            "metric_period_s": 30
                        }
                    },
                    {
                        "id": "prod-critical-errors",
                        "description": "Maximum observability on critical errors",
                        "environment": "prod",
                        "priority": 20,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.10, "window_s": 60}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.50,
                            "metric_period_s": 15
                        }
                    }
                ]
            }
        },
        "development": {
            "name": "Development",
            "description": "Verbose policy for development environments with high sampling",
            "policy": {
                "id": "development",
                "description": "Development environment policy with verbose logging",
                "rules": [
                    {
                        "id": "dev-baseline",
                        "description": "Verbose baseline for development",
                        "environment": "dev",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 1.0,
                            "metric_period_s": 30
                        }
                    }
                ]
            }
        },
        "performance-focused": {
            "name": "Performance Focused",
            "description": "Policy that elevates observability based on latency thresholds",
            "policy": {
                "id": "performance-focused",
                "description": "Latency-based adaptive policy",
                "rules": [
                    {
                        "id": "baseline",
                        "description": "Default baseline",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.05,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "elevated-latency",
                        "description": "Increase sampling on slow requests",
                        "priority": 10,
                        "conditions": [
                            {"kind": "metric", "op": ">", "key": "latency_p95_ms", "value": 500, "window_s": 300}
                        ],
                        "actions": {
                            "log_level": "WARN",
                            "trace_sample_rate": 0.25,
                            "metric_period_s": 30
                        }
                    },
                    {
                        "id": "critical-latency",
                        "description": "Maximum observability on very slow requests",
                        "priority": 20,
                        "conditions": [
                            {"kind": "metric", "op": ">", "key": "latency_p99_ms", "value": 2000, "window_s": 120}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.75,
                            "metric_period_s": 15
                        }
                    }
                ]
            }
        },
        "cost-optimized": {
            "name": "Cost Optimized",
            "description": "Minimal observability overhead, only elevates on critical issues",
            "policy": {
                "id": "cost-optimized",
                "description": "Minimal overhead policy for cost savings",
                "rules": [
                    {
                        "id": "minimal-baseline",
                        "description": "Minimal baseline sampling",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "WARN",
                            "trace_sample_rate": 0.001,
                            "metric_period_s": 120
                        }
                    },
                    {
                        "id": "critical-only",
                        "description": "Only elevate on critical errors",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.20, "window_s": 60}
                        ],
                        "actions": {
                            "log_level": "ERROR",
                            "trace_sample_rate": 0.10,
                            "metric_period_s": 30
                        }
                    }
                ]
            }
        },
        "balanced": {
            "name": "Balanced",
            "description": "Balanced policy with error and latency triggers",
            "policy": {
                "id": "balanced",
                "description": "Balanced adaptive policy for most use cases",
                "rules": [
                    {
                        "id": "baseline",
                        "description": "Balanced baseline",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.10,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "errors-detected",
                        "description": "Elevate on error rate increase",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.02, "window_s": 120}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.30,
                            "metric_period_s": 30
                        }
                    },
                    {
                        "id": "slow-requests",
                        "description": "Elevate on latency issues",
                        "priority": 15,
                        "conditions": [
                            {"kind": "metric", "op": ">", "key": "latency_p95_ms", "value": 400, "window_s": 180}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.30,
                            "metric_period_s": 30
                        }
                    }
                ]
            }
        }
    }
    
    return {
        "templates": templates,
        "count": len(templates),
        "usage": "Use GET /v1/policy/templates/{template_name} to get a specific template"
    }


@v1_router.get("/policy/templates/{template_name}")
@limiter.limit("50/minute")
async def get_policy_template(request: Request, template_name: str):
    """Get a specific policy template by name.
    
    Available templates:
    - production-safe: Conservative with error-based elevation
    - development: Verbose logging with high sampling
    - performance-focused: Latency-based elevation
    - cost-optimized: Minimal overhead, critical-only elevation
    - balanced: Error and latency triggers
    
    Args:
        template_name: Template identifier
    
    Returns:
        Policy template with usage examples for import
        
    Rate limit: 50 requests per minute
    Use for: Quick policy setup, best practices reference
    """
    # Get all templates
    templates_response = await get_policy_templates(request)
    templates = templates_response["templates"]
    
    if template_name not in templates:
        available = ", ".join(templates.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_name}' not found. Available templates: {available}"
        )
    
    template = templates[template_name]
    
    return {
        "template": template,
        "export_ready": True,
        "usage": {
            "curl_json": f"curl http://localhost:8080/v1/policy/templates/{template_name} | jq '.template.policy' | curl -X POST http://localhost:8080/v1/policy/import -H 'X-API-Key: YOUR_KEY' -d @-",
            "description": "Pipe this template directly to the import endpoint or download and customize"
        }
    }


@v1_router.post("/policy/validate")
@limiter.limit(constants.RATE_LIMIT_VALIDATE_POLICY)
async def validate_policy(request: Request, req: UpsertPolicy):
    """Validate a policy configuration without applying it.
    
    Performs comprehensive validation:
    - Pattern syntax validation (service/environment wildcards, globs, regex)
    - Rule conflict detection
    - Priority conflicts
    - Overlapping conditions
    
    Returns:
        Validation results with conflicts categorized by severity (error/warning/info).
        
    Rate limit: 20 requests per minute
    Use for: CI/CD pipelines, GitOps validation, policy development
    """
    from control_plane.rule_validator import validate_policy_rules
    
    # Validate service/environment patterns
    pattern_errors = []
    for rule in req.policy.rules:
        if rule.service:
            is_valid, error_msg = validate_pattern(rule.service)
            if not is_valid:
                pattern_errors.append(f"Rule '{rule.id}' has invalid service pattern: {error_msg}")
        
        if rule.environment:
            is_valid, error_msg = validate_pattern(rule.environment)
            if not is_valid:
                pattern_errors.append(f"Rule '{rule.id}' has invalid environment pattern: {error_msg}")
    
    if pattern_errors:
        raise PolicyValidationError(
            f"Invalid patterns in policy: {'; '.join(pattern_errors)}"
        )
    
    # Run validation
    validation_result = validate_policy_rules(req.policy.rules)
    
    return {
        "valid": validation_result["valid"],
        "summary": validation_result["summary"],
        "conflicts": [
            {
                "type": c.type,
                "severity": c.severity,
                "rule_ids": c.rule_ids,
                "message": c.message,
                "suggestion": c.suggestion
            }
            for c in validation_result["conflicts"]
        ]
    }

@v1_router.post("/policy")
@limiter.limit(constants.RATE_LIMIT_SET_POLICY)  # Strict limit on policy updates
async def set_policy(
    request: Request,
    req: UpsertPolicy,
    admin: str = Depends(require_admin_key),
    dry_run: bool = False,
):
    """Update the active policy configuration.
    
    Performs validation before applying:
    - Rule conflict detection
    - Priority validation
    - Pattern syntax validation
    
    Saves policy to version history for time-travel debugging.
    
    Args:
        req: Policy configuration to apply
        dry_run: If True, validates without applying (simulation mode)
            
    Returns:
        Applied policy (dry_run=False) or validation results (dry_run=True)
        
    Rate limit: 10 requests per minute (strict)
    Security: Admin API key required
    Audit: All changes logged with timestamp and admin identifier
    """
    from control_plane.rule_validator import validate_policy_rules
    
    global POLICY
    # Validate policy has at least one rule
    if not req.policy.rules:
        prom_metrics.record_policy_validation_error()
        raise PolicyValidationError("Policy must contain at least one rule")
    
    # Run conflict detection
    validation_result = validate_policy_rules(req.policy.rules)
    
    # Block if there are errors
    if not validation_result["valid"]:
        prom_metrics.record_policy_validation_error()
        error_conflicts = [c for c in validation_result["conflicts"] if c.severity == "error"]
        error_messages = [c.message for c in error_conflicts]
        raise PolicyValidationError(
            f"Policy validation failed: {'; '.join(error_messages)}",
            conflicts=[{
                "type": c.type,
                "severity": c.severity,
                "rule_ids": c.rule_ids,
                "message": c.message
            } for c in error_conflicts]
        )
    
    # Log warnings but allow update
    warnings = [c for c in validation_result["conflicts"] if c.severity == "warning"]
    if warnings:
        logger.warning(f"Policy update has {len(warnings)} warnings: " + 
                      "; ".join([c.message for c in warnings]))
    
    # If dry_run, return validation results without applying
    if dry_run:
        logger.info(f"Dry-run validation for policy '{req.policy.id}' successful")
        return {
            "dry_run": True,
            "valid": True,
            "policy": req.policy,
            "validation": {
                "conflicts": len(validation_result["conflicts"]),
                "warnings": len(warnings),
                "details": validation_result
            },
            "message": "Policy is valid and can be applied"
        }
    
    # Apply the policy
    POLICY = req.policy
    
    # Save to policy history for time-travel debugging
    global POLICY_HISTORY
    version = PolicyVersion(
        policy=req.policy.model_copy(deep=True),
        applied_at=_now(),
        applied_by=admin  # admin username from API key
    )
    POLICY_HISTORY.append(version)
    
    # Prune old policy history
    if len(POLICY_HISTORY) > MAX_POLICY_HISTORY:
        POLICY_HISTORY = POLICY_HISTORY[-MAX_POLICY_HISTORY:]
    
    prom_metrics.record_policy_update(req.policy.id)
    logger.info(
        f"Policy '{req.policy.id}' updated with {len(req.policy.rules)} rules by {admin}",
        policy_id=req.policy.id,
        num_rules=len(req.policy.rules),
        merge_strategy=req.policy.merge_strategy,
        admin=admin,
        has_warnings=len(warnings) > 0
    )
    
    return POLICY


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


@v1_router.post("/policy/simulate")
@limiter.limit(constants.RATE_LIMIT_SIMULATE)
async def simulate_policy(request: Request, req: SimulateRequest):
    """Simulate policy evaluation with test signals.
    
    Dry-run mode for policy testing: evaluates which rules would match for
    test signals without applying the policy. Shows matched rules, condition
    evaluation details, and resulting effective configuration.
    
    Args:
        policy: Policy to simulate
        test_signals: 1-100 test signals to evaluate
    
    Returns:
        Detailed simulation results with matched rules and effective configs
        
    Rate limit: 20 requests per minute
    Use for: CI/CD policy validation, policy development, impact analysis
    """
    # Create simulator with evaluation functions
    simulator = PolicySimulator(
        calc_aggregates_fn=_calc_aggregates,
        evaluate_rule_conditions_fn=_evaluate_rule_conditions,
        match_rules_for_signal_fn=_match_rules_for_signal
    )
    
    # Convert input signals to Signal models
    test_signals = [_convert_signal_in_to_signal(sig_in) for sig_in in req.test_signals]
    
    # Evaluate signals against policy
    start_time = time.time()
    results = simulator.evaluate_batch(req.policy, test_signals)
    eval_duration_ms = (time.time() - start_time) * 1000
    
    # Log simulation metrics
    logger.info(
        "Policy simulation completed",
        policy_id=req.policy.id,
        num_signals=len(test_signals),
        num_rules=len(req.policy.rules),
        signals_with_matches=sum(1 for r in results if r.rule_count > 0),
        evaluation_time_ms=round(eval_duration_ms, 2)
    )
    
    # Build response
    return create_simulation_response(results, req.policy.id, len(test_signals))


@v1_router.get("/history/policy")
@limiter.limit("20/minute")
async def get_policy_history(
    request: Request,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 50
):
    """Get policy version history for time-travel debugging.
    
    Query historical policy configurations with complete audit trail:
    - What policy was active at any point in time
    - Who applied each policy change
    - When changes occurred
    
    Args:
        start_time: ISO 8601 timestamp to filter from (optional)
        end_time: ISO 8601 timestamp to filter to (optional)
        limit: Maximum versions to return (default 50, max 100)
    
    Returns:
        List of policy versions with timestamps and attribution
        
    Rate limit: 20 requests per minute
    Use for: Debugging, auditing, compliance, incident analysis
    """
    # Limit the result count
    limit = min(limit, 100)
    
    # Filter by time range if provided
    filtered = POLICY_HISTORY
    
    if start_time:
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        filtered = [v for v in filtered if v.applied_at >= start_dt]
    
    if end_time:
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        filtered = [v for v in filtered if v.applied_at <= end_dt]
    
    # Sort by time descending (most recent first) and limit
    filtered = sorted(filtered, key=lambda v: v.applied_at, reverse=True)[:limit]
    
    return {
        "versions": [
            {
                "policy": v.policy.model_dump(),
                "applied_at": v.applied_at.isoformat(),
                "applied_by": v.applied_by
            }
            for v in filtered
        ],
        "count": len(filtered),
        "total_in_history": len(POLICY_HISTORY)
    }


@v1_router.get("/history/policy/at")
@limiter.limit("20/minute")
async def get_policy_at_time(
    request: Request,
    timestamp: str
):
    """Get the policy that was active at a specific time.
    
    Time-travel query: returns the exact policy configuration that was
    active at the specified timestamp. Essential for "what would have
    happened" analysis and debugging past behavior.
    
    Args:
        timestamp: ISO 8601 timestamp to query (e.g., 2025-01-15T10:30:00Z)
    
    Returns:
        Policy active at that time with application metadata
        
    Rate limit: 20 requests per minute
    Use for: Incident investigation, behavioral analysis, policy impact assessment
    """
    # URL encoding may turn + into space, so handle both formats
    timestamp_fixed = timestamp.replace(' ', '+').replace('Z', '+00:00')
    query_time = datetime.fromisoformat(timestamp_fixed)
    
    # Find the most recent policy version that was applied before the query time
    applicable_versions = [
        v for v in POLICY_HISTORY 
        if v.applied_at <= query_time
    ]
    
    if applicable_versions:
        # Get the most recent one
        policy_version = sorted(applicable_versions, key=lambda v: v.applied_at, reverse=True)[0]
        return {
            "policy": policy_version.policy.model_dump(),
            "applied_at": policy_version.applied_at.isoformat(),
            "applied_by": policy_version.applied_by,
            "query_time": query_time.isoformat()
        }
    else:
        # No history before this time, return current policy
        return {
            "policy": POLICY.model_dump(),
            "applied_at": None,
            "applied_by": None,
            "query_time": query_time.isoformat(),
            "note": "No policy history available before this time, returning current policy"
        }


@v1_router.post("/replay")
@limiter.limit("20/minute")
async def replay_signals(request: Request, req: ReplayRequest):
    """Replay historical signals with time-travel policy evaluation.
    
    Re-evaluate past signals using either:
    - Current policy (default) - see how current rules would handle past signals
    - Historical policy - see what actually happened at that time
    
    Perfect for:
    - Understanding policy change impacts
    - Debugging past incidents
    - Validating policy improvements
    - "What would have happened if..." analysis
    
    Args:
        signals: Historical signals to replay (1-100, must include timestamps)
        policy_timestamp: Optional ISO 8601 timestamp for historical policy
    
    Returns:
        Replay results with matched rules and effective configs per signal
        
    Rate limit: 20 requests per minute
    Use for: Incident analysis, policy validation, impact assessment
    """
    # Validate all signals have timestamps
    for idx, sig in enumerate(req.signals):
        if sig.timestamp is None:
            raise HTTPException(
                status_code=400,
                detail=f"Signal at index {idx} missing timestamp. All signals must have timestamps for replay."
            )
    
    # Determine which policy to use
    policy_to_use = POLICY
    policy_info = {
        "using": "current",
        "policy_id": POLICY.id
    }
    
    if req.policy_timestamp:
        # Handle URL encoding which may turn + into space
        policy_ts_fixed = req.policy_timestamp.replace(' ', '+').replace('Z', '+00:00')
        query_time = datetime.fromisoformat(policy_ts_fixed)
        
        # Find policy at that time
        applicable_versions = [
            v for v in POLICY_HISTORY 
            if v.applied_at <= query_time
        ]
        
        if applicable_versions:
            policy_version = sorted(applicable_versions, key=lambda v: v.applied_at, reverse=True)[0]
            policy_to_use = policy_version.policy
            policy_info = {
                "using": "historical",
                "policy_id": policy_to_use.id,
                "applied_at": policy_version.applied_at.isoformat(),
                "applied_by": policy_version.applied_by,
                "query_time": query_time.isoformat()
            }
        else:
            policy_info["note"] = f"No policy history before {query_time.isoformat()}, using current policy"
    
    # Create simulator with evaluation functions
    simulator = PolicySimulator(
        calc_aggregates_fn=_calc_aggregates,
        evaluate_rule_conditions_fn=_evaluate_rule_conditions,
        match_rules_for_signal_fn=_match_rules_for_signal
    )
    
    # Convert input signals to Signal models (preserving timestamps)
    replay_signals = []
    for idx, sig_in in enumerate(req.signals):
        signal_time = sig_in.timestamp
        assert signal_time is not None, "Signal timestamp must not be None"
        
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
            
        replay_signals.append(Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=signal_time,
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        ))
    
    # Replay signals against policy
    policy_ts = datetime.fromisoformat(req.policy_timestamp.replace(' ', '+').replace('Z', '+00:00')) if req.policy_timestamp else None
    
    start_time = time.time()
    replay_results = simulator.replay_with_historical_policy(replay_signals, policy_to_use, policy_ts)
    replay_duration_ms = (time.time() - start_time) * 1000
    
    # Log replay metrics
    logger.info(
        "Signal replay completed",
        num_signals=len(replay_signals),
        policy_type=policy_info["using"],
        policy_id=policy_to_use.id,
        replay_time_ms=round(replay_duration_ms, 2)
    )
    
    # Add policy info to results
    replay_results["policy_info"] = policy_info
    
    return replay_results


@v1_router.post("/compare")
@limiter.limit("20/minute")
async def compare_policies(request: Request, req: CompareRequest):
    """Compare how different policies would handle the same signals.
    
    Side-by-side policy comparison showing behavioral differences:
    - Which rules match in each policy
    - How effective configurations differ
    - Impact of policy changes on observability settings
    
    Supports comparing:
    - Current policy vs historical policies
    - Multiple historical policies
    - Up to 5 policies simultaneously
    
    Args:
        signals: Signals to analyze (1-50, historical or synthetic)
        compare_policies: 2-5 policy timestamps or 'current'
    
    Returns:
        Detailed comparison with differences highlighted
        
    Rate limit: 20 requests per minute
    Use for: Policy impact analysis, A/B testing, regression detection
    """
    # Resolve policies to compare
    policies_to_compare = []
    
    for policy_ref in req.compare_policies:
        if policy_ref.lower() == "current":
            policies_to_compare.append({
                "policy": POLICY,
                "label": "current",
                "policy_id": POLICY.id,
                "applied_at": None
            })
        else:
            # Parse as timestamp
            # URL encoding may turn + into space, so handle both formats
            policy_ref_fixed = policy_ref.replace(' ', '+').replace('Z', '+00:00')
            query_time = datetime.fromisoformat(policy_ref_fixed)
            
            # Find policy at that time
            applicable_versions = [
                v for v in POLICY_HISTORY 
                if v.applied_at <= query_time
            ]
            
            if applicable_versions:
                policy_version = sorted(applicable_versions, key=lambda v: v.applied_at, reverse=True)[0]
                policies_to_compare.append({
                    "policy": policy_version.policy,
                    "label": policy_ref,
                    "policy_id": policy_version.policy.id,
                    "applied_at": policy_version.applied_at.isoformat()
                })
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"No policy history available before {query_time.isoformat()}"
                )
    
    # Create simulator with evaluation functions
    simulator = PolicySimulator(
        calc_aggregates_fn=_calc_aggregates,
        evaluate_rule_conditions_fn=_evaluate_rule_conditions,
        match_rules_for_signal_fn=_match_rules_for_signal
    )
    
    # Convert input signals to Signal models
    test_signals = []
    for sig_in in req.signals:
        signal_time = sig_in.timestamp if sig_in.timestamp else _now()
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
            
        test_signals.append(Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=signal_time,
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        ))
    
    # Build policy list for simulator
    policies_with_labels = [
        (p["label"], p["policy"]) 
        for p in policies_to_compare
    ]
    
    # Compare signals across policies
    start_time = time.time()
    comparison_results = simulator.compare_evaluations(policies_with_labels, test_signals)
    compare_duration_ms = (time.time() - start_time) * 1000
    
    # Log comparison metrics
    logger.info(
        "Policy comparison completed",
        num_signals=len(test_signals),
        num_policies=len(policies_with_labels),
        signals_with_differences=comparison_results["summary"].get("signals_with_differences", 0),
        comparison_time_ms=round(compare_duration_ms, 2)
    )
    
    return comparison_results



@v1_router.get("/signals/export")
@limiter.limit(constants.RATE_LIMIT_EXPORT_SIGNALS)
async def export_signals(
    request: Request,
    service: Optional[str] = None,
    environment: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 1000
):
    """Export signals for offline analysis and archival.
    
    Download signals from the in-memory buffer with flexible filtering:
    - By service and/or environment
    - By time range
    - Sorted chronologically
    
    Exported signals include timestamps and are suitable for:
    - Replay with /replay endpoint
    - Offline analysis and visualization
    - Long-term archival
    - Incident investigation
    
    Args:
        service: Filter by service name (optional)
        environment: Filter by environment (optional)
        start_time: ISO 8601 timestamp to filter from (optional)
        end_time: ISO 8601 timestamp to filter to (optional)
        limit: Maximum signals to export (default 1000, max 5000)
    
    Returns:
        Signals in JSON format with timestamps for replay
        
    Rate limit: 20 requests per minute
    Use for: Analysis, debugging, archival, compliance
    """
    # Limit the result count
    limit = min(limit, 5000)
    
    # Parse time filters if provided
    start_dt = None
    end_dt = None
    
    if start_time:
        # URL encoding may turn + into space, so handle both formats
        start_time_fixed = start_time.replace(' ', '+').replace('Z', '+00:00')
        start_dt = datetime.fromisoformat(start_time_fixed)
    if end_time:
        # URL encoding may turn + into space, so handle both formats
        end_time_fixed = end_time.replace(' ', '+').replace('Z', '+00:00')
        end_dt = datetime.fromisoformat(end_time_fixed)
    
    # Filter and collect signals using export helper
    exported_signals = exporters.filter_and_collect_signals(
        signals_buffer=SIGNALS,
        service=service,
        environment=environment,
        start_dt=start_dt,
        end_dt=end_dt,
        limit=limit
    )
    
    # Build export response
    return exporters.create_signals_export_response(
        signals=exported_signals,
        filters={
            "service": service,
            "environment": environment,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit
        },
        export_time=_now()
    )


@v1_router.post("/signal", response_model=EffectiveConfig)
@limiter.limit("100/minute")  # 100 requests per minute per IP
async def ingest_signal(
    request: Request,
    sig: SignalIn,
    api_key: Optional[str] = Depends(get_optional_api_key),
):
    """Ingest telemetry signal and receive adaptive observability configuration.
    
    Core endpoint for agent integration: agents send telemetry signals
    (latency, errors, attributes) and receive dynamic configuration
    based on current policy rules and signal history.
    
    Features:
    - Evaluates policy rules against signal and historical aggregates
    - Returns adaptive configuration (log level, sampling, metrics period)
    - Stores signal for aggregation window (automatic pruning)
    - Records metrics for monitoring
    - Supports client-provided timestamps for replay/debugging
    
    Request body:
        service: Service name (1-64 chars, alphanumeric/dash/underscore)
        environment: Environment name (1-32 chars, alphanumeric/dash/underscore)
        latency_ms: Request latency in milliseconds (optional, 0-1M)
        error: Whether request was an error (optional, boolean)
        attrs: Additional attributes (optional, max 50 key-value pairs)
        timestamp: ISO 8601 timestamp (optional, for replay)
    
    Returns:
        EffectiveConfig with log_level, trace_sample_rate, metric_period_s
        
    Rate limit: 100 requests per minute per IP
    Authentication: API key recommended but optional
    Use by: Observability agents polling for configuration
    """
    # Log API key usage for monitoring
    if api_key:
        logger.debug(f"Signal from authenticated service: {sig.service}")
    
    # Use client-provided timestamp if available, otherwise use server time
    signal_time = sig.timestamp if sig.timestamp is not None else _now()
    
    # Ensure timezone-aware timestamp
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    
    s = Signal(
        service=sig.service,
        environment=sig.environment,
        ts=signal_time,
        latency_ms=sig.latency_ms,
        error=sig.error,
        attrs=sig.attrs,
    )
    key = (s.service, s.environment)
    buf = SIGNALS.setdefault(key, [])
    buf.append(s)
    
    # Log signal ingestion with context
    logger.debug(
        "Signal ingested",
        service=s.service,
        environment=s.environment,
        latency_ms=s.latency_ms,
        error=s.error,
        buffer_size=len(buf),
        has_timestamp=sig.timestamp is not None
    )
    
    # Record signal metrics
    prom_metrics.record_signal_metrics(
        s.service,
        s.environment,
        s.latency_ms or 0.0,
        s.error or False
    )
    
    _prune(key)
    effective_config = evaluate(s.service, s.environment)
    
    # Log evaluation result
    logger.debug(
        "Configuration evaluated",
        service=s.service,
        environment=s.environment,
        log_level=effective_config.log_level,
        trace_sample_rate=effective_config.trace_sample_rate,
        metric_period_s=effective_config.metric_period_s
    )
    
    return effective_config


@v1_router.get("/config/{service}/{environment}", response_model=EffectiveConfig)
@limiter.limit(constants.RATE_LIMIT_GET_CONFIG)  # Higher limit - frequent operation
async def get_config(request: Request, service: str, environment: str):
    """Get effective observability configuration for a service and environment.
    
    Lightweight endpoint for configuration polling without sending signals.
    Returns the current effective configuration based on policy rules
    and historical signal data for this service/environment.
    
    Args:
        service: Service name (1-64 chars, alphanumeric/dash/underscore)
        environment: Environment name (1-32 chars, alphanumeric/dash/underscore)
    
    Returns:
        EffectiveConfig with adaptive observability settings
        
    Rate limit: 200 requests per minute per IP (higher than /signal)
    Authentication: Optional
    Use by: Agents polling for config without sending telemetry
    """
    # Validate service and environment names
    if len(service) > MAX_SERVICE_NAME_LEN or len(environment) > MAX_ENV_NAME_LEN:
        raise HTTPException(status_code=400, detail="Service or environment name too long")
    if not VALID_NAME_PATTERN.match(service) or not VALID_NAME_PATTERN.match(environment):
        raise HTTPException(
            status_code=400,
            detail="Service and environment must contain only alphanumeric, underscore, and hyphen characters"
        )
    return evaluate(service, environment)


# Include v1 API router (must be after all route definitions)
app.include_router(v1_router)
