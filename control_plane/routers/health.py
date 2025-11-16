"""Health and Readiness Check Endpoints"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from control_plane.database import get_db

# Import from main.py - these will be available after main.py loads
from control_plane.main import (
    _now,
    _check_database_health,
    _check_signal_buffer_health,
    POLICY,
    health_service
)

router = APIRouter(tags=["health"])


@router.get("/healthz")
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
    if health_service:
        # Use health service for health checks
        health_status = await health_service.check_health(db)
    else:
        # Fallback for tests
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
    
    return JSONResponse(content=health_status, status_code=status_code)


@router.get("/readyz")
async def readyz(db: AsyncSession = Depends(get_db)):
    """Readiness check endpoint for Kubernetes/Docker.
    
    Returns 200 if service is ready to accept traffic, 503 otherwise.
    Checks critical dependencies like database connectivity.
    """
    if health_service:
        # Use health service for readiness checks
        readiness_status = await health_service.check_readiness(db)
    else:
        # Fallback for tests
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
    
    return JSONResponse(content=readiness_status, status_code=status_code)


@router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint.
    
    Returns:
        Prometheus-formatted metrics including:
        - HTTP request duration by endpoint and method
        - Request count by status code
        - Active requests gauge
        - Custom business metrics (policy changes, signals ingested, etc.)
        
    Format: Prometheus text exposition format
    Use by: Prometheus, Grafana, monitoring systems
    """
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response as PrometheusResponse
    
    metrics_data = generate_latest()
    return PrometheusResponse(content=metrics_data, media_type=CONTENT_TYPE_LATEST)
