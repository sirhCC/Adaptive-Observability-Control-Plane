"""
Tests for feature flag system (Item #17).
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

from control_plane.feature_flags import (
    FeatureFlagResult,
    FeatureFlagCache,
    FeatureFlagProvider,
    StaticFeatureFlagProvider,
    LaunchDarklyProvider,
    SplitIOProvider,
    CustomHTTPProvider,
    FeatureFlagService,
    init_feature_flags,
    get_feature_flag_service,
)


class TestFeatureFlagCache:
    """Test the TTL-based cache."""
    
    @pytest.mark.asyncio
    async def test_cache_set_and_get(self):
        """Test basic cache set and get operations."""
        cache = FeatureFlagCache(ttl_seconds=60)
        
        await cache.set("test-key", True)
        cached = await cache.get("test-key")
        
        assert cached is not None
        assert cached is True
    
    @pytest.mark.asyncio
    async def test_cache_expiration(self):
        """Test that cached entries expire after TTL."""
        cache = FeatureFlagCache(ttl_seconds=1)
        
        await cache.set("test-key", True)
        
        # Should be cached immediately
        assert await cache.get("test-key") is not None
        
        # Wait for expiration
        await asyncio.sleep(1.1)
        
        # Should be expired
        assert await cache.get("test-key") is None
    
    @pytest.mark.asyncio
    async def test_cache_clear(self):
        """Test cache clearing."""
        cache = FeatureFlagCache(ttl_seconds=60)
        
        await cache.set("test-key-1", True)
        await cache.set("test-key-2", False)
        
        assert await cache.get("test-key-1") is not None
        assert await cache.get("test-key-2") is not None
        
        await cache.clear()
        
        assert await cache.get("test-key-1") is None
        assert await cache.get("test-key-2") is None


class TestStaticFeatureFlagProvider:
    """Test the static feature flag provider."""
    
    @pytest.mark.asyncio
    async def test_static_flag_enabled(self):
        """Test evaluating an enabled static flag."""
        provider = StaticFeatureFlagProvider({"test-flag": True})
        result = await provider.evaluate("test-flag", {})
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_static_flag_disabled(self):
        """Test evaluating a disabled static flag."""
        provider = StaticFeatureFlagProvider({"test-flag": False})
        result = await provider.evaluate("test-flag", {})
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_static_flag_missing(self):
        """Test evaluating a missing flag falls back to default."""
        provider = StaticFeatureFlagProvider({})
        result = await provider.evaluate("missing-flag", {}, default=True)
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_static_flag_set(self):
        """Test dynamically setting flags."""
        provider = StaticFeatureFlagProvider({})
        provider.set_flag("dynamic-flag", True)
        
        result = await provider.evaluate("dynamic-flag", {})
        assert result is True


# Note: Tests for LaunchDarkly, Split.io, and Custom HTTP providers
# are skipped because they use optional dependencies with lazy imports.
# These providers are tested through integration tests and manual verification.


class TestFeatureFlagService:
    """Test the main feature flag service."""
    
    @pytest.mark.asyncio
    async def test_service_caching(self):
        """Test that service caches evaluation results."""
        provider = StaticFeatureFlagProvider({"test-flag": True})
        service = FeatureFlagService(provider, cache_ttl=60)
        
        # First evaluation
        result1 = await service.evaluate("test-flag", {})
        assert result1.source == "provider"
        
        # Second evaluation should hit cache
        result2 = await service.evaluate("test-flag", {})
        assert result2.source == "cache"
    
    @pytest.mark.asyncio
    async def test_service_context_sensitive_caching(self):
        """Test that cache keys include context."""
        provider = StaticFeatureFlagProvider({"test-flag": True})
        service = FeatureFlagService(provider, cache_ttl=60)
        
        # Different contexts should have separate cache entries
        result1 = await service.evaluate("test-flag", {"user": "alice"})
        result2 = await service.evaluate("test-flag", {"user": "bob"})
        
        # Both should be from provider (different cache keys)
        assert result1.source == "provider"
        assert result2.source == "provider"
        
        # Same context should hit cache
        result3 = await service.evaluate("test-flag", {"user": "alice"})
        assert result3.source == "cache"
    
    @pytest.mark.asyncio
    async def test_service_cache_clear(self):
        """Test clearing the service cache."""
        provider = StaticFeatureFlagProvider({"test-flag": True})
        service = FeatureFlagService(provider, cache_ttl=60)
        
        # Cache a result
        result1 = await service.evaluate("test-flag", {})
        assert result1.source == "provider"
        
        result2 = await service.evaluate("test-flag", {})
        assert result2.source == "cache"
        
        # Clear cache
        await service.clear_cache()
        
        # Should fetch from provider again
        result3 = await service.evaluate("test-flag", {})
        assert result3.source == "provider"
    
    @pytest.mark.asyncio
    async def test_service_default_value(self):
        """Test that service uses default values correctly."""
        provider = StaticFeatureFlagProvider({})
        service = FeatureFlagService(provider, cache_ttl=60)
        
        result = await service.evaluate("missing-flag", {}, default=True)
        assert result.value is True


class TestFeatureFlagInitialization:
    """Test feature flag initialization functions."""
    
    def test_init_static_provider(self):
        """Test initializing with static provider."""
        init_feature_flags(provider_type="static", cache_ttl=30)
        service = get_feature_flag_service()
        
        assert service is not None
        assert isinstance(service.provider, StaticFeatureFlagProvider)
    
    @patch.dict("os.environ", {"LD_SDK_KEY": "test-sdk-key"})
    def test_init_launchdarkly_provider(self):
        """Test initializing with LaunchDarkly provider."""
        init_feature_flags(provider_type="launchdarkly", cache_ttl=30)
        service = get_feature_flag_service()
        
        assert service is not None
        assert isinstance(service.provider, LaunchDarklyProvider)
    
    @patch.dict("os.environ", {"SPLIT_API_KEY": "test-api-key"})
    def test_init_splitio_provider(self):
        """Test initializing with Split.io provider."""
        init_feature_flags(provider_type="splitio", cache_ttl=30)
        service = get_feature_flag_service()
        
        assert service is not None
        assert isinstance(service.provider, SplitIOProvider)
    
    @patch.dict("os.environ", {"FF_ENDPOINT_URL": "http://example.com", "FF_AUTH_TOKEN": "token"})
    def test_init_custom_provider(self):
        """Test initializing with custom HTTP provider."""
        init_feature_flags(provider_type="custom", cache_ttl=30)
        service = get_feature_flag_service()
        
        assert service is not None
        assert isinstance(service.provider, CustomHTTPProvider)
    
    def test_get_service_singleton(self):
        """Test that get_feature_flag_service returns singleton."""
        init_feature_flags(provider_type="static")
        service1 = get_feature_flag_service()
        service2 = get_feature_flag_service()
        
        assert service1 is service2
