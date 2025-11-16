# Code Quality & Refactoring Roadmap

This document tracks code quality improvements, refactoring tasks, and technical debt for the Adaptive Observability Control Plane.

**Status:** 5 of 10 tasks complete (50%)

**Last Updated:** November 15, 2025

---

## 🎯 Quick Wins (High Impact, Low Effort)

### 1. Extract Database Health Check Helper ⚠️
**Status:** ✅ COMPLETED (2025-11-15)  
**Priority:** High  
**Estimated Effort:** 30 minutes  
**Impact:** Removes code duplication, improves maintainability

**Current Issue:**
- Database connectivity check duplicated in `healthz()` and `readyz()` endpoints
- Same SQLAlchemy query logic repeated twice

**Proposed Solution:**
- Create `async def check_database_health(db: AsyncSession) -> dict` helper function
- Return standardized health status dict
- Reuse in both health check endpoints

**Files to Modify:**
- `control_plane/main.py` (lines ~710-720, ~770-780)

**Benefits:**
- DRY principle - single source of truth
- Easier to maintain and test
- Consistent health check responses

---

### 2. Create Constants Module 📋
**Status:** ✅ COMPLETED (2025-11-15)  
**Priority:** High  
**Estimated Effort:** 45 minutes  
**Impact:** Centralizes configuration, eliminates magic numbers

**Current Issue:**
- Rate limits hardcoded in decorators: `"20/minute"`, `"100/minute"`, `"1000/minute"`
- Field validation limits scattered: `min_length=1`, `max_length=100`, `max_length=50`
- Magic numbers throughout: `MAX_SIGNALS_PER_SERVICE = 10000`, buffer sizes, etc.

**Proposed Solution:**
- Create `control_plane/constants.py` module
- Group constants logically:
  - `RATE_LIMITS` - All rate limit strings
  - `VALIDATION_LIMITS` - Field validation bounds
  - `BUFFER_LIMITS` - Signal buffer configuration
  - `TIME_LIMITS` - Timeout and TTL values

**Files to Create:**
- `control_plane/constants.py` (new file)

**Files to Modify:**
- `control_plane/main.py` - Import and use constants
- Multiple endpoints with rate limiting

**Benefits:**
- Single source of truth for configuration
- Easy to adjust limits without hunting through code
- Self-documenting code with named constants

---

### 3. Add Strict MyPy Configuration ✅
**Status:** ✅ COMPLETED (2025-11-15)  
**Priority:** Medium  
**Estimated Effort:** 1 hour  
**Impact:** Catches type errors at development time

**Current Issue:**
- No static type checking configured
- Some functions missing return type annotations
- Potential type safety issues not caught

**Proposed Solution:**
- Create `mypy.ini` or add `[tool.mypy]` to `pyproject.toml`
- Enable strict mode: `strict = true`
- Add missing type hints throughout codebase
- Configure ignored imports for third-party libraries

**Files to Create:**
- `mypy.ini` (new file) or update `pyproject.toml`

**Files to Modify:**
- Multiple files to add missing type hints
- CI pipeline to run mypy checks

**Benefits:**
- Catch type errors before runtime
- Better IDE support and autocomplete
- Improved code documentation via types

---

## 🔨 Medium Effort Improvements

### 4. Break Down `simulate_policy()` Function 🔧
**Status:** ✅ COMPLETED (2025-11-15)  
**Priority:** Medium  
**Estimated Effort:** 1.5 hours  
**Impact:** Improves readability and testability

**Current Issue:**
- `simulate_policy()` is ~150+ lines with nested logic
- Does too many things: signal conversion, aggregation, rule matching, result building
- Hard to test individual pieces

**Proposed Solution:**
- Extract helper functions:
  - `def _convert_signal_in_to_signal(sig_in: SignalIn) -> Signal`
  - `def _match_rules_for_signal(policy: Policy, signal: Signal, aggs: dict) -> List[dict]`
  - `def _build_simulation_result(signal: Signal, matched_rules: List[dict], effective: EffectiveConfig) -> dict`
- Main function becomes orchestration of smaller pieces

**Files to Modify:**
- `control_plane/main.py` (lines ~1525-1675 approximately)

