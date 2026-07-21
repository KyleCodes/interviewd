# interviewd — plan overview

A interview-scoring platform whose *point* is the platform: gateway → load-balanced
stateless API → Postgres-backed queue → autoscaled worker fleet → Redis result cache,
observed end-to-end with Prometheus + Loki + Grafana, and load-tested with Artillery
until it saturates.

## Architecture

```
 artillery ──► gateway (nginx :8080) ── LB round-robin ──► api ×N (FastAPI)
                                                       INSERT │        │ GET /score:
                                                              ▼        ▼ redis first
              prometheus :9090 ◄── scrape /metrics ──┐  jobs_queue   redis
                    ▲              (api, worker,     │  (postgres)   (score cache)
                    │               autoscaler)      │      ▲           ▲
 grafana :3000 ◄────┴─ promql                        │      │ SKIP LOCKED
     ▲                                               │  worker ×N (Go) ─┘
     └── logs ── loki ◄── promtail (docker socket)   │  simulated LLM call, ack=DELETE
                                                     │      ▲
              autoscaler (Python) ── polls depth/p95 ┴──────┘
                    └── docker socket → compose --scale api=N / worker=N
                    └── exports desired/current replica gauges (the money panel)
```

## Component choices (one line each)

| Concern | Choice | Why |
|---|---|---|
| API gateway + LB | nginx, docker-DNS re-resolution | one config file; LBs across `--scale api=N` with no reload |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` | N consumers with no coordinator; depth/age queryable; ack = transactional DELETE |
| Workers | Go, deliberately slow (simulated LLM call) | saturation is the demo, not throughput |
| Cache | Redis (`SETEX` by worker, read by api) | clean hit/miss story, hit-ratio metric |
| Autoscaling | custom Python control loop over docker socket | explainable end-to-end; queue depth → workers, api p95 → api replicas; cooldowns both directions |
| Metrics | Prometheus histograms/gauges/counters | latency percentiles, saturation, replicas-over-time are native |
| Logs | promtail → Loki | centralized search, zero per-service config |
| Dashboards | Grafana, provisioned JSON | one operator screen: depth, age, replicas, latency, throughput, saturation, cache ratio, logs |
| Load | Artillery ramp phases | find the arrival rate where drain < enqueue at max workers |

## Scaling policy (initial)

- workers: depth > 200 → +1 (max 8); depth < 20 → −1 (min 1); 15s cooldown
- api: p95 > 500ms → +1 (max 6); p95 < 150ms → −1 (min 2); 15s cooldown

## Non-goals

App quality, image processing reality, auth, TLS, multi-host, CI. Each is a
"with more time" line, not an accident.
