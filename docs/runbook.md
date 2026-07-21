# Runbook

Demo scripts and failure handling. All demos assume `make up` is done and
Grafana ("Interviewd Ops", localhost:3000) is on screen.

## 1. Scale-up demo

```sh
make load-ramp
```

Narration, per panel, as the ramp climbs:

1. **Enqueued vs processed** — enqueued steps up first; the gap is the story.
2. **Queue depth** — the gap integrates here; depth climbs past 200.
3. **Scale-event bars / replicas desired-vs-current** — ≥15s later the
   autoscaler acts; desired steps up, current follows. Point out cooldown
   spacing between events.
4. **Worker busy** — pinned at 1.0 before each scale-up, dips after.
5. **Latency p95** — crosses 500ms, api tier scales 2 -> 3 independently.
6. **Final phase (150/s)** — workers flat at 8, depth diverges: this is
   saturation, by definition, on screen.
7. **After the run** — depth drains, replicas walk back down one cooldown at
   a time. Symmetric policy, no flapping.

## 2. Failure drill

```sh
make load            # terminal 1: steady 20/s background
make demo-failure    # terminal 2: stops the workers
```

Narrate: workers stop; processed/s -> 0; depth and oldest-age climb together
(age is what distinguishes "broken" from "busy"). Autoscaler desired maxes out
but current stays 0 — the desired/current gap is the outage on screen. Workers
restart; depth drains; age drops to 0. **No job loss**: ack is a transactional
delete under `FOR UPDATE SKIP LOCKED`, so anything claimed-but-unacked at kill
time was reclaimed, never dropped. Verify: total scores written matches total
accepted POSTs.

## 3. Manual override

```sh
make scale SERVICE=worker N=6
```

Replicas jump to 6 immediately. Note on the replicas panel that within a
cooldown or two the autoscaler converges back to whatever depth justifies —
the loop treats manual scaling as drift, not instruction. This is the correct
behavior for a reconciling controller; pin WORKER_MIN/MAX if you want it held.

## 4. Common failures

| symptom | likely cause | check / fix |
|---|---|---|
| Prometheus target down | container not up or crashed | localhost:9090/targets; `make logs`; `make reset` if unhealthy |
| Grafana panel empty | no traffic yet, or scrape gap | run `make load`; confirm target up; panels need ~1m of rate() window |
| Autoscaler flapping (up/down/up) | thresholds too close, or cooldown not enforced | Loki `{compose_service="autoscaler"}`; verify 15s between decision lines; widen depth deadband (200/20) |
| Replicas desired ≠ current, persistent | docker socket error or container start failure | Loki autoscaler + `docker ps`; check socket mount |
| GET /score always 404 | workers dead or Redis+Postgres write failing | oldest-age panel; `{compose_service="worker"} |= "error"` |
| Everything 5xx at high load | Postgres connection ceiling | worker/api logs for connection errors; lower replica maxima or raise pg max_connections |
| Port 8080/3000/9090 in use | stale stack or other process | `make down`; `lsof -i :8080` |
