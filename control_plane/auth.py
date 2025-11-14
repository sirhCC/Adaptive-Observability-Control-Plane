"""Authentication and authorization utilities."""
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from jose import JWTError, jwt
from passlib.context import CryptContext
from loguru import logger

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
API_KEY_NAME = "X-API-Key"

# Admin API key for policy management (should be set via environment)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", None)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security schemes
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> dict:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"Token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """Dependency to validate API key for agent authentication."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    # In production, validate against database of registered API keys
    # For now, accept any non-empty API key for agent endpoints
    if len(api_key) < 10:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format",
        )
    
    logger.debug(f"API key validated: {api_key[:8]}...")
    return api_key


async def require_admin_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """Dependency to require admin API key for sensitive operations."""
    if not ADMIN_API_KEY:
        # If no admin key is configured, allow operation (backward compatible)
        logger.warning("ADMIN_API_KEY not configured - skipping auth check")
        return "admin"
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    if api_key != ADMIN_API_KEY:
        logger.warning(f"Invalid admin API key attempt")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin credentials",
        )
    
    logger.info("Admin access granted")
    return "admin"


async def get_optional_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """Optional API key - doesn't fail if missing."""
    return api_key


def generate_api_key() -> str:
    """Generate a new secure API key."""
    return f"aoc_{secrets.token_urlsafe(32)}"
