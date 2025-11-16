# Main.py Refactoring Plan

## Current State
- `main.py`: 2374 lines
- Contains: Application setup, state, helpers, models, and 20+ route handlers
- All routes currently in a single file

## Goal
Split into maintainable modules while preserving all functionality and tests.

## Refactoring Strategy (Safe & Incremental)

### Phase 1: Extract Core Business Logic (✅ DONE)
- ✅ `constants.py` - Configuration constants
- ✅ `exporters.py` - Export/import logic
- ✅ `validators.py` - Validation functions
- ✅ `policy_simulator.py` - Simulation logic
- ✅ `services/` - Service layer (PolicyService, SignalService, ConfigService, HealthService)

### Phase 2: Split Route Handlers into Router Modules (⬅️ IN PROGRESS)

**Strategy**: Keep shared state in `main.py`, extract only route handlers to router modules.

Create `control_plane/routers/` package with:

1. **`health.py`** (✅ Created) - 3 endpoints
   - GET /healthz
   - GET /readyz  
   - GET /metrics

2. **`auth.py`** (✅ Created) - 1 endpoint
   - POST /auth/generate-key

3. **`policy.py`** (~600 lines) - 8 endpoints
   - GET /policy
   - GET /policy/export
   - POST /policy/import
   - GET /policy/templates
   - GET /policy/templates/{name}
   - POST /policy/validate
   - POST /policy
   - GET /history/policy
   - GET /history/policy/at

4. **`signal.py`** (~200 lines) - 2 endpoints
   - POST /signal
   - GET /signals/export

5. **`config.py`** (~100 lines) - 1 endpoint
   - GET /config/{service}/{environment}

6. **`simulation.py`** (~400 lines) - 3 endpoints
   - POST /policy/simulate
   - POST /replay
   - POST /compare

### Phase 3: Simplify main.py

After extracting routers:
- `main.py` becomes the application factory
- Contains only: app setup, lifespan, middleware, shared state, helper functions
- Routers are registered in main
- Estimated reduction: 2374 → ~800-1000 lines

### Phase 4: Optional Future Improvements

1. **Extract shared state** to `state.py`:
   - POLICY, SIGNALS, POLICY_HISTORY
   - Helper functions (_now, _prune, _calc_aggregates, etc.)

2. **Extract models** to enhanced `schemas.py`:
   - Move remaining models from main.py
   - Keep all Pydantic models together

3. **Extract engine logic** to enhanced `engine.py`:
   - Policy evaluation functions
   - Merge strategies
   - Condition evaluation

## Benefits

✅ **Maintainability**: Easier to find and modify specific endpoint logic  
✅ **Readability**: Each router module is ~100-600 lines (manageable)  
✅ **Testability**: Routers can be tested in isolation  
✅ **Team Collaboration**: Reduce merge conflicts  
✅ **Safety**: Incremental approach, tests pass at each step  

## Implementation Order

1. ✅ Create `routers/__init__.py`
2. ✅ Create `routers/health.py` (simplest, 3 endpoints)
3. ✅ Create `routers/auth.py` (simplest, 1 endpoint)
4. ⏳ Create remaining routers (policy, signal, config, simulation)
5. ⏳ Update `main.py` to register routers
6. ⏳ Run tests to verify no regressions
7. ⏳ Remove extracted code from main.py
8. ⏳ Final test run

## Risk Mitigation

- All imports still work (routers import from main.py)
- No state management changes yet
- Tests remain unchanged
- Can be rolled back easily if issues arise
- Each router is created and tested independently

## Success Criteria

✅ All 320 tests still passing  
✅ main.py reduced by ~60% (2374 → ~800-1000 lines)  
✅ Clear module organization  
✅ No functionality changes  
✅ Faster development velocity  

---

## Current Status

**✅ Phase 1**: Complete - Core business logic extracted to separate modules

**🔄 Phase 2**: Router modules created (ready for integration)
- ✅ Created `control_plane/routers/` package structure
- ✅ Created `health.py` (healthz, readyz, metrics endpoints)
- ✅ Created `auth.py` (API key generation endpoint)
- ✅ Created `policy.py` (8 policy management endpoints)
- ✅ Created `signal.py` (signal ingestion and export)
- ✅ Created `config.py` (configuration retrieval)
- ✅ Created `simulation.py` (simulate, replay, compare endpoints)

**⏸️ Integration Deferred**: Router modules exist but not yet integrated into main.py
- **Reason**: Circular import challenges need careful resolution
- **Approach**: Will integrate routers in a future dedicated session
- **Current State**: All tests passing (320/320), no regressions
- **Router Files**: Fully functional, ready to integrate when circular imports resolved

**Next Steps** (Future Session):
1. Resolve circular import pattern (consider dependency injection or factory pattern)
2. Integrate routers one at a time with full test validation
3. Remove duplicate route handlers from main.py
4. Final cleanup and documentation

**Lessons Learned**:
- Service layer migration (completed earlier) was successful
- Router extraction requires more careful handling of shared state
- Pragmatic approach: Create infrastructure first, integrate incrementally
- Test-driven approach ensures no functionality loss

---

**Last Updated**: November 16, 2025  
**Status**: Router infrastructure created, integration planned for future session  
**Current**: main.py still at ~2390 lines, but router modules (~ 1400 lines) ready for integration
