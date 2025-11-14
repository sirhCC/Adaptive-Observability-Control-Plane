# Control Plane Dockerfile
FROM python:3.14-slim

WORKDIR /app

# Install runtime deps
RUN pip install --no-cache-dir fastapi==0.112.2 uvicorn[standard]==0.30.6 pydantic==2.8.2

# Copy app
COPY main.py /app/control_plane/main.py
ENV PYTHONPATH=/app

EXPOSE 8080

# Health check configuration
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; import sys; \
    req = urllib.request.Request('http://localhost:8080/v1/healthz'); \
    resp = urllib.request.urlopen(req); \
    sys.exit(0 if resp.status == 200 else 1)" || exit 1

CMD ["uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8080"]
