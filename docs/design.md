# Design

Read this first. It covers the architecture, every non-obvious choice, the
control loop, and where this design stops scaling.

## Architecture

```
                         ┌─────────────────────────────────────────────────┐
                         │              observability plane                │
                         │  Prometheus (5s scrape) ── Grafana :3000       │
                         │  promtail ──> Loki ────────┘  "Interviewd Ops" │
                         └───────△──────────△──────────△───────△──────────┘
                                 │ metrics  │          │       │
 Artillery ──> nginx gateway ──> api x2-6 ──> Postgres ──> worker x1-8
 (loadtest)    :8080, LB via     (FastAPI)    jobs_queue    (Go, WORK_MS
               docker-DNS        POST 202     FOR UPDATE    simulated LLM)
               re-resolution     GET score    SKIP LOCKED       │
                                    │                           ├──> Redis
                                    │  read-through cache       │    SETEX 300
                                    └───────── Redis <──────────┤    score:<id>
                                                                └──> Postgres
                                                                     scores
              ┌──────────────────────────────────────────────┐
              │ autoscaler (control loop, 15s cooldowns)     │
              │   reads: queue depth, oldest-job age, api p95│
              │   acts:  scale worker 1-8, api 2-6           │
              │   via:   docker socket                       │
              │   exports its own decisions as metrics       │
              └──────────────────────────────────────────────┘
```

## Decisions

| component   | chose | over | because |
|---|---|---|---|
| gateway/LB  | nginx with docker-DNS re-resolution | traefik, haproxy | One static config. DNS round-robin picks up scaled api replicas automatically — no reload, no dynamic-config machinery. |
| queue       | Postgres `FOR UPDATE SKIP LOCKED` | Redis streams, Kafka, SQS | No second stateful system. Ack is a transactional delete — a crashed worker's job is simply reclaimed. Depth and oldest-age are one SQL query. N consumers, zero coordination code. |
| cache       | Redis SETEX read-through (TTL 300) | in-process cache | Shared across 2-6 api replicas, so hit ratio survives scaling. TTL = bounded staleness, no invalidation protocol. |
| autoscaling | custom control loop over the docker socket | k8s HPA | Explainable end-to-end in ~100 lines. Cooldowns in both directions prevent flapping. Exports its own decisions as metrics, so the dashboard shows *why* it scaled. k8s is the with-more-time answer, not the 90-minute one. |
| metrics     | Prometheus histograms/gauges | logs-derived metrics | Percentiles (api p95) and saturation (worker_busy) are native; no log parsing pipeline to get a number the autoscaler needs every 15s. |
| logs        | promtail -> Loki | per-service log config | Docker-socket discovery labels every stream by compose service automatically. New replicas need nothing. |
| workers     | deliberately slow (WORK_MS=300ms sleep + hash burn) | fast workers | Simulates LLM call latency. The point of this exercise is inducing and observing scaling events — the apps themselves are explicitly not what's graded. |

## The control loop

The autoscaler polls three signals every cycle: queue depth (SQL count), oldest
unclaimed job age, and api p95 from Prometheus. Policy: depth > 200 adds a
worker, depth < 20 removes one (bounds 1-8); p95 > 500ms adds an api replica,
p95 < 150ms removes one (bounds 2-6). A 15s cooldown applies after any action,
in both directions, so one slow scrape can't cause flapping. Depth — not CPU —
is the right worker signal for queue-based work: workers sleeping on a
simulated LLM call show near-zero CPU while the backlog grows unbounded; depth
measures the actual work owed. Oldest-age is the tie-breaker that catches a
stuck queue even when depth looks stable.

## Scaling limits

Single Postgres is the accepted stateful bottleneck — it is queue, scores
store, and depth oracle. The seam is deliberately narrow: enqueue is one
INSERT, claim is one SELECT; swapping in Kafka or SQS touches two functions,
nothing else. Single-host docker-compose is the substrate limit — the
autoscaler adds containers, not machines. Both are the right trade at this
scale and the first things to replace beyond it.

## Saturation findings

Measured (full detail + method in [load-testing.md](./load-testing.md)):

- **Saturation point ≈ 26 jobs/s enqueue** at WORKER_MAX=8, WORK_MS=300 —
  the fleet hits its theoretical drain ceiling (8 × 1000/307ms) exactly.
  Above it, depth grows at (enqueue − 26)/s and oldest-age is unbounded.
- **What broke first:** not the ceiling — a concurrency bug *below* it.
  Worker v1 held hot `scores` row locks across in-transaction sleeps →
  lock convoys (3s batches → 17.6s) and cross-replica deadlocks; 8 busy
  workers did 4.5 jobs/s. Diagnosed from the throughput panel (processed/s
  falling as replicas rose) + deadlock errors in Loki.
- **Fix:** compute first, then one short key-sorted upsert window at the end
  of the tx. Restored the exact theoretical ceiling.
- api tier never stressed (p95 flat at 5–7ms): enqueue is one INSERT. The
  scaling bottleneck at this shape is always the worker fleet, by design.
