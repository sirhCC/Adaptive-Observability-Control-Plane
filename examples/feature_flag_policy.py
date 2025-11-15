"""
Feature Flag Policy Example

This example demonstrates how to use feature flags in policy rules.
"""

# Example 1: Enable debug logging when feature flag is on
debug_on_feature_flag_policy = {
    "id": "debug-with-feature-flag",
    "description": "Enable debug logging when debug-mode feature flag is enabled",
    "service": "my-service",
    "environment": "production",
    "rules": [
        {
            "id": "debug-on-flag",
            "conditions": [
                {
                    "kind": "feature_flag",
                    "key": "debug-mode",  # Feature flag name
                    "op": "==",
                    "value": True
                }
            ],
            "actions": [
                {"kind": "set_log_level", "target": "DEBUG"},
                {"kind": "set_trace_sample_rate", "target": 1.0}
            ]
        }
    ]
}

# Example 2: Disable metrics collection for cost savings
cost_optimization_policy = {
    "id": "cost-optimization",
    "description": "Reduce observability costs based on feature flag",
    "service": "batch-processor",
    "environment": "production",
    "rules": [
        {
            "id": "reduce-sampling",
            "conditions": [
                {
                    "kind": "feature_flag",
                    "key": "cost-savings-mode",
                    "op": "==",
                    "value": True
                }
            ],
            "actions": [
                {"kind": "set_trace_sample_rate", "target": 0.01},  # 1% sampling
                {"kind": "set_log_level", "target": "ERROR"}
            ]
        }
    ]
}

# Example 3: Combined with other conditions
hybrid_policy = {
    "id": "hybrid-conditions",
    "description": "Feature flag combined with error rate",
    "service": "api",
    "environment": "production",
    "rules": [
        {
            "id": "debug-on-errors-and-flag",
            "merge_strategy": "union",  # Combine with other rules
            "conditions": [
                {
                    "kind": "error_rate",
                    "op": ">",
                    "value": 0.05  # 5% error rate
                },
                {
                    "kind": "feature_flag",
                    "key": "enhanced-debugging",
                    "op": "==",
                    "value": True
                }
            ],
            "actions": [
                {"kind": "set_log_level", "target": "DEBUG"},
                {"kind": "set_trace_sample_rate", "target": 1.0}
            ]
        }
    ]
}

# Example 4: Context-sensitive feature flags
# The feature flag service passes service, environment, and signal attributes
# to the flag provider, enabling context-aware flags
context_aware_policy = {
    "id": "context-aware",
    "description": "Feature flag evaluated with service context",
    "service": "payment-service",
    "environment": "production",
    "rules": [
        {
            "id": "debug-for-premium-users",
            "conditions": [
                {
                    # This flag receives context like:
                    # {
                    #   "service": "payment-service",
                    #   "environment": "production",
                    #   "user_tier": "premium",  # from signal attrs
                    #   "region": "us-east"      # from signal attrs
                    # }
                    "kind": "feature_flag",
                    "key": "premium-user-debugging",
                    "op": "==",
                    "value": True
                }
            ],
            "actions": [
                {"kind": "set_log_level", "target": "DEBUG"}
            ]
        }
    ]
}


if __name__ == "__main__":
    import httpx
    import os
    
    # Set up static feature flags for testing
    os.environ["FF_PROVIDER"] = "static"
    
    # Example: Update control plane with feature flag policy
    admin_key = os.getenv("ADMIN_API_KEY", "admin123")
    control_plane_url = "http://localhost:8080"
    
    response = httpx.post(
        f"{control_plane_url}/v1/policy",
        json={"policy": debug_on_feature_flag_policy},
        headers={"X-API-Key": admin_key}
    )
    
    print("Feature Flag Policy Example")
    print("=" * 50)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    print("\nTo enable the debug-mode feature flag:")
    print("1. Use static provider: Set flags in code")
    print("2. Use LaunchDarkly: Configure in LD dashboard")
    print("3. Use Split.io: Configure in Split dashboard")
    print("4. Use custom HTTP: Implement your endpoint")
