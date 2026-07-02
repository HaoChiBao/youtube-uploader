"""FastAPI application — local dev server for youtube-uploader."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.auth import (
    auth_enabled,
    auth_status,
    clear_session_cookie,
    create_session,
    request_is_authenticated,
    set_session_cookie,
    verify_login_secret,
)
from api.endpoint_docs import API_ENDPOINTS, API_TAGS, AUTH_NOTE
from api.middleware import AuthMiddleware
from api.openapi_enrich import install_openapi_enrichment
from api.cache import build_dashboard, clear_all_caches, get_token_status, job_view_to_out
from api.capabilities import CLI_COMMANDS, YOUTUBE_FEATURES, ASSEMBLY_INTEGRATION_NOTES
from uploader.object_storage import assembly_r2_status
from api.job_ingest import (
    parse_stage_form_fields,
    register_job_from_request,
    stage_job_from_upload,
)
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
from api.static_dir import static_dir
from api.schemas import (
    AuthenticatedYouTubeChannelOut,
    AuthenticatedYouTubeChannelsResponse,
    CapabilitiesOut,
    CategoryCreateRequest,
    CategoryListResponse,
    ChannelListResponse,
    ChannelOut,
    ChannelRemovalOut,
    ChannelUpdateRequest,
    PublishConfigOut,
    DashboardResponse,
    HealthResponse,
    JobDetailResponse,
    JobMediaOut,
    JobOut,
    JobRegisterRequest,
    LoginRequest,
    OAuthStartRequest,
    OAuthStartResponse,
    PlanItemOut,
    ReconcileUploadsResponse,
    ReconcileActionOut,
    RunRequest,
    RunResponse,
    RunStatusOut,
    ActiveUploadOut,
    ActiveUploadsResponse,
    CancelUploadResponse,
    ParallelRunResponse,
    StagedJobOut,
    TokenStatus,
    YouTubeVideoOut,
    ScheduledVideosResponse,
)
from uploader import __version__
from uploader.channel_store import patch_channel_config, remove_channel_from_config
from uploader.category_store import (
    CategoryError,
    CategoryNotFoundError,
    add_category,
    list_saved_categories,
    remove_category,
)
from uploader.job_metadata import load_job_metadata
from uploader.job_claim import cancel_upload_job
from uploader.job_store import prepare_job_for_upload, remove_job
from uploader.oauth import oauth_is_configured
from uploader.oauth_web import (
    api_redirect_uri,
    build_authorization_url,
    credentials_to_json,
    exchange_code_for_credentials,
    new_oauth_state,
    register_channel_from_credentials,
)
from uploader.job_views import (
    entry_to_job_view,
    job_media_availability,
    list_jobs as list_jobs_unified,
    load_channel_jobs,
    resolve_job_asset_uri,
)
from uploader.object_storage import exists, guess_media_type, is_s3_uri, presigned_get_url
from uploader.registry import UploadRegistry
from uploader.cache_signals import bump
from uploader.scheduler import (
    build_channel_upload_plan,
    run_all_channels,
    run_channel,
)
from uploader.job_views import list_active_uploads
from uploader.upload_reconcile import reconcile_uploads
from uploader.state_store import ensure_bucket_structure
from uploader.worker_dispatch import dispatch_parallel_uploads


def create_app() -> FastAPI:
    app = FastAPI(
        title="YouTube Uploader API",
        description=(
            "HTTP API for multi-channel YouTube upload queue management. "
            "Stage AI-generated videos into `queue/` on Cloudflare R2, then upload to YouTube on a schedule.\n\n"
            f"{AUTH_NOTE}"
        ),
        version=__version__,
        openapi_tags=API_TAGS,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    app.add_middleware(AuthMiddleware)

    assets = static_dir()
    if assets.is_dir():
        app.mount("/static", StaticFiles(directory=str(assets)), name="static")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return RedirectResponse(url="/", status_code=302)

    @app.post("/login", tags=["auth"], include_in_schema=True)
    def login(body: LoginRequest, request: Request, response: Response):
        """Sign in to the dashboard with password or API token (sets session cookie)."""
        if not auth_enabled():
            return {"status": "ok", "auth": False}
        if not verify_login_secret(body.password):
            raise HTTPException(401, "Invalid password or API token")
        set_session_cookie(response, create_session(), request=request)
        return {"status": "ok", "auth": True}

    @app.get("/v1/auth/session", tags=["auth"], include_in_schema=True)
    def auth_session(request: Request):
        """Whether the current browser/API client is authenticated (public)."""
        return {
            "auth_enabled": auth_enabled(),
            "authenticated": request_is_authenticated(request),
        }

    @app.post("/logout", tags=["auth"], include_in_schema=True)
    def logout(request: Request, response: Response):
        """Clear dashboard session cookie."""
        clear_session_cookie(response, request=request)
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        index_path = static_dir() / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        root = static_dir()
        return HTMLResponse(
            "<h1>Dashboard unavailable</h1>"
            f"<p>index.html not found at <code>{root}</code>.</p>"
            "<p>Set <code>UPLOADER_STATIC_DIR</code> to the folder containing index.html "
            "(Docker default: <code>/app/api/static</code>).</p>"
            "<p>API docs: <a href='/docs'>/docs</a></p>"
        )

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    @app.get("/v1/health", response_model=HealthResponse, tags=["health"])
    def health():
        return HealthResponse(version=__version__)

    @app.get("/v1/auth/status", tags=["health"])
    def auth_status_route():
        """Whether API/dashboard auth is enabled (no secret values returned)."""
        return auth_status()

    @app.get("/v1/capabilities", response_model=CapabilitiesOut, tags=["health"])
    def capabilities():
        endpoints = [
            {
                "method": ep["method"],
                "path": ep["path"],
                "summary": ep["summary"],
                "description": ep.get("description", ep.get("purpose", "")),
                "purpose": ep.get("purpose", ""),
                "details": ep.get("details", ""),
                "usage": ep.get("usage", ""),
                "example_response": ep.get("example_response"),
                "example_request": ep.get("example_request"),
                "auth": ep.get("auth", False),
            }
            for ep in API_ENDPOINTS
        ]
        return CapabilitiesOut(
            cli_commands=CLI_COMMANDS,
            youtube_features=YOUTUBE_FEATURES,
            api_endpoints=endpoints,
            auth_note=AUTH_NOTE,
            assembly_integration={
                "register_endpoint": "POST /v1/channels/{channel_ref}/jobs/register",
                "assembly_r2": assembly_r2_status(),
                "notes": ASSEMBLY_INTEGRATION_NOTES,
            },
        )

    def _token_status(channel) -> TokenStatus:
        oauth = get_oauth_settings()
        return get_token_status(
            channel.id,
            channel.token_path,
            oauth,
        )

    def _publish_out(ch) -> PublishConfigOut:
        pub = ch.publish
        return PublishConfigOut(
            timezone=pub.timezone,
            hour=pub.hour,
            interval_hours=pub.interval_hours,
            uploads_per_day=pub.uploads_per_day,
        )

    def _channel_out(ch) -> ChannelOut:
        bundle = load_channel_jobs(ch, base=get_storage_base())
        return ChannelOut(
            id=ch.id,
            name=ch.name,
            youtube_channel_id=ch.youtube_channel_id,
            custom_url=ch.custom_url,
            category=ch.category,
            token_path=ch.token_path,
            registry_path=ch.registry_path,
            auth=_token_status(ch),
            publish=_publish_out(ch),
            pending_count=bundle.pending_count,
            uploaded_count=bundle.uploaded_count,
            failed_count=bundle.failed_count,
        )

    @app.get("/v1/dashboard", response_model=DashboardResponse, tags=["dashboard"])
    def dashboard(refresh: bool = Query(default=False, description="Bypass cache and reload from storage")):
        """Cached snapshot of all channels plus queue and uploaded jobs."""
        return build_dashboard(config_path(), force=refresh)

    @app.get("/v1/channels", response_model=ChannelListResponse, tags=["channels"])
    def list_channels():
        """List configured channels with OAuth status and queue counts."""
        config = get_app_config()
        return ChannelListResponse(
            config_uri=get_config_uri(),
            storage=get_storage_backend(),
            categories=config.categories,
            channels=[_channel_out(ch) for ch in config.channels],
        )

    @app.get("/v1/categories", response_model=CategoryListResponse, tags=["categories"])
    def list_categories():
        """List saved assembly/content categories (deduplicated)."""
        categories = list_saved_categories(config_path())
        return CategoryListResponse(categories=categories, count=len(categories))

    @app.post("/v1/categories", response_model=CategoryListResponse, tags=["categories"])
    def create_category(body: CategoryCreateRequest):
        """Create a new assembly/content category."""
        try:
            categories = add_category(body.name, config_path=config_path())
        except CategoryError as e:
            raise HTTPException(400, str(e)) from e
        clear_all_caches()
        return CategoryListResponse(categories=categories, count=len(categories))

    @app.delete("/v1/categories/{category_name}", response_model=CategoryListResponse, tags=["categories"])
    def delete_category(category_name: str):
        """Delete a category and clear it from any channels using it."""
        try:
            categories = remove_category(category_name, config_path=config_path())
        except CategoryError as e:
            raise HTTPException(400, str(e)) from e
        except CategoryNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        clear_all_caches()
        return CategoryListResponse(categories=categories, count=len(categories))

    @app.get(
        "/v1/youtube/channels",
        response_model=AuthenticatedYouTubeChannelsResponse,
        tags=["youtube"],
    )
    def list_authenticated_youtube_channels():
        """List YouTube channels with valid OAuth (ready for upload and YouTube API calls)."""
        config = get_app_config()
        authenticated: list[AuthenticatedYouTubeChannelOut] = []
        for ch in config.channels:
            auth = _token_status(ch)
            if not auth.valid:
                continue
            authenticated.append(
                AuthenticatedYouTubeChannelOut(
                    id=ch.id,
                    name=ch.name,
                    youtube_channel_id=ch.youtube_channel_id,
                    custom_url=ch.custom_url,
                    category=ch.category,
                )
            )
        return AuthenticatedYouTubeChannelsResponse(channels=authenticated, count=len(authenticated))

    @app.get("/v1/channels/{channel_ref}", response_model=ChannelOut, tags=["channels"])
    def get_channel(channel_ref: str):
        """Get one channel by id, name, @handle, or YouTube channel id."""
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return _channel_out(ch)

    @app.patch("/v1/channels/{channel_ref}", response_model=ChannelOut, tags=["channels"])
    def update_channel(channel_ref: str, body: ChannelUpdateRequest):
        """Update channel settings (category, publish scheduling)."""
        if body.category is None and body.publish is None:
            raise HTTPException(400, "No fields to update")
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

        publish_fields: set[str] = set()
        if body.publish is not None:
            publish_fields = body.publish.model_fields_set

        try:
            updated = patch_channel_config(
                ch.id,
                config_path=config_path(),
                category=body.category,
                update_category=body.category is not None,
                publish_timezone=body.publish.timezone if body.publish else None,
                publish_hour=body.publish.hour if body.publish else None,
                publish_interval_hours=body.publish.interval_hours if body.publish else None,
                publish_uploads_per_day=body.publish.uploads_per_day if body.publish else None,
                update_publish_timezone="timezone" in publish_fields,
                update_publish_hour="hour" in publish_fields,
                update_publish_interval_hours="interval_hours" in publish_fields,
                update_publish_uploads_per_day="uploads_per_day" in publish_fields,
            )
        except CategoryNotFoundError as e:
            raise HTTPException(400, str(e)) from e
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        clear_all_caches()
        return _channel_out(updated)

    @app.delete("/v1/channels/{channel_ref}", response_model=ChannelRemovalOut, tags=["channels"])
    def delete_channel(channel_ref: str):
        """Remove a channel from the uploader (disconnect OAuth; keep queue/upload data in storage)."""
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        try:
            result = remove_channel_from_config(ch.id, config_path=config_path())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        clear_all_caches()
        bump("queue")
        msg = f"Removed channel {result.channel_id} from config"
        if result.pending_jobs:
            msg += f" ({result.pending_jobs} pending job(s) remain in storage)"
        return ChannelRemovalOut(
            channel_id=result.channel_id,
            name=result.name,
            token_deleted=result.token_deleted,
            pending_jobs_remaining=result.pending_jobs,
            message=msg,
        )

    def _entry_to_job(entry) -> JobOut:
        return job_view_to_out(entry_to_job_view(entry, base=get_storage_base()))

    def _media_urls(channel_ref: str, job_id: str) -> JobMediaOut:
        ch = resolve_channel_ref(channel_ref)
        reg = UploadRegistry(ch.registry_path)
        entry = reg.get(job_id)
        if entry is None:
            raise HTTPException(404, f"Job not found: {job_id}")
        base = get_storage_base()
        avail = job_media_availability(entry, base=base)
        root = f"/v1/channels/{ch.id}/jobs/{job_id}/media"
        return JobMediaOut(
            thumbnail=f"{root}/thumbnail" if avail["thumbnail"] else "",
            video=f"{root}/video" if avail["video"] else "",
            thumbnail_available=avail["thumbnail"],
            video_available=avail["video"],
        )

    @app.get("/v1/jobs", response_model=list[JobOut], tags=["jobs"])
    def list_jobs(
        channel: str | None = Query(default=None),
        status: str | None = Query(default="pending"),
        location: str | None = Query(
            default=None,
            description="queue | uploaded | all — filter by R2 folder (queue/ vs uploaded/)",
        ),
    ):
        """List jobs across channels; filter by channel, status, and storage folder."""
        config = get_app_config()
        channels = config.channels
        if channel:
            try:
                channels = [resolve_channel_ref(channel)]
            except KeyError as e:
                raise HTTPException(404, str(e)) from e

        loc = location or ("uploaded" if status == "uploaded" else "queue" if status == "pending" else "all")
        if loc not in ("queue", "uploaded", "all"):
            raise HTTPException(400, "location must be queue, uploaded, or all")

        status_filter = status if status else None
        views = list_jobs_unified(
            channels,
            base=get_storage_base(),
            location=loc,  # type: ignore[arg-type]
            status=status_filter,
        )
        return [job_view_to_out(v) for v in views]

    async def _stage_job_multipart(
        channel_ref: str,
        *,
        video: UploadFile,
        title: str,
        description: str = "",
        thumbnail: UploadFile | None = None,
        job_id: str | None = None,
        privacy: str | None = None,
        category_id: str | None = None,
        tags: str | None = None,
        language: str | None = None,
        metadata_json: str | None = None,
        is_short: str | None = None,
        made_for_kids: str | None = None,
    ) -> StagedJobOut:
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

        metadata, parsed_tags, parsed_short, parsed_mfk = parse_stage_form_fields(
            metadata_json=metadata_json,
            tags=tags,
            is_short=is_short,
            made_for_kids=made_for_kids,
        )
        config = get_app_config()
        out = await stage_job_from_upload(
            ch,
            video=video,
            title=title,
            description=description,
            thumbnail=thumbnail,
            job_id=job_id,
            base=get_storage_base(),
            config_defaults=config.job_defaults,
            privacy=privacy,
            is_short=parsed_short,
            category_id=category_id,
            tags=parsed_tags,
            made_for_kids=parsed_mfk,
            language=language,
            metadata=metadata,
        )
        bump("queue")
        return out

    @app.post(
        "/v1/channels/{channel_ref}/jobs",
        response_model=StagedJobOut,
        status_code=201,
        tags=["jobs"],
    )
    async def create_job(
        channel_ref: str,
        video: UploadFile = File(..., description="Video file (.mp4)"),
        title: str = Form(...),
        description: str = Form(default=""),
        thumbnail: UploadFile | None = File(default=None),
        job_id: str | None = Form(default=None),
        privacy: str | None = Form(default=None),
        is_short: str | None = Form(default=None, description="true|false"),
        category_id: str | None = Form(default=None),
        tags: str | None = Form(default=None, description="Comma-separated tags"),
        made_for_kids: str | None = Form(default=None, description="true|false"),
        language: str | None = Form(default=None),
        metadata: str | None = Form(default=None, description="JSON metadata object"),
    ):
        """Stage a video into queue/ via multipart upload (primary AI pipeline ingest endpoint)."""
        return await _stage_job_multipart(
            channel_ref,
            video=video,
            title=title,
            description=description,
            thumbnail=thumbnail,
            job_id=job_id,
            privacy=privacy,
            category_id=category_id,
            tags=tags,
            language=language,
            metadata_json=metadata,
            is_short=is_short,
            made_for_kids=made_for_kids,
        )

    @app.post(
        "/v1/jobs",
        response_model=StagedJobOut,
        status_code=201,
        tags=["jobs"],
    )
    async def create_job_alias(
        channel_id: str = Form(..., description="Channel id or handle"),
        video: UploadFile = File(...),
        title: str = Form(...),
        description: str = Form(default=""),
        thumbnail: UploadFile | None = File(default=None),
        job_id: str | None = Form(default=None),
        privacy: str | None = Form(default=None),
        is_short: str | None = Form(default=None),
        category_id: str | None = Form(default=None),
        tags: str | None = Form(default=None),
        made_for_kids: str | None = Form(default=None),
        language: str | None = Form(default=None),
        metadata: str | None = Form(default=None),
    ):
        """Alias for POST /v1/channels/{id}/jobs with channel_id in form body."""
        return await _stage_job_multipart(
            channel_id,
            video=video,
            title=title,
            description=description,
            thumbnail=thumbnail,
            job_id=job_id,
            privacy=privacy,
            category_id=category_id,
            tags=tags,
            language=language,
            metadata_json=metadata,
            is_short=is_short,
            made_for_kids=made_for_kids,
        )

    @app.post(
        "/v1/channels/{channel_ref}/jobs/register",
        response_model=StagedJobOut,
        tags=["jobs"],
    )
    def register_job(channel_ref: str, body: JobRegisterRequest):
        """Register a pending job when video files already exist in R2 or local storage."""
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        config = get_app_config()
        out, created = register_job_from_request(
            ch,
            body,
            base=get_storage_base(),
            config_defaults=config.job_defaults,
        )
        return JSONResponse(
            status_code=201 if created else 200,
            content=out.model_dump(),
        )

    @app.get("/v1/channels/{channel_ref}/jobs", response_model=list[JobOut], tags=["jobs"])
    def list_channel_jobs(
        channel_ref: str,
        status: str | None = Query(default=None, description="pending | uploading | uploaded | failed"),
        location: str | None = Query(
            default="all",
            description="queue | uploaded | all — filter by R2 folder",
        ),
    ):
        """List jobs for one channel with optional status and storage filters."""
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        loc = location or "all"
        if loc not in ("queue", "uploaded", "all"):
            raise HTTPException(400, "location must be queue, uploaded, or all")
        base = get_storage_base()
        views = list_jobs_unified([ch], base=base, location=loc, status=status)
        return [job_view_to_out(v) for v in views]

    @app.get("/v1/channels/{channel_ref}/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(
        channel_ref: str,
        job_id: str,
        media: bool = Query(default=False, description="Include lazy-load media preview URLs"),
    ):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        reg = UploadRegistry(ch.registry_path)
        entry = reg.get(job_id)
        if entry is None:
            raise HTTPException(404, f"Job not found: {job_id}")
        base = get_storage_base()
        job_out = job_view_to_out(entry_to_job_view(entry, base=base))
        meta = load_job_metadata(entry, base=base, channel=ch, config_defaults=get_app_config().job_defaults)
        media_out = _media_urls(ch.id, job_id) if media else None
        return JobDetailResponse(
            job=job_out,
            metadata=meta.to_dict() if meta else None,
            media=media_out,
        )

    @app.post(
        "/v1/channels/{channel_ref}/jobs/{job_id}/upload",
        response_model=RunResponse,
        status_code=202,
        tags=["jobs"],
    )
    def upload_one_job(channel_ref: str, job_id: str, body: RunRequest | None = None):
        """Upload (or re-upload) a single job. Re-queues uploaded/failed jobs automatically."""
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        if not _token_status(ch).valid:
            raise HTTPException(400, f"Channel {ch.id} is not authenticated. Start OAuth first.")
        base = get_storage_base()
        reg = UploadRegistry(ch.registry_path)
        try:
            prepare_job_for_upload(ch, job_id, base=base, registry=reg)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        opts = body or RunRequest(parallel=True, count=1)
        if not opts.parallel:
            raise HTTPException(400, "Single-job upload requires parallel=true")
        config = get_app_config()
        oauth = get_oauth_settings()
        result = dispatch_parallel_uploads(
            ch.id,
            config,
            base=base,
            count=1,
            job_ids=[job_id],
            no_schedule=opts.no_schedule,
            privacy=opts.privacy,
            upload_retries=opts.upload_retries,
            retry_delay=opts.retry_delay,
            tags=opts.tags,
            start=opts.start,
            interval_hours=opts.interval_hours,
            uploads_per_day=opts.uploads_per_day,
            oauth_client_secret=oauth.client_secret_path,
            oauth_client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
        )
        bump("queue")
        if not result.dispatched:
            reason = result.skipped[0]["reason"] if result.skipped else "could not dispatch worker"
            raise HTTPException(409, reason)
        return RunResponse(
            run_id=f"parallel_{secrets.token_hex(6)}",
            channel_id=ch.id,
            status="dispatched",
            message=f"Upload started for {job_id}. Poll GET /v1/uploads/active",
        )

    @app.get("/v1/channels/{channel_ref}/jobs/{job_id}/media/{asset}")
    def job_media(channel_ref: str, job_id: str, asset: str):
        if asset not in ("thumbnail", "video"):
            raise HTTPException(400, "asset must be thumbnail or video")
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        reg = UploadRegistry(ch.registry_path)
        entry = reg.get(job_id)
        if entry is None:
            raise HTTPException(404, f"Job not found: {job_id}")
        from uploader import bucket_layout

        filename = bucket_layout.JOB_THUMBNAIL if asset == "thumbnail" else bucket_layout.JOB_VIDEO
        base = get_storage_base()
        uri = resolve_job_asset_uri(entry, filename, base=base)
        if not uri or not exists(uri):
            raise HTTPException(404, f"{asset} not available for this job")
        if is_s3_uri(uri):
            try:
                return RedirectResponse(presigned_get_url(uri), status_code=307)
            except Exception as e:
                raise HTTPException(502, str(e)) from e
        path = Path(uri)
        if not path.is_file():
            raise HTTPException(404, f"{asset} file not found")
        return FileResponse(path, media_type=guess_media_type(path.name))

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
        oauth = get_oauth_settings()
        upload_plan = build_channel_upload_plan(
            ch,
            get_app_config(),
            pending,
            start=start,
            interval_hours=interval_hours,
            no_schedule=no_schedule,
            oauth_client_secret=oauth.client_secret_path,
            oauth_client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
        )
        out: list[PlanItemOut] = []
        for entry, publish_at in upload_plan.items:
            if upload_plan.upload_immediately or not publish_at:
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

    @app.get("/v1/uploads/active", response_model=ActiveUploadsResponse)
    def active_uploads():
        config = get_app_config()
        base = get_storage_base()
        views = list_active_uploads(config.channels, base=base)
        uploads = [
            ActiveUploadOut(
                channel_id=v.channel_id,
                job_id=v.id,
                title=v.title or v.id,
                status=v.status,
                upload_phase=v.upload_phase,
                upload_progress=v.upload_progress,
                upload_message=v.upload_message,
                upload_worker_id=v.upload_worker_id,
                publish_at=v.publish_at,
                completed=(v.status == "uploaded" or v.upload_phase == "done"),
                youtube_url=v.youtube_url or "",
            )
            for v in views
        ]
        return ActiveUploadsResponse(uploads=uploads, count=len(uploads))

    @app.post("/v1/uploads/reconcile", response_model=ReconcileUploadsResponse, tags=["uploads"])
    def reconcile_stuck_uploads(
        dry_run: bool = Query(default=False, description="Report actions without applying"),
        channel: str | None = Query(default=None, description="Limit to one channel id"),
    ):
        """Repair stuck uploading jobs and archive uploaded jobs left in queue/."""
        config = get_app_config()
        oauth = get_oauth_settings()
        try:
            result = reconcile_uploads(
                config,
                base=get_storage_base(),
                oauth=oauth,
                channel_id=channel,
                dry_run=dry_run,
            )
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        if not dry_run and result.actions:
            clear_all_caches()
        return ReconcileUploadsResponse(
            scanned=result.scanned,
            dry_run=dry_run,
            actions=[
                ReconcileActionOut(
                    channel_id=a.channel_id,
                    job_id=a.job_id,
                    action=a.action,
                    detail=a.detail,
                )
                for a in result.actions
            ],
        )

    @app.post(
        "/v1/channels/{channel_ref}/jobs/{job_id}/cancel-upload",
        response_model=CancelUploadResponse,
    )
    def cancel_upload(channel_ref: str, job_id: str):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        registry = UploadRegistry(ch.registry_path)
        try:
            cancel_upload_job(registry, ch.id, job_id, base=get_storage_base())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        bump("queue")
        return CancelUploadResponse(channel_id=ch.id, job_id=job_id)

    def _execute_run(tracked, body: RunRequest):
        set_running(tracked)
        try:
            config = get_app_config()
            ch = resolve_channel_ref(tracked.channel_id)
            oauth = get_oauth_settings()
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
                uploads_per_day=body.uploads_per_day,
                oauth_client_secret=oauth.client_secret_path,
                oauth_client_config=oauth.client_config,
                oauth_port=oauth.oauth_port,
            )
            set_complete(tracked, result)
            bump("queue")
        except Exception as e:
            set_failed(tracked, str(e))
            bump("queue")

    @app.post("/v1/channels/{channel_ref}/runs", response_model=RunResponse, status_code=202)
    def start_run(channel_ref: str, body: RunRequest, background_tasks: BackgroundTasks):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        if not _token_status(ch).valid:
            raise HTTPException(400, f"Channel {ch.id} is not authenticated. Start OAuth first.")
        reg = UploadRegistry(ch.registry_path)
        if body.job_ids:
            for jid in body.job_ids:
                try:
                    prepare_job_for_upload(ch, jid, base=get_storage_base(), registry=reg)
                except ValueError as e:
                    raise HTTPException(400, str(e)) from e
        pending = reg.pending(channel_id=ch.id)
        if body.job_ids:
            wanted = {j.strip() for j in body.job_ids if j and j.strip()}
            pending = [e for e in pending if e.id in wanted]
        if not pending:
            raise HTTPException(400, "No pending jobs in queue")

        if body.parallel:
            base = get_storage_base()
            config = get_app_config()
            oauth = get_oauth_settings()
            result = dispatch_parallel_uploads(
                ch.id,
                config,
                base=base,
                count=body.count,
                job_ids=body.job_ids,
                no_schedule=body.no_schedule,
                privacy=body.privacy,
                upload_retries=body.upload_retries,
                retry_delay=body.retry_delay,
                tags=body.tags,
                start=body.start,
                interval_hours=body.interval_hours,
                uploads_per_day=body.uploads_per_day,
                oauth_client_secret=oauth.client_secret_path,
                oauth_client_config=oauth.client_config,
                oauth_port=oauth.oauth_port,
            )
            bump("queue")
            n = len(result.dispatched)
            return RunResponse(
                run_id=f"parallel_{secrets.token_hex(6)}",
                channel_id=ch.id,
                status="dispatched",
                message=f"Dispatched {n} parallel upload worker(s). Poll GET /v1/uploads/active",
            )

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
            oauth = get_oauth_settings()
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
                uploads_per_day=body.uploads_per_day,
                oauth_client_secret=oauth.client_secret_path,
                oauth_client_config=oauth.client_config,
                oauth_port=oauth.oauth_port,
            )
            return results

        background_tasks.add_task(_all)
        return {"status": "queued", "message": "Run-all started in background (poll channels for results)"}

    @app.get(
        "/v1/channels/{channel_ref}/youtube/scheduled",
        response_model=ScheduledVideosResponse,
    )
    def youtube_scheduled(channel_ref: str):
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
            scheduled_only=True,
            oauth_port=oauth.oauth_port,
        )
        tail = max((v.publish_at for v in videos if v.publish_at), default=None)
        return ScheduledVideosResponse(
            channel_id=ch.id,
            count=len(videos),
            tail_publish_at=tail,
            videos=[
                YouTubeVideoOut(
                    video_id=v.video_id,
                    title=v.title,
                    privacy_status=v.privacy_status,
                    publish_at=v.publish_at,
                    url=v.url,
                    is_scheduled=v.is_scheduled,
                )
                for v in videos
            ],
        )

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

    def _oauth_start(mode: str, channel_id: str = "", category: str = "") -> OAuthStartResponse:
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
                category=category.strip(),
                code_verifier=code_verifier,
            )
        )
        return OAuthStartResponse(auth_url=url, state=state_obj.nonce, redirect_uri=redirect)

    @app.post("/v1/oauth/start", response_model=OAuthStartResponse)
    def oauth_start_add(body: OAuthStartRequest | None = None):
        category = body.category if body else ""
        return _oauth_start("add", category=category)

    @app.post("/v1/channels/{channel_ref}/oauth/start", response_model=OAuthStartResponse)
    def oauth_start_reauth(channel_ref: str, body: OAuthStartRequest | None = None):
        try:
            ch = resolve_channel_ref(channel_ref)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        category = body.category if body else ""
        return _oauth_start("reauth", ch.id, category=category)

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
            result = register_channel_from_credentials(
                oauth,
                credentials_to_json(creds),
                config_path=config_path(),
                category=session.category,
                channel_id_override=session.channel_id if session.mode == "reauth" else None,
            )
        except Exception as e:
            return RedirectResponse(url=f"/?oauth_error={e}")
        clear_all_caches()
        channel = result.channel
        if session.mode == "reauth" and result.action == "added":
            return RedirectResponse(
                url=f"/?oauth_success={channel.id}&oauth_action=added_different_account"
            )
        if result.action == "updated":
            return RedirectResponse(url=f"/?oauth_success={channel.id}&oauth_action=updated")
        return RedirectResponse(url=f"/?oauth_success={channel.id}&oauth_action=added")

    @app.post("/v1/storage/init")
    def storage_init():
        try:
            created = ensure_bucket_structure(config_path())
        except Exception as e:
            raise HTTPException(500, str(e)) from e
        return {"created": created, "count": len(created)}

    install_openapi_enrichment(app)
    return app


app = create_app()
