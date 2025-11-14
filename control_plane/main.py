from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional
import re
import os
import time

from fastapi import FastAPI, HTTPException, Request, Depends
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
from control_plane.exceptions import (
    register_exception_handlers,
    PolicyValidationError,
    SignalProcessingError,
    DatabaseError
)

# Configuration
MAX_SIGNALS_PER_SERVICE = 10000  # Max signals to keep per (service, env)
MAX_SERVICE_NAME_LEN = 64
MAX_ENV_NAME_LEN = 32
VALID_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

# Rate limiter setup
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Adaptive Observability Control Plane", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Register custom exception handlers
register_exception_handlers(app)

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


class Policy(BaseModel):
    id: str
    description: Optional[str] = None
    rules: List[Rule] = Field(default_factory=list)


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


# --- Helpers

def _now() -> datetime:
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


op_map = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


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
    
    return False


# --- Rule evaluation

def evaluate(service: str, env: str) -> EffectiveConfig:
    start_time = time.time()
    key = (service, env)
    _prune(key)
    buf = SIGNALS.get(key, [])

    effective = EffectiveConfig(service=service, environment=env)

    for rule in sorted((r for r in POLICY.rules if r.enabled), key=lambda r: r.priority):
        # scope match
        if rule.service and rule.service != service:
            continue
        if rule.environment and rule.environment != env:
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

        # Apply actions (last writer wins within ordered rules)
        a = rule.actions
        if a.log_level:
            effective.log_level = a.log_level
        if a.trace_sample_rate is not None:
            effective.trace_sample_rate = a.trace_sample_rate
        if a.metric_period_s is not None:
            effective.metric_period_s = a.metric_period_s

    # Record evaluation metrics
    duration = time.time() - start_time
    prom_metrics.policy_evaluations_total.labels(service=service, environment=env).inc()
    prom_metrics.policy_evaluation_duration_seconds.labels(service=service, environment=env).observe(duration)

    return effective


# --- Startup

@app.on_event("startup")
async def startup_event():
    """Initialize database and seed default policy if needed."""
    await init_db()
    
    # Set control plane info
    prom_metrics.control_plane_info.info({
        'version': app.version,
        'title': app.title,
    })
    
    logger.info("Prometheus metrics enabled at /metrics")
    
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
            logger.info("Seeded default policy to database")
        break  # Only need one iteration


# --- API
class UpsertPolicy(BaseModel):
    policy: Policy


@v1_router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)):
    """Health check endpoint with component status."""
    health_status = {
        "status": "healthy",
        "timestamp": _now().isoformat(),
        "components": {}
    }
    
    # Check database connectivity
    try:
        # Simple query to verify database is accessible
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        health_status["components"]["database"] = "healthy"
    except Exception as e:
        health_status["components"]["database"] = "unhealthy"
        health_status["status"] = "degraded"
        logger.error(f"Database health check failed: {e}")
    
    # Check signal buffer status
    try:
        total_signals = sum(len(buf) for buf in SIGNALS.values())
        health_status["components"]["signal_buffer"] = {
            "status": "healthy",
            "total_signals": total_signals,
            "services": len(SIGNALS)
        }
    except Exception as e:
        health_status["components"]["signal_buffer"] = "unhealthy"
        health_status["status"] = "degraded"
        logger.error(f"Signal buffer health check failed: {e}")
    
    # Set HTTP status code based on health
    status_code = 200 if health_status["status"] == "healthy" else 503
    
    from fastapi.responses import JSONResponse
    return JSONResponse(content=health_status, status_code=status_code)


@v1_router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    
    metrics_data = generate_latest()
    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)


@v1_router.post("/auth/generate-key")
@limiter.limit("5/minute")
async def generate_key(
    request: Request,
    admin: str = Depends(require_admin_key),
):
    """Generate a new API key for agent authentication. Requires admin access."""
    from control_plane.auth import generate_api_key
    
    new_key = generate_api_key()
    logger.info(f"Generated new API key: {new_key[:12]}...")
    return {
        "api_key": new_key,
        "created_at": _now().isoformat(),
        "note": "Store this key securely - it won't be shown again",
    }


@v1_router.get("/policy", response_model=Policy)
@limiter.limit("50/minute")
async def get_policy(request: Request):
    """Get the current policy configuration."""
    return POLICY


