from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Adaptive Observability Control Plane", version="0.1.0")


# --- Models
class Condition(BaseModel):
    kind: str = Field(description="metric|error_rate|feature_flag|time|always")
    op: str = Field(description=">|>=|<|<=|==|!=|in|contains|always")
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
    return datetime.utcnow()


def _prune(key: tuple[str, str]):
    cutoff = _now() - timedelta(seconds=WINDOW_MAX)
    buf = SIGNALS.get(key)
    if not buf:
        return
    SIGNALS[key] = [s for s in buf if s.ts >= cutoff]


def _calc_aggregates(buf: List[Signal]) -> Dict[str, float]:
    # Simple aggregates p95 and error rate over the buffer
    if not buf:
        return {"latency_p95_ms": 0.0, "error_rate": 0.0}
    latencies = [s.latency_ms for s in buf if s.latency_ms is not None]
    latencies.sort()
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0.0
    err = sum(1 for s in buf if s.error) / max(1, len(buf))
    return {"latency_p95_ms": float(p95), "error_rate": float(err)}


op_map = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# --- Rule evaluation

def evaluate(service: str, env: str) -> EffectiveConfig:
    key = (service, env)
    _prune(key)
    buf = SIGNALS.get(key, [])
    aggs = _calc_aggregates(buf)

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

        # Apply actions (last writer wins within ordered rules)
        a = rule.actions
        if a.log_level:
            effective.log_level = a.log_level
        if a.trace_sample_rate is not None:
            effective.trace_sample_rate = a.trace_sample_rate
        if a.metric_period_s is not None:
            effective.metric_period_s = a.metric_period_s

    return effective


# --- API
class UpsertPolicy(BaseModel):
    policy: Policy


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": _now().isoformat()}


@app.get("/policy", response_model=Policy)
async def get_policy():
    return POLICY


@app.post("/policy", response_model=Policy)
async def set_policy(req: UpsertPolicy):
    global POLICY
    POLICY = req.policy
    return POLICY


class SignalIn(BaseModel):
    service: str
    environment: str
    latency_ms: Optional[float] = None
    error: Optional[bool] = None
    attrs: Dict[str, str] = Field(default_factory=dict)


@app.post("/signal", response_model=EffectiveConfig)
async def ingest_signal(sig: SignalIn):
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
    _prune(key)
    return evaluate(s.service, s.environment)


@app.get("/config/{service}/{environment}", response_model=EffectiveConfig)
async def get_config(service: str, environment: str):
    return evaluate(service, environment)
