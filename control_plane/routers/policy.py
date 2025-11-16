"""Policy Management Endpoints"""

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import Response
from typing import Literal, Optional
import yaml
from loguru import logger

from control_plane.auth import require_admin_key
from control_plane import constants, exporters
from control_plane import metrics as prom_metrics
from control_plane.exceptions import PolicyValidationError
from control_plane.pattern_matching import validate_pattern

# Lazy imports to avoid circular dependencies
def _get_main():
    import control_plane.main as main
    return main

# Get models for type annotations (FastAPI needs them at import time)
def _get_models():
    main = _get_main()
    return main.Policy, main.UpsertPolicy, main.PolicyVersion
    
Policy, UpsertPolicy, PolicyVersion = _get_models()

router = APIRouter(tags=["policy"])


@router.get("/policy", response_model=Policy)
async def get_policy(request: Request):
    """Get the current active policy configuration."""
    main = _get_main()
    if main.policy_service:
        return main.policy_service.get_current_policy()
    return main.POLICY


@router.get("/policy/export")
async def export_policy(
    request: Request,
    format: Literal["json", "yaml"] = "json",
    include_history: bool = False
):
    """Export current policy configuration in JSON or YAML format."""
    main = _get_main()
    export_data = exporters.create_policy_export_data(
        policy=main.POLICY,
        exported_at=main._now(),
        include_history=include_history,
        policy_history=main.POLICY_HISTORY if include_history else None
    )
    
    if format == "yaml":
        content = exporters.export_policy_to_yaml(export_data)
        return Response(
            content=content,
            media_type="application/x-yaml",
            headers={"Content-Disposition": f"attachment; filename=policy-{main.POLICY.id}.yaml"}
        )
    else:
        content = exporters.export_policy_to_json(export_data)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=policy-{main.POLICY.id}.json"}
        )


