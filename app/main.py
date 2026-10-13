import os
import shutil
import uuid

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://frigate:5000")

app = FastAPI(title="chismoso-server")
JOBS: dict = {}


@app.get("/", response_class=HTMLResponse)
def root():
    return (
        "<html><head><title>chismoso-server</title></head>"
        "<body><h1>chismoso-server</h1>"
        "<p>Lightweight Frigate NVR wrapper (UI + API). Frigate: {}</p>"
        '<p>Health: <a href="/health">/health</a> | '
        'Cameras: <a href="/api/cameras">/api/cameras</a></p>'
        "</body></html>".format(FRIGATE_URL)
    )


@app.get("/health")
def health():
    if not FRIGATE_URL or not FRIGATE_URL.strip():
        return JSONResponse(
            {
                "status": "error",
                "error": "FRIGATE_URL is empty, set /etc/chismoso-server.env",
            },
            status_code=500,
        )
    return JSONResponse({"status": "ok", "frigate_url": FRIGATE_URL})


def _proxy(path: str):
    url = FRIGATE_URL.rstrip("/") + path
    try:
        r = httpx.get(url, timeout=10.0)
    except Exception as e:
        return JSONResponse(
            {
                "error": "frigate unreachable",
                "upstream": "{}: {}".format(type(e).__name__, e),
            },
            status_code=502,
        )
    if r.status_code != 200:
        return JSONResponse(
            {"error": "frigate error", "upstream": r.text[:500]},
            status_code=502,
        )
    try:
        return JSONResponse(r.json())
    except Exception:
        return Response(content=r.text, media_type="application/json")


@app.get("/api/cameras")
def api_cameras():
    return _proxy("/api/cameras")


@app.get("/api/load")
def api_load():
    return _proxy("/api/stats")


@app.post("/api/exports")
def api_exports(payload: dict):
    exe = shutil.which("chismoso")
    if not exe:
        return JSONResponse(
            {"error": "chismoso CLI not installed, exports unavailable"},
            status_code=503,
        )
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued", "payload": payload}
    return {"job_id": job_id}
