# Adaptive Observability Control Plane

A minimal, runnable reference that demonstrates an adaptive observability control plane:

- Control Plane (FastAPI):
  - Stores policies that map conditions (SLOs, error rates, latency, feature flags) to actions (log levels, trace sampling rates, metric frequencies).
  - Exposes APIs for agents to fetch effective policy by service/environment and to report telemetry signals.
  - Includes a simple rule engine and a rolling state for recent signals.

- Agent Demo (Python):
  - Simulates a service that periodically sends signals (latency, errors) to the control plane.
  - Dynamically adjusts log level and trace sampling based on control plane decisions.

This is intentionally small to make it easy to extend.

## Quick start

Requirements:

- Python 3.10+

Create and activate a virtual environment, install deps, start the control plane, then run the demo agent.

```powershell
# From repo root
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt

# Start control plane (in a terminal)
uvicorn control_plane.main:app --reload --host 0.0.0.0 --port 8080

# In another terminal: run the demo agent
.\.venv\Scripts\Activate.ps1
python agent_demo\run_demo.py
```

Open <http://localhost:8080/docs> for the API docs.

## Run tests

Once Python is installed and the venv is active:

```powershell
pip install -r requirements.txt
pytest -q
```

## Repo layout

- `control_plane/`: FastAPI app and rule engine
- `agent_demo/`: simple agent that polls policy and reports signals

## Next steps

- Persist policies and signals in a DB (SQLite/Postgres) and add migrations.
- Add auth (API keys or OIDC) and multi-tenant scoping.
- Add adaptive budgets (per-tenant sampling quotas) and dynamic span/metric selectors.
- Integrate with OpenTelemetry Collector for pushing updated sampling configs.
- Provide SDK shims for common languages.
