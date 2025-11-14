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
- RESTful API with OpenAPI/Swagger docs
- Health check endpoint for monitoring
- Structured logging with loguru
- 61 comprehensive tests with >80% coverage

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

**Test Coverage**: 61 tests covering:
- API integration (21 tests)
- Authentication & authorization (10 tests)
- Input validation (17 tests)
- Rule engine logic (11 tests)
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
│   └── auth.py              # Authentication & authorization
├── agent_demo/
│   ├── run_demo.py          # Demo agent simulating a service
│   └── Dockerfile           # Container image for demo agent
├── tests/
│   ├── test_api_integration.py
│   ├── test_auth.py
│   ├── test_validation.py
│   ├── test_engine.py
│   └── test_engine_comprehensive.py
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

### Public Endpoints
- `GET /healthz` - Health check
- `GET /policy` - Get current policy configuration
- `GET /config/{service}/{environment}` - Get effective config for a service
- `POST /signal` - Ingest telemetry signal (optional API key)

### Protected Endpoints (Require Admin API Key)
- `POST /policy` - Update policy configuration
- `POST /auth/generate-key` - Generate new API keys

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
2. **Comprehensive Test Coverage** - 61 tests, >80% coverage
3. **Database Persistence** - SQLite/Postgres with migrations
4. **Authentication & Authorization** - API keys, admin protection

### 🚧 In Progress
5. **Observability for Control Plane** - Metrics, tracing, alerts

### 📋 Planned
- Advanced aggregation functions (percentiles, histograms)
- Rule conflict detection and warnings
- API versioning
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
