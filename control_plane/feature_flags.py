"""
Feature flag integration for adaptive observability policies.

Supports multiple feature flag providers:
- LaunchDarkly
- Split.io
- Custom HTTP endpoints
- Static configuration (for testing)

Features:
- Provider abstraction with pluggable backends
- TTL-based caching to reduce API calls
- Fallback values for reliability
- Async-ready for non-blocking evaluation
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import os
import json
import asyncio
from dataclasses import dataclass
from loguru import logger


@dataclass
class FeatureFlagResult:
    """Result of a feature flag evaluation."""
    flag_key: str
    value: bool
    source: str  # "cache", "provider", "fallback"
    evaluated_at: datetime
    
    
class FeatureFlagCache:
    """Simple TTL-based cache for feature flag evaluations."""
    
    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple[bool, datetime]] = {}
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[bool]:
        """Get cached value if not expired."""
        async with self._lock:
            if key in self._cache:
                value, cached_at = self._cache[key]
                if datetime.now() - cached_at < timedelta(seconds=self.ttl_seconds):
                    return value
                else:
                    # Expired, remove from cache
                    del self._cache[key]
            return None
    
    async def set(self, key: str, value: bool) -> None:
        """Cache a value with current timestamp."""
        async with self._lock:
            self._cache[key] = (value, datetime.now())
    
    async def clear(self) -> None:
        """Clear all cached values."""
        async with self._lock:
            self._cache.clear()
    
    def size(self) -> int:
        """Return number of cached entries."""
        return len(self._cache)


class FeatureFlagProvider(ABC):
    """Abstract base class for feature flag providers."""
    
    @abstractmethod
    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Dict[str, Any]] = None,
        default: bool = False
    ) -> bool:
        """
        Evaluate a feature flag.
        
        Args:
            flag_key: The feature flag key/name
            context: Optional context (user, service, environment, etc.)
            default: Fallback value if evaluation fails
            
        Returns:
            bool: The feature flag value
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Return the provider name."""
        pass


class StaticFeatureFlagProvider(FeatureFlagProvider):
    """Static feature flag provider using configuration."""
    
    def __init__(self, flags: Optional[Dict[str, bool]] = None):
        """
        Initialize with static flag values.
        
        Args:
            flags: Dictionary mapping flag keys to boolean values
        """
        self.flags = flags or {}
    
    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Dict[str, Any]] = None,
        default: bool = False
    ) -> bool:
        """Evaluate flag from static configuration."""
        return self.flags.get(flag_key, default)
    
    def get_name(self) -> str:
        return "static"
    
    def set_flag(self, flag_key: str, value: bool) -> None:
        """Set a flag value (useful for testing)."""
        self.flags[flag_key] = value


class LaunchDarklyProvider(FeatureFlagProvider):
    """LaunchDarkly feature flag provider."""
    
    def __init__(self, sdk_key: Optional[str] = None):
        """
        Initialize LaunchDarkly provider.
        
        Args:
            sdk_key: LaunchDarkly SDK key (can also use LD_SDK_KEY env var)
        """
        self.sdk_key = sdk_key or os.getenv("LD_SDK_KEY")
        self._client = None
        
        if not self.sdk_key:
            logger.warning("LaunchDarkly SDK key not configured")
    
    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Dict[str, Any]] = None,
        default: bool = False
    ) -> bool:
        """Evaluate flag using LaunchDarkly SDK."""
        if not self.sdk_key:
            logger.warning(f"LaunchDarkly not configured, returning default for {flag_key}")
            return default
        
        try:
            # Lazy import to avoid requiring launchdarkly-server-sdk
            if self._client is None:
                import ldclient
                from ldclient.config import Config
                ldclient.set_config(Config(self.sdk_key))
                self._client = ldclient.get()
            
            # Build user context from provided context
            user = context or {"key": "anonymous"}
            result = self._client.variation(flag_key, user, default)
            return bool(result)
        except ImportError:
            logger.error("launchdarkly-server-sdk not installed")
            return default
        except Exception as e:
            logger.error(f"Error evaluating LaunchDarkly flag {flag_key}: {e}")
            return default
    
    def get_name(self) -> str:
        return "launchdarkly"


class SplitIOProvider(FeatureFlagProvider):
    """Split.io feature flag provider."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Split.io provider.
        
        Args:
            api_key: Split.io API key (can also use SPLIT_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("SPLIT_API_KEY")
        self._factory = None
        
        if not self.api_key:
            logger.warning("Split.io API key not configured")
    
    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Dict[str, Any]] = None,
        default: bool = False
    ) -> bool:
        """Evaluate flag using Split.io SDK."""
        if not self.api_key:
            logger.warning(f"Split.io not configured, returning default for {flag_key}")
            return default
        
        try:
            # Lazy import to avoid requiring splitio-client
            if self._factory is None:
                from splitio import get_factory
                self._factory = get_factory(self.api_key)
                await asyncio.sleep(1)  # Wait for SDK initialization
            
            client = self._factory.client()
            
            # Extract user key from context
            user_key = context.get("key", "anonymous") if context else "anonymous"
            treatment = client.get_treatment(user_key, flag_key)
            
            # Split.io returns "on"/"off" strings
            return treatment == "on"
        except ImportError:
            logger.error("splitio-client not installed")
            return default
        except Exception as e:
            logger.error(f"Error evaluating Split.io flag {flag_key}: {e}")
            return default
    
    def get_name(self) -> str:
        return "splitio"