@router.post("/policy/import")
async def import_policy(
    request: Request,
    admin: str = Depends(require_admin_key),
    dry_run: bool = False
):
    """Import policy configuration from JSON or YAML."""
    from control_plane.rule_validator import validate_policy_rules
    main = _get_main()
    
    body_bytes = await request.body()
    body_str = body_bytes.decode('utf-8')
    
    try:
        import_data = yaml.safe_load(body_str)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML/JSON format: {str(e)}")
    
    if isinstance(import_data, dict) and "policy" in import_data:
        policy_data = import_data["policy"]
    else:
        policy_data = import_data
    
    try:
        imported_policy = Policy(**policy_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid policy structure: {str(e)}")
    
    if not imported_policy.rules:
        prom_metrics.record_policy_validation_error()
        raise PolicyValidationError("Policy must contain at least one rule")
    
    validation_result = validate_policy_rules(imported_policy.rules)
    
    if not validation_result["valid"]:
        prom_metrics.record_policy_validation_error()
        error_conflicts = [c for c in validation_result["conflicts"] if c.severity == "error"]
        error_messages = [c.message for c in error_conflicts]
        raise PolicyValidationError(
            f"Policy validation failed: {'; '.join(error_messages)}",
            conflicts=[{
                "type": c.type,
                "severity": c.severity,
                "rule_ids": c.rule_ids,
                "message": c.message
            } for c in error_conflicts]
        )
    
    warnings = [c for c in validation_result["conflicts"] if c.severity == "warning"]
    if warnings:
        logger.warning(f"Policy import has {len(warnings)} warnings: " + 
                      "; ".join([c.message for c in warnings]))
    
    if dry_run:
        logger.info(f"Dry-run import validation for policy '{imported_policy.id}' successful")
        return {
            "dry_run": True,
            "valid": True,
            "policy": imported_policy.model_dump(),
            "validation": {
                "conflicts": len(validation_result["conflicts"]),
                "warnings": len(warnings),
                "details": validation_result
            },
            "message": "Policy import is valid and can be applied"
        }
    
    # Apply the imported policy
    import control_plane.main as main_module
    main_module.POLICY = imported_policy
    
    version = PolicyVersion(
        policy=imported_policy.model_copy(deep=True),
        applied_at=main._now(),
        applied_by=admin
    )
    main_module.POLICY_HISTORY.append(version)
    
    if len(main_module.POLICY_HISTORY) > main.MAX_POLICY_HISTORY:
        main_module.POLICY_HISTORY = main_module.POLICY_HISTORY[-main.MAX_POLICY_HISTORY:]
    
    prom_metrics.record_policy_update(imported_policy.id)
    logger.info(
        f"Policy '{imported_policy.id}' imported successfully by {admin}",
        policy_id=imported_policy.id,
        num_rules=len(imported_policy.rules),
        admin=admin
    )
    
    return {
        "imported": True,
        "policy": imported_policy.model_dump(),
        "message": f"Policy '{imported_policy.id}' imported and applied successfully"
    }


@router.get("/policy/templates")
async def get_policy_templates(request: Request):
    """Get policy templates/presets for common scenarios."""
    main = _get_main()
    if main.policy_service:
        return main.policy_service.get_default_templates()
    
    # Fallback templates
    templates = {
        "production-safe": {
            "name": "Production Safe",
            "description": "Conservative policy for production environments with error-based elevation",
            "policy": {
                "id": "production-safe",
                "description": "Conservative production policy",
                "rules": [
                    {
                        "id": "prod-baseline",
                        "description": "Baseline for production - minimal overhead",
                        "environment": "prod",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.01,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "prod-high-errors",
                        "description": "Elevate on high error rates in production",
                        "environment": "prod",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.05, "window_s": 300}
                        ],
                        "actions": {
                            "log_level": "WARN",
                            "trace_sample_rate": 0.10,
                            "metric_period_s": 30
                        }
                    },
                    {
                        "id": "prod-critical-errors",
                        "description": "Maximum observability on critical errors",
                        "environment": "prod",
                        "priority": 20,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.10, "window_s": 60}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.50,
                            "metric_period_s": 15
                        }
                    }
                ]
            }
        },
        "development": {
            "name": "Development",
            "description": "Verbose policy for development environments with high sampling",
            "policy": {
                "id": "development",
                "description": "Development environment policy with verbose logging",
                "rules": [
                    {
                        "id": "dev-baseline",
                        "description": "Verbose baseline for development",
                        "environment": "dev",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 1.0,
                            "metric_period_s": 30
                        }
                    }
                ]
            }
        },
        "performance-focused": {
            "name": "Performance Focused",
            "description": "Policy that elevates observability based on latency thresholds",
            "policy": {
                "id": "performance-focused",
                "description": "Latency-based adaptive policy",
                "rules": [
                    {
                        "id": "baseline",
                        "description": "Default baseline",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.05,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "elevated-latency",
                        "description": "Increase sampling on slow requests",
                        "priority": 10,
                        "conditions": [
                            {"kind": "metric", "op": ">", "key": "latency_p95_ms", "value": 500, "window_s": 300}
                        ],
                        "actions": {
                            "log_level": "WARN",
                            "trace_sample_rate": 0.25,
                            "metric_period_s": 30
                        }
                    },
                    {
                        "id": "critical-latency",
                        "description": "Maximum observability on very slow requests",
                        "priority": 20,
                        "conditions": [
                            {"kind": "metric", "op": ">", "key": "latency_p99_ms", "value": 2000, "window_s": 120}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.75,
                            "metric_period_s": 15
                        }
                    }
                ]
            }
        },
        "cost-optimized": {
            "name": "Cost Optimized",
            "description": "Minimal observability overhead, only elevates on critical issues",
            "policy": {
                "id": "cost-optimized",
                "description": "Minimal overhead policy for cost savings",
                "rules": [
                    {
                        "id": "minimal-baseline",
                        "description": "Minimal baseline sampling",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "WARN",
                            "trace_sample_rate": 0.001,
                            "metric_period_s": 120
                        }
                    },
                    {
                        "id": "critical-only",
                        "description": "Only elevate on critical errors",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.20, "window_s": 60}
                        ],
                        "actions": {
                            "log_level": "ERROR",
                            "trace_sample_rate": 0.10,
                            "metric_period_s": 30
                        }
                    }
                ]
            }
        },
        "balanced": {
            "name": "Balanced",
            "description": "Balanced policy with error and latency triggers",
            "policy": {
                "id": "balanced",
                "description": "Balanced adaptive policy for most use cases",
                "rules": [
                    {
                        "id": "baseline",
                        "description": "Balanced baseline",
                        "priority": 0,
                        "conditions": [{"kind": "always", "op": "always"}],
                        "actions": {
                            "log_level": "INFO",
                            "trace_sample_rate": 0.10,
                            "metric_period_s": 60
                        }
                    },
                    {
                        "id": "errors-detected",
                        "description": "Elevate on error rate increase",
                        "priority": 10,
                        "conditions": [
                            {"kind": "error_rate", "op": ">", "key": "rate", "value": 0.02, "window_s": 120}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.30,
                            "metric_period_s": 30
                        }
                    },
                    {
                        "id": "slow-requests",
                        "description": "Elevate on latency issues",
                        "priority": 15,
                        "conditions": [
                            {"kind": "metric", "op": ">", "key": "latency_p95_ms", "value": 400, "window_s": 180}
                        ],
                        "actions": {
                            "log_level": "DEBUG",
                            "trace_sample_rate": 0.30,
                            "metric_period_s": 30
                        }
                    }
                ]
            }
        }
    }
    
    return {
        "templates": templates,
        "count": len(templates),
        "usage": "Use GET /v1/policy/templates/{template_name} to get a specific template"
    }


