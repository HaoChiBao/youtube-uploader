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
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "channels: []\n\ngoogle:\n  oauth_port: 8080\n",
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
