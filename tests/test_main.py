import os

os.environ.setdefault("FRIGATE_URL", "http://frigate:5000")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_root_serves_html():
    r = client.get("/")
    assert r.status_code == 200
    assert "chismoso" in r.text.lower()


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_exports_page_has_form():
    r = client.get("/exports")
    assert r.status_code == 200
    assert "<form" in r.text
    assert "/api/cameras" in r.text


def test_api_cameras_proxies(monkeypatch):
    import app.main as m

    class FakeResp:
        status_code = 200

        def json(self):
            return [{"name": "pool"}]

        text = "[]"

    def fake_get(url, timeout=10.0):
        assert url.endswith("/api/cameras")
        return FakeResp()

    monkeypatch.setattr(m.httpx, "get", fake_get)
    r = client.get("/api/cameras")
    assert r.status_code == 200
    assert r.json() == [{"name": "pool"}]


def test_api_cameras_upstream_down(monkeypatch):
    import app.main as m

    def boom(url, timeout=10.0):
        raise RuntimeError("conn refused")

    monkeypatch.setattr(m.httpx, "get", boom)
    r = client.get("/api/cameras")
    assert r.status_code == 502
    assert "error" in r.json()


def _payload(**kw):
    base = {
        "cameras": ["pool"],
        "start": "2026-10-10T00:00",
        "end": "2026-10-11T00:00",
        "speed": 1440,
        "name": "test",
    }
    base.update(kw)
    return base


def test_exports_posts_to_frigate_custom_endpoint(monkeypatch):
    import app.main as m

    calls = []

    class FakeResp:
        status_code = 202

        def json(self):
            return {"success": True, "export_id": "abc123", "status": "queued"}

    def fake_post(url, json=None, timeout=30.0):
        calls.append((url, json))
        return FakeResp()

    monkeypatch.setattr(m.httpx, "post", fake_post)
    r = client.post("/api/exports", json=_payload())
    assert r.status_code == 202
    assert r.json() == {"jobs": [{"camera": "pool", "export_id": "abc123", "status": "queued"}]}
    url, body = calls[0]
    assert "/api/export/custom/pool/start/" in url
    assert body["source"] == "recordings"
    assert body["ffmpeg_output_args"] == "-vf setpts=PTS/1440 -r 25"


def test_exports_speed_25_uses_frigate_defaults(monkeypatch):
    import app.main as m

    calls = []

    class FakeResp:
        status_code = 202

        def json(self):
            return {"success": True, "export_id": "x", "status": "queued"}

    def fake_post(url, json=None, timeout=30.0):
        calls.append((url, json))
        return FakeResp()

    monkeypatch.setattr(m.httpx, "post", fake_post)
    r = client.post("/api/exports", json=_payload(speed=25))
    assert r.status_code == 202
    assert "ffmpeg_output_args" not in calls[0][1]


def test_exports_no_cameras_400():
    r = client.post("/api/exports", json=_payload(cameras=[]))
    assert r.status_code == 400


def test_exports_bad_range_400():
    r = client.post(
        "/api/exports", json=_payload(start="2026-10-11T00:00", end="2026-10-10T00:00")
    )
    assert r.status_code == 400


def test_exports_frigate_rejection_passthrough(monkeypatch):
    import app.main as m

    class FakeResp:
        status_code = 400
        text = "No recordings found for time range"

        def json(self):
            return {"success": False, "message": self.text}

    monkeypatch.setattr(m.httpx, "post", lambda url, json=None, timeout=30.0: FakeResp())
    r = client.post("/api/exports", json=_payload())
    assert r.status_code == 400
    assert "pool" in r.json()["error"]


def test_exports_queue_full_503(monkeypatch):
    import app.main as m

    class FakeResp:
        status_code = 503
        text = "queue full"

    monkeypatch.setattr(m.httpx, "post", lambda url, json=None, timeout=30.0: FakeResp())
    r = client.post("/api/exports", json=_payload())
    assert r.status_code == 503


def test_export_job_status_proxies(monkeypatch):
    import app.main as m

    class FakeResp:
        status_code = 200

        def json(self):
            return {"id": "abc", "status": "running", "progress_percent": 42}

        text = "{}"

    def fake_get(url, timeout=10.0):
        assert url.endswith("/api/jobs/export/abc")
        return FakeResp()

    monkeypatch.setattr(m.httpx, "get", fake_get)
    r = client.get("/api/export-jobs/abc")
    assert r.status_code == 200
    assert r.json()["status"] == "running"
