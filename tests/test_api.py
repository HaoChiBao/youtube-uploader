"""Tests for FastAPI app."""

from datetime import datetime, timedelta, timezone
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
        "categories:\n"
        "  - korean\n"
        "  - japanese\n"
        "channels:\n"
        "  - id: testchan\n"
        "    name: Test Channel\n"
        "    category: korean\n"
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
    assert data["channels"][0]["category"] == "korean"


def test_patch_channel_category(client):
    r = client.patch(
        "/v1/channels/testchan",
        json={"category": "japanese"},
    )
    assert r.status_code == 200
    assert r.json()["category"] == "japanese"
    assert client.get("/v1/channels/testchan").json()["category"] == "japanese"

    r2 = client.patch("/v1/channels/testchan", json={"category": ""})
    assert r2.status_code == 200
    assert r2.json()["category"] == ""


def test_categories_crud(client):
    r = client.get("/v1/categories")
    assert r.status_code == 200
    assert "korean" in r.json()["categories"]

    dup = client.post("/v1/categories", json={"name": "korean"})
    assert dup.status_code == 400

    created = client.post("/v1/categories", json={"name": "lofi"})
    assert created.status_code == 200
    assert "lofi" in created.json()["categories"]

    bad_patch = client.patch("/v1/channels/testchan", json={"category": "unknown-cat"})
    assert bad_patch.status_code == 400

    deleted = client.delete("/v1/categories/lofi")
    assert deleted.status_code == 200
    assert "lofi" not in deleted.json()["categories"]

    missing = client.delete("/v1/categories/nope")
    assert missing.status_code == 404


def test_channels_list_includes_categories(client):
    r = client.get("/v1/channels")
    assert r.status_code == 200
    data = r.json()
    assert "categories" in data
    assert "korean" in data["categories"]


def test_dashboard_includes_categories(client):
    r = client.get("/v1/dashboard")
    assert r.status_code == 200
    assert "categories" in r.json()


def test_delete_channel(client):
    r = client.delete("/v1/channels/testchan")
    assert r.status_code == 200
    body = r.json()
    assert body["channel_id"] == "testchan"
    assert body["removed"] is True
    assert client.get("/v1/channels/testchan").status_code == 404
    channels = client.get("/v1/channels").json()["channels"]
    assert not any(c["id"] == "testchan" for c in channels)


def test_authenticated_youtube_channels(client, monkeypatch: pytest.MonkeyPatch):
    from api.schemas import TokenStatus

    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(
            has_token=True,
            valid=channel_id == "testchan",
            status="ok",
        ),
    )
    r = client.get("/v1/youtube/channels")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert len(data["channels"]) == 1
    assert data["channels"][0]["id"] == "testchan"
    assert data["channels"][0]["category"] == "korean"
    assert "auth" not in data["channels"][0]


def test_jobs_list(client):
    r = client.get("/v1/jobs?status=pending")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_index_html(client):
    r = client.get("/")
    assert r.status_code == 200


