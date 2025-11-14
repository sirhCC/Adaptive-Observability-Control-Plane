"""Custom exceptions and error handlers for the control plane."""
from typing import Optional, Dict, Any
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
import traceback
from loguru import logger


class ControlPlaneException(Exception):
    """Base exception for control plane errors."""
    
    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class PolicyValidationError(ControlPlaneException):
    """Raised when policy validation fails."""
    
    def __init__(self, message: str, conflicts: list = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"conflicts": conflicts or []}
        )


class PolicyNotFoundError(ControlPlaneException):
    """Raised when a requested policy is not found."""
    
    def __init__(self, policy_id: str):
        super().__init__(
            message=f"Policy '{policy_id}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"policy_id": policy_id}
        )


class SignalProcessingError(ControlPlaneException):
    """Raised when signal processing fails."""
    
    def __init__(self, message: str, signal_data: Optional[Dict] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details={"signal_data": signal_data}
        )


class DatabaseError(ControlPlaneException):
    """Raised when database operations fail."""
    
    def __init__(self, message: str, operation: Optional[str] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"operation": operation}
        )


class AuthenticationError(ControlPlaneException):
    """Raised when authentication fails."""
    
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            details={}
        )


class AuthorizationError(ControlPlaneException):
    """Raised when authorization fails."""
    
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            details={}
        )


class RateLimitError(ControlPlaneException):
    """Raised when rate limit is exceeded."""
    
    def __init__(self, message: str = "Rate limit exceeded", retry_after: Optional[int] = None):
        details = {}
        if retry_after:
            details["retry_after"] = retry_after
        super().__init__(
            message=message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details=details
        )


async def control_plane_exception_handler(request: Request, exc: ControlPlaneException) -> JSONResponse:
    """Handle custom control plane exceptions."""
    logger.error(f"Control plane error: {exc.message} (status={exc.status_code})")
    
    response_data = {
        "error": exc.__class__.__name__,
        "message": exc.message,
        "status_code": exc.status_code,
    }
    
    if exc.details:
        response_data["details"] = exc.details
    
    return JSONResponse(
        status_code=exc.status_code,
        content=response_data
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic validation errors with detailed messages."""
    errors = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        errors.append({
            "field": field,
            "message": error["msg"],
            "type": error["type"]
        })
    
    logger.warning(f"Validation error on {request.url.path}: {len(errors)} error(s)")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "message": "Request validation failed",
            "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "details": {
                "errors": errors,
                "error_count": len(errors)
            }
        }
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions with proper logging."""
    # Log full traceback for debugging
    logger.error(f"Unexpected error on {request.url.path}: {exc}")
    logger.error(traceback.format_exc())
    
    # Don't expose internal details in production
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred",
            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "details": {
                "type": exc.__class__.__name__,
                # Only include exception message in non-production
                # In production, this should be removed or gated by DEBUG flag
                "message": str(exc) if True else None  # TODO: Add DEBUG flag check
            }
        }
    )


def register_exception_handlers(app):
    """Register all exception handlers with the FastAPI app."""
    app.add_exception_handler(ControlPlaneException, control_plane_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
