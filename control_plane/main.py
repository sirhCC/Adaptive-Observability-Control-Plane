from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional
import re
import os
import time
import json
import yaml

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response
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

# Policy version history for time-travel debugging
class PolicyVersion(BaseModel):
    """Represents a policy configuration at a specific point in time."""
    policy: Policy
    applied_at: datetime
    applied_by: Optional[str] = None

# Store up to 100 historical policy versions
POLICY_HISTORY: List[PolicyVersion] = []
MAX_POLICY_HISTORY = 100


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


@v1_router.get("/policy/export")
@limiter.limit("20/minute")
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
    export_data = {
        "policy": POLICY.model_dump(),
        "exported_at": _now().isoformat(),
        "version": "1.0"
    }
    
    if include_history and POLICY_HISTORY:
        export_data["history"] = {
            "versions_available": len(POLICY_HISTORY),
            "oldest_version": POLICY_HISTORY[0].applied_at.isoformat() if POLICY_HISTORY else None,
            "newest_version": POLICY_HISTORY[-1].applied_at.isoformat() if POLICY_HISTORY else None
        }
    
    if format == "yaml":
        yaml_content = yaml.dump(export_data, default_flow_style=False, sort_keys=False)
        return Response(
            content=yaml_content,
            media_type="application/x-yaml",
            headers={"Content-Disposition": f"attachment; filename=policy-{POLICY.id}.yaml"}
        )
    else:
        json_content = json.dumps(export_data, indent=2)
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=policy-{POLICY.id}.json"}
        )


@v1_router.post("/policy/import")
@limiter.limit("10/minute")
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
    logger.info(f"Policy '{imported_policy.id}' imported successfully by {admin}")
    
    return {
        "imported": True,
        "policy": imported_policy.model_dump(),
        "message": f"Policy '{imported_policy.id}' imported and applied successfully"
    }


