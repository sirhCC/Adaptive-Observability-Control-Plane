# Adaptive Observability Control Plane - Improvement Roadmap

This document tracks improvements, fixes, and features to be implemented, prioritized from high to low.

## 🔴 HIGH PRIORITY (Critical for Production)

### 1. Input Validation & Rate Limiting ⚠️
**Status:** ✅ COMPLETED  
**Impact:** Security & Stability

- [x] Add rate limiting on `/signal` endpoint - agents could DDoS the control plane
- [x] Add validation on service/env names (could inject special chars)
- [x] Implement max buffer size for SIGNALS - memory leak risk
- [x] Add payload size limits
- [ ] Add request throttling per service/tenant (future enhancement)

### 2. Comprehensive Test Coverage 🧪
**Status:** ✅ COMPLETED  
**Impact:** Reliability

- [x] Test all API endpoints (health, policy CRUD, signal ingestion, config)
- [x] Test edge cases (empty signals, single signal, all errors, mixed latencies)
- [x] Add integration tests with real HTTP calls (TestClient)
- [x] Test input validation (service names, latency bounds, attrs limits)
- [x] Test error responses (422, 404, 400)
- [x] Test concurrent requests and data isolation
- [x] Test aggregate calculations (p95, error rate, window filtering)
- [x] Test signal pruning and buffer management
- [x] Test rate limiting functionality
- [x] 51 comprehensive tests passing, coverage significantly improved

### 3. Database Persistence 💾
**Status:** ✅ COMPLETED  
**Impact:** Data Loss Prevention

- [x] Database models for policies, signals, and audit logs
- [x] SQLite for local dev with async support (aiosqlite)
- [x] Postgres support via psycopg2-binary
- [x] Alembic migrations configured and initial schema created
- [x] Policy audit log with versioning
- [x] Repository layer for clean database operations
- [x] Startup event seeds default policy automatically
- [x] Database URL configurable via environment variable

### 4. Authentication & Authorization 🔐
**Status:** ✅ COMPLETED  
**Impact:** Security

- [x] API key validation infrastructure with `X-API-Key` header
- [x] Admin API key protection for sensitive endpoints (`ADMIN_API_KEY` env var)
- [x] Protected `/policy` POST endpoint - requires admin authentication
- [x] API key generation endpoint (`/auth/generate-key`) for admins
- [x] Optional authentication on `/signal` endpoint (recommended but not required)
- [x] JWT token support with python-jose
- [x] Password hashing with bcrypt via passlib
- [x] Backward compatible - works without auth configured
- [x] 10 comprehensive authentication tests passing

### 5. Observability for the Control Plane Itself 📊
**Status:** ✅ COMPLETED  
**Impact:** Operational Visibility

- [x] Add metrics on rule evaluation performance (latency, throughput)
- [x] Add structured logging for policy changes
- [x] Track signal ingestion rate and buffer sizes
- [x] Expose Prometheus `/metrics` endpoint
- [x] Custom Prometheus metrics for control plane operations
  - `control_plane_signals_ingested_total` - Total signals ingested per service/env
  - `control_plane_signals_with_errors_total` - Error signals per service/env
  - `control_plane_signal_latency_ms` - Signal latency histogram
  - `control_plane_signal_buffer_size` - Current buffer size gauge
  - `control_plane_signal_buffer_pruned_total` - Pruned signals counter
  - `control_plane_policy_evaluations_total` - Policy evaluations per service/env
  - `control_plane_policy_evaluation_duration_seconds` - Evaluation duration histogram
  - `control_plane_rule_matches_total` - Rule match counter per rule/service/env
  - `control_plane_policy_updates_total` - Policy update counter
  - `control_plane_policy_validation_errors_total` - Validation error counter
  - `control_plane_db_queries_total` - Database query counter
  - `control_plane_db_query_duration_seconds` - Database query duration histogram
- [x] 11 comprehensive metrics tests passing
- [ ] Add alerting when control plane is unhealthy (future)
- [ ] Add distributed tracing for request flows (future)
- [ ] Add Grafana dashboard for control plane health (future)

