"""API Routers

This package contains all API route handlers organized by domain:
- policy: Policy management endpoints
- signal: Signal ingestion and export
- config: Configuration retrieval
- health: Health and readiness checks
- auth: Authentication endpoints
- simulation: Policy simulation and replay
"""

from control_plane.routers.policy import router as policy_router
from control_plane.routers.signal import router as signal_router
from control_plane.routers.config import router as config_router
from control_plane.routers.health import router as health_router
from control_plane.routers.auth import router as auth_router
from control_plane.routers.simulation import router as simulation_router

__all__ = [
    "policy_router",
    "signal_router",
    "config_router",
    "health_router",
    "auth_router",
    "simulation_router",
]
