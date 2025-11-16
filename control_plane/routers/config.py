"""Configuration Retrieval Endpoints"""

from fastapi import APIRouter, HTTPException, Request
from control_plane import constants

# Import from main.py
from control_plane.main import (
    EffectiveConfig, limiter,
    MAX_SERVICE_NAME_LEN, MAX_ENV_NAME_LEN, VALID_NAME_PATTERN,
    evaluate, config_service
)

router = APIRouter(tags=["config"])


@router.get("/config/{service}/{environment}", response_model=EffectiveConfig)
@limiter.limit(constants.RATE_LIMIT_GET_CONFIG)
async def get_config(request: Request, service: str, environment: str):
    """Get effective observability configuration for a service and environment."""
    if config_service:
        return config_service.get_effective_config(service, environment)
    
    # Fallback for tests
    if len(service) > MAX_SERVICE_NAME_LEN or len(environment) > MAX_ENV_NAME_LEN:
        raise HTTPException(status_code=400, detail="Service or environment name too long")
    if not VALID_NAME_PATTERN.match(service) or not VALID_NAME_PATTERN.match(environment):
        raise HTTPException(
            status_code=400,
            detail="Service and environment must contain only alphanumeric, underscore, and hyphen characters"
        )
    return evaluate(service, environment)
