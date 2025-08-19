from control_plane.main import evaluate, SIGNALS, Signal, _now


def setup_function(_):
    SIGNALS.clear()


def test_defaults_prod():
    cfg = evaluate("svc", "prod")
    assert cfg.log_level == "INFO"
    assert abs(cfg.trace_sample_rate - 0.2) < 1e-9
    assert cfg.metric_period_s == 30


def test_elevate_on_errors():
    # Generate 100 signals with 10% errors over recent window
    svc, env = "svc", "prod"
    key = (svc, env)
    import random

    for i in range(100):
        SIGNALS.setdefault(key, []).append(
            Signal(
                service=svc,
                environment=env,
                ts=_now(),
                latency_ms=100 + random.random() * 50,
                error=(i % 10 == 0),
                attrs={},
            )
        )
    cfg = evaluate(svc, env)
    assert cfg.log_level == "DEBUG"
    assert cfg.trace_sample_rate >= 0.4
    assert cfg.metric_period_s <= 20