@v1_router.post("/policy/validate")
@limiter.limit("20/minute")
async def validate_policy(request: Request, req: UpsertPolicy):
    """Validate a policy configuration without applying it."""
    from control_plane.rule_validator import validate_policy_rules
    
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
@limiter.limit("10/minute")  # Strict limit on policy updates
async def set_policy(
    request: Request,
    req: UpsertPolicy,
    admin: str = Depends(require_admin_key),
    dry_run: bool = False,
):
    """Update the policy configuration. Requires admin API key.
    
    Args:
        dry_run: If True, validate the policy but don't apply it.
            Returns validation results without applying changes.
            
    Returns:
        If dry_run is False: Policy object that was applied
        If dry_run is True: Dict with validation results and policy preview
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
    prom_metrics.record_policy_update(req.policy.id)
    logger.info(f"Policy '{req.policy.id}' updated with {len(req.policy.rules)} rules")
    
    return POLICY


class SignalIn(BaseModel):
    service: str = Field(..., min_length=1, max_length=MAX_SERVICE_NAME_LEN)
    environment: str = Field(..., min_length=1, max_length=MAX_ENV_NAME_LEN)
    latency_ms: Optional[float] = Field(None, ge=0.0, le=1_000_000.0)
    error: Optional[bool] = None
    attrs: Dict[str, str] = Field(default_factory=dict, max_length=50)
    
    @field_validator('service', 'environment')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not VALID_NAME_PATTERN.match(v):
            raise ValueError(f"Name must contain only alphanumeric, underscore, and hyphen characters: {v}")
        return v
    
    @field_validator('attrs')
    @classmethod
    def validate_attrs(cls, v: Dict[str, str]) -> Dict[str, str]:
        for key, value in v.items():
            if len(key) > 128 or len(value) > 1024:
                raise ValueError("Attribute keys must be ≤128 chars, values ≤1024 chars")
        return v


class SimulateRequest(BaseModel):
    """Request to simulate policy evaluation with test signals."""
    policy: Policy
    test_signals: List[SignalIn] = Field(
        ..., 
        min_length=1, 
        max_length=100,
        description="Test signals to evaluate (1-100)"
    )


@v1_router.post("/policy/simulate")
@limiter.limit("20/minute")
async def simulate_policy(request: Request, req: SimulateRequest):
    """Simulate policy evaluation with test signals.
    
    Shows which rules would match for each test signal without applying the policy.
    Useful for testing policy changes before deployment.
    """
    results = []
    
    for idx, sig_in in enumerate(req.test_signals):
        # Convert SignalIn to Signal
        test_signal = Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=_now(),
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        )
        
        # Create mock buffer with just this signal for aggregates
        mock_buffer = [test_signal]
        agg = _calc_aggregates(mock_buffer)
        
        # Evaluate which rules would match
        matched_rules = []
        effective_config = EffectiveConfig(
            service=test_signal.service,
            environment=test_signal.environment
        )
        
        for rule in req.policy.rules:
            if not rule.enabled:
                continue
                
            # Check if rule scope matches
            if rule.service and rule.service != "*" and rule.service != test_signal.service:
                continue
            if rule.environment and rule.environment != "*" and rule.environment != test_signal.environment:
                continue
            
            # Evaluate conditions
            match = True
            condition_results = []
            
            for cond in rule.conditions:
                cond_match = _eval_condition(cond, test_signal, agg)
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
            
            if match:
                matched_rules.append({
                    "rule_id": rule.id,
                    "priority": rule.priority,
                    "description": rule.description,
                    "conditions": condition_results,
                    "actions": rule.actions.model_dump(exclude_none=True)
                })
                
                # Apply actions to effective config
                if rule.actions.log_level:
                    effective_config.log_level = rule.actions.log_level
                if rule.actions.trace_sample_rate is not None:
                    effective_config.trace_sample_rate = rule.actions.trace_sample_rate
                if rule.actions.metric_period_s is not None:
                    effective_config.metric_period_s = rule.actions.metric_period_s
        
        results.append({
            "signal_index": idx,
            "service": test_signal.service,
            "environment": test_signal.environment,
            "latency_ms": test_signal.latency_ms,
            "error": test_signal.error,
            "matched_rules": matched_rules,
            "rule_count": len(matched_rules),
            "effective_config": effective_config.model_dump()
        })
    
    return {
        "simulation_results": results,
        "total_signals": len(req.test_signals),
        "policy_id": req.policy.id,
        "summary": {
            "signals_with_matches": sum(1 for r in results if r["rule_count"] > 0),
            "signals_without_matches": sum(1 for r in results if r["rule_count"] == 0),
            "total_rule_matches": sum(r["rule_count"] for r in results)
        }
    }


@v1_router.post("/signal", response_model=EffectiveConfig)
@limiter.limit("100/minute")  # 100 requests per minute per IP
async def ingest_signal(
    request: Request,
    sig: SignalIn,
    api_key: Optional[str] = Depends(get_optional_api_key),
):
    """Ingest telemetry signal and return effective configuration. API key recommended."""
    # Log API key usage for monitoring
    if api_key:
        logger.debug(f"Signal from authenticated service: {sig.service}")
    
    s = Signal(
        service=sig.service,
        environment=sig.environment,
        ts=_now(),
        latency_ms=sig.latency_ms,
        error=sig.error,
        attrs=sig.attrs,
    )
    key = (s.service, s.environment)
    buf = SIGNALS.setdefault(key, [])
    buf.append(s)
    
    # Record signal metrics
    prom_metrics.record_signal_metrics(
        s.service,
        s.environment,
        s.latency_ms or 0.0,
        s.error or False
    )
    
    _prune(key)
    return evaluate(s.service, s.environment)


@v1_router.get("/config/{service}/{environment}", response_model=EffectiveConfig)
@limiter.limit("200/minute")  # 200 requests per minute per IP
async def get_config(request: Request, service: str, environment: str):
    """Get effective configuration for a service and environment."""
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
