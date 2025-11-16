"""API Routers

This package contains all API route handlers organized by domain:
- policy: Policy management endpoints
- signal: Signal ingestion and export
- config: Configuration retrieval
- health: Health and readiness checks
- auth: Authentication endpoints
- simulation: Policy simulation and replay

Import routers directly from their modules to avoid circular dependencies:
    from control_plane.routers.health import router as health_router
    from control_plane.routers.auth import router as auth_router
"""

# Don't auto-import all routers to avoid circular dependencies
# Import individually as needed

__all__ = []