class CustomHTTPProvider(FeatureFlagProvider):
    """Custom HTTP endpoint feature flag provider."""
    
    def __init__(self, endpoint_url: Optional[str] = None, auth_token: Optional[str] = None):
        """
        Initialize custom HTTP provider.
        
        Args:
            endpoint_url: Base URL for feature flag API
            auth_token: Optional authentication token
        """
        self.endpoint_url = endpoint_url or os.getenv("FF_ENDPOINT_URL")
        self.auth_token = auth_token or os.getenv("FF_AUTH_TOKEN")
        
        if not self.endpoint_url:
            logger.warning("Custom feature flag endpoint not configured")
    
    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Dict[str, Any]] = None,
        default: bool = False
    ) -> bool:
        """Evaluate flag by calling custom HTTP endpoint."""
        if not self.endpoint_url:
            logger.warning(f"Custom endpoint not configured, returning default for {flag_key}")
            return default
        
        try:
            import httpx
            
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
            
            # Make async HTTP request
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.endpoint_url}/evaluate",
                    json={"flag_key": flag_key, "context": context},
                    headers=headers,
                    timeout=2.0  # 2 second timeout
                )
                response.raise_for_status()
                data = response.json()
                return bool(data.get("value", default))
        except ImportError:
            logger.error("httpx not installed for custom HTTP provider")
            return default
        except Exception as e:
            logger.error(f"Error evaluating custom HTTP flag {flag_key}: {e}")
            return default
    
    def get_name(self) -> str:
        return "custom_http"


class FeatureFlagService:
    """
    Main feature flag service with caching and provider management.
    
    Usage:
        ff_service = FeatureFlagService(provider="static")
        result = await ff_service.evaluate("my-flag", context={"service": "api"})
    """
    
    def __init__(
        self,
        provider: Optional[FeatureFlagProvider] = None,
        cache_ttl: int = 60
    ):
        """
        Initialize feature flag service.
        
        Args:
            provider: Feature flag provider instance
            cache_ttl: Cache TTL in seconds (default: 60)
        """
        self.provider = provider or StaticFeatureFlagProvider()
        self.cache = FeatureFlagCache(ttl_seconds=cache_ttl)
        logger.info(f"Feature flag service initialized with {self.provider.get_name()} provider")
    
    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Dict[str, Any]] = None,
        default: bool = False,
        use_cache: bool = True
    ) -> FeatureFlagResult:
        """
        Evaluate a feature flag with caching.
        
        Args:
            flag_key: The feature flag key
            context: Optional evaluation context
            default: Fallback value
            use_cache: Whether to use cache (default: True)
            
        Returns:
            FeatureFlagResult with value and metadata
        """
        # Build cache key including context for context-sensitive flags
        cache_key = self._build_cache_key(flag_key, context)
        
        # Check cache first
        if use_cache:
            cached_value = await self.cache.get(cache_key)
            if cached_value is not None:
                return FeatureFlagResult(
                    flag_key=flag_key,
                    value=cached_value,
                    source="cache",
                    evaluated_at=datetime.now()
                )
        
        # Evaluate from provider
        try:
            value = await self.provider.evaluate(flag_key, context, default)
            source = "provider"
            
            # Cache the result
            if use_cache:
                await self.cache.set(cache_key, value)
        except Exception as e:
            logger.error(f"Error evaluating feature flag {flag_key}: {e}")
            value = default
            source = "fallback"
        
        return FeatureFlagResult(
            flag_key=flag_key,
            value=value,
            source=source,
            evaluated_at=datetime.now()
        )
    
    def _build_cache_key(self, flag_key: str, context: Optional[Dict[str, Any]]) -> str:
        """Build cache key including context for proper cache isolation."""
        if not context:
            return flag_key
        
        # Include relevant context fields in cache key
        context_str = json.dumps(context, sort_keys=True)
        return f"{flag_key}:{context_str}"
    
    async def clear_cache(self) -> None:
        """Clear the feature flag cache."""
        await self.cache.clear()
        logger.info("Feature flag cache cleared")
    
    def get_cache_size(self) -> int:
        """Get number of cached flag evaluations."""
        return self.cache.size()
    
    def get_provider_name(self) -> str:
        """Get the current provider name."""
        return self.provider.get_name()


# Global feature flag service instance
_ff_service: Optional[FeatureFlagService] = None


def init_feature_flags(
    provider_type: str = "static",
    cache_ttl: int = 60,
    **provider_kwargs
) -> FeatureFlagService:
    """
    Initialize the global feature flag service.
    
    Args:
        provider_type: Provider type ("static", "launchdarkly", "splitio", "custom")
        cache_ttl: Cache TTL in seconds
        **provider_kwargs: Additional provider-specific arguments
        
    Returns:
        Initialized FeatureFlagService
    """
    global _ff_service
    
    # Create provider based on type
    if provider_type == "static":
        provider = StaticFeatureFlagProvider(flags=provider_kwargs.get("flags", {}))
    elif provider_type == "launchdarkly":
        provider = LaunchDarklyProvider(sdk_key=provider_kwargs.get("sdk_key"))
    elif provider_type == "splitio":
        provider = SplitIOProvider(api_key=provider_kwargs.get("api_key"))
    elif provider_type == "custom":
        provider = CustomHTTPProvider(
            endpoint_url=provider_kwargs.get("endpoint_url"),
            auth_token=provider_kwargs.get("auth_token")
        )
    else:
        logger.warning(f"Unknown provider type '{provider_type}', using static")
        provider = StaticFeatureFlagProvider()
    
    _ff_service = FeatureFlagService(provider=provider, cache_ttl=cache_ttl)
    return _ff_service


def get_feature_flag_service() -> FeatureFlagService:
    """Get the global feature flag service instance."""
    global _ff_service
    if _ff_service is None:
        # Initialize with default static provider if not already initialized
        _ff_service = init_feature_flags()
    return _ff_service
