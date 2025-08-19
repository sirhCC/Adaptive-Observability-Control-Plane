# Control Plane Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install runtime deps
RUN pip install --no-cache-dir fastapi==0.112.2 uvicorn[standard]==0.30.6 pydantic==2.8.2

# Copy app
COPY main.py /app/control_plane/main.py
ENV PYTHONPATH=/app

EXPOSE 8080
CMD ["uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8080"]