---

## 🟡 MEDIUM PRIORITY (Important for Robustness)

### 6. Advanced Aggregation Functions
**Status:** ✅ COMPLETED  
**Impact:** Feature Completeness

- [x] Add p50, p90, p99 percentile calculations
- [x] Add avg, max, min aggregations
- [x] Add rate calculations (req/sec, error count)
- [x] Add count aggregations (request count, error count)
- [x] Sliding window aggregations (via window_s parameter)
- [x] Support for all percentiles in rule conditions
- [x] 14 comprehensive tests for aggregation functions
- [ ] Add histogram buckets (future enhancement)
- [ ] Support custom percentiles via API (future enhancement)

### 7. Rule Conflict Detection & Warnings
**Status:** ✅ COMPLETED  
**Impact:** User Experience

- [x] Detect overlapping rules with same priority
- [x] Validate rule priorities and scope overlaps
- [x] Warn when rules may never fire due to earlier 'always' rules
- [x] Detect duplicate rule IDs (blocks update)
- [x] Detect 'always' conditions mixed with other conditions
- [x] Add `/policy/validate` endpoint for pre-flight validation
- [x] Provide actionable suggestions for resolving conflicts
- [x] Integrated validation in policy update endpoint
- [x] Severity levels: error (blocks), warning (logs), info
- [x] 17 comprehensive tests for conflict detection
- [ ] Show "effective rules" for test signals (future enhancement)

### 8. Better Error Handling
**Status:** ✅ COMPLETED  
**Impact:** Debugging & Reliability

- [x] Replace generic 500 errors with specific error codes
- [x] Custom exception hierarchy for structured errors
- [x] Return structured error responses (error type, message, status, details)
- [x] Add validation errors with field-level details
- [x] Enhanced health check endpoint with component status
- [x] 18 comprehensive tests for error handling
- [ ] Add request ID tracking for debugging (future enhancement)
- [ ] Add error telemetry and categorization (future enhancement)

### 9. API Versioning
**Status:** ✅ COMPLETED  
**Impact:** Future-proofing

- [x] Add `/v1` prefix to all routes
- [x] Create APIRouter with version prefix
- [x] Update all tests to use versioned endpoints
- [x] Verify all 121 tests pass with new paths
- [ ] Implement version negotiation via headers (future enhancement)
- [ ] Add deprecation warnings for old endpoints (when v2 is needed)
- [ ] Document breaking change policy (when v2 is needed)
- [ ] Add migration guides between versions (when v2 is needed)

### 10. Configuration Validation Endpoint  
**Status:** ✅ Completed  
**Impact:** Developer Experience

