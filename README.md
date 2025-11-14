# 🎛️ Adaptive Observability Control Plane

[![CI](https://github.com/sirhCC/Adaptive-Observability-Control-Plane/actions/workflows/ci.yml/badge.svg)](https://github.com/sirhCC/Adaptive-Observability-Control-Plane/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.121.2-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-ready control plane for adaptive observability that dynamically adjusts logging levels, trace sampling rates, and metric collection based on real-time service behavior.

## ✨ Features

### 🎯 Core Capabilities
- **Dynamic Policy Engine** - Rules that map conditions (error rates, latency, SLOs) to observability actions
- **Real-time Adaptation** - Automatically adjusts sampling and logging based on service health
- **Multi-Service Support** - Independent policies per service and environment
- **Time-Window Aggregation** - Configurable rolling windows for metrics (p95, error rates)

### 🔒 Security & Production Ready
- ✅ **API Key Authentication** - Secure agent and admin endpoints
- ✅ **Rate Limiting** - Per-endpoint throttling with slowapi
- ✅ **Input Validation** - Comprehensive validation with Pydantic
- ✅ **Database Persistence** - SQLite (dev) / Postgres (prod) with Alembic migrations
- ✅ **Audit Logging** - Full policy change history with versioning

### 📊 Observability
- ✅ **Prometheus Metrics** - `/metrics` endpoint with custom control plane metrics
- ✅ **Structured Logging** - loguru with contextual logging
- ✅ **Health Checks** - `/healthz` endpoint for monitoring
- ✅ **OpenAPI/Swagger** - Interactive API documentation
- ✅ **154 Comprehensive Tests** - >80% code coverage with extensive error handling tests

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.12+**
- **pip** (Python package manager)

### Installation

```powershell
# Clone the repository
git clone https://github.com/sirhCC/Adaptive-Observability-Control-Plane.git
cd Adaptive-Observability-Control-Plane

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -U pip
pip install -r requirements.txt

# Initialize database
alembic upgrade head
```

### Running the Control Plane

```powershell
# Start the server (with auto-reload for development)
uvicorn control_plane.main:app --reload --host 0.0.0.0 --port 8080
```

The control plane will be available at:
- 🌐 **API**: http://localhost:8080
- 📚 **Swagger UI**: http://localhost:8080/docs
- 📖 **ReDoc**: http://localhost:8080/redoc

### Running the Demo Agent

In a separate terminal:

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Run the demo agent (simulates a service sending signals)
python agent_demo\run_demo.py
```

The agent will:
- Send telemetry signals (latency, errors) every 2 seconds
- Receive dynamic configuration from the control plane
- Adapt its observability based on real-time conditions

---

## 🧪 Testing

```powershell
# Run all tests
pytest -q

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_auth.py -v

# Run with coverage report
pytest --cov=control_plane --cov-report=html
```

**Test Coverage**: 154 tests covering:
- API integration (21 tests)
- Authentication & authorization (10 tests)
- Input validation (17 tests)
- Rule engine logic (11 tests)
- Prometheus metrics (11 tests)
- Advanced aggregations (14 tests)
- Rule conflict detection (17 tests)
- Error handling & exceptions (18 tests)
- Policy testing & simulation (10 tests)
- Signal replay & time-travel (23 tests)
- Edge cases & window filtering

---

## 📁 Project Structure

```
Adaptive-Observability-Control-Plane/
├── control_plane/
│   ├── main.py              # FastAPI application & rule engine
│   ├── models.py            # SQLAlchemy database models
│   ├── database.py          # Database connection & session management
│   ├── repository.py        # Data access layer
│   ├── auth.py              # Authentication & authorization
│   ├── exceptions.py        # Custom exception classes and handlers
│   ├── metrics.py           # Prometheus metrics
│   └── rule_validator.py    # Rule conflict detection
├── agent_demo/
│   ├── run_demo.py          # Demo agent simulating a service
│   └── Dockerfile           # Container image for demo agent
├── tests/
│   ├── test_api_integration.py      # 21 API endpoint tests
│   ├── test_auth.py                 # 10 authentication tests
│   ├── test_validation.py           # 17 input validation tests
│   ├── test_engine.py               # Basic rule engine tests
│   ├── test_engine_comprehensive.py # 11 comprehensive engine tests
│   ├── test_aggregations.py         # 14 aggregation function tests
│   ├── test_metrics.py              # 11 Prometheus metrics tests
│   ├── test_rule_validation.py      # 17 conflict detection tests
│   └── test_error_handling.py       # 18 error handling tests
├── alembic/
│   ├── versions/            # Database migrations
│   └── env.py               # Alembic configuration
├── .github/
│   ├── workflows/ci.yml     # GitHub Actions CI pipeline
│   └── dependabot.yml       # Automated dependency updates
├── requirements.txt         # Python dependencies
├── docker-compose.yml       # Multi-container orchestration
└── IMPROVEMENTS.md          # Roadmap & completed features
```

---

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Database connection string | `sqlite:///./control_plane.db` |
| `SECRET_KEY` | JWT token secret key | Auto-generated |
| `ADMIN_API_KEY` | Admin authentication key | None (optional) |

### Example Configuration

```powershell
# Development (SQLite)
$env:DATABASE_URL = "sqlite:///./control_plane.db"

# Production (Postgres)
$env:DATABASE_URL = "postgresql://user:pass@localhost/observability"
$env:ADMIN_API_KEY = "your-secure-admin-key-here"
$env:SECRET_KEY = "your-secret-key-here"
```

---

## 📡 API Endpoints

All API endpoints are versioned under the `/v1` prefix for future compatibility.

### Public Endpoints
- `GET /v1/healthz` - Health check with component status
- `GET /v1/metrics` - Prometheus metrics endpoint
- `GET /v1/policy` - Get current policy configuration
- `POST /v1/policy/validate` - Validate policy configuration (no changes applied)
- `GET /v1/config/{service}/{environment}` - Get effective config for a service
- `POST /v1/signal` - Ingest telemetry signal (optional API key)

### Protected Endpoints (Require Admin API Key)
- `POST /v1/policy` - Update policy configuration
- `POST /v1/policy?dry_run=true` - Validate policy without applying (dry-run mode)
- `POST /v1/policy/simulate` - Simulate policy with test signals
- `POST /v1/auth/generate-key` - Generate new API keys

### Signal Replay & Time-Travel Debugging

**Client-Provided Timestamps** enable replaying historical signals:

```powershell
# Send signal with historical timestamp
curl -X POST http://localhost:8080/v1/signal `
  -H "Content-Type: application/json" `
  -d '{
    "service": "api",
    "environment": "prod",
    "latency_ms": 450,
    "error": true,
    "timestamp": "2025-11-14T15:30:00+00:00"
  }'
```

**Timestamps must be:**
- ISO 8601 format with timezone
- Within 7 days in the past
- Within 1 day in the future

**Policy History** tracks all policy changes with timestamps:

```powershell
# Get policy version history
curl http://localhost:8080/v1/history/policy

# Get policy active at specific time
curl "http://localhost:8080/v1/history/policy/at?timestamp=2025-11-14T15:00:00+00:00"
```

**Signal Replay** re-evaluates historical signals with policies:

```powershell
# Replay signals with current policy
curl -X POST http://localhost:8080/v1/replay `
  -H "Content-Type: application/json" `
  -d '{
    "signals": [
      {"service": "api", "environment": "prod", "latency_ms": 500, "timestamp": "2025-11-14T15:00:00+00:00"}
    ]
  }'

# Replay with historical policy (time-travel)
curl -X POST http://localhost:8080/v1/replay `
  -H "Content-Type: application/json" `
  -d '{
    "signals": [...],
    "policy_timestamp": "2025-11-14T10:00:00+00:00"
  }'
```

**Response includes:**
- Effective configuration for each signal
- Rules that matched
- Policy information (current or historical)

**Policy Comparison** shows "what would have happened":

```powershell
# Compare how different policies handle same signals
curl -X POST http://localhost:8080/v1/compare `
  -H "Content-Type: application/json" `
  -d '{
    "signals": [
      {"service": "api", "environment": "prod", "latency_ms": 600}
    ],
    "compare_policies": ["2025-11-14T10:00:00+00:00", "current"]
  }'
```

**Response includes:**
- Effective config from each policy
- Differences between policies
- Summary statistics

**Signal Export** for offline analysis:

```powershell
# Export all signals
curl "http://localhost:8080/v1/signals/export"

# Export filtered signals
curl "http://localhost:8080/v1/signals/export?service=api&environment=prod&limit=100"

# Export with time range
curl "http://localhost:8080/v1/signals/export?start_time=2025-11-14T10:00:00+00:00&end_time=2025-11-14T16:00:00+00:00"
```

Exported signals are in JSON format suitable for replay.

### Policy Testing & Simulation

**Policy Simulation** allows you to test how policies would behave with test signals before deploying them:

```powershell
# Simulate policy with test signals
curl -X POST http://localhost:8080/v1/policy/simulate `
  -H "Content-Type: application/json" `
  -d '{
    "policy": {
      "id": "test-policy",
      "rules": [...]
    },
    "test_signals": [
      {"service": "api", "environment": "prod", "latency_ms": 600, "error": false},
      {"service": "api", "environment": "prod", "latency_ms": 200, "error": true}
    ]
  }'
```

**Response includes:**
- Which rules matched for each test signal
- Detailed condition evaluation results (matched/not matched)
- Effective configuration that would be applied
- Summary statistics (signals with/without matches, total rule matches)

**Dry-Run Mode** validates policies without applying them:

```powershell
# Validate policy without applying changes
curl -X POST "http://localhost:8080/v1/policy?dry_run=true" `
  -H "Content-Type: application/json" `
  -H "X-API-Key: $env:ADMIN_API_KEY" `
  -d '{
    "policy": {
      "id": "new-policy",
      "rules": [...]
    }
  }'
```

**Response includes:**
- Validation status (valid/invalid)
- Conflict detection results
- Warnings about overlapping rules or priorities
- Policy preview without actual deployment

### Prometheus Metrics

The `/metrics` endpoint exposes control plane operational metrics in Prometheus format:

**Signal Metrics:**
- `control_plane_signals_ingested_total` - Total signals ingested per service/environment
- `control_plane_signals_with_errors_total` - Total error signals
- `control_plane_signal_latency_ms` - Signal latency histogram
- `control_plane_signal_buffer_size` - Current buffer size gauge
- `control_plane_signal_buffer_pruned_total` - Pruned signals counter

**Policy Metrics:**
- `control_plane_policy_evaluations_total` - Policy evaluations per service/environment
- `control_plane_policy_evaluation_duration_seconds` - Evaluation duration histogram
- `control_plane_rule_matches_total` - Rule matches per rule/service/environment
- `control_plane_policy_updates_total` - Policy updates counter
- `control_plane_policy_validation_errors_total` - Validation errors counter

**Database Metrics:**
- `control_plane_db_queries_total` - Database queries per operation/table
- `control_plane_db_query_duration_seconds` - Query duration histogram

Example scrape configuration for Prometheus:

```yaml
scrape_configs:
  - job_name: 'control-plane'
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

### Example: Send a Signal

```powershell
# Send telemetry signal
curl -X POST http://localhost:8080/signal `
  -H "Content-Type: application/json" `
  -H "X-API-Key: your-api-key" `
  -d '{
    "service": "checkout-api",
    "environment": "prod",
    "latency_ms": 250.5,
    "error": false,
    "attrs": {"region": "us-east-1"}
  }'
