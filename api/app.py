"""FastAPI application — local dev server for youtube-uploader."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.capabilities import CLI_COMMANDS, YOUTUBE_FEATURES
from api.deps import (
    api_public_base,
    config_path,
    get_app_config,
    get_config_uri,
    get_oauth_settings,
    get_storage_backend,
    get_storage_base,
    oauth_configured,
    resolve_channel_ref,
)
from api.oauth_sessions import OAuthSession, pop_session, save_session
from api.run_tracker import create_run, get_run, set_complete, set_failed, set_running
from api.schemas import (
    CapabilitiesOut,
    ChannelListResponse,
    ChannelOut,
    HealthResponse,
    JobOut,
    OAuthStartResponse,
    PlanItemOut,
    RunRequest,
    RunResponse,
    RunStatusOut,
    TokenStatus,
    YouTubeVideoOut,
)
from uploader import __version__
from uploader.channel_list import list_channel_videos
from uploader.job_metadata import load_job_metadata
from uploader.job_store import remove_job
from uploader.oauth import oauth_is_configured
from uploader.oauth_web import (
    api_redirect_uri,
    build_authorization_url,
    credentials_to_json,
    exchange_code_for_credentials,
    inspect_token_file,
    new_oauth_state,
    register_channel_from_credentials,
)
from uploader.registry import UploadRegistry
from uploader.scheduler import compute_publish_schedule, parse_start, run_all_channels, run_channel
from uploader.state_store import ensure_bucket_structure

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="YouTube Uploader API",
        description="HTTP API for multi-channel YouTube upload queue management",
        version=__version__,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        index_path = STATIC_DIR / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return HTMLResponse("<h1>YouTube Uploader API</h1><p>See <a href='/docs'>/docs</a></p>")

    @app.get("/health", response_model=HealthResponse)
    @app.get("/v1/health", response_model=HealthResponse)
    def health():
        return HealthResponse(version=__version__)

    @app.get("/v1/capabilities", response_model=CapabilitiesOut)
    def capabilities():
        endpoints = [
            {"method": "GET", "path": "/v1/health", "description": "Health check"},
            {"method": "GET", "path": "/v1/capabilities", "description": "CLI inventory + YouTube features"},
            {"method": "GET", "path": "/v1/channels", "description": "List channels + auth status + pending counts"},
            {"method": "GET", "path": "/v1/channels/{id}", "description": "Single channel detail"},
            {"method": "GET", "path": "/v1/jobs", "description": "List queue jobs (?channel= &status=)"},
            {"method": "GET", "path": "/v1/channels/{id}/jobs/{job_id}", "description": "Job detail + metadata"},
            {"method": "DELETE", "path": "/v1/channels/{id}/jobs/{job_id}", "description": "Remove from queue"},
            {"method": "GET", "path": "/v1/channels/{id}/plan", "description": "Preview publish schedule"},
            {"method": "POST", "path": "/v1/channels/{id}/runs", "description": "Start upload run (background)"},
            {"method": "GET", "path": "/v1/runs/{run_id}", "description": "Poll run status"},
            {"method": "POST", "path": "/v1/runs/all", "description": "Run all channels (background)"},
            {"method": "GET", "path": "/v1/channels/{id}/youtube/videos", "description": "List YouTube videos"},
            {"method": "POST", "path": "/v1/oauth/start", "description": "Start OAuth (add channel)"},
            {"method": "POST", "path": "/v1/channels/{id}/oauth/start", "description": "Start OAuth (reauth)"},
            {"method": "GET", "path": "/v1/oauth/callback", "description": "OAuth callback (browser redirect)"},
            {"method": "POST", "path": "/v1/storage/init", "description": "Initialize R2 bucket layout"},
        ]
        return CapabilitiesOut(
            cli_commands=CLI_COMMANDS,
            youtube_features=YOUTUBE_FEATURES,
            api_endpoints=endpoints,
        )

    def _token_status(channel) -> TokenStatus:
        oauth = get_oauth_settings()
        info = inspect_token_file(
            channel.token_path,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
        )
        return TokenStatus(
            has_token=info.get("has_token", False),
            valid=info.get("valid", False),
            status=info.get("status", "unknown"),
        )

    def _channel_out(ch) -> ChannelOut:
        reg = UploadRegistry(ch.registry_path)
        pending = len(reg.pending(channel_id=ch.id))
        return ChannelOut(
            id=ch.id,
            name=ch.name,
            youtube_channel_id=ch.youtube_channel_id,
            custom_url=ch.custom_url,
            token_path=ch.token_path,
            registry_path=ch.registry_path,
            auth=_token_status(ch),
            pending_count=pending,
        )

    @app.get("/v1/channels", response_model=ChannelListResponse)
    def list_channels():
        config = get_app_config()
        return ChannelListResponse(
            config_uri=get_config_uri(),
            storage=get_storage_backend(),
            channels=[_channel_out(ch) for ch in config.channels],
        )

    @app.get("/v1/channels/{channel_ref}", response_model=ChannelOut)
    def get_channel(channel_ref: str):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return _channel_out(ch)

    def _entry_to_job(entry) -> JobOut:
        return JobOut(
            id=entry.id,
            channel_id=entry.channel_id,
            status=entry.status,
            title=entry.title,
            description=entry.description,
            video_uri=entry.resolved_video_uri(),
            thumbnail_uri=entry.resolved_thumbnail_uri(),
            youtube_id=entry.youtube_id,
            youtube_url=entry.youtube_url,
            publish_at=entry.publish_at,
            created_at=entry.created_at,
            error=entry.error,
        )

    @app.get("/v1/jobs", response_model=list[JobOut])
    def list_jobs(
        channel: str | None = Query(default=None),
        status: str | None = Query(default="pending"),
    ):
        config = get_app_config()
        channels = config.channels
        if channel:
            try:
                channels = [resolve_channel_ref(channel)]
            except KeyError as e:
                raise HTTPException(404, str(e)) from e

        jobs: list[JobOut] = []
        for ch in channels:
            reg = UploadRegistry(ch.registry_path)
            if status == "pending":
                entries = reg.pending(channel_id=ch.id)
            elif status:
                entries = [e for e in reg.load() if e.status == status and e.channel_id == ch.id]
            else:
                entries = [e for e in reg.load() if e.channel_id == ch.id]
            jobs.extend(_entry_to_job(e) for e in entries)
        return jobs

    @app.get("/v1/channels/{channel_ref}/jobs/{job_id}")
    def get_job(channel_ref: str, job_id: str):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        reg = UploadRegistry(ch.registry_path)
        entry = reg.get(job_id)
        if entry is None:
            raise HTTPException(404, f"Job not found: {job_id}")
        base = get_storage_base()
        meta = load_job_metadata(entry, base=base, channel=ch, config_defaults=get_app_config().job_defaults)
        return {"job": _entry_to_job(entry), "metadata": meta.to_dict() if meta else None}

    @app.delete("/v1/channels/{channel_ref}/jobs/{job_id}")
    def delete_job(channel_ref: str, job_id: str):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        try:
            removed = remove_job(ch, job_id, base=get_storage_base())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"removed": removed.job_id, "deleted_files": len(removed.deleted_paths)}

    @app.get("/v1/channels/{channel_ref}/plan", response_model=list[PlanItemOut])
    def plan_channel(
        channel_ref: str,
        limit: int | None = Query(default=None),
        no_schedule: bool = Query(default=False),
        start: str | None = Query(default=None),
        interval_hours: float | None = Query(default=None),
    ):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        reg = UploadRegistry(ch.registry_path)
        pending = reg.pending(channel_id=ch.id)
        if limit is not None:
            pending = pending[: max(0, limit)]
        if not pending:
            return []
        ivl = interval_hours if interval_hours is not None else ch.publish.interval_hours
        start_dt = parse_start(start, timezone_name=ch.publish.timezone, default_hour=ch.publish.hour)
        plan = compute_publish_schedule(pending, start_dt, ivl, no_schedule=no_schedule)
        out: list[PlanItemOut] = []
        for entry, publish_at in plan:
            if no_schedule or not publish_at:
                display = "now (no schedule)"
            else:
                publish_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
                display = publish_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
            out.append(
                PlanItemOut(
                    job_id=entry.id,
                    title=entry.title or "(no title)",
                    publish_at=publish_at,
                    publish_display=display,
                )
            )
        return out

    def _execute_run(tracked, body: RunRequest):
        set_running(tracked)
        try:
            config = get_app_config()
            ch = resolve_channel_ref(tracked.channel_id)
            pending_count = len(UploadRegistry(ch.registry_path).pending(channel_id=ch.id))
            limit = body.count
            if limit is not None and limit > pending_count:
                limit = pending_count

            result = run_channel(
                tracked.channel_id,
                config,
                limit=limit,
                no_schedule=body.no_schedule,
                privacy=body.privacy,
                upload_retries=body.upload_retries,
                retry_delay=body.retry_delay,
                tags=body.tags,
                start=body.start,
                interval_hours=body.interval_hours,
            )
            set_complete(tracked, result)
        except Exception as e:
            set_failed(tracked, str(e))

    @app.post("/v1/channels/{channel_ref}/runs", response_model=RunResponse, status_code=202)
    def start_run(channel_ref: str, body: RunRequest, background_tasks: BackgroundTasks):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        if not _token_status(ch).valid:
            raise HTTPException(400, f"Channel {ch.id} is not authenticated. Start OAuth first.")
        pending = UploadRegistry(ch.registry_path).pending(channel_id=ch.id)
        if not pending:
            raise HTTPException(400, "No pending jobs in queue")
        tracked = create_run(ch.id)
        background_tasks.add_task(_execute_run, tracked, body)
        count_msg = str(body.count) if body.count is not None else "all"
        return RunResponse(
            run_id=tracked.run_id,
            channel_id=ch.id,
            status="queued",
            message=f"Uploading {count_msg} job(s). Poll GET /v1/runs/{tracked.run_id}",
        )

    @app.get("/v1/runs/{run_id}", response_model=RunStatusOut)
    def run_status(run_id: str):
        tracked = get_run(run_id)
        if tracked is None:
            raise HTTPException(404, "Run not found")
        with tracked.lock:
            if tracked.result:
                r = tracked.result
                return RunStatusOut(
                    run_id=run_id,
                    channel_id=tracked.channel_id,
                    status=tracked.status,
                    total=r.total,
                    uploaded=r.uploaded,
                    failed=r.failed,
                    urls=r.urls,
                    errors=[{"job_id": j, "error": e} for j, e in r.errors],
                )
            if tracked.error:
                return RunStatusOut(
                    run_id=run_id,
                    channel_id=tracked.channel_id,
                    status=tracked.status,
                    errors=[{"error": tracked.error}],
                )
            return RunStatusOut(run_id=run_id, channel_id=tracked.channel_id, status=tracked.status)

    @app.post("/v1/runs/all", status_code=202)
    def start_run_all(body: RunRequest, background_tasks: BackgroundTasks):
        def _all():
            config = get_app_config()
            limit = body.count
            results = run_all_channels(
                config,
                limit=limit,
                no_schedule=body.no_schedule,
                privacy=body.privacy,
                upload_retries=body.upload_retries,
                retry_delay=body.retry_delay,
                tags=body.tags,
                start=body.start,
                interval_hours=body.interval_hours,
            )
            return results

        background_tasks.add_task(_all)
        return {"status": "queued", "message": "Run-all started in background (poll channels for results)"}

    @app.get("/v1/channels/{channel_ref}/youtube/videos", response_model=list[YouTubeVideoOut])
    def youtube_videos(
        channel_ref: str,
        scheduled_only: bool = Query(default=False),
    ):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        oauth = get_oauth_settings()
        if not _token_status(ch).valid:
            raise HTTPException(400, "Channel not authenticated")
        videos = list_channel_videos(
            ch.token_path,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            scheduled_only=scheduled_only,
            oauth_port=oauth.oauth_port,
        )
        return [
            YouTubeVideoOut(
                video_id=v.video_id,
                title=v.title,
                privacy_status=v.privacy_status,
                publish_at=v.publish_at,
                url=v.url,
                is_scheduled=v.is_scheduled,
            )
            for v in videos
        ]

    def _oauth_start(mode: str, channel_id: str = "") -> OAuthStartResponse:
        if not oauth_configured():
            raise HTTPException(503, "Google OAuth not configured in .env")
        oauth = get_oauth_settings()
        state_obj = new_oauth_state(mode=mode, channel_id=channel_id)  # type: ignore[arg-type]
        redirect = api_redirect_uri(oauth, api_base=api_public_base())
        url, code_verifier = build_authorization_url(
            oauth, redirect_uri=redirect, state=state_obj.nonce, force_reauth=True
        )
        save_session(
            OAuthSession(
                nonce=state_obj.nonce,
                mode=state_obj.mode,
                channel_id=channel_id,
                code_verifier=code_verifier,
            )
        )
        return OAuthStartResponse(auth_url=url, state=state_obj.nonce, redirect_uri=redirect)

    @app.post("/v1/oauth/start", response_model=OAuthStartResponse)
    def oauth_start_add():
        return _oauth_start("add")

    @app.post("/v1/channels/{channel_ref}/oauth/start", response_model=OAuthStartResponse)
    def oauth_start_reauth(channel_ref: str):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return _oauth_start("reauth", ch.id)

    @app.get("/v1/oauth/callback", include_in_schema=False)
    def oauth_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse(url=f"/?oauth_error={error}")
        if not code or not state:
            raise HTTPException(400, "Missing code or state")
        session = pop_session(state)
        if session is None:
            raise HTTPException(400, "OAuth session expired; start again")
        if not session.code_verifier:
            return RedirectResponse(url="/?oauth_error=OAuth+session+missing+PKCE+verifier.+Try+Connect+again.")
        oauth = get_oauth_settings()
        redirect = api_redirect_uri(oauth, api_base=api_public_base())
        try:
            creds = exchange_code_for_credentials(
                oauth,
                redirect_uri=redirect,
                code=code,
                state=state,
                expected_state=state,
                code_verifier=session.code_verifier,
            )
            channel = register_channel_from_credentials(
                oauth,
                credentials_to_json(creds),
                config_path=config_path(),
                channel_id_override=session.channel_id if session.mode == "reauth" else None,
            )
        except Exception as e:
            return RedirectResponse(url=f"/?oauth_error={e}")
        return RedirectResponse(url=f"/?oauth_success={channel.id}")

    @app.post("/v1/storage/init")
    def storage_init():
        try:
            created = ensure_bucket_structure(config_path())
        except Exception as e:
            raise HTTPException(500, str(e)) from e
        return {"created": created, "count": len(created)}

    return app


app = create_app()
