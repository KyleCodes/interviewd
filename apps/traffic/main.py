# traffic: the load dial for demos. Publishes traffic_target_rps (offered load)
# so Grafana can overlay it against the pipeline's processed/s — the gap between
# the two lines is the saturation evidence that justifies autoscaling.
import asyncio
import logging
import os
import random
import time

import httpx
from fastapi import FastAPI, Response
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8080")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("traffic")

TARGET_RPS = Gauge("traffic_target_rps", "Offered load set by the operator dial")
SENT = Counter("traffic_requests_sent_total", "Requests fired at the gateway", ["endpoint"])
ERRORS = Counter("traffic_request_errors_total", "Requests that failed or were shed")

state = {"target_rps": 0, "sent_total": 0, "errors_total": 0}
sem = asyncio.Semaphore(500)  # in-flight cap: we generate load, we don't OOM ourselves

app = FastAPI()


async def fire(client: httpx.AsyncClient) -> None:
    # 80/20 write/read mix; interview ids repeat (1..50) so the api's cache gets hits.
    n = random.randint(1, 50)
    try:
        if random.random() < 0.8:
            SENT.labels(endpoint="enqueue").inc()
            await client.post(f"{GATEWAY_URL}/interviews", json={"interview_id": f"iv-{n}"})
        else:
            SENT.labels(endpoint="score").inc()
            await client.get(f"{GATEWAY_URL}/score/iv-{n}")  # 404 is normal early
        state["sent_total"] += 1
    except Exception:
        state["errors_total"] += 1
        ERRORS.inc()
    finally:
        sem.release()


async def load_loop() -> None:
    # Pace in 100ms batches so N rps is spread across the second, not a burst.
    async with httpx.AsyncClient(
        timeout=5.0, limits=httpx.Limits(max_connections=500)
    ) as client:
        carry = 0.0
        while True:
            tick = time.monotonic()
            carry += state["target_rps"] / 10.0
            n, carry = int(carry), carry % 1
            for _ in range(n):
                if sem.locked():  # cap reached: shed instead of queueing unbounded work
                    state["errors_total"] += 1
                    ERRORS.inc()
                    continue
                await sem.acquire()
                asyncio.create_task(fire(client))
            await asyncio.sleep(max(0.0, 0.1 - (time.monotonic() - tick)))


@app.on_event("startup")
async def startup() -> None:
    TARGET_RPS.set(0)
    asyncio.create_task(load_loop())


class Rate(BaseModel):
    target_rps: int


@app.get("/api/state")
async def get_state():
    return state


@app.post("/api/rate")
async def set_rate(rate: Rate):
    rps = max(0, min(500, rate.target_rps))
    state["target_rps"] = rps
    TARGET_RPS.set(rps)
    log.info("level=info msg=rate_changed target_rps=%d", rps)
    return {"target_rps": rps}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


PAGE = """<!doctype html><meta charset="utf-8"><title>traffic dial</title>
<style>
body{background:#111;color:#ddd;font-family:monospace;text-align:center;padding-top:8vh}
#rps{font-size:6rem;color:#6f6}
input[type=range]{width:60%;margin:1rem 0}
button{background:#222;color:#ddd;border:1px solid #444;font-family:monospace;
padding:.4rem .9rem;margin:.2rem;cursor:pointer}
button:hover{border-color:#6f6}
small{color:#888}
</style>
<div id="rps">0</div><div>target rps</div>
<input type="range" id="dial" min="0" max="300" value="0">
<div>
<button onclick="setRate(0)">0</button><button onclick="setRate(5)">5</button>
<button onclick="setRate(25)">25</button><button onclick="setRate(60)">60</button>
<button onclick="setRate(120)">120</button><button onclick="setRate(250)">250</button>
</div>
<p><small id="stats">sent 0 / errors 0</small></p>
<p><small>drag the dial, then watch replicas at localhost:3000</small></p>
<script>
const dial=document.getElementById("dial");
async function setRate(n){dial.value=n;
await fetch("/api/rate",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({target_rps:+n})});poll();}
dial.oninput=()=>setRate(dial.value);
async function poll(){const s=await(await fetch("/api/state")).json();
document.getElementById("rps").textContent=s.target_rps;dial.value=s.target_rps;
document.getElementById("stats").textContent=`sent ${s.sent_total} / errors ${s.errors_total}`;}
setInterval(poll,2000);poll();
</script>"""


@app.get("/")
async def index():
    return Response(PAGE, media_type="text/html")
