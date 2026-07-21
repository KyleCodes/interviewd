# interviewd

Platform take-home: an AI interview-scoring pipeline whose point is the
platform, not the app. nginx gateway/LB, autoscaled FastAPI apis + Go workers,
Postgres `SKIP LOCKED` queue, Redis result cache, observed end-to-end with
Prometheus + Loki + Grafana, load-tested with Artillery until it saturates.
The apps are deliberately slow — inducing scaling events is the demo.

**Requirements:** docker + make. Node only for Artillery/nx (via `npx`).

## Quickstart

```sh
make up
```

- Gateway: http://localhost:8080
- Grafana ("Interviewd Ops", no login): http://localhost:3000
- Prometheus: http://localhost:9090

```sh
curl -X POST localhost:8080/interviews -H 'content-type: application/json' \
  -d '{"interview_id": "iv-1"}'          # -> 202, enqueued
curl localhost:8080/score/iv-1           # -> 404 until scored, then {score, cache: hit|miss}
```

Autoscaling demo — run and watch replicas step up in Grafana:

```sh
make load-ramp
```

Other verbs: `make down | reset | logs | scale | load | demo-failure | deploy | rollback`.

## Docs

- [design.md](docs/design.md) — architecture, decisions table, control loop, limits. Read first.
- [observability.md](docs/observability.md) — every dashboard panel: PromQL and what it tells an operator.
- [load-testing.md](docs/load-testing.md) — how to run, results table, saturation definition.
- [runbook.md](docs/runbook.md) — demo scripts, failure drill, common failures.
- [planning/](docs/planning/) — raw constraints, plan, tickets.

## The 7 assignment questions

1. **Share code** — this repo; `make up` is the whole install.
2. **How it was designed** — gateway -> LB'd api -> pg queue -> autoscaled workers -> redis cache, with a custom control loop; every choice justified in [design.md](docs/design.md).
3. **How it is observed** — Prometheus (latency histograms, saturation, replica gauges) + Loki logs on one Grafana screen; panel-by-panel in [observability.md](docs/observability.md).
4. **Demonstrate scaling; saturation point** — `make load-ramp` steps 5 -> 150 req/s; saturation = arrival rate where enqueue persistently beats drain at 8 workers; [load-testing.md](docs/load-testing.md).
5. **What breaks under load** — queue depth diverges at max workers first (single-pg drain ceiling); full list in [load-testing.md](docs/load-testing.md).
6. **What was changed to improve it** — autoscaler thresholds/cooldowns tuned from ramp observations; recorded against results in [load-testing.md](docs/load-testing.md).
7. **With more time** — k8s + HPA instead of the custom loop, Kafka/SQS behind the one-INSERT/one-SELECT queue seam, multi-host; seams identified in [design.md](docs/design.md).
