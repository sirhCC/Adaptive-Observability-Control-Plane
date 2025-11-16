"""Health check service.

Handles health and readiness check logic:
- Database connectivity checks
- Signal buffer health
- Application status
"""

from typing import Dict, Any, Optional
from datetime import datetime
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from control_plane import constants


class HealthService:
    """Service for health and readiness checks."""
    
    def __init__(self, policy_state: Any, signals_buffer: Dict):
        """Initialize health service.
        
        Args:
            policy_state: Reference to global POLICY object
            signals_buffer: Reference to global SIGNALS buffer
        """
        self.policy_state = policy_state
        self.signals_buffer = signals_buffer
    
    async def check_health(self, db: AsyncSession) -> Dict[str, Any]:
        """Perform comprehensive health check.
        
        Args:
            db: Database session for connectivity check
            
        Returns:
            Dict with health status and component details
        """
        from control_plane.main import _now
        
        health_status = {
            "status": "healthy",
            "timestamp": _now().isoformat(),
            "components": {}
        }
        
        # Check database
        db_health = await self._check_database_health(db)
        if db_health["status"] == "unhealthy":
            health_status["status"] = "degraded"
            health_status["components"]["database"] = "unhealthy"
        else:
            health_status["components"]["database"] = "healthy"
        
        # Check signal buffer
        buffer_health = self._check_buffer_health()
        if buffer_health["status"] == "unhealthy":
            health_status["status"] = "degraded"
            health_status["components"]["signal_buffer"] = "unhealthy"
        else:
            health_status["components"]["signal_buffer"] = {
                "status": "healthy",
                "total_signals": buffer_health["total_signals"],
                "services": buffer_health["services"]
            }
        
        return health_status
    
    async def check_readiness(self, db: AsyncSession) -> Dict[str, Any]:
        """Perform readiness check for accepting traffic.
        
        Args:
            db: Database session for connectivity check
            
        Returns:
            Dict with readiness status and check details
        """
        from control_plane.main import _now
        
        readiness_status = {
            "ready": True,
            "timestamp": _now().isoformat(),
            "checks": {}
        }
        
        # Check database connectivity (critical for readiness)
        db_health = await self._check_database_health(db)
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
            if self.policy_state and self.policy_state.rules:
                readiness_status["checks"]["policy"] = {
                    "status": "ready",
                    "message": f"Policy initialized with {len(self.policy_state.rules)} rules"
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
        
        return readiness_status
    
    async def _check_database_health(self, db: AsyncSession) -> Dict[str, Any]:
        """Check database connectivity and health.
        
        Args:
            db: Database session
            
        Returns:
            Dict with database health status
        """
        try:
            # Simple query to verify database connectivity
            from sqlalchemy import text
            result = await db.execute(text("SELECT 1"))
            result.fetchone()
            
            return {
                "status": "healthy",
                "message": "Database connection successful"
            }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }
    
    def _check_buffer_health(self) -> Dict[str, Any]:
        """Check signal buffer health and status.
        
        Returns:
            Dict with buffer health information
        """
        total_signals = sum(len(buf) for buf in self.signals_buffer.values())
        service_count = len(self.signals_buffer)
        
        # Check if buffer is approaching limits
        max_total = constants.MAX_SIGNALS_PER_SERVICE * 100  # Reasonable upper bound
        if total_signals > max_total:
            return {
                "status": "unhealthy",
                "message": f"Signal buffer overfull: {total_signals} signals",
                "total_signals": total_signals,
                "services": service_count
            }
        
        return {
            "status": "healthy",
            "total_signals": total_signals,
            "services": service_count
        }
