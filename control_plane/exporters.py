"""Export utilities for policies and signals.

Provides reusable functions for exporting data in various formats (JSON, YAML, CSV).
Used by API endpoints for data portability, analysis, and archival.
"""

import json
import csv
import io
from typing import List, Dict, Any, Optional
from datetime import datetime

import yaml


def export_policy_to_json(policy_data: Dict[str, Any], indent: int = 2) -> str:
    """Export policy configuration to JSON format.
    
    Args:
        policy_data: Policy data dictionary
        indent: JSON indentation level (default: 2)
        
    Returns:
        JSON string representation
    """
    return json.dumps(policy_data, indent=indent)


def export_policy_to_yaml(policy_data: Dict[str, Any]) -> str:
    """Export policy configuration to YAML format.
    
    Args:
        policy_data: Policy data dictionary
        
    Returns:
        YAML string representation
    """
    return yaml.dump(policy_data, default_flow_style=False, sort_keys=False)


def create_policy_export_data(
    policy: Any,
    exported_at: datetime,
    include_history: bool = False,
    policy_history: Optional[List[Any]] = None
) -> Dict[str, Any]:
    """Create policy export data structure.
    
    Args:
        policy: Policy model instance
        exported_at: Export timestamp
        include_history: Whether to include version history metadata
        policy_history: List of PolicyVersion instances (if include_history=True)
        
    Returns:
        Export data dictionary
    """
    export_data = {
        "policy": json.loads(policy.model_dump_json()),
        "exported_at": exported_at.isoformat(),
        "version": "1.0"
    }
    
    if include_history and policy_history:
        export_data["history"] = {
            "versions_available": len(policy_history),
            "oldest_version": policy_history[0].applied_at.isoformat() if policy_history else None,
            "newest_version": policy_history[-1].applied_at.isoformat() if policy_history else None
        }
    
    return export_data


def export_signals_to_json(signals: List[Dict[str, Any]], indent: int = 2) -> str:
    """Export signals to JSON format.
    
    Args:
        signals: List of signal dictionaries
        indent: JSON indentation level (default: 2)
        
    Returns:
        JSON string representation
    """
    return json.dumps(signals, indent=indent)


def export_signals_to_csv(signals: List[Dict[str, Any]]) -> str:
    """Export signals to CSV format.
    
    Args:
        signals: List of signal dictionaries
        
    Returns:
        CSV string representation
    """
    if not signals:
        return ""
    
    output = io.StringIO()
    
    # Get fieldnames from first signal
    fieldnames = list(signals[0].keys())
    
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for signal in signals:
        # Handle nested attrs dict by converting to JSON string
        row = signal.copy()
        if 'attrs' in row and isinstance(row['attrs'], dict):
            row['attrs'] = json.dumps(row['attrs'])
        writer.writerow(row)
    
    return output.getvalue()


def create_signals_export_response(
    signals: List[Dict[str, Any]],
    filters: Dict[str, Any],
    export_time: datetime
) -> Dict[str, Any]:
    """Create signals export response structure.
    
    Args:
        signals: List of exported signal dictionaries
        filters: Applied filters (service, environment, time range, limit)
        export_time: Export timestamp
        
    Returns:
        Export response dictionary
    """
    return {
        "signals": signals,
        "count": len(signals),
        "filters": filters,
        "export_time": export_time.isoformat()
    }


def filter_and_collect_signals(
    signals_buffer: Dict[tuple, List[Any]],
    service: Optional[str] = None,
    environment: Optional[str] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    limit: int = 1000
) -> List[Dict[str, Any]]:
    """Filter and collect signals from buffer based on criteria.
    
    Args:
        signals_buffer: Dictionary mapping (service, env) tuples to signal lists
        service: Filter by service name (optional)
        environment: Filter by environment (optional)
        start_dt: Start datetime for filtering (optional)
        end_dt: End datetime for filtering (optional)
        limit: Maximum signals to collect
        
    Returns:
        List of filtered signal dictionaries, sorted by timestamp
    """
    exported_signals = []
    
    for key, buffer in signals_buffer.items():
        svc, env = key
        
        # Apply service/environment filters
        if service and svc != service:
            continue
        if environment and env != environment:
            continue
        
        # Filter and export signals
        for signal in buffer:
            # Apply time range filters
            if start_dt and signal.ts < start_dt:
                continue
            if end_dt and signal.ts > end_dt:
                continue
            
            exported_signals.append({
                "service": signal.service,
                "environment": signal.environment,
                "timestamp": signal.ts.isoformat(),
                "latency_ms": signal.latency_ms,
                "error": signal.error,
                "attrs": signal.attrs
            })
            
            # Check limit
            if len(exported_signals) >= limit:
                break
        
        if len(exported_signals) >= limit:
            break
    
    # Sort by timestamp
    exported_signals.sort(key=lambda s: s["timestamp"])
    
    return exported_signals