```

### Example: Update Policy (Admin)

```powershell
# Update policy (requires admin key)
curl -X POST http://localhost:8080/policy `
  -H "Content-Type: application/json" `
  -H "X-API-Key: $env:ADMIN_API_KEY" `
  -d '{
    "policy": {
      "id": "production-policy",
      "description": "Production adaptive policy",
      "rules": [
        {
          "id": "high-error-rate",
          "priority": 10,
          "conditions": [
            {"kind": "error_rate", "op": ">", "value": 0.05, "window_s": 60}
          ],
          "actions": {
            "log_level": "DEBUG",
            "trace_sample_rate": 1.0,
            "metric_period_s": 10
          }
        }
      ]
    }
  }'
```

### Available Aggregation Metrics

The control plane calculates comprehensive metrics for rule evaluation:

**Latency Percentiles:**
- `latency_p50_ms` - Median latency (50th percentile)
- `latency_p90_ms` - 90th percentile latency
- `latency_p95_ms` - 95th percentile latency
- `latency_p99_ms` - 99th percentile latency

**Latency Statistics:**
- `latency_avg_ms` - Average latency
- `latency_min_ms` - Minimum latency
- `latency_max_ms` - Maximum latency

**Error Metrics:**
- `error_rate` - Error rate (0.0-1.0)
- `error_count` - Total error count in window

**Request Metrics:**
- `request_count` - Total request count in window
- `request_rate_per_sec` - Requests per second

**Example Rule Using Advanced Metrics:**

```json
{
  "id": "p99-latency-spike",
  "description": "Alert on p99 latency spike",
  "priority": 5,
  "conditions": [
    {"kind": "metric", "op": ">", "key": "latency_p99_ms", "value": 1000, "window_s": 60}
  ],
  "actions": {
    "log_level": "WARN",
    "trace_sample_rate": 0.5
  }
}
```

---

## 🐳 Docker Deployment

```powershell
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f control_plane