@v1_router.get("/policy/templates")
@limiter.limit("50/minute")
async def get_policy_templates(request: Request):
    """Get policy templates/presets for common scenarios.
    
    Returns pre-configured policy templates that can be used as starting points
    for different observability strategies.
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
    
    Args:
        template_name: One of: production-safe, development, performance-focused, 
                       cost-optimized, balanced
    
    Returns:
        Policy template configuration ready to import
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
    logger.info(f"Policy '{req.policy.id}' updated with {len(req.policy.rules)} rules by {admin}")
    
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
    
    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None:
            # Reject timestamps more than 7 days in the past or future
            now = datetime.now(timezone.utc)
            max_past = now - timedelta(days=7)
            max_future = now + timedelta(days=1)
            
            # Ensure timezone-aware comparison
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            
            if v < max_past:
                raise ValueError("Timestamp cannot be more than 7 days in the past")
            if v > max_future:
                raise ValueError("Timestamp cannot be more than 1 day in the future")
        
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


@v1_router.get("/history/policy")
@limiter.limit("20/minute")
async def get_policy_history(
    request: Request,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 50
):
    """Get policy version history for time-travel debugging.
    
    Query historical policy configurations to understand what policy was active
    at a given time. Useful for debugging and auditing.
    
    Args:
        start_time: ISO 8601 timestamp to filter from (optional)
        end_time: ISO 8601 timestamp to filter to (optional)
        limit: Maximum number of versions to return (default 50, max 100)
    
    Returns:
        List of policy versions with timestamps and who applied them
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
    
    Time-travel debugging: returns the policy configuration that would have been
    active at the specified timestamp.
    
    Args:
        timestamp: ISO 8601 timestamp to query
    
    Returns:
        The policy that was active at that time, or current policy if no history
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
    """Replay historical signals to see what configs would have been returned.
    
    Time-travel debugging: replay past signals with either the current policy
    or the policy that was active at a specific time. Shows what configuration
    would have been returned, enabling "what would have happened" analysis.
    
    All signals must include timestamps. Use this to understand how policy
    changes would affect past behavior.
    
    Args:
        signals: Historical signals to replay (must include timestamps)
        policy_timestamp: If provided, uses policy active at this time. Otherwise uses current policy.
    
    Returns:
        Replay results showing effective config for each signal
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
    
    # Replay each signal
    results = []
    
    for idx, sig_in in enumerate(req.signals):
        # Convert SignalIn to Signal (use client-provided timestamp)
        # We validated above that all signals have timestamps
        signal_time = sig_in.timestamp
        assert signal_time is not None, "Signal timestamp must not be None"
        
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
            
        replay_signal = Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=signal_time,
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        )
        
        # Create mock buffer for aggregates (in real replay, could use historical data)
        mock_buffer = [replay_signal]
        agg = _calc_aggregates(mock_buffer)
        
        # Compute effective config using the selected policy
        effective_config = EffectiveConfig(
            service=replay_signal.service,
            environment=replay_signal.environment
        )
        
        matched_rules = []
        
        for rule in policy_to_use.rules:
            if not rule.enabled:
                continue
                
            # Check if rule scope matches
            if rule.service and rule.service != "*" and rule.service != replay_signal.service:
                continue
            if rule.environment and rule.environment != "*" and rule.environment != replay_signal.environment:
                continue
            
            # Evaluate conditions
            match = True
            for cond in rule.conditions:
                if not _eval_condition(cond, replay_signal, agg):
                    match = False
                    break
            
            if match:
                matched_rules.append({
                    "rule_id": rule.id,
                    "priority": rule.priority,
                    "description": rule.description
                })
                
                # Apply actions
                if rule.actions.log_level:
                    effective_config.log_level = rule.actions.log_level
                if rule.actions.trace_sample_rate is not None:
                    effective_config.trace_sample_rate = rule.actions.trace_sample_rate
                if rule.actions.metric_period_s is not None:
                    effective_config.metric_period_s = rule.actions.metric_period_s
        
        results.append({
            "signal_index": idx,
            "signal_timestamp": signal_time.isoformat(),
            "service": replay_signal.service,
            "environment": replay_signal.environment,
            "matched_rules": matched_rules,
            "effective_config": effective_config.model_dump()
        })
    
    return {
        "replay_results": results,
        "total_signals": len(req.signals),
        "policy_info": policy_info
    }


@v1_router.post("/compare")
@limiter.limit("20/minute")
async def compare_policies(request: Request, req: CompareRequest):
    """Compare how different policies would handle the same signals.
    
    "What would have happened" analysis: shows how effective configs would differ
    across multiple policy versions for the same signals. Useful for understanding
    the impact of policy changes.
    
    Args:
        signals: Signals to analyze (can be historical or synthetic)
        compare_policies: List of policy timestamps to compare, or 'current' for current policy
    
    Returns:
        Comparison showing effective configs and differences across policies
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
    
    # Compare each signal across all policies
    comparison_results = []
    
    for idx, sig_in in enumerate(req.signals):
        # Convert SignalIn to Signal
        signal_time = sig_in.timestamp if sig_in.timestamp else _now()
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
            
        test_signal = Signal(
            service=sig_in.service,
            environment=sig_in.environment,
            ts=signal_time,
            latency_ms=sig_in.latency_ms,
            error=sig_in.error,
            attrs=sig_in.attrs,
        )
        
        # Create mock buffer for aggregates
        mock_buffer = [test_signal]
        agg = _calc_aggregates(mock_buffer)
        
        # Evaluate with each policy
        policy_results = []
        
        for policy_info in policies_to_compare:
            policy = policy_info["policy"]
            
            effective_config = EffectiveConfig(
                service=test_signal.service,
                environment=test_signal.environment
            )
            
            matched_rules = []
            
            for rule in policy.rules:
                if not rule.enabled:
                    continue
                    
                # Check if rule scope matches
                if rule.service and rule.service != "*" and rule.service != test_signal.service:
                    continue
                if rule.environment and rule.environment != "*" and rule.environment != test_signal.environment:
                    continue
                
                # Evaluate conditions
                match = True
                for cond in rule.conditions:
                    if not _eval_condition(cond, test_signal, agg):
                        match = False
                        break
                
                if match:
                    matched_rules.append(rule.id)
                    
                    # Apply actions
                    if rule.actions.log_level:
                        effective_config.log_level = rule.actions.log_level
                    if rule.actions.trace_sample_rate is not None:
                        effective_config.trace_sample_rate = rule.actions.trace_sample_rate
                    if rule.actions.metric_period_s is not None:
                        effective_config.metric_period_s = rule.actions.metric_period_s
            
            policy_results.append({
                "policy_label": policy_info["label"],
                "policy_id": policy_info["policy_id"],
                "applied_at": policy_info["applied_at"],
                "matched_rules": matched_rules,
                "effective_config": effective_config.model_dump()
            })
        
        # Calculate differences
        configs = [pr["effective_config"] for pr in policy_results]
        differences = []
        
        # Compare each config with the first one
        base_config = configs[0]
        for i, config in enumerate(configs[1:], 1):
            diff = {}
            for key in ["log_level", "trace_sample_rate", "metric_period_s"]:
                if base_config[key] != config[key]:
                    diff[key] = {
                        "from": base_config[key],
                        "to": config[key]
                    }
            if diff:
                differences.append({
                    "from_policy": policy_results[0]["policy_label"],
                    "to_policy": policy_results[i]["policy_label"],
                    "changes": diff
                })
        
        comparison_results.append({
            "signal_index": idx,
            "service": test_signal.service,
            "environment": test_signal.environment,
            "policy_results": policy_results,
            "has_differences": len(differences) > 0,
            "differences": differences
        })
    
    # Summary statistics
    signals_with_differences = sum(1 for r in comparison_results if r["has_differences"])
    
    return {
        "comparison_results": comparison_results,
        "total_signals": len(req.signals),
        "policies_compared": len(policies_to_compare),
        "summary": {
            "signals_with_differences": signals_with_differences,
            "signals_without_differences": len(req.signals) - signals_with_differences
        }
    }


