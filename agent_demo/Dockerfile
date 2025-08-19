# Agent Demo Dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir httpx==0.27.0 loguru==0.7.2

COPY run_demo.py /app/agent_demo/run_demo.py
ENV PYTHONPATH=/app

ENV CP_URL=http://control-plane:8080
ENV SERVICE=checkout
ENV ENV=prod

CMD ["python", "agent_demo/run_demo.py"]