**Benefits:**
- Each helper can be unit tested independently
- Easier to understand flow at a glance
- Reusable components for similar endpoints

---

### 5. Extract CSV/JSON Export Helpers 📊
**Status:** ✅ COMPLETED (2025-11-15)  
**Priority:** Medium  
**Estimated Effort:** 1 hour  
**Impact:** Reusable export functionality

**Current Issue:**
- CSV/JSON export logic embedded in route handlers
- Could be reused for other export needs
- Makes endpoints harder to test

**Proposed Solution:**
- Create `control_plane/exporters.py` module with:
  - `def export_signals_to_csv(signals: List[Signal]) -> str`
  - `def export_signals_to_json(signals: List[Signal]) -> str`
  - `def export_policy_to_yaml(policy: Policy) -> str`
  - `def export_policy_to_json(policy: Policy) -> str`

**Files to Create:**
- `control_plane/exporters.py` (new file)

**Files to Modify:**
- `control_plane/main.py` - Use exporters in endpoints

**Benefits:**
- Reusable across multiple endpoints
- Easier to test export logic
- Cleaner route handlers focused on HTTP concerns

---

### 6. Consolidate Policy Evaluation Logic 🎯
**Status:** ❌ Not Started  
**Priority:** Medium  
**Estimated Effort:** 2 hours  
**Impact:** DRY up simulate/replay/compare endpoints

**Current Issue:**
- Policy evaluation logic duplicated across:
  - `simulate_policy()` - evaluates test signals
  - `replay_signals()` - evaluates historical signals
  - `compare_policies()` - evaluates against multiple policies
- Each has slight variations but core logic is the same

**Proposed Solution:**
- Extract to `control_plane/policy_simulator.py`:
  - `class PolicySimulator` with methods:
    - `evaluate_signal(policy: Policy, signal: Signal, buffer: List[Signal]) -> EvaluationResult`
    - `evaluate_batch(policy: Policy, signals: List[Signal]) -> List[EvaluationResult]`
    - `compare_evaluations(policies: List[Policy], signals: List[Signal]) -> ComparisonResult`

**Files to Create:**
- `control_plane/policy_simulator.py` (new file)

**Files to Modify:**
- `control_plane/main.py` - Use PolicySimulator in endpoints

**Benefits:**
- Single source of truth for simulation logic
- Easier to add new simulation features
- Better test coverage of core logic

---

### 7. Add Comprehensive Structured Logging 📝
**Status:** ❌ Not Started  
**Priority:** Low  
**Estimated Effort:** 1.5 hours  
**Impact:** Better debugging and observability

**Current Issue:**
- Logging is inconsistent across endpoints
- Missing contextual information in complex operations
- Hard to trace request flow through system

**Proposed Solution:**
- Add structured logging contexts with:
  - Request IDs for tracing
  - Service/environment context
  - Performance metrics (duration, buffer sizes)
  - Error context with structured fields
- Use loguru's `bind()` for contextual logging

**Files to Modify:**
- `control_plane/main.py` - Add logging to key operations
- Complex functions lacking debug visibility

**Benefits:**
- Better production debugging
- Easier to diagnose issues
- Performance monitoring

---

## 🏗️ Bigger Refactoring Projects

### 8. Split Main Router into Multiple Modules 📦
**Status:** ❌ Not Started  
**Priority:** Medium  
**Estimated Effort:** 3 hours  
**Impact:** Better code organization for large file (2148 lines)

**Current Issue:**
- `main.py` is 2148 lines - too large for easy navigation
- All routes in single file makes it hard to find specific endpoints
- Violates single responsibility principle

**Proposed Solution:**
- Split into focused router modules:
  - `control_plane/routers/health.py` - healthz, readyz, metrics
  - `control_plane/routers/policy.py` - policy CRUD, templates, validation
  - `control_plane/routers/signals.py` - signal ingestion, export
  - `control_plane/routers/simulation.py` - simulate, replay, compare
  - `control_plane/routers/admin.py` - admin endpoints, keys
- Keep main.py as app initialization and router registration