# Stop services
docker-compose down
```

Services exposed:
- Control Plane: http://localhost:8080
- Demo Agent: Running in background

---

## 🛣️ Roadmap

See [IMPROVEMENTS.md](IMPROVEMENTS.md) for the complete roadmap.

### ✅ Completed
1. **Input Validation & Rate Limiting** - Security & stability
2. **Comprehensive Test Coverage** - 121 tests, >80% coverage
3. **Database Persistence** - SQLite/Postgres with migrations
4. **Authentication & Authorization** - API keys, admin protection
5. **Observability for Control Plane** - Prometheus metrics
6. **Advanced Aggregation Functions** - Percentiles, stats, error metrics
7. **Rule Conflict Detection** - Policy validation with actionable warnings
8. **Better Error Handling** - Custom exceptions with structured responses
9. **API Versioning** - All endpoints now under `/v1` prefix

### 🚧 In Progress
None - ready for Item #10!

### 📋 Planned
- Configuration validation endpoint (pre-flight validation)
- Multi-tenancy support
- OpenTelemetry Collector integration
- Language-specific SDK shims

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Setup

```powershell
# Install development dependencies
pip install -r requirements.txt
pip install pytest-cov black isort mypy

# Run tests before committing
pytest -v

# Format code
black control_plane/ agent_demo/ tests/
isort control_plane/ agent_demo/ tests/
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- Database with [SQLAlchemy](https://www.sqlalchemy.org/)
- Migrations with [Alembic](https://alembic.sqlalchemy.org/)
- Testing with [pytest](https://pytest.org/)
- Authentication with [python-jose](https://github.com/mpdavis/python-jose)

---

## 📬 Contact

**Project Repository**: [github.com/sirhCC/Adaptive-Observability-Control-Plane](https://github.com/sirhCC/Adaptive-Observability-Control-Plane)

**Issues**: [Report a bug or request a feature](https://github.com/sirhCC/Adaptive-Observability-Control-Plane/issues)

---

<p align="center">
  <b>⭐ Star this repo if you find it useful!</b>
</p>
