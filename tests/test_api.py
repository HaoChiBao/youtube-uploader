"""Tests for FastAPI app."""

from pathlib import Path

from fastapi.testclient import TestClient

pytest_plugins = []
try:
    from api.app import create_app
except ImportError:
    create_app = None  # type: ignore

import pytest


@pytest.fixture(autouse=True)
def isolated_api_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep API tests off the developer's real R2 bucket and config."""
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_API_KEY", raising=False)
    monkeypatch.delenv("UPLOADER_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("UPLOADER_SESSION_SECRET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "channels:\n"
        "  - id: testchan\n"
        "    name: Test Channel\n"
        "    token_path: secrets/testchan/youtube_token.json\n"
        "    registry_path: state/testchan/upload_registry.txt\n"
        "\n"
        "google:\n"
        "  oauth_port: 8080\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UPLOADER_CONFIG", str(config_path))
    try:
        from api.cache import clear_all_caches

        clear_all_caches()
    except ImportError:
        pass


@pytest.fixture
def client():
    if create_app is None:
        pytest.skip("API deps not installed")
    return TestClient(create_app())


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_capabilities(client):
    r = client.get("/v1/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert "cli_commands" in data
    assert "youtube_features" in data
    assert len(data["youtube_features"]) >= 5


def test_dashboard(client):
    r = client.get("/v1/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert "channels" in data
    assert "queue_jobs" in data
    assert "uploaded_jobs" in data
    assert "jobs" in data
    assert "config_uri" in data
    assert data["cached"] is False
    r2 = client.get("/v1/dashboard")
    assert r2.json()["cached"] is True
    r3 = client.get("/v1/dashboard?refresh=true")
    assert r3.json()["cached"] is False


def test_channels_list(client):
    r = client.get("/v1/channels")
    assert r.status_code == 200
    data = r.json()
    assert "channels" in data
    assert isinstance(data["channels"], list)
    assert "config_uri" in data


def test_jobs_list(client):
    r = client.get("/v1/jobs?status=pending")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_index_html(client):
    r = client.get("/")
    assert r.status_code == 200


def test_create_job_multipart(client, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    r = client.post(
        "/v1/channels/testchan/jobs",
        data={"title": "AI Video", "description": "Generated clip"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["channel_id"] == "testchan"
    assert body["status"] == "pending"
    assert body["title"] == "AI Video"
    assert body["job_id"]
    assert "queue" in body["video_uri"].replace("\\", "/")

    listed = client.get("/v1/jobs?channel=testchan&status=pending")
    assert listed.status_code == 200
    jobs = listed.json()
    assert len(jobs) == 1
    assert jobs[0]["id"] == body["job_id"]


def test_create_job_duplicate_id(client, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    files = {"video": ("clip.mp4", video.read_bytes(), "video/mp4")}
    data = {"title": "First", "job_id": "fixed-job-id"}
    assert client.post("/v1/channels/testchan/jobs", data=data, files=files).status_code == 201
    r = client.post("/v1/channels/testchan/jobs", data=data, files=files)
    assert r.status_code == 409


def test_create_job_unknown_channel(client, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    r = client.post(
        "/v1/channels/nope/jobs",
        data={"title": "X"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
    )
    assert r.status_code == 404


def test_register_job_from_local_uri(client, tmp_path: Path):
    job_id = "reg-job-1"
    queue_dir = tmp_path / "queue" / "testchan" / job_id
    queue_dir.mkdir(parents=True)
    video_path = queue_dir / "video.mp4"
    video_path.write_bytes(b"video")

    r = client.post(
        "/v1/channels/testchan/jobs/register",
        json={
            "title": "Pre-uploaded",
            "description": "Already on disk",
            "video_uri": str(video_path),
            "job_id": job_id,
            "privacy": "unlisted",
            "is_short": True,
            "tags": ["ai", "generated"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job_id"] == job_id
    assert body["privacy"] == "unlisted"
    assert body["is_short"] is True


def test_api_key_required_when_configured(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_API_KEY", "secret-key")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    r = client.post(
        "/v1/channels/testchan/jobs",
        data={"title": "Blocked"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
    )
    assert r.status_code == 401

    r2 = client.post(
        "/v1/channels/testchan/jobs",
        data={"title": "Allowed"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
        headers={"X-API-Key": "secret-key"},
    )
    assert r2.status_code == 201


def test_dashboard_login_session(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "dashboard-secret")
    c = TestClient(create_app())
    assert c.get("/v1/dashboard").status_code == 401
    login = c.post("/login", json={"password": "dashboard-secret"})
    assert login.status_code == 200
    assert c.get("/v1/dashboard").status_code == 200


def test_dashboard_shell_public_when_auth_enabled(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "dashboard-secret")
    c = TestClient(create_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Enter password" in r.text

    session = c.get("/v1/auth/session")
    assert session.status_code == 200
    body = session.json()
    assert body["auth_enabled"] is True
    assert body["authenticated"] is False


def test_auth_session_after_login(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "dashboard-secret")
    c = TestClient(create_app())
    c.post("/login", json={"password": "dashboard-secret"})
    body = c.get("/v1/auth/session").json()
    assert body["authenticated"] is True


def test_cataloged_api_routes_require_auth(client, monkeypatch: pytest.MonkeyPatch):
    from api.endpoint_docs import API_ENDPOINTS

    monkeypatch.setenv("UPLOADER_API_KEY", "secret-key")
    c = TestClient(create_app())
    sample = {
        "channel_ref": "testchan",
        "job_id": "job-1",
        "run_id": "run-1",
        "asset": "video",
    }
    for ep in API_ENDPOINTS:
        if not ep.get("auth", True):
            continue
        path = ep["path"]
        for key, val in sample.items():
            path = path.replace("{" + key + "}", val)
        method = ep["method"].lower()
        req = getattr(c, method, None)
        if req is None:
            continue
        r = req(path)
        assert r.status_code == 401, f"{ep['method']} {ep['path']} should require auth, got {r.status_code}"


def test_bearer_token_auth(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_API_KEY", "bearer-token")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    c = TestClient(create_app())
    r = c.post(
        "/v1/channels/testchan/jobs",
        data={"title": "Via Bearer"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
        headers={"Authorization": "Bearer bearer-token"},
    )
    assert r.status_code == 201
