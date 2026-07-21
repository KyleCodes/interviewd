"""interviewd autoscaler: horizontally scales `api` and `worker` compose
services against queue depth (postgres) and p95 latency (prometheus).

Compose CLI is not installed in this container, so scale-up clones an
existing container of the service (image/env/network/labels) via the docker
socket. Tradeoff: compose doesn't know about the clones, but compose labels
are preserved so promtail/prometheus discovery and our own replica counting
still work. Good enough for a demo control plane.
"""
import os
import time

import docker
import psycopg
import requests
from prometheus_client import Counter, Gauge, start_http_server

env = os.environ.get
DATABASE_URL = env("DATABASE_URL")
PROMETHEUS_URL = env("PROMETHEUS_URL", "http://prometheus:9090")
PROJECT = env("COMPOSE_PROJECT", "interviewd")
POLL_SECONDS = float(env("POLL_SECONDS", "5"))
LIMITS = {  # per-service (min, max)
    "worker": (int(env("WORKER_MIN", "1")), int(env("WORKER_MAX", "8"))),
    "api": (int(env("API_MIN", "2")), int(env("API_MAX", "6"))),
}
DEPTH_UP, DEPTH_DOWN = int(env("DEPTH_UP", "200")), int(env("DEPTH_DOWN", "20"))
P95_UP_MS, P95_DOWN_MS = float(env("P95_UP_MS", "500")), float(env("P95_DOWN_MS", "150"))
COOLDOWN_SECONDS = float(env("COOLDOWN_SECONDS", "15"))

P95_QUERY = 'histogram_quantile(0.95, sum(rate(api_request_duration_seconds_bucket[1m])) by (le)) * 1000'

g_depth = Gauge("autoscaler_queue_depth", "Jobs waiting in queue")
g_oldest = Gauge("autoscaler_oldest_job_seconds", "Age of oldest queued job")
g_current = Gauge("autoscaler_current_replicas", "Running replicas", ["service"])
g_desired = Gauge("autoscaler_desired_replicas", "Desired replicas", ["service"])
g_p95 = Gauge("autoscaler_api_p95_ms", "API p95 latency (ms)")
c_events = Counter("autoscaler_scale_events_total", "Scale actions", ["service", "direction"])

client = docker.from_env()
db = None
last_scaled = {"worker": 0.0, "api": 0.0}  # per-service cooldown clocks


def read_queue():
    """Queue depth + oldest job age straight from postgres. One autocommit
    connection, rebuilt on any error (postgres restarts are expected in demos)."""
    global db
    try:
        if db is None or db.closed:
            db = psycopg.connect(DATABASE_URL, autocommit=True)
        with db.cursor() as cur:
            cur.execute("SELECT count(*), coalesce(extract(epoch from now()-min(received_at)),0) FROM jobs_queue")
            depth, oldest = cur.fetchone()
            return int(depth), float(oldest)
    except Exception:
        db = None
        raise


def read_p95():
    """API p95 (ms) from prometheus. None on empty/NaN — skip api scaling
    that tick rather than acting on a missing signal."""
    r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": P95_QUERY}, timeout=5)
    result = r.json().get("data", {}).get("result", [])
    if not result:
        return None
    val = float(result[0]["value"][1])
    return None if val != val else val  # NaN check


def running(svc):
    """Compose-labeled containers of svc, running only, oldest first."""
    labels = [f"com.docker.compose.project={PROJECT}", f"com.docker.compose.service={svc}"]
    cs = [c for c in client.containers.list(filters={"label": labels}) if c.status == "running"]
    return sorted(cs, key=lambda c: c.attrs["Created"])


def template_for(svc, containers):
    """Clone source: prefer the running compose-managed container, else any
    running one, else a *stopped* compose-managed one — so the fleet can be
    resurrected from zero (e.g. after an operator kills every worker)."""
    t = next((c for c in containers if "com.docker.compose.container-number" in c.labels), None)
    if t or containers:
        return t or containers[0]
    labels = [f"com.docker.compose.project={PROJECT}", f"com.docker.compose.service={svc}"]
    stopped = client.containers.list(all=True, filters={"label": labels})
    return next((c for c in stopped if "com.docker.compose.container-number" in c.labels),
                stopped[0] if stopped else None)


