# Load testing

## How to run

```sh
make up            # terminal 1: bring the stack up, wait for healthy
make load-ramp     # terminal 2: 5 -> 150 req/s ramp (loadtest/ramp.yml)
```

Watch localhost:3000 ("Interviewd Ops") during the run. `make load` runs the
steady 20/s profile (loadtest/steady.yml) instead — used for the failure drill.

## Results

| phase | arrival rate | p95 | depth (end of phase) | workers | apis | notes |
|---|---|---|---|---|---|---|
| warm     | 5/s   | TODO | TODO | TODO | TODO | TODO |
| light    | 15/s  | TODO | TODO | TODO | TODO | TODO |
| moderate | 40/s  | TODO | TODO | TODO | TODO | TODO |
| heavy    | 80/s  | TODO | TODO | TODO | TODO | TODO |
| beyond   | 150/s | TODO | TODO | TODO | TODO | TODO |

## Definitions

**Saturation point** — the arrival rate at which enqueued/s persistently
exceeds processed/s with workers pinned at max (8): queue depth diverges
instead of draining, and oldest-job age grows without bound. Below it the
system queues and recovers; above it, backlog is permanent until load drops.

Theoretical drain ceiling: 8 workers x (1000 / WORK_MS=300) ≈ 26 jobs/s, so
saturation is expected between the 15/s and 40/s phases; the ramp's later
phases demonstrate divergence, not survival.

## What breaks first

TODO after the run — candidates to confirm/refute:

- [ ] depth divergence at max workers (expected first)
- [ ] api p95 blowout at max api replicas
- [ ] Postgres connection ceiling under combined api+worker load
- [ ] nginx upstream timeouts / 5xx during the 150/s phase
- [ ] Artillery-side timeouts (client, not system)

Copy the confirmed findings into design.md "## Saturation findings".
