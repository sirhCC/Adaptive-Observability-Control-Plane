"""Authentication Endpoints"""

from fastapi import APIRouter, Depends, Request
from control_plane.auth import require_admin_key
from control_plane import constants
import uuid

router = APIRouter(tags=["auth"])


def _get_main():
    """Lazy import of main module to avoid circular dependencies."""
    import control_plane.main as main
    return main


@router.post("/auth/generate-key")
async def generate_key(
    request: Request,
    description: str = "API key",
    admin: str = Depends(require_admin_key)
):
    """Generate a new API key (admin only).
    
    Args:
        description: Human-readable description of the key's purpose
        
    Returns:
        Generated API key and metadata
        
    Rate limit: 5 requests per minute (handled by decorator in main.py)
    Authentication: Admin key required
    """
    new_key = str(uuid.uuid4())
    return {
        "api_key": new_key,
        "description": description,
        "created_at": str(request.state.request_start_time) if hasattr(request.state, "request_start_time") else None,
        "note": "Store this key securely - it cannot be retrieved later"
    }