@router.get("/policy/templates/{template_name}")
async def get_policy_template(request: Request, template_name: str):
    """Get a specific policy template by name."""
    templates_response = await get_policy_templates(request)
    templates = templates_response["templates"]
    
    if template_name not in templates:
        available = ", ".join(templates.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_name}' not found. Available templates: {available}"
        )
    
    template = templates[template_name]
    
    return {
        "template": template,
        "export_ready": True,
        "usage": {
            "curl_json": f"curl http://localhost:8080/v1/policy/templates/{template_name} | jq '.template.policy' | curl -X POST http://localhost:8080/v1/policy/import -H 'X-API-Key: YOUR_KEY' -d @-",
            "description": "Pipe this template directly to the import endpoint or download and customize"
        }
    }


@router.post("/policy/validate")
async def validate_policy_endpoint(request: Request, req: UpsertPolicy):
    """Validate a policy configuration without applying it."""
    from control_plane.rule_validator import validate_policy_rules
    
    pattern_errors = []
    for rule in req.policy.rules:
        if rule.service:
            is_valid, error_msg = validate_pattern(rule.service)
            if not is_valid:
                pattern_errors.append(f"Rule '{rule.id}' has invalid service pattern: {error_msg}")
        
        if rule.environment:
            is_valid, error_msg = validate_pattern(rule.environment)
            if not is_valid:
                pattern_errors.append(f"Rule '{rule.id}' has invalid environment pattern: {error_msg}")
    
    if pattern_errors:
        raise PolicyValidationError(
            f"Invalid patterns in policy: {'; '.join(pattern_errors)}"
        )
    
    validation_result = validate_policy_rules(req.policy.rules)
    
    return {
        "valid": validation_result["valid"],
        "summary": validation_result["summary"],
        "conflicts": [
            {
                "type": c.type,
                "severity": c.severity,
                "rule_ids": c.rule_ids,
                "message": c.message,
                "suggestion": c.suggestion
            }
            for c in validation_result["conflicts"]
        ]
    }


