# Tickets & parallelization

Lanes touch disjoint paths, so A–D run in parallel (separate agents/terminals).
Lane 0 first (shared files), Lane Z last (integration).

## Lane 0 — platform skeleton (serial, first)
- **T1** `docker-compose.yml`, `.env`, `Makefile`, `nx.json`, `package.json`,
  per-app `project.json`, promtail config, grafana datasources, loki service.
  Everything else assumes these exist.

## Lane A — worker (`apps/worker/**`)
- **T2** Go consumer: claim batch with `FOR UPDATE SKIP LOCKED`; per job
  sleep `WORK_MS` (default 300) + SHA256 burn; `SETEX score:<interview_id>` in Redis;
  upsert `scores`; DELETE claimed = ack; one tx. Prometheus on :9100:
  `worker_jobs_processed_total`, `worker_job_duration_seconds` (histogram),
  `worker_busy` (gauge). Reconnect with backoff.

## Lane B — gateway + autoscaler (`apps/gateway/**`, `apps/autoscaler/**`)
- **T3** nginx: `resolver 127.0.0.11 valid=5s`; `proxy_pass` via variable so
  replica set is re-resolved; JSON access log to stdout; :8080.
- **T4** autoscaler: every 5s read queue depth + oldest age (Postgres) and api
  p95 (Prometheus HTTP API); apply policy (see 01-overview); scale via docker
  CLI against the compose project; export gauges
  `autoscaler_{desired,current}_replicas{service=}`, `autoscaler_queue_depth`,
  `autoscaler_oldest_job_seconds`; log every decision.

## Lane C — observability config (`ops/prometheus/**`, `ops/grafana/**`)
- **T5** prometheus.yml: `dns_sd_configs` A-record scrape of `api`:8000 and
  `worker`:9100 (docker DNS returns every replica IP), static scrape of
  autoscaler:9102. 5s interval.
- **T6** Grafana ops dashboard JSON: queue depth, oldest age, **replicas over
  time (desired vs current)**, api p50/p95/p99, enqueue vs processed rate,
  worker busy %, cache hit ratio, error rate, Loki logs panel.

## Lane D — load + docs (`loadtest/**`, `docs/*.md`)
- **T7** artillery `ramp.yml` (phases 5→20→50→100 rps; 80% POST /interviews from a
  small interview_id pool, 20% GET /score same pool) and `steady.yml`.
- **T8** skeletons: `design.md`, `observability.md`, `load-testing.md`,
  `runbook.md`, `README.md`.

## Lane Z — integrate + demo (serial, last)
- **T9** `make up`; fix builds; verify every Prometheus target up; dashboard live.
- **T10** run ramp; watch autoscaler step workers 1→8; record saturation point
  and what-breaks notes into `docs/load-testing.md`.
- **T11** git init, GitHub repo, final README + the 7 assignment answers.

## Dependency graph

```
T1 ──┬── T2 (A) ──┐
     ├── T3,T4 (B)┤
     ├── T5,T6 (C)├── T9 ── T10 ── T11
     └── T7,T8 (D)┘
```