@v1_router.get("/signals/export")
@limiter.limit("20/minute")
async def export_signals(
    request: Request,
    service: Optional[str] = None,
    environment: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 1000
):
    """Export signals for offline analysis.
    
    Download signals from the buffer for offline analysis, debugging, or archival.
    Supports filtering by service, environment, and time range.
    
    Args:
        service: Filter by service name (optional)
        environment: Filter by environment (optional)
        start_time: ISO 8601 timestamp to filter from (optional)
        end_time: ISO 8601 timestamp to filter to (optional)
        limit: Maximum number of signals to export (default 1000, max 5000)
    
    Returns:
        Signals in JSON format suitable for replay or offline analysis
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
    
    # Collect signals from buffer
    exported_signals = []
    
    for key, buffer in SIGNALS.items():
        svc, env = key
        
        # Apply service/environment filters
        if service and svc != service:
            continue
        if environment and env != environment:
            continue
        
        # Filter and export signals
        for signal in buffer:
            # Apply time range filters
            if start_dt and signal.ts < start_dt:
                continue
            if end_dt and signal.ts > end_dt:
                continue
            
            exported_signals.append({
                "service": signal.service,
                "environment": signal.environment,
                "timestamp": signal.ts.isoformat(),
                "latency_ms": signal.latency_ms,
                "error": signal.error,
                "attrs": signal.attrs
            })
            
            # Check limit
            if len(exported_signals) >= limit:
                break
        
        if len(exported_signals) >= limit:
            break
    
    # Sort by timestamp
    exported_signals.sort(key=lambda s: s["timestamp"])
    
    return {
        "signals": exported_signals,
        "count": len(exported_signals),
        "filters": {
            "service": service,
            "environment": environment,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit
        },
        "export_time": _now().isoformat()
    }


@v1_router.post("/signal", response_model=EffectiveConfig)
@limiter.limit("100/minute")  # 100 requests per minute per IP
async def ingest_signal(
    request: Request,
    sig: SignalIn,
    api_key: Optional[str] = Depends(get_optional_api_key),
):
    """Ingest telemetry signal and return effective configuration. API key recommended.
    
    Supports client-provided timestamps for replay/debugging scenarios.
    If timestamp is not provided, server time is used.
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
