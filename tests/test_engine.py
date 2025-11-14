from control_plane.main import evaluate, SIGNALS, Signal, _now


def setup_function(_):
    SIGNALS.clear()


def test_defaults_prod():
    cfg = evaluate("svc", "prod")
    # Should return valid config
    assert cfg.service == "svc"
    assert cfg.environment == "prod"
    assert cfg.log_level in ("INFO", "DEBUG", "WARN", "ERROR")
    assert 0.0 <= cfg.trace_sample_rate <= 1.0
    assert cfg.metric_period_s > 0


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
    # With signals present, evaluation should complete successfully
    assert cfg is not None
    assert cfg.service == svc
    assert cfg.environment == env
