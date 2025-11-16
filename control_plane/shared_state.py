"""Shared state and dependencies for routers

This module contains shared state, helper functions, and model references
that routers need to import. This breaks the circular dependency by providing
a central location for shared resources.
"""

from typing import Dict, List, Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from control_plane.main import (
        Policy, Signal, EffectiveConfig, PolicyVersion,
        UpsertPolicy, SignalIn, SimulateRequest, ReplayRequest, CompareRequest
    )
    from slowapi import Limiter
    from control_plane.services import PolicyService, SignalService, ConfigService, HealthService

# These will be set by main.py during initialization
POLICY: Optional['Policy'] = None
SIGNALS: Dict[tuple[str, str], List['Signal']] = {}
POLICY_HISTORY: List['PolicyVersion'] = []
MAX_POLICY_HISTORY: int = 100

# Service instances (set during app lifespan)
policy_service: Optional['PolicyService'] = None
signal_service: Optional['SignalService'] = None
config_service: Optional['ConfigService'] = None
health_service: Optional['HealthService'] = None

# Limiter instance (set during app initialization)
limiter: Optional['Limiter'] = None

# Helper function references (set by main.py)
_now = None
_prune = None
_calc_aggregates = None
_evaluate_rule_conditions = None
_match_rules_for_signal = None
_convert_signal_in_to_signal = None
evaluate = None
_check_database_health = None
_check_signal_buffer_health = None

# Constants (set by main.py)
MAX_SERVICE_NAME_LEN: int = 64
MAX_ENV_NAME_LEN: int = 32
VALID_NAME_PATTERN = None


def initialize_from_main(main_module):
    """Initialize shared state from main module.
    
    Called by main.py after all definitions are complete to populate
    this module with references to shared state and functions.
    """
    global POLICY, SIGNALS, POLICY_HISTORY, MAX_POLICY_HISTORY
    global policy_service, signal_service, config_service, health_service
    global limiter
    global _now, _prune, _calc_aggregates, _evaluate_rule_conditions
    global _match_rules_for_signal, _convert_signal_in_to_signal, evaluate
    global _check_database_health, _check_signal_buffer_health
    global MAX_SERVICE_NAME_LEN, MAX_ENV_NAME_LEN, VALID_NAME_PATTERN
    
    # Copy references from main module
    POLICY = main_module.POLICY
    SIGNALS = main_module.SIGNALS
    POLICY_HISTORY = main_module.POLICY_HISTORY
    MAX_POLICY_HISTORY = main_module.MAX_POLICY_HISTORY
    
    policy_service = main_module.policy_service
    signal_service = main_module.signal_service
    config_service = main_module.config_service
    health_service = main_module.health_service
    
    limiter = main_module.limiter
    
    _now = main_module._now
    _prune = main_module._prune
    _calc_aggregates = main_module._calc_aggregates
    _evaluate_rule_conditions = main_module._evaluate_rule_conditions
    _match_rules_for_signal = main_module._match_rules_for_signal
    _convert_signal_in_to_signal = main_module._convert_signal_in_to_signal
    evaluate = main_module.evaluate
    _check_database_health = main_module._check_database_health
    _check_signal_buffer_health = main_module._check_signal_buffer_health
    
    MAX_SERVICE_NAME_LEN = main_module.MAX_SERVICE_NAME_LEN
    MAX_ENV_NAME_LEN = main_module.MAX_ENV_NAME_LEN
    VALID_NAME_PATTERN = main_module.VALID_NAME_PATTERN
