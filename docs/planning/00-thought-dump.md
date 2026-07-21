# Thought dump (raw constraints, verbatim priorities)

- 1.5 hours to complete.
- Proctor: **services can be junk, not graded whatsoever**. Purely platform building —
  scalable infra + observability. Not optimizing app performance at all; the *worse*
  the apps perform, the easier to cause scaling events.
- Proctor priorities:
  1. **Observability** — operational dashboard for the platform: queue depth,
     oldest item age, number of instances of a service running **over time**.
  2. **Load testing** — must demonstrate autoscaling.
- Grading checklist:
  - Observability (latency, server saturation, scaling/instance count)
  - Scaling / autoscaling under load
  - Workers
  - Infra: API gateway, load balancer, S2S communication, cache management
  - Package into a GitHub repo
- End-of-exercise deliverables (from the assignment):
  1. Share code (zip/repo)
  2. How it was designed
  3. How the system is observed
  4. Demonstrate scaling; find saturation point
  5. What breaks under load
  6. What was changed to improve it
  7. What we'd do with more time
- Substrate decision: proctor allows minikube; we chose **Docker Compose + custom
  autoscaler** — a custom control loop is explainable end-to-end and carries no
  first-time-cluster risk under a 90-minute clock. k8s/HPA is the "with more
  time" answer.
- Load tool: **Artillery** (familiar — demoable live).
- Scale signal: **queue depth → workers**, api p95 latency → api replicas.
- Domain skin: AI interview scoring pipeline. Worker is deliberately slow
  (sleep + CPU burn) so saturation is trivial to induce.
- Reusing patterns from a prior personal platform build: Postgres SKIP LOCKED
  queue, promtail→Loki→Grafana logs, nx run-commands monorepo, Makefile UX.
  New here: nginx gateway/LB, Redis cache, Prometheus metrics, autoscaler.