def test_openapi_docs_include_examples(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    health_op = schema["paths"]["/v1/health"]["get"]
    assert "Purpose" in health_op["description"]
    assert "How to use" in health_op["description"]
    example = health_op["responses"]["200"]["content"]["application/json"]["example"]
    assert example["status"] == "ok"
    yt_channels = schema["paths"]["/v1/youtube/channels"]["get"]
    assert "Example response" in yt_channels["description"]


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


def test_register_job_idempotent(client, tmp_path: Path) -> None:
    job_id = "mv_api_test"
    video = tmp_path / f"{job_id}.mp4"
    video.write_bytes(b"v")

    payload = {
        "job_id": job_id,
        "title": "Assembler test",
        "video_uri": str(video),
    }
    r1 = client.post("/v1/channels/testchan/jobs/register", json=payload)
    assert r1.status_code == 201, r1.text

    r2 = client.post("/v1/channels/testchan/jobs/register", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["job_id"] == job_id


def test_capabilities_assembly_integration(client) -> None:
    r = client.get("/v1/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert "assembly_integration" in data
    assert data["assembly_integration"]["register_endpoint"].endswith("/jobs/register")
    assert "assembly_r2" in data["assembly_integration"]


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
    assert 'id="login-gate"' in r.text
    assert "dashboard password" in r.text.lower()

    session = c.get("/v1/auth/session")
    assert session.status_code == 200
    body = session.json()
    assert body["auth_enabled"] is True
    assert body["authenticated"] is False


def test_login_get_redirects_to_dashboard(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "dashboard-secret")
    c = TestClient(create_app())
    r = c.get("/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/"


def test_dashboard_served_via_static_dir_env(client, monkeypatch: pytest.MonkeyPatch):
    src = Path(__file__).resolve().parents[1] / "api" / "static"
    monkeypatch.setenv("UPLOADER_STATIC_DIR", str(src))
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "dashboard-secret")
    c = TestClient(create_app())
    r = c.get("/")
    assert r.status_code == 200
    assert 'id="login-gate"' in r.text
    assert "btn-login" in r.text


def test_auth_session_after_login(monkeypatch: pytest.MonkeyPatch):
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


def test_upload_one_job_retries_failed(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from api.schemas import TokenStatus
    from uploader.worker_dispatch import DispatchedUpload, ParallelDispatchResult

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4")
    staged = client.post(
        "/v1/channels/testchan/jobs",
        data={"title": "Retry via API"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
    )
    assert staged.status_code == 201
    job_id = staged.json()["job_id"]

    from uploader.registry import UploadRegistry
    from uploader.channels import load_config
    import os

    config_path = Path(os.environ["UPLOADER_CONFIG"])
    config = load_config(config_path)
    ch = config.channels[0]
    reg = UploadRegistry(ch.registry_path)
    reg.mark_failed(job_id, error="simulated failure")

    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(
            has_token=True, valid=True, status="ok"
        ),
    )

    def fake_dispatch(channel_id, config, **kwargs):
        assert kwargs.get("job_ids") == [job_id]
        return ParallelDispatchResult(
            channel_id=channel_id,
            dispatched=[
                DispatchedUpload(
                    channel_id=channel_id,
                    job_id=job_id,
                    worker_id="test-worker",
                    backend="local",
                )
            ],
        )

    monkeypatch.setattr("api.app.dispatch_parallel_uploads", fake_dispatch)

    r = client.post(
        f"/v1/channels/testchan/jobs/{job_id}/upload",
        json={"parallel": True, "count": 1},
    )
    assert r.status_code == 202, r.text
    assert reg.get(job_id).status == "pending"


def test_upload_one_job_requeues_uploaded(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from api.schemas import TokenStatus
    from uploader.job_store import archive_job
    from uploader.registry import UploadRegistry, STATUS_PENDING
    from uploader.worker_dispatch import DispatchedUpload, ParallelDispatchResult
    from uploader.channels import load_config
    import os

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4")
    staged = client.post(
        "/v1/channels/testchan/jobs",
        data={"title": "Re-upload via API"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
    )
    job_id = staged.json()["job_id"]
    config_path = Path(os.environ["UPLOADER_CONFIG"])
    config = load_config(config_path)
    ch = config.channels[0]
    base = config_path.parent.parent
    archive_job(ch.id, job_id, base=base)
    reg = UploadRegistry(ch.registry_path)
    reg.mark_uploaded(job_id, youtube_id="old123")

    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(
            has_token=True, valid=True, status="ok"
        ),
    )
    monkeypatch.setattr(
        "api.app.dispatch_parallel_uploads",
        lambda channel_id, config, **kwargs: ParallelDispatchResult(
            channel_id=channel_id,
            dispatched=[
                DispatchedUpload(
                    channel_id=channel_id,
                    job_id=job_id,
                    worker_id="test-worker",
                    backend="local",
                )
            ],
        ),
    )

    r = client.post(
        f"/v1/channels/testchan/jobs/{job_id}/upload",
        json={"parallel": True},
    )
    assert r.status_code == 202, r.text
    entry = reg.get(job_id)
    assert entry.status == STATUS_PENDING
    assert entry.youtube_id == ""


def test_register_stores_upload_at(client, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.post(
        "/v1/channels/testchan/jobs/register",
        json={
            "title": "Scheduled queue job",
            "video_uri": str(video),
            "upload_at": future,
            "publish_at": "2026-08-01T12:00:00Z",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["upload_at"] == future
    assert body["publish_at"] == "2026-08-01T12:00:00Z"
    assert body["upload_at_schedule_status"] == "disabled"


def test_register_defaults_upload_at_from_publish_at(client, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.post(
        "/v1/channels/testchan/jobs/register",
        json={
            "title": "Publish drives upload",
            "video_uri": str(video),
            "publish_at": future,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["publish_at"] == future
    assert body["upload_at"] == future
    assert body["upload_at_schedule_status"] == "disabled"


def test_register_past_upload_at_is_ready(client, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "Already due", "video_uri": str(video), "upload_at": past},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["upload_at_schedule_status"] == "ready"


def test_register_arms_cloud_scheduler(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UPLOADER_UPLOAD_AT_SCHEDULER", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo")
    monkeypatch.setenv("UPLOADER_API_PUBLIC_URL", "https://uploader.example.com")

    def fake_create(channel_id, job_id, upload_at):
        return f"projects/demo/locations/us-central1/jobs/ua-{channel_id}-{job_id}"

    monkeypatch.setattr(
        "uploader.upload_at_scheduler.create_upload_at_scheduler_job",
        fake_create,
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    future = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "Cron armed", "video_uri": str(video), "upload_at": future},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["upload_at_schedule_status"] == "scheduled"
    assert "ua-testchan-" in body["upload_at_scheduler_job"]


def test_dispatch_at_rejects_future(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    staged = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "Later", "video_uri": str(video), "upload_at": future},
    )
    assert staged.status_code == 201
    job_id = staged.json()["job_id"]
    r = client.post(f"/v1/channels/testchan/jobs/{job_id}/dispatch-at")
    assert r.status_code == 409
    assert "future" in r.text


def test_dispatch_at_dispatches_when_ready(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.schemas import TokenStatus
    from uploader.worker_dispatch import DispatchedUpload, ParallelDispatchResult

    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(has_token=True, valid=True, status="ok"),
    )
    calls = []

    def fake_dispatch(*args, **kwargs):
        calls.append(kwargs)
        return ParallelDispatchResult(
            channel_id="testchan",
            dispatched=[
                DispatchedUpload(
                    channel_id="testchan",
                    job_id=kwargs["job_ids"][0],
                    worker_id="wrk_test",
                )
            ],
        )

    monkeypatch.setattr("api.app.dispatch_parallel_uploads", fake_dispatch)
    cancel_calls = []

    def fake_cancel(*a, **k):
        cancel_calls.append(True)
        return True

    monkeypatch.setattr("api.app.cancel_upload_at_schedule", fake_cancel)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    staged = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "Due", "video_uri": str(video), "upload_at": past},
    )
    assert staged.status_code == 201
    job_id = staged.json()["job_id"]
    r = client.post(f"/v1/channels/testchan/jobs/{job_id}/dispatch-at")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dispatched"] is True
    assert body["status"] == "dispatched"
    assert calls and calls[0].get("ignore_upload_at") is True
    assert cancel_calls, "scheduler should be cleaned only after successful dispatch"


def test_dispatch_at_keeps_scheduler_when_oauth_missing(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.schemas import TokenStatus

    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(
            has_token=False, valid=False, status="missing"
        ),
    )
    cancel_calls = []
    monkeypatch.setattr(
        "api.app.cancel_upload_at_schedule",
        lambda *a, **k: cancel_calls.append(True) or True,
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    staged = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "Due", "video_uri": str(video), "upload_at": past},
    )
    job_id = staged.json()["job_id"]
    r = client.post(f"/v1/channels/testchan/jobs/{job_id}/dispatch-at")
    assert r.status_code == 400
    assert cancel_calls == [], "must not cancel scheduler before OAuth is fixed"


def test_delete_job_cancels_upload_at_schedule(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel_calls = []

    def fake_cancel(channel_id, job_id, **kwargs):
        cancel_calls.append((channel_id, job_id))
        return True

    monkeypatch.setattr("api.app.cancel_upload_at_schedule", fake_cancel)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    staged = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "To delete", "video_uri": str(video)},
    )
    assert staged.status_code == 201
    job_id = staged.json()["job_id"]
    r = client.delete(f"/v1/channels/testchan/jobs/{job_id}")
    assert r.status_code == 200, r.text
    assert cancel_calls == [("testchan", job_id)]


def test_runs_skips_future_upload_at(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.schemas import TokenStatus
    from uploader.worker_dispatch import ParallelDispatchResult

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    staged = client.post(
        "/v1/channels/testchan/jobs/register",
        json={"title": "Later", "video_uri": str(video), "upload_at": future},
    )
    assert staged.status_code == 201
    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(has_token=True, valid=True, status="ok"),
    )
    calls = []

    def fake_dispatch(*args, **kwargs):
        calls.append(kwargs)
        return ParallelDispatchResult(channel_id="testchan")

    monkeypatch.setattr("api.app.dispatch_parallel_uploads", fake_dispatch)
    r = client.post("/v1/channels/testchan/runs", json={"parallel": True})
    assert r.status_code == 400
    assert "ready for upload" in r.text
    assert calls == []


def test_upload_direct(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.schemas import TokenStatus

    monkeypatch.setattr(
        "api.app.get_token_status",
        lambda channel_id, token_path, oauth: TokenStatus(has_token=True, valid=True, status="ok"),
    )

    def fake_upload(video_path, **kwargs):
        return {"id": "yt123", "_thumbnail_warning": None}

    monkeypatch.setattr("api.direct_upload.upload_video_with_retry", fake_upload)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4")
    r = client.post(
        "/v1/channels/testchan/upload/direct",
        data={"title": "Direct upload", "privacy": "unlisted", "no_schedule": "true"},
        files={"video": ("clip.mp4", video.read_bytes(), "video/mp4")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["youtube_id"] == "yt123"
    assert body["youtube_url"] == "https://youtu.be/yt123"
    assert body["privacy"] == "unlisted"
