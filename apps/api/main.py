# interviewd api — stateless HTTP edge. Enqueues scoring jobs, serves cached scores.
# Every request hits Postgres and/or Redis; no in-process state => scale to N freely.
# Instrumented for Prometheus: request latency histogram + cache hit/miss counters.
import os
import time
import logging

import redis
from fastapi import FastAPI, HTTPException, Response
from psycopg_pool import ConnectionPool
from pydantic import BaseModel
from prometheus_client import (
    Counter, Histogram, Gauge, CONTENT_TYPE_LATEST, generate_latest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s api %(message)s")
log = logging.getLogger("api")

pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=10, open=True)
rds = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

# --- Prometheus metrics ------------------------------------------------------
# Histogram => Grafana derives p50/p95/p99 via histogram_quantile(). This is the
# "latency" signal, measured at the api (post-gateway).
REQ_LATENCY = Histogram(
    "api_request_duration_seconds", "API request latency", ["method", "route", "status"],
    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10),
)
REQ_INFLIGHT = Gauge("api_requests_in_flight", "Concurrent requests being served")
CACHE_REQUESTS = Counter("api_cache_requests_total", "Score cache lookups")
CACHE_HITS = Counter("api_cache_hits_total", "Score cache hits")
JOBS_ENQUEUED = Counter("api_jobs_enqueued_total", "Jobs pushed to the queue")

app = FastAPI(title="interviewd-api")


class InterviewIn(BaseModel):
    interview_id: str


@app.middleware("http")
async def observe(request, call_next):
    # One histogram observation per request, labeled by route+status. Cheap and
    # gives per-endpoint latency without touching each handler.
    REQ_INFLIGHT.inc()
    start = time.perf_counter()
    status = "500"
    try:
        resp = await call_next(request)
        status = str(resp.status_code)
        return resp
    finally:
        REQ_INFLIGHT.dec()
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQ_LATENCY.labels(request.method, path, status).observe(time.perf_counter() - start)


@app.post("/interviews", status_code=202)
def enqueue(iv: InterviewIn):
    # Fire-and-forget: one INSERT, return 202. Ingest is cheap while scoring is
    # slow (simulated LLM call) => the queue backs up under load => the
    # autoscaler adds workers. That's the whole demo.
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO jobs_queue (interview_id) VALUES (%s)", (iv.interview_id,)
        )
    JOBS_ENQUEUED.inc()
    return {"queued": True, "interview_id": iv.interview_id}


@app.get("/score/{interview_id}")
def get_score(interview_id: str):
    # Cache-management story: Redis first. HIT => return immediately. MISS =>
    # fall through to the durable copy in Postgres and repopulate the cache
    # (read-through); still pending => 404.
    CACHE_REQUESTS.inc()
    cached = rds.get(f"score:{interview_id}")
    if cached is not None:
        CACHE_HITS.inc()
        return {"interview_id": interview_id, "cache": "hit", "score": int(cached)}
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT score FROM scores WHERE interview_id = %s", (interview_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not scored yet")
    rds.setex(f"score:{interview_id}", 300, str(row[0]))
    return {"interview_id": interview_id, "cache": "miss", "score": row[0]}


@app.get("/stats")
def stats():
    # Convenience read: platform-level counts for a quick smoke check.
    with pool.connection() as conn:
        row = conn.execute("SELECT count(*), coalesce(avg(compute_ms),0) FROM scores").fetchone()
        depth = conn.execute("SELECT count(*) FROM jobs_queue").fetchone()[0]
    return {"scores_produced": row[0], "avg_compute_ms": round(float(row[1]), 1), "queue_depth": depth}


@app.get("/healthz")
def healthz():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    rds.ping()
    return {"ok": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
