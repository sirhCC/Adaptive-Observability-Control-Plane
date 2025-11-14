"""Observability metrics for the control plane."""
from prometheus_client import Counter, Histogram, Gauge, Info
import time
from functools import wraps
from typing import Callable
from loguru import logger

# API Metrics
http_requests_total = Counter(
    'control_plane_http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

http_request_duration_seconds = Histogram(
    'control_plane_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint']
)

# Policy Engine Metrics
policy_evaluations_total = Counter(
    'control_plane_policy_evaluations_total',
    'Total policy evaluations',
    ['service', 'environment']
)

policy_evaluation_duration_seconds = Histogram(
    'control_plane_policy_evaluation_duration_seconds',
    'Policy evaluation duration in seconds',
    ['service', 'environment']
)

rule_matches_total = Counter(
    'control_plane_rule_matches_total',
    'Total rule matches',
    ['rule_id', 'service', 'environment']
)

# Signal Metrics
signals_ingested_total = Counter(
    'control_plane_signals_ingested_total',
    'Total signals ingested',
    ['service', 'environment']
)

signals_with_errors_total = Counter(
    'control_plane_signals_with_errors_total',
    'Total signals indicating errors',
    ['service', 'environment']
)

signal_latency_ms = Histogram(
    'control_plane_signal_latency_ms',
    'Signal latency values in milliseconds',
    ['service', 'environment'],
    buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
)

# Buffer Metrics
signal_buffer_size = Gauge(
    'control_plane_signal_buffer_size',
    'Current size of signal buffer',
    labelnames=['service', 'environment']
)

signal_buffer_pruned_total = Counter(
    'control_plane_signal_buffer_pruned_total',
    'Total signals pruned from buffer',
    ['service', 'environment']
)

# Policy Management Metrics
policy_updates_total = Counter(
    'control_plane_policy_updates_total',
    'Total policy updates',
    ['policy_id']
)

policy_validation_errors_total = Counter(
    'control_plane_policy_validation_errors_total',
    'Total policy validation errors'
)

# Database Metrics
db_queries_total = Counter(
    'control_plane_db_queries_total',
    'Total database queries',
    ['operation', 'table']
)

db_query_duration_seconds = Histogram(
    'control_plane_db_query_duration_seconds',
    'Database query duration in seconds',
    ['operation', 'table']
)

# Control Plane Info
control_plane_info = Info(
    'control_plane',
    'Control plane version and build information'
)


def track_evaluation_time(service: str, environment: str):
    """Decorator to track policy evaluation time."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                policy_evaluations_total.labels(
                    service=service,
                    environment=environment
                ).inc()
                return result
            finally:
                duration = time.time() - start
                policy_evaluation_duration_seconds.labels(
                    service=service,
                    environment=environment
                ).observe(duration)
        return wrapper
    return decorator


def track_db_operation(operation: str, table: str):
    """Decorator to track database operation time."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                db_queries_total.labels(
                    operation=operation,
                    table=table
                ).inc()
                return result
            finally:
                duration = time.time() - start
                db_query_duration_seconds.labels(
                    operation=operation,
                    table=table
                ).observe(duration)
        return wrapper
    return decorator


def record_signal_metrics(service: str, environment: str, latency_ms: float, error: bool):
    """Record metrics for an ingested signal."""
    signals_ingested_total.labels(
        service=service,
        environment=environment
    ).inc()
    
    if error:
        signals_with_errors_total.labels(
            service=service,
            environment=environment
        ).inc()
    
    if latency_ms is not None:
        signal_latency_ms.labels(
            service=service,
            environment=environment
        ).observe(latency_ms)


def update_buffer_size(service: str, environment: str, size: int):
    """Update signal buffer size gauge."""
    signal_buffer_size.labels(
        service=service,
        environment=environment
    ).set(size)


def record_buffer_pruned(service: str, environment: str, count: int):
    """Record number of signals pruned from buffer."""
    if count > 0:
        signal_buffer_pruned_total.labels(
            service=service,
            environment=environment
        ).inc(count)


def record_rule_match(rule_id: str, service: str, environment: str):
    """Record when a rule matches."""
    rule_matches_total.labels(
        rule_id=rule_id,
        service=service,
        environment=environment
    ).inc()


def record_policy_update(policy_id: str):
    """Record policy update."""
    policy_updates_total.labels(policy_id=policy_id).inc()
    logger.info(f"Policy updated: {policy_id}")


def record_policy_validation_error():
    """Record policy validation error."""
    policy_validation_errors_total.inc()
    logger.warning("Policy validation error occurred")