def scale(svc, containers, desired):
    cur = len(containers)
    if desired > cur:
        # Clone template: same image/env/network + compose labels so discovery
        # (promtail, prometheus) and replica counting treat it as a real member.
        # Drop container-number label to avoid duplicate compose identities.
        # CRITICAL: attach to the network with the service name as DNS alias —
        # compose does this implicitly; without it the gateway/prometheus DNS
        # round-robin (`api`, `worker` A-records) would never see the clone.
        # Template preference (see template_for): running compose-managed >
        # running clone > stopped compose-managed. Deploys recreate the compose
        # container with the new image, so clones inherit the current release.
        t = template_for(svc, containers)
        if t is None:
            print(f"level=error msg=no_template service={svc}", flush=True)
            return
        # Pick the *project* network, never docker's default bridge: a clone
        # itself carries both (create() attaches bridge implicitly), and
        # re-connecting a new container to bridge 400s.
        nets = list(t.attrs["NetworkSettings"]["Networks"].keys())
        net_name = next((n for n in nets if PROJECT in n), f"{PROJECT}_default")
        clone = client.containers.create(
            image=t.image.id,
            environment=list(t.attrs["Config"]["Env"]),
            labels={k: v for k, v in t.labels.items() if not k.startswith("com.docker.compose.container-number")},
        )
        client.networks.get(net_name).connect(clone, aliases=[svc])
        clone.start()
        c_events.labels(service=svc, direction="up").inc()
    else:
        # Kill the newest (likely one we cloned); graceful stop lets in-flight
        # jobs finish inside the 5s window.
        victim = containers[-1]
        victim.stop(timeout=5)
        victim.remove()
        c_events.labels(service=svc, direction="down").inc()
    last_scaled[svc] = time.time()
    print(f"level=info msg=scaled service={svc} {cur}->{desired}", flush=True)


def decide(svc, cur, signal, up_thr, down_thr):
    """Independent policy per service: +/-1 step within [min,max]."""
    lo, hi = LIMITS[svc]
    desired = cur
    if signal > up_thr:
        desired = min(cur + 1, hi)
    elif signal < down_thr:
        desired = max(cur - 1, lo)
    return desired


def tick():
    depth, oldest = read_queue()
    g_depth.set(depth)
    g_oldest.set(oldest)

    p95 = None
    try:
        p95 = read_p95()
    except Exception as e:
        print(f"level=error msg=prometheus_query_failed err={e!r}", flush=True)
    g_p95.set(p95 if p95 is not None else float("nan"))

    now = time.time()
    actions = []
    plan = {}
    for svc, signal, up_thr, down_thr in (("worker", depth, DEPTH_UP, DEPTH_DOWN), ("api", p95, P95_UP_MS, P95_DOWN_MS)):
        containers = running(svc)
        cur = len(containers)
        g_current.labels(service=svc).set(cur)  # exported every tick: Grafana's "instances over time"
        desired = cur if signal is None else decide(svc, cur, signal, up_thr, down_thr)
        g_desired.labels(service=svc).set(desired)
        plan[svc] = (cur, desired)
        if desired != cur:
            # Cooldown prevents flapping: signals lag the last scale action.
            if now - last_scaled[svc] >= COOLDOWN_SECONDS:
                scale(svc, containers, desired)
                actions.append(f"{svc}:{'up' if desired > cur else 'down'}")
            else:
                actions.append(f"{svc}:cooldown")

    p95_s = f"{p95:.1f}" if p95 is not None else "none"
    wc, wd = plan["worker"]
    ac, ad = plan["api"]
    print(f"level=info depth={depth} oldest_s={oldest:.1f} p95_ms={p95_s} "
          f"workers={wc}->{wd} apis={ac}->{ad} action={','.join(actions) or 'none'}", flush=True)


def main():
    start_http_server(9102)  # scraped by prometheus; drives the Grafana replica graphs
    print(f"level=info msg=autoscaler_started project={PROJECT} poll={POLL_SECONDS}s", flush=True)
    while True:
        try:
            tick()
        except Exception as e:
            # Control plane must outlive any bad tick (db restart, socket hiccup).
            print(f"level=error msg=tick_failed err={e!r}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
