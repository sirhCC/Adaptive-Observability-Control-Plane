"""Storage and state management for the control plane.

This module manages:
- In-memory signal buffers
- Current policy state
- Policy version history
- Buffer pruning and limits
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from loguru import logger

from control_plane.schemas import Policy, Signal, PolicyVersion, Rule, Action, Condition, MergeStrategy
from control_plane import metrics as prom_metrics


# Configuration constants
MAX_SIGNALS_PER_SERVICE = 10000  # Max signals to keep per (service, env)
WINDOW_MAX = 5 * 60  # seconds to keep raw events
MAX_POLICY_HISTORY = 100  # Maximum policy versions to retain


# Global state
# Current active policy
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

# Policy version history for time-travel debugging
POLICY_HISTORY: List[PolicyVersion] = []


def _now() -> datetime:
    """Get current UTC time with timezone awareness."""
    return datetime.now(timezone.utc)


def _prune(key: tuple[str, str]) -> None:
    """Prune oldest signals if buffer exceeds max size.
    
    Args:
        key: Tuple of (service, environment) identifying the signal buffer
    """
    cutoff = _now() - timedelta(seconds=WINDOW_MAX)
    buf = SIGNALS.get(key)
    if not buf:
        return
    
    # Remove signals older than WINDOW_MAX
    original_len = len(buf)
    buf[:] = [s for s in buf if s.ts > cutoff]
    
    # Also enforce max buffer size
    if len(buf) > MAX_SIGNALS_PER_SERVICE:
        # Remove oldest signals beyond limit
        excess = len(buf) - MAX_SIGNALS_PER_SERVICE
        buf[:] = buf[excess:]
        logger.warning(f"Buffer for {key} exceeded max size, pruned {excess} oldest signals")
    
    pruned = original_len - len(buf)
    if pruned > 0:
        service, env = key
        prom_metrics.signal_buffer_pruned_total.labels(service=service, environment=env).inc(pruned)


def add_signal(service: str, environment: str, signal: Signal) -> None:
    """Add a signal to the buffer and prune if necessary.
    
    Args:
        service: Service name
        environment: Environment name
        signal: Signal to add
    """
    key = (service, environment)
    buf = SIGNALS.setdefault(key, [])
    buf.append(signal)
    
    # Record signal metrics
    prom_metrics.record_signal_metrics(
        service,
        environment,
        signal.latency_ms or 0.0,
        signal.error or False
    )
    
    # Prune old signals
    _prune(key)


def get_signals(service: str, environment: str) -> List[Signal]:
    """Get all signals for a service/environment.
    
    Args:
        service: Service name
        environment: Environment name
    
    Returns:
        List of signals (empty list if none exist)
    """
    key = (service, environment)
    return SIGNALS.get(key, [])


def get_all_signals() -> Dict[tuple[str, str], List[Signal]]:
    """Get all signals across all services/environments.
    
    Returns:
        Dictionary mapping (service, environment) tuples to signal lists
    """
    return SIGNALS


def update_policy(new_policy: Policy, applied_by: Optional[str] = None) -> None:
    """Update the current policy and save to history.
    
    Args:
        new_policy: New policy to apply
        applied_by: Admin identifier who applied the policy
    """
    global POLICY, POLICY_HISTORY
    
    # Update current policy
    POLICY = new_policy
    
    # Save to history
    version = PolicyVersion(
        policy=new_policy.model_copy(deep=True),
        applied_at=_now(),
        applied_by=applied_by
    )
    POLICY_HISTORY.append(version)
    
    # Prune old history
    if len(POLICY_HISTORY) > MAX_POLICY_HISTORY:
        POLICY_HISTORY = POLICY_HISTORY[-MAX_POLICY_HISTORY:]
    
    prom_metrics.record_policy_update(new_policy.id)
    logger.info(f"Policy '{new_policy.id}' updated with {len(new_policy.rules)} rules by {applied_by or 'unknown'}")


def get_policy() -> Policy:
    """Get the current active policy.
    
    Returns:
        Current policy
    """
    return POLICY


def get_policy_history() -> List[PolicyVersion]:
    """Get all policy versions in history.
    
    Returns:
        List of policy versions ordered by application time
    """
    return POLICY_HISTORY


def get_buffer_stats() -> Dict[str, int]:
    """Get statistics about signal buffers.
    
    Returns:
        Dictionary with total_signals and service_count
    """
    total_signals = sum(len(buf) for buf in SIGNALS.values())
    service_count = len(SIGNALS)
    
    return {
        "total_signals": total_signals,
        "service_count": service_count
    }
