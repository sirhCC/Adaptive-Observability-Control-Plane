"""Authentication Endpoints"""

from fastapi import APIRouter, Depends, Request
from control_plane.auth import require_admin_key
from control_plane import constants
import uuid

router = APIRouter(tags=["auth"])


@router.post("/auth/generate-key")
@Depends(require_admin_key)
async def generate_key(
    request: Request,
    description: str = "API key"
):
    """Generate a new API key (admin only).
    
    Args:
        description: Human-readable description of the key's purpose
        
    Returns:
        Generated API key and metadata
        
    Rate limit: 5 requests per minute
    Authentication: Admin key required
    """
    new_key = str(uuid.uuid4())
    return {
        "api_key": new_key,
        "description": description,
        "created_at": str(request.state.request_start_time) if hasattr(request.state, "request_start_time") else None,
        "note": "Store this key securely - it cannot be retrieved later"
    }
