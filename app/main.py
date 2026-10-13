import os
from datetime import datetime

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://frigate:5000")

app = FastAPI(title="chismoso-server")


def _frigate_output_args(speed: int) -> dict:
    # Frigate default (=25x, 30fps) when no ffmpeg args are sent.
    if speed == 25:
        return {}
    return {"ffmpeg_output_args": "-vf setpts=PTS/{} -r 25".format(speed)}


def _to_ts(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


@app.get("/", response_class=HTMLResponse)
def root():
    return (
        "<html><head><title>chismoso-server</title></head>"
        "<body><h1>chismoso-server</h1>"
        "<p>Lightweight Frigate NVR wrapper (UI + API). Frigate: {}</p>"
        '<p>Health: <a href="/health">/health</a> | '
        'Cameras: <a href="/api/cameras">/api/cameras</a> | '
        '<a href="/exports"><b>Timelapse erstellen</b></a></p>'
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


@app.get("/api/exports")
def api_exports_list():
    return _proxy("/api/exports")


@app.get("/api/export-jobs/{job_id}")
def api_export_job(job_id: str):
    return _proxy("/api/jobs/export/{}".format(job_id))


@app.post("/api/exports", status_code=202)
def api_exports(payload: dict):
    cameras = payload.get("cameras") or []
    if not cameras:
        return JSONResponse({"error": "no cameras given"}, status_code=400)
    try:
        start_ts = _to_ts(payload["start"])
        end_ts = _to_ts(payload["end"])
    except (KeyError, ValueError) as e:
        return JSONResponse(
            {"error": "bad start/end datetime, use ISO like 2026-10-10T00:00: {}".format(e)},
            status_code=400,
        )
    if end_ts <= start_ts:
        return JSONResponse({"error": "end must be after start"}, status_code=400)
    try:
        speed = int(payload.get("speed", 25))
    except (TypeError, ValueError):
        return JSONResponse({"error": "bad speed, use e.g. 25, 60, 1440"}, status_code=400)
    name = payload.get("name") or "chismoso-timelapse"

    results = []
    for cam in cameras:
        url = "{}/api/export/custom/{}/start/{}/end/{}".format(
            FRIGATE_URL.rstrip("/"), cam, start_ts, end_ts
        )
        body = {"source": "recordings", "name": name}
        body.update(_frigate_output_args(speed))
        try:
            r = httpx.post(url, json=body, timeout=30.0)
        except Exception as e:
            return JSONResponse(
                {
                    "error": "frigate unreachable",
                    "upstream": "{}: {}".format(type(e).__name__, e),
                },
                status_code=502,
            )
        if r.status_code == 503:
            return JSONResponse(
                {"error": "frigate export queue is full, try again later"},
                status_code=503,
            )
        if r.status_code not in (200, 202):
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:500]
            return JSONResponse(
                {"error": "frigate rejected export for camera {}".format(cam), "upstream": detail},
                status_code=400,
            )
        data = r.json()
        results.append(
            {"camera": cam, "export_id": data.get("export_id"), "status": data.get("status", "queued")}
        )
    return {"jobs": results}


@app.get("/exports", response_class=HTMLResponse)
def exports_page():
    return """<html><head><title>Timelapse erstellen</title></head>
<body><h1>Timelapse erstellen</h1>
<form id="f">
<fieldset><legend>Kameras</legend><div id="cams">lade...</div></fieldset>
<p>Start: <input type="datetime-local" id="start" required></p>
<p>Ende: <input type="datetime-local" id="end" required></p>
<p>Speed: <select id="speed">
<option value="25">25x (Standard)</option>
<option value="60">60x (1h = 1min)</option>
<option value="144">144x</option>
<option value="720">720x</option>
<option value="1440" selected>1440x (1 Tag = 1min)</option>
</select> oder custom: <input type="number" id="custom" min="2" max="3600" placeholder="z.B. 300"></p>
<p>Name: <input type="text" id="name" value="chismoso-timelapse"></p>
<p><button type="submit">Export starten</button></p>
</form>
<h2>Status</h2><pre id="out">-</pre>
<p><a href="/">zur&uuml;ck</a> | Fertige Videos: Frigate-UI &gt; Export (Download dort)</p>
<script>
async function loadCams(){
  const r = await fetch('/api/cameras');
  const div = document.getElementById('cams');
  if(!r.ok){ div.textContent = 'Fehler: Frigate nicht erreichbar ('+r.status+')'; return; }
  const cams = await r.json();
  const names = cams.map(c => (typeof c === 'string') ? c : c.name);
  div.innerHTML = names.map(n => '<label><input type="checkbox" name="cam" value="'+n+'"> '+n+'</label><br>').join('');
}
loadCams();
async function poll(id, out){
  const r = await fetch('/api/export-jobs/'+id);
  const j = await r.json();
  out.textContent = JSON.stringify(j, null, 2);
  if(j.status === 'queued' || j.status === 'running'){ setTimeout(()=>poll(id,out), 5000); }
}
document.getElementById('f').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const out = document.getElementById('out');
  const cams = [...document.querySelectorAll('input[name=cam]:checked')].map(c=>c.value);
  const custom = document.getElementById('custom').value;
  const body = {cameras: cams,
    start: document.getElementById('start').value,
    end: document.getElementById('end').value,
    speed: custom ? parseInt(custom) : parseInt(document.getElementById('speed').value),
    name: document.getElementById('name').value};
  out.textContent = 'starte...';
  const r = await fetch('/api/exports', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await r.json();
  if(!r.ok){ out.textContent = 'Fehler '+r.status+': '+JSON.stringify(j); return; }
  out.textContent = JSON.stringify(j, null, 2);
  j.jobs.forEach(job => poll(job.export_id, out));
});
</script>
</body></html>"""
