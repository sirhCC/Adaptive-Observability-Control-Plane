"""Signal Ingestion and Export Endpoints"""

from fastapi import APIRouter, Request, Depends
from typing import Optional
from datetime import datetime, timezone
from loguru import logger

from control_plane import constants, exporters
from control_plane import metrics as prom_metrics
from control_plane.auth import get_optional_api_key


def _get_main():
    """Lazy import to avoid circular dependencies."""
    import control_plane.main as main
    return main


# Get models for type annotations (FastAPI needs them at import time)
def _get_models():
    main = _get_main()
    return main.SignalIn, main.EffectiveConfig

SignalIn, EffectiveConfig = _get_models()

router = APIRouter(tags=["signals"])


@router.get("/signals/export")
async def export_signals(
    request: Request,
    service: Optional[str] = None,
    environment: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 1000
):
    """Export signals for offline analysis and archival."""
    main = _get_main()
    limit = min(limit, 5000)
    
    start_dt = None
    end_dt = None
    
    if start_time:
        start_time_fixed = start_time.replace(' ', '+').replace('Z', '+00:00')
        start_dt = datetime.fromisoformat(start_time_fixed)
    if end_time:
        end_time_fixed = end_time.replace(' ', '+').replace('Z', '+00:00')
        end_dt = datetime.fromisoformat(end_time_fixed)
    
    exported_signals = exporters.filter_and_collect_signals(
        signals_buffer=main.SIGNALS,
        service=service,
        environment=environment,
        start_dt=start_dt,
        end_dt=end_dt,
        limit=limit
    )
    
    return exporters.create_signals_export_response(
        signals=exported_signals,
        filters={
            "service": service,
            "environment": environment,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit
        },
        export_time=main._now()
    )


@router.post("/signal", response_model=EffectiveConfig)
async def ingest_signal(
    request: Request,
    sig: SignalIn,
    api_key: Optional[str] = Depends(get_optional_api_key),
):
    """Ingest telemetry signal and receive adaptive observability configuration."""
    main = _get_main()
    
    if api_key:
        logger.debug(f"Signal from authenticated service: {sig.service}")
    
    signal_time = sig.timestamp if sig.timestamp is not None else main._now()
    
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    
    s = main.Signal(
        service=sig.service,
        environment=sig.environment,
        ts=signal_time,
        latency_ms=sig.latency_ms,
        error=sig.error,
        attrs=sig.attrs,
    )
    
    if main.signal_service:
        main.signal_service.ingest_signal(s)
        effective_config = main.evaluate(s.service, s.environment)
    else:
        key = (s.service, s.environment)
        buf = main.SIGNALS.setdefault(key, [])
        buf.append(s)
        
        logger.debug(
            "Signal ingested",
            service=s.service,
            environment=s.environment,
            latency_ms=s.latency_ms,
            error=s.error,
            buffer_size=len(buf),
            has_timestamp=sig.timestamp is not None
        )
        
        prom_metrics.record_signal_metrics(
            s.service,
            s.environment,
            s.latency_ms or 0.0,
            s.error or False
        )
        
        main._prune(key)
        effective_config = main.evaluate(s.service, s.environment)
    
    logger.debug(
        "Configuration evaluated",
        service=s.service,
        environment=s.environment,
        log_level=effective_config.log_level,
        trace_sample_rate=effective_config.trace_sample_rate,
        metric_period_s=effective_config.metric_period_s
    )
    
    return effective_config