- [x] Add `POST /v1/policy/validate` to test rules without applying (completed in Item #7)
- [x] Add `POST /v1/policy/simulate` to test policy with sample signals
- [x] Add dry-run mode for policy updates (`?dry_run=true`)
- [x] Show which rules would match given test inputs with detailed evaluation results
- [x] Validate condition logic before saving with conflict detection
- [x] 10 comprehensive tests for simulation and dry-run features

**Implementation Notes:**
- Policy simulation accepts 1-100 test signals and returns:
  - Matched rules for each signal with condition evaluation details
  - Effective configuration that would be applied
  - Summary statistics (signals with/without matches, total rule matches)
- Dry-run mode validates and simulates without applying changes:
  - Returns validation results, conflict warnings, and policy preview
  - Same authentication requirements as regular policy updates
- Detailed condition evaluation shows which conditions matched/failed
- Integrated with existing rule conflict detection from Item #7

### 11. ✅ Signal Replay & Time-Travel
**Status:** Completed  
**Impact:** Debugging

- [x] Accept client-provided timestamps in `/signal` payload
  - Timestamps validated: 7 days past, 1 day future, timezone-aware
  - Backward compatible (optional timestamp field)
- [x] Add endpoints to query historical configs
  - `GET /v1/history/policy` - Policy version history
  - `GET /v1/history/policy/at` - Policy active at specific time
- [x] Support replaying past signals for debugging
  - `POST /v1/replay` - Replay signals with current or historical policy
  - Time-travel: use policy from any point in history
- [x] Add "what would have happened" analysis
  - `POST /v1/compare` - Compare policies side-by-side
  - Shows differences in effective configs
- [x] Add signal export for offline analysis
  - `GET /v1/signals/export` - Export with filters (service, env, time range)
  - Format suitable for replay
- [x] Comprehensive tests (23 tests, all passing)
- [x] Documentation updated in README.md

---

## 🟢 LOW PRIORITY (Nice to Have)

### 12. ✅ Export/Import Policies
**Status:** Completed  
**Impact:** Portability

- [x] Add `GET /v1/policy/export` returning YAML/JSON
  - Supports both JSON and YAML formats
  - Optional history metadata included
  - Proper content-type and download headers
- [x] Add `POST /v1/policy/import` from file
  - Auto-detects JSON/YAML format
  - Dry-run mode for validation
  - Full validation with conflict detection
- [x] Support GitOps workflow (policy as code)
  - Export/import round-trip tested
  - Templates can be imported directly
- [x] Add policy templates/presets
  - 5 templates: production-safe, development, performance-focused, cost-optimized, balanced
  - GET `/v1/policy/templates` - List all templates
  - GET `/v1/policy/templates/{name}` - Get specific template
- [x] Comprehensive tests (25 tests, all passing)
- [x] Documentation updated in README.md

### 13. ✅ Docker Health Checks
**Status:** Completed  
**Impact:** Deployment

- [x] Add HEALTHCHECK to Dockerfiles
  - Control plane Dockerfile includes HEALTHCHECK directive
  - 30s interval, 5s timeout, 3 retries, 10s start period
- [x] Implement `/v1/healthz` liveness check
  - Returns 200 when healthy, 503 when degraded
  - Checks database connectivity and signal buffer status
  - Suitable for Docker HEALTHCHECK and K8s liveness probe
- [x] Implement `/v1/readyz` readiness check (DB connectivity)
  - Returns 200 when ready, 503 when not ready
  - Validates database connection and policy initialization
  - Suitable for K8s readiness and startup probes
- [x] Add startup probes for Kubernetes
  - Example K8s configuration with all probe types documented
- [x] Add dependency health checks
  - Database connectivity validation
  - Policy initialization check
  - Signal buffer status monitoring
- [x] Update docker-compose.yml with health checks
  - Agent waits for control plane to be healthy (depends_on with condition: service_healthy)
- [x] Comprehensive tests (22 tests, all passing)
- [x] Documentation with K8s examples

### 14. ✅ CORS Configuration
**Status:** Completed  
**Impact:** Web Integration

- [x] Add CORS middleware
  - CORSMiddleware integrated with FastAPI
  - Proper ordering before other middleware
- [x] Make CORS origins configurable
  - `CORS_ORIGINS` - Comma-separated origins (default: `*`)
  - `CORS_ALLOW_CREDENTIALS` - Support cookies/auth (default: `false`)
  - `CORS_ALLOW_METHODS` - Allowed HTTP methods (default: `*`)
  - `CORS_ALLOW_HEADERS` - Allowed headers (default: `*`)
- [x] Support browser-based admin UIs
  - Full browser compatibility
  - Custom headers (X-API-Key) supported
  - Rate limit headers exposed
- [x] Add preflight request handling
  - All OPTIONS requests handled automatically
  - Preflight works without authentication
- [x] Comprehensive tests (26 tests, all passing)
- [x] Documentation with browser examples

### 15. ✅ Graceful Shutdown
**Status:** Completed  
**Impact:** Reliability

- [x] Add signal handlers for SIGTERM/SIGINT
  - Signal handlers installed during lifespan startup
  - SIGTERM for Docker/Kubernetes
  - SIGINT for local development (Ctrl+C)
- [x] Wait for in-flight requests to complete
  - Brief grace period (2s) before full shutdown
  - Respects SHUTDOWN_TIMEOUT configuration
- [x] Flush buffered signals before exit
  - Logs count of signals flushed
  - Clears SIGNALS buffer after flush
  - Error handling for flush failures
- [x] Add configurable shutdown timeout
  - `SHUTDOWN_TIMEOUT` environment variable (default: 30s)
  - Prevents indefinite hangs
- [x] Add shutdown hooks for cleanup
  - Modern lifespan context manager (replaces deprecated on_event)
  - Startup: Database init, metrics setup, policy seeding
  - Shutdown: Signal flush, request wait, resource cleanup
- [x] Comprehensive tests (22 tests, all passing)
- [x] Documentation with K8s and Docker examples

### 16. Action Merge Strategies
**Status:** Not Started  
**Impact:** Flexibility

- [ ] Add configurable merge strategies (not just "last writer wins")
- [ ] Support: min/max sampling rate
- [ ] Support: strictest log level
- [ ] Support: additive actions
- [ ] Make strategy configurable per-policy or per-rule

### 17. Feature Flag Support
**Status:** Not Started  
**Impact:** Feature Completeness

- [ ] Implement `kind="feature_flag"` condition (currently defined but not used)
- [ ] Integrate with LaunchDarkly
- [ ] Integrate with Split.io
- [ ] Integrate with custom feature flag service
- [ ] Add feature flag evaluation caching

### 18. Wildcard Service/Env Matching
**Status:** Not Started  
**Impact:** Flexibility

- [ ] Implement `*` wildcard for service/env (mentioned in comments but not working)
- [ ] Add regex pattern matching for service names
- [ ] Add glob pattern matching
- [ ] Support service groups/tags

### 19. Metrics Endpoint
**Status:** Not Started  
**Impact:** Monitoring

- [ ] Add Prometheus `/metrics` endpoint
- [ ] Expose: `rule_evaluations_total` counter
- [ ] Expose: `signals_received_total` counter
- [ ] Expose: `active_signals_count` gauge
- [ ] Expose: `policy_changes_total` counter
- [ ] Expose: `rule_evaluation_duration_seconds` histogram
- [ ] Add custom labels (service, env, rule_id)

### 20. Code Quality Fixes
**Status:** Not Started  
**Impact:** Maintainability

- [x] Remove unused `HTTPException` import
- [x] Refactor `global POLICY` to use dependency injection
- [ ] Add type hints for `op_map` functions
- [ ] Add docstrings to all functions and classes
- [ ] Split `main.py` into modules:
  - [ ] `models.py` - Pydantic models
  - [ ] `engine.py` - Rule evaluation logic
  - [ ] `api.py` - FastAPI routes
  - [ ] `storage.py` - Data persistence layer
- [ ] Add linting configuration (ruff, black, mypy)
- [ ] Add pre-commit hooks
- [ ] Add code complexity checks

---

## 📝 Notes

### Recently Completed
- ✅ Fixed p95 percentile calculation bug
- ✅ Replaced deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)`
- ✅ Added per-rule window filtering for aggregates
- ✅ Added stricter type validation with Literal types

### Quick Wins (Low Effort, High Impact)
1. Fix linter warnings (#20)
2. Add basic tests (#2)
3. Add rate limiting (#1)
4. Add better error handling (#8)

### Dependencies
- Item #3 (Database) blocks horizontal scaling
- Item #4 (Auth) should be implemented before exposing to internet
- Item #5 (Observability) needed before production deployment
- Item #7 (Rule conflicts) depends on #10 (validation endpoint)

### Estimated Timeline
- **Phase 1 (Week 1-2):** Items #1, #2, #8, #20
- **Phase 2 (Week 3-4):** Items #3, #4, #5
- **Phase 3 (Month 2):** Items #6, #7, #9, #10
- **Phase 4 (Month 3+):** Remaining items as needed
