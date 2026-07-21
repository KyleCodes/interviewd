# Observability

Grafana dashboard "Interviewd Ops" — localhost:3000, no login. Prometheus
scrapes every 5s; Loki carries all container logs. For each panel group:
what it shows, the PromQL family behind it, what an operator concludes.

## Queue depth

- Shows: unclaimed jobs in `jobs_queue`, exported by the autoscaler.
- PromQL: `autoscaler_queue_depth`
- Conclusion: flat-low = healthy. Climbing while replicas still step up =
  scaling in progress, fine. Climbing with replicas flat at 8 and busy=1 =
  saturated; enqueue > processed persistently means shed load or raise
  WORKER_MAX. Depth is the primary "work owed" signal.

## Oldest job age

- Shows: age of the oldest unclaimed job.
- PromQL: `autoscaler_oldest_job_age_seconds`
- Conclusion: depth can look stable while age climbs — that means the queue is
  stuck or starved, not merely busy. Age spiking with depth flat = workers dead
  (see failure drill). This is the panel that distinguishes "backlogged" from
  "broken".

## Replicas: desired vs current

- Shows: autoscaler's desired replica count vs what docker reports, per
  service, over time.
- PromQL: `autoscaler_replicas_desired{service=~"worker|api"}`,
  `autoscaler_replicas_current{...}`
- Conclusion: steps in desired that current follows within seconds = loop
  healthy. Desired oscillating every cycle = thresholds too close or cooldown
  broken. Persistent desired/current gap = docker socket or start failures —
  go to Loki.

## Latency percentiles

- Shows: api p50/p95/p99 from the request-duration histogram.
- PromQL: `histogram_quantile(0.95, sum by (le) (rate(api_request_duration_seconds_bucket[1m])))`
- Conclusion: p95 > 500ms is the api scale-up trigger; if p95 stays high after
  api replicas max at 6, the bottleneck is downstream (queue/db), not the api
  tier — adding api replicas won't help and the panel proves it.

## Enqueued vs processed rate

- Shows: jobs entering vs leaving the queue, per second.
- PromQL: `rate(api_jobs_enqueued_total[1m])` vs `rate(worker_jobs_processed_total[1m])`
- Conclusion: the saturation panel. Processed tracking enqueued = keeping up.
  A persistent gap at max workers = the arrival rate exceeds max drain rate;
  the gap integrates directly into the depth panel. This pair defines the
  saturation point recorded in load-testing.md.

## Worker busy fraction

- Shows: fraction of workers mid-job (`worker_busy` gauge, 0/1 per worker,
  averaged).
- PromQL: `avg(worker_busy)`
- Conclusion: ~1.0 = no headroom; scale-up justified. ~0.2 at min replicas =
  overprovisioned, scale-down expected. Busy=1 with depth flat = perfectly
  sized — the ideal steady state.

## Cache hit ratio

- Shows: Redis hits over total score reads.
- PromQL: `rate(api_cache_hits_total[1m]) / (rate(api_cache_hits_total[1m]) + rate(api_cache_misses_total[1m]))`
- Conclusion: rises as the 50-id pool gets scored and re-polled; sawtooth
  every ~300s as SETEX TTLs expire. Hit ratio near 0 under repeat traffic =
  Redis or key-format problem.

## Status-code rates

- Shows: gateway/api responses by class (2xx/4xx/5xx).
- PromQL: `sum by (status) (rate(api_requests_total[1m]))`
- Conclusion: 404s on /score early in a run are expected (not yet scored) and
  fade as workers catch up. Any 5xx rate is real breakage; a 5xx step during
  the ramp marks where the system breaks rather than merely queues.

## Scale-event bars

- Shows: discrete autoscaler decisions (service, direction) as annotations/bars.
- PromQL: `changes(autoscaler_replicas_desired[1m])` (or the autoscaler's
  decision counter).
- Conclusion: correlates every other panel with *when the loop acted*. Events
  spaced ≥15s apart = cooldown working. Up/down/up within a minute = flapping;
  widen the depth or p95 deadband.

## Loki logs

- Shows: all container logs, labeled by compose service via docker-socket
  discovery.
- Query: `{compose_service="autoscaler"}`, `{compose_service="worker"} |= "error"`
- Conclusion: the "why" behind any metric anomaly — autoscaler decision lines,
  worker claim/ack logs, nginx upstream errors. No per-service config; new
  replicas are labeled automatically.

## Reading a scale event

Ramp phase begins. Enqueued/s steps up first; processed/s lags. Depth starts
climbing; busy fraction hits 1.0. Fifteen-plus seconds later a scale-event bar
appears, desired workers ticks up, current follows within a few seconds.
Processed/s rises to meet enqueued/s; depth stops climbing, then drains; oldest
age falls back to ~0. Meanwhile p95 crossing 500ms triggers the same sequence
on the api tier. When the phase ends, depth drains below 20, busy fraction
falls, and the loop walks replicas back down — one step per cooldown window.
One ramp, every panel accounted for.
