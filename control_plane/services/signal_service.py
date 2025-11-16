"""Signal ingestion and management service.

Handles all signal-related business logic:
- Signal validation and ingestion
- Buffer management and pruning
- Signal retrieval and filtering
- Statistics computation
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
from loguru import logger

from control_plane import constants


class SignalService:
    """Service for managing signal ingestion and storage."""
    
    def __init__(self, signals_buffer: Dict[Tuple[str, str], List[Any]], window_max: int = 300):
        """Initialize signal service with buffer management.
        
        Args:
            signals_buffer: Reference to global SIGNALS buffer
            window_max: Maximum time window in seconds to retain signals
        """
        self.signals_buffer = signals_buffer
        self.window_max = window_max
    
    def ingest_signal(self, signal: Any) -> Dict[str, Any]:
        """Ingest a new signal into the buffer with validation.
        
        Args:
            signal: Signal object to ingest
            
        Returns:
            Dict with ingestion status and metadata
        """
        from control_plane.main import _now
        
        key = (signal.service, signal.environment)
        
        # Validate signal
        validation = self._validate_signal(signal)
        if not validation["valid"]:
            logger.warning(
                f"Signal validation failed for {signal.service}/{signal.environment}",
                extra={"errors": validation["errors"]}
            )
            return {
                "status": "rejected",
                "errors": validation["errors"]
            }
        
        # Initialize buffer if needed
        if key not in self.signals_buffer:
            self.signals_buffer[key] = []
        
        # Add signal to buffer
        self.signals_buffer[key].append(signal)
        
        # Prune old signals and enforce limits
        self._prune_buffer(key)
        
        buffer_size = len(self.signals_buffer[key])
        
        logger.info(
            f"Signal ingested: {signal.service}/{signal.environment}",
            extra={
                "service": signal.service,
                "environment": signal.environment,
                "buffer_size": buffer_size,
                "has_error": signal.error or False,
                "latency_ms": signal.latency_ms
            }
        )
        
        return {
            "status": "accepted",
            "buffer_size": buffer_size,
            "service": signal.service,
            "environment": signal.environment
        }
    
    def get_signals(
        self,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[Any]:
        """Retrieve signals with optional filtering.
        
        Args:
            service: Optional service name filter
            environment: Optional environment filter
            since: Optional timestamp to filter signals after
            limit: Optional maximum number of signals to return
            
        Returns:
            List of signals matching the filters
        """
        all_signals = []
        
        for (svc, env), signals in self.signals_buffer.items():
            # Apply service filter
            if service and svc != service:
                continue
            
            # Apply environment filter
            if environment and env != environment:
                continue
            
            # Apply time filter
            filtered_signals = signals
            if since:
                filtered_signals = [s for s in signals if s.ts >= since]
            
            all_signals.extend(filtered_signals)
        
        # Sort by timestamp (newest first)
        all_signals.sort(key=lambda s: s.ts, reverse=True)
        
        # Apply limit
        if limit:
            all_signals = all_signals[:limit]
        
        return all_signals
    
    def get_buffer_stats(self) -> Dict[str, Any]:
        """Get statistics about the signal buffer.
        
        Returns:
            Dict with buffer statistics
        """
        total_signals = sum(len(buf) for buf in self.signals_buffer.values())
        service_count = len(self.signals_buffer)
        
        services = []
        for (service, env), buf in self.signals_buffer.items():
            error_count = sum(1 for s in buf if getattr(s, 'error', False))
            avg_latency = None
            if buf:
                latencies = [s.latency_ms for s in buf if s.latency_ms is not None]
                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
            
            services.append({
                "service": service,
                "environment": env,
                "signal_count": len(buf),
                "error_count": error_count,
                "avg_latency_ms": avg_latency
            })
        
        return {
            "total_signals": total_signals,
            "service_count": service_count,
            "services": services
        }
    
    def clear_signals(
        self,
        service: Optional[str] = None,
        environment: Optional[str] = None
    ) -> Dict[str, Any]:
        """Clear signals from buffer with optional filtering.
        
        Args:
            service: Optional service name to clear
            environment: Optional environment to clear
            
        Returns:
            Dict with count of cleared signals
        """
        cleared = 0
        
        if service and environment:
            # Clear specific service/environment
            key = (service, environment)
            if key in self.signals_buffer:
                cleared = len(self.signals_buffer[key])
                del self.signals_buffer[key]
        elif service:
            # Clear all environments for service
            keys_to_delete = [
                k for k in self.signals_buffer.keys()
                if k[0] == service
            ]
            for key in keys_to_delete:
                cleared += len(self.signals_buffer[key])
                del self.signals_buffer[key]
        elif environment:
            # Clear all services for environment
            keys_to_delete = [
                k for k in self.signals_buffer.keys()
                if k[1] == environment
            ]
            for key in keys_to_delete:
                cleared += len(self.signals_buffer[key])
                del self.signals_buffer[key]
        else:
            # Clear everything
            cleared = sum(len(buf) for buf in self.signals_buffer.values())
            self.signals_buffer.clear()
        
        logger.info(f"Cleared {cleared} signals", extra={"count": cleared})
        
        return {
            "cleared": cleared,
            "remaining": sum(len(buf) for buf in self.signals_buffer.values())
        }
    
    def _validate_signal(self, signal: Any) -> Dict[str, Any]:
        """Validate a signal for correctness.
        
        Args:
            signal: Signal to validate
            
        Returns:
            Validation result with any errors
        """
        errors = []
        
        # Validate service name
        if not signal.service or len(signal.service) > constants.MAX_SERVICE_NAME_LEN:
            errors.append(f"Invalid service name length (max {constants.MAX_SERVICE_NAME_LEN})")
        
        # Validate environment name
        if not signal.environment or len(signal.environment) > constants.MAX_ENV_NAME_LEN:
            errors.append(f"Invalid environment name length (max {constants.MAX_ENV_NAME_LEN})")
        
        # Validate timestamp
        if not signal.ts:
            errors.append("Missing timestamp")
        else:
            # Check if timestamp is in the future
            from control_plane.main import _now
            if signal.ts > _now() + timedelta(minutes=5):
                errors.append("Timestamp too far in the future")
        
        # Validate latency if present
        if signal.latency_ms is not None:
            if signal.latency_ms < 0:
                errors.append("Latency cannot be negative")
            if signal.latency_ms > 300000:  # 5 minutes
                errors.append("Latency value suspiciously high")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    
    def _prune_buffer(self, key: Tuple[str, str]):
        """Prune old signals and enforce max buffer size.
        
        Args:
            key: (service, environment) tuple identifying the buffer
        """
        from control_plane.main import _now
        
        buf = self.signals_buffer.get(key)
        if not buf:
            return
        
        # Remove old signals
        cutoff = _now() - timedelta(seconds=self.window_max)
        buf = [s for s in buf if s.ts >= cutoff]
        
        # Enforce max buffer size (keep most recent)
        if len(buf) > constants.MAX_SIGNALS_PER_SERVICE:
            buf = sorted(buf, key=lambda s: s.ts, reverse=True)[:constants.MAX_SIGNALS_PER_SERVICE]
        
        self.signals_buffer[key] = buf
