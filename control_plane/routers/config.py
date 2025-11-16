"""Configuration Retrieval Endpoints"""

from fastapi import APIRouter, HTTPException, Request
from control_plane import constants


def _get_main():
    """Lazy import to avoid circular dependencies."""
    import control_plane.main as main
    return main


router = APIRouter(tags=["config"])


@router.get("/config/{service}/{environment}")
async def get_config(request: Request, service: str, environment: str):
    """Get effective observability configuration for a service and environment."""
    main = _get_main()
    
    if main.config_service:
        return main.config_service.get_effective_config(service, environment)
    
    # Fallback for tests
    if len(service) > main.MAX_SERVICE_NAME_LEN or len(environment) > main.MAX_ENV_NAME_LEN:
        raise HTTPException(status_code=400, detail="Service or environment name too long")
    if not main.VALID_NAME_PATTERN.match(service) or not main.VALID_NAME_PATTERN.match(environment):
        raise HTTPException(
            status_code=400,
            detail="Service and environment must contain only alphanumeric, underscore, and hyphen characters"
        )
    return main.evaluate(service, environment)
