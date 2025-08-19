import asyncio
import os
import random
from datetime import datetime

import httpx
from loguru import logger

CP_URL = os.getenv("CP_URL", "http://localhost:8080")
SERVICE = os.getenv("SERVICE", "checkout")
ENV = os.getenv("ENV", "prod")


async def send_signal(client: httpx.AsyncClient, latency_ms: float, error: bool):
    resp = await client.post(
        f"{CP_URL}/signal",
        json={
            "service": SERVICE,
            "environment": ENV,
            "latency_ms": latency_ms,
            "error": error,
            "attrs": {"host": os.getenv("COMPUTERNAME", "demo")},
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


async def get_config(client: httpx.AsyncClient):
    resp = await client.get(f"{CP_URL}/config/{SERVICE}/{ENV}", timeout=5.0)
    resp.raise_for_status()
    return resp.json()


async def main():
    logger.info("Starting demo agent for service='{}' env='{}'", SERVICE, ENV)
    async with httpx.AsyncClient() as client:
        # Fetch initial config
        cfg = await get_config(client)
        logger.info("Initial config: {}", cfg)
        log_level = cfg.get("log_level", "INFO").upper()
        sample_rate = float(cfg.get("trace_sample_rate", 0.1))
        period_s = int(cfg.get("metric_period_s", 60))

        while True:
            # Simulate latency and random errors, with occasional spikes
            base = random.gauss(150, 30)
            spike = 0.0
            if random.random() < 0.1:
                spike = random.uniform(200, 500)
            latency = max(1.0, base + spike)
            error = random.random() < (0.01 + (0.1 if spike > 0 else 0))

            cfg = await send_signal(client, latency, error)

            # Adapt local settings on response
            if cfg["log_level"] != log_level:
                log_level = cfg["log_level"]
                logger.info("Adapting log level -> {}", log_level)
            if abs(cfg["trace_sample_rate"] - sample_rate) > 1e-6:
                sample_rate = cfg["trace_sample_rate"]
                logger.info("Adapting trace_sample_rate -> {}", sample_rate)
            if cfg["metric_period_s"] != period_s:
                period_s = cfg["metric_period_s"]
                logger.info("Adapting metric_period_s -> {}", period_s)

            # Sample a trace/log message based on sample_rate
            if random.random() < sample_rate:
                logger.debug(
                    "trace sampled: ts={} latency_ms={:.1f} error={} cfg={}",
                    datetime.utcnow().isoformat(),
                    latency,
                    error,
                    cfg,
                )

            await asyncio.sleep(max(1, min(period_s, 10)))


if __name__ == "__main__":
    asyncio.run(main())