@router.post("/policy")
async def set_policy(
    request: Request,
    req: UpsertPolicy,
    admin: str = Depends(require_admin_key),
    dry_run: bool = False,
):
    """Update the active policy configuration."""
    from control_plane.rule_validator import validate_policy_rules
    main = _get_main()
    import control_plane.main as main_module
    
    if not req.policy.rules:
        prom_metrics.record_policy_validation_error()
        raise PolicyValidationError("Policy must contain at least one rule")
    
    validation_result = validate_policy_rules(req.policy.rules)
    
    if not validation_result["valid"]:
        prom_metrics.record_policy_validation_error()
        error_conflicts = [c for c in validation_result["conflicts"] if c.severity == "error"]
        error_messages = [c.message for c in error_conflicts]
        raise PolicyValidationError(
            f"Policy validation failed: {'; '.join(error_messages)}",
            conflicts=[{
                "type": c.type,
                "severity": c.severity,
                "rule_ids": c.rule_ids,
                "message": c.message
            } for c in error_conflicts]
        )
    
    warnings = [c for c in validation_result["conflicts"] if c.severity == "warning"]
    if warnings:
        logger.warning(f"Policy update has {len(warnings)} warnings: " + 
                      "; ".join([c.message for c in warnings]))
    
    if dry_run:
        logger.info(f"Dry-run validation for policy '{req.policy.id}' successful")
        return {
            "dry_run": True,
            "valid": True,
            "policy": req.policy,
            "validation": {
                "conflicts": len(validation_result["conflicts"]),
                "warnings": len(warnings),
                "details": validation_result
            },
            "message": "Policy is valid and can be applied"
        }
    
    if main.policy_service:
        try:
            result = main.policy_service.update_policy(req.policy, updated_by=admin)
            prom_metrics.record_policy_update(req.policy.id)
            return main.policy_service.get_current_policy()
        except Exception as e:
            logger.error(f"Policy update via service failed: {e}")
            raise
    else:
        main_module.POLICY = req.policy
        
        version = PolicyVersion(
            policy=req.policy.model_copy(deep=True),
            applied_at=main._now(),
            applied_by=admin
        )
        main_module.POLICY_HISTORY.append(version)
        
        if len(main_module.POLICY_HISTORY) > main.MAX_POLICY_HISTORY:
            main_module.POLICY_HISTORY = main_module.POLICY_HISTORY[-main.MAX_POLICY_HISTORY:]
        
        prom_metrics.record_policy_update(req.policy.id)
        logger.info(
            f"Policy '{req.policy.id}' updated with {len(req.policy.rules)} rules by {admin}",
            policy_id=req.policy.id,
            num_rules=len(req.policy.rules),
            merge_strategy=req.policy.merge_strategy,
            admin=admin,
            has_warnings=len(warnings) > 0
        )
        
        return main_module.POLICY


@router.get("/history/policy")
async def get_policy_history(
    request: Request,
    limit: int = 10,
    since: Optional[str] = None,
    until: Optional[str] = None
):
    """Get policy version history for time-travel debugging."""
    main = _get_main()
    from datetime import datetime, timezone
    POLICY_HISTORY = main.POLICY_HISTORY
    
    filtered_history = POLICY_HISTORY
    
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
            filtered_history = [v for v in filtered_history if v.applied_at >= since_dt]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'since' timestamp format")
    
    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace('Z', '+00:00'))
            filtered_history = [v for v in filtered_history if v.applied_at <= until_dt]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'until' timestamp format")
    
    sorted_history = sorted(filtered_history, key=lambda v: v.applied_at, reverse=True)
    limited_history = sorted_history[:limit]
    
    return {
        "versions": [
            {
                "policy": v.policy.model_dump(),
                "applied_at": v.applied_at.isoformat(),
                "applied_by": v.applied_by
            }
            for v in limited_history
        ],
        "count": len(limited_history),
        "total_in_history": len(POLICY_HISTORY)
    }


@router.get("/history/policy/at")
async def get_policy_at_time(
    request: Request,
    timestamp: str
):
    """Get the policy that was active at a specific time."""
    main = _get_main()
    from datetime import datetime
    POLICY_HISTORY = main.POLICY_HISTORY
    POLICY = main.POLICY
    
    try:
        # Handle various ISO 8601 formats
        timestamp_fixed = timestamp.replace(' ', '+').replace('Z', '+00:00')
        query_time = datetime.fromisoformat(timestamp_fixed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format. Use ISO 8601 format. Error: {str(e)}")
    
    applicable_versions = [v for v in POLICY_HISTORY if v.applied_at <= query_time]
    
    if applicable_versions:
        # Get the most recent one
        policy_at_time = max(applicable_versions, key=lambda v: v.applied_at)
        return {
            "policy": policy_at_time.policy.model_dump(),
            "applied_at": policy_at_time.applied_at.isoformat(),
            "applied_by": policy_at_time.applied_by,
            "query_time": query_time.isoformat()
        }
    else:
        # No history before this time, return current policy
        return {
            "policy": POLICY.model_dump(),
            "applied_at": None,
            "applied_by": None,
            "query_time": query_time.isoformat(),
            "note": "No policy history available before this time, returning current policy"
        }
