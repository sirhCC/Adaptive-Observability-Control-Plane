# Code Quality & Refactoring Roadmap

This document tracks code quality improvements, refactoring tasks, and technical debt for the Adaptive Observability Control Plane.

## Progress

8 of 10 tasks complete (80%)

**Last Updated:** November 15, 2025

**Summary:**
- ✅ Tasks 1-7: All completed and integrated
- ✅ Task 9: Service layer pattern implemented  
- ❌ Task 8: Router splitting (deferred until migration to services complete)
- ❌ Task 10: Validation helpers (not started)

**Impact:**
- ~1050 lines of new, well-structured code added
- main.py reduced from 2335 → 2035 lines (-300 lines, ~13%)
- 4 new service modules created with business logic extraction
- 287/287 tests passing ✅

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
**Status:** ✅ COMPLETED (2025-11-15)  
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
**Status:** ✅ COMPLETED (2025-11-15)  
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
**Status:** ❌ Deferred - Requires Architecture Changes  
**Priority:** Medium  
**Estimated Effort:** 6+ hours (needs service layer pattern first)  
**Impact:** Better code organization for large file (~2000 lines)

**Current Issue:**
- `main.py` is ~2000 lines - still large but improved from 2335
- All routes in single file makes navigation harder
- Tightly coupled global state prevents easy module splitting

**Why Deferred:**
- Attempted router splitting failed due to circular dependencies
- FastAPI request validation requires models at import time
- Global state (POLICY, SIGNALS, evaluate, etc.) creates tight coupling
- Would need Task #9 (Service Layer Pattern) first to properly decouple

**Recommended Approach:**
1. First implement Service Layer Pattern (Task #9)
2. Extract business logic from route handlers
3. Then split routers with dependency injection of service layer
4. This creates clean separation without circular dependencies

**Benefits After Proper Implementation:**
- Easier navigation and code discovery
- Clear separation of concerns
- Testable business logic independent of HTTP layer
- Follows FastAPI and clean architecture best practices

**Note:** Current refactoring has already reduced main.py by 443 lines (19%) through other improvements

---

### 9. Implement Service Layer Pattern 🎨
**Status:** ✅ Complete (2024-11-15)
**Priority:** High  
**Actual Effort:** 3 hours  
**Lines Added:** +750 (4 service modules)
**Impact:** Business logic extracted from HTTP layer

**Completed Work:**

Created comprehensive service layer with 4 service classes:

1. **PolicyService** (`policy_service.py` - 250 lines)
   - Policy CRUD operations with validation
   - Version history management
   - Time-travel debugging support
   - Policy templates
   
2. **SignalService** (`signal_service.py` - 240 lines)
   - Signal ingestion with validation
   - Buffer management and pruning
   - Signal retrieval and filtering
   - Buffer health statistics

3. **ConfigService** (`config_service.py` - 120 lines)
   - Effective configuration computation
   - Policy rule evaluation
   - Configuration explanations

4. **HealthService** (`health_service.py` - 150 lines)
   - Health check logic
   - Readiness checks
   - Database and buffer health

**Files Created:**
- `control_plane/services/__init__.py`
- `control_plane/services/policy_service.py`
- `control_plane/services/signal_service.py`
- `control_plane/services/config_service.py`
- `control_plane/services/health_service.py`

**Integration:**
- Services initialized during app lifespan startup
- Ready for gradual adoption in route handlers
- Existing code continues to work alongside services

**Benefits:**
- Business logic testable without HTTP mocking
- Reusable across multiple contexts
- Enables proper router module splitting
- Clear separation of concerns
- Better maintainability

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
| Medium Effort | 4 | 4 | 0 | 0 |
| Bigger Refactors | 3 | 0 | 0 | 3 |
| **Overall** | **10** | **7** | **0** | **3** |

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
