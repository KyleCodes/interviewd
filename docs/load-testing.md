# Load testing

## How to run

```sh
make up            # terminal 1: bring the stack up, wait for healthy
make load-ramp     # terminal 2: 5 -> 150 req/s ramp (loadtest/ramp.yml)
```

Watch localhost:3000 ("Interviewd Ops") during the run. `make load` runs the
steady 20/s profile (loadtest/steady.yml) instead — used for the failure drill.

## Results (traffic-dial run, 2026-07-21)

Run: traffic dial (localhost:8090) at 100 rps for ~4 min (≈80/s enqueue after
the 80/20 mix), then 0 to observe drain + scale-down. Full cycle on the
"Replicas over time" panel: workers 1→8 under load, 8→1 after drain.

| observation | value |
|---|---|
| api p95 throughout (enqueue is one INSERT) | **5–7 ms** — api tier never stressed |
| worker scale-up | 1→8, one step per 15s cooldown, first step ~15s after depth crossed 200 |
| peak queue depth | **9,136** jobs |
| peak oldest-job age | **462 s** |
| drain rate at 8 workers — worker v1 | **4.5 jobs/s** (collapse — see below) |
| drain rate at 8 workers — worker v2 | **26.0 jobs/s** — exactly the theoretical ceiling 8 × 1000/307ms |
| batch duration v1 → v2 | 17.6 s → **3.07 s** (10 × 300ms + ~70ms overhead) |
| scale-down | 8→1 stepping on 15s cooldowns once depth < 20 |

**Saturation point: ≈26 jobs/s enqueue** (≈33 rps on the dial at the 80/20
mix) with WORK_MS=300 and WORKER_MAX=8. Below it the queue drains; above it
depth grows linearly at (enqueue − 26)/s and oldest-age grows without bound.
The ceiling scales linearly with WORKER_MAX and 1/WORK_MS by design.

## Definitions

**Saturation point** — the arrival rate at which enqueued/s persistently
exceeds processed/s with workers pinned at max (8): queue depth diverges
instead of draining, and oldest-job age grows without bound. Below it the
system queues and recovers; above it, backlog is permanent until load drops.

Theoretical drain ceiling: 8 workers x (1000 / WORK_MS=300) ≈ 26 jobs/s, so
saturation is expected between the 15/s and 40/s phases; the ramp's later
phases demonstrate divergence, not survival.

## What broke first (confirmed under load)

**Hot-row lock convoys + deadlocks in the worker — the real finding of this
exercise.** Worker v1 upserted into `scores` per job, mid-batch, inside the
claim transaction: each upsert took a row lock on that interview's `scores`
row and then *held it through the remaining jobs' 300ms sleeps* (seconds per
lock). With only 50 distinct interview_ids and 8 replicas, workers piled up
behind each other's locks (batches ballooned 3s → 17.6s) and, because
replicas acquired those locks in claim order rather than a consistent order,
Postgres detected deadlocks and killed transactions
(`ERROR: deadlock detected (SQLSTATE 40P01)` — visible in the Loki panel).
Net effect: 8 busy workers processed **4.5 jobs/s** — 6× worse than one
healthy worker's ceiling.

How it was noticed: throughput panel showed processed/s *falling* as replicas
rose; worker_busy pinned at 1.0; Loki showed the deadlock errors.

**Fix (worker v2):** compute all scores first, then take the `scores` locks
once, at the end of the transaction, in key-sorted order — a short (~ms),
consistently-ordered lock window. Batches returned to the 3.07s floor and the
fleet hit the exact theoretical ceiling of 26 jobs/s. Deploy demo'd live:
rebuild → recreate compose-managed worker → autoscaler re-cloned the fleet
from the new template while the backlog drained.

**Did not break:** api p95 (flat 5–7ms — enqueue is one cheap INSERT; api
scaling on p95 never triggered and would need a heavier read path to demo),
nginx (no 5xx), Postgres connections (18 peak vs default 100), Redis.