**Files to Create:**
- `control_plane/routers/__init__.py`
- `control_plane/routers/health.py`
- `control_plane/routers/policy.py`
- `control_plane/routers/signals.py`
- `control_plane/routers/simulation.py`
- `control_plane/routers/admin.py`

**Files to Modify:**
- `control_plane/main.py` - Slim down to app setup only

**Benefits:**
- Easier navigation and code discovery
- Clear separation of concerns
- Easier for teams to work in parallel
- Follows FastAPI best practices

---

### 9. Implement Service Layer Pattern 🎨
**Status:** ❌ Not Started  
**Priority:** Low  
**Estimated Effort:** 4 hours  
**Impact:** Separates business logic from HTTP layer

**Current Issue:**
- Business logic embedded in route handlers
- Hard to reuse logic outside HTTP context
- Difficult to test without HTTP framework

**Proposed Solution:**
- Create service layer modules:
  - `control_plane/services/policy_service.py` - Policy operations
  - `control_plane/services/signal_service.py` - Signal processing
  - `control_plane/services/evaluation_service.py` - Rule evaluation
- Route handlers become thin wrappers calling services

**Files to Create:**
- `control_plane/services/__init__.py`
- `control_plane/services/policy_service.py`
- `control_plane/services/signal_service.py`
- `control_plane/services/evaluation_service.py`

**Files to Modify:**
- Route handlers to delegate to services

**Benefits:**
- Testable without HTTP mocking
- Reusable in CLI, background jobs, etc.
- Clear architecture boundaries
- Follows clean architecture principles

---

### 10. Create Validation Helper Module 🔍
**Status:** ❌ Not Started  
**Priority:** Low  
**Estimated Effort:** 1.5 hours  
**Impact:** Centralize repeated validation patterns

**Current Issue:**
- Validation logic repeated across endpoints
- Service/environment name validation duplicated
- Timestamp parsing/validation scattered

**Proposed Solution:**
- Create `control_plane/validators.py`:
  - `def validate_service_name(name: str) -> str` - raises on invalid
  - `def validate_environment_name(name: str) -> str`
  - `def validate_timestamp(ts_str: str) -> datetime`
  - `def validate_time_range(start: datetime, end: datetime) -> None`
- Use in Pydantic validators and route handlers

**Files to Create:**
- `control_plane/validators.py` (new file)

**Files to Modify:**
- Pydantic models to use validators
- Route handlers with validation logic

**Benefits:**
- Consistent validation across codebase
- Single place to update validation rules
- Better error messages

---

## 📊 Progress Summary

| Category | Total | Complete | In Progress | Not Started |
|----------|-------|----------|-------------|-------------|
| Quick Wins | 3 | 3 | 0 | 0 |
| Medium Effort | 4 | 2 | 0 | 2 |
| Bigger Refactors | 3 | 0 | 0 | 3 |
| **Overall** | **10** | **5** | **0** | **5** |

---

## 🎯 Recommended Order

1. **Extract Database Health Check Helper** (30 min) - Quick win, immediate value
2. **Create Constants Module** (45 min) - Foundation for cleaner code
3. **Add Strict MyPy Configuration** (1 hour) - Catch issues early
4. **Break Down simulate_policy()** (1.5 hours) - Improve most complex function
5. **Extract CSV/JSON Export Helpers** (1 hour) - Reusable utilities
6. **Consolidate Policy Evaluation Logic** (2 hours) - Major DRY improvement
7. **Add Comprehensive Structured Logging** (1.5 hours) - Better observability
8. **Split Main Router into Modules** (3 hours) - Major organization improvement
9. **Create Validation Helper Module** (1.5 hours) - Cleaner validation
10. **Implement Service Layer Pattern** (4 hours) - Architecture improvement (optional)

**Total Estimated Time:** ~16.5 hours

---

## 📝 Notes

- All changes should maintain backward compatibility
- Run full test suite (287 tests) after each change
- Update documentation as needed
- Consider adding tests for new helper functions
- Keep IMPROVEMENTS.md updated with completed refactoring work

---

## 🔗 Related Documents

- [IMPROVEMENTS.md](./IMPROVEMENTS.md) - Feature roadmap (100% complete)
- [README.md](./README.md) - Project documentation
- [tests/](./tests/) - Test suite (313 tests)
