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


def test_exports_no_binary_returns_503(monkeypatch):
    import app.main as m

    monkeypatch.setattr(m.shutil, "which", lambda x: None)
    r = client.post(
        "/api/exports",
        json={
            "cameras": ["pool"],
            "start": "2026-10-10 00:00",
            "end": "2026-10-11 00:00",
            "speed": 1440,
        },
    )
    assert r.status_code == 503
    assert "chismoso" in r.json()["error"].lower()
