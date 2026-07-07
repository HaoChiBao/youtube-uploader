"""Canonical HTTP API endpoint catalog (README, /v1/capabilities, OpenAPI /docs)."""

from __future__ import annotations

from typing import Any

API_TAGS: list[dict] = [
    {"name": "health", "description": "Liveness, version, auth status, and API inventory."},
    {"name": "auth", "description": "Dashboard login and session (browser clients)."},
    {"name": "dashboard", "description": "Cached snapshot of channels and jobs for the web UI."},
    {"name": "channels", "description": "Configured YouTube channels, OAuth status, and queue counts."},
    {"name": "jobs", "description": "Upload queue: stage videos, list pending/history, preview media, remove jobs."},
    {"name": "runs", "description": "Background YouTube upload runs and progress polling."},
    {"name": "uploads", "description": "Live upload progress and stuck-job reconcile."},
    {"name": "oauth", "description": "Browser OAuth to add or re-authenticate YouTube channels."},
    {"name": "storage", "description": "Initialize Cloudflare R2 / local bucket layout."},
    {"name": "youtube", "description": "Read YouTube channel data and list videos on YouTube."},
]

_BASE = "https://your-uploader-host"
_KEY = 'curl -H "X-API-Key: $UPLOADER_API_KEY"'


def _ep(
    method: str,
    path: str,
    tag: str,
    summary: str,
    purpose: str,
    usage: str,
    example_response: Any,
    *,
    auth: bool = True,
    status_code: int | None = None,
    details: str = "",
    example_request: Any = None,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "tag": tag,
        "summary": summary,
        "description": purpose,
        "purpose": purpose,
        "details": details,
        "usage": usage,
        "example_response": example_response,
        "example_request": example_request,
        "auth": auth,
        "status_code": status_code,
    }


API_ENDPOINTS: list[dict[str, Any]] = [
    _ep(
        "GET",
        "/v1/health",
        "health",
        "Health check",
        "Confirm the API process is running (load balancers, uptime monitors).",
        f"{_KEY} {_BASE}/v1/health",
        {"status": "ok", "version": "0.1.0"},
        auth=False,
    ),
    _ep(
        "GET",
        "/v1/auth/session",
        "auth",
        "Browser session status",
        "Check whether the current browser/API client is authenticated (used by the dashboard login gate).",
        f"curl -b cookies.txt {_BASE}/v1/auth/session",
        {"auth_enabled": True, "authenticated": False},
        auth=False,
        details="No API key needed. Returns whether dashboard password auth is enabled and if this request has a valid session.",
    ),
    _ep(
        "POST",
        "/login",
        "auth",
        "Dashboard sign-in",
        "Exchange dashboard password (or API token) for a session cookie used by the web UI.",
        (
            f'curl -X POST {_BASE}/login \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"password": "your-dashboard-password"}\' \\\n'
            "  -c cookies.txt"
        ),
        {"status": "ok", "auth": True},
        auth=False,
    ),
    _ep(
        "POST",
        "/logout",
        "auth",
        "Sign out",
        "Clear the dashboard session cookie.",
        f'curl -X POST {_BASE}/logout -b cookies.txt',
        {"status": "ok"},
        auth=False,
    ),
    _ep(
        "GET",
        "/v1/auth/status",
        "health",
        "Auth configuration status",
        "Discover which auth mechanisms are enabled without exposing secrets.",
        f"{_KEY} {_BASE}/v1/auth/status",
        {
            "enabled": True,
            "api_key_required": True,
            "dashboard_password_required": True,
            "session_cookie": "uploader_session",
            "api_key_header": "X-API-Key",
            "bearer_supported": True,
        },
    ),
    _ep(
        "GET",
        "/v1/capabilities",
        "health",
        "CLI and API inventory",
        "Discover all CLI commands, YouTube features, and HTTP routes (includes this catalog).",
        f"{_KEY} {_BASE}/v1/capabilities",
        {
            "cli_commands": [{"command": "uploader queue add", "description": "Stage a video"}],
            "youtube_features": [{"id": "schedule_publish", "name": "Scheduled publish"}],
            "api_endpoints": [{"method": "GET", "path": "/v1/health", "summary": "Health check"}],
            "auth_note": "Set UPLOADER_API_KEY for machine clients.",
        },
    ),
    _ep(
        "GET",
        "/v1/dashboard",
        "dashboard",
        "Channels + queue + uploaded jobs (cached)",
        "Single request for the web UI: all channels, pending queue, and upload history.",
        f"{_KEY} '{_BASE}/v1/dashboard?refresh=false'",
        {
            "config_uri": "s3://youtuber-uploader/config/channels.yaml",
            "storage": "r2",
            "cached": True,
            "channels": [
                {
                    "id": "justcavefire",
                    "name": "Just Cave Fire",
                    "auth": {"has_token": True, "valid": True, "status": "ok"},
                    "pending_count": 2,
                    "uploaded_count": 5,
                    "failed_count": 0,
                }
            ],
            "queue_jobs": [{"id": "job_abc", "channel_id": "justcavefire", "status": "pending", "title": "My Video"}],
            "uploaded_jobs": [],
        },
        details="Pass `?refresh=true` to bypass cache and reload from R2.",
    ),
    _ep(
        "GET",
        "/v1/channels",
        "channels",
        "List all channels",
        "List every configured channel with OAuth status and queue counts (including broken auth).",
        f"{_KEY} {_BASE}/v1/channels",
        {
            "config_uri": "s3://youtuber-uploader/config/channels.yaml",
            "storage": "r2",
            "channels": [
                {
                    "id": "justcavefire",
                    "name": "Just Cave Fire",
                    "youtube_channel_id": "UCxxxxxxxx",
                    "custom_url": "@justcavefire",
                    "category": "korean",
                    "token_path": "s3://youtuber-uploader/secrets/justcavefire/youtube_token.json",
                    "registry_path": "s3://youtuber-uploader/state/justcavefire/upload_registry.txt",
                    "auth": {"has_token": True, "valid": True, "status": "ok"},
                    "pending_count": 1,
                    "uploaded_count": 3,
                    "failed_count": 0,
                }
            ],
        },
    ),
    _ep(
        "GET",
        "/v1/youtube/channels",
        "youtube",
        "List authenticated YouTube channels",
        "Return only channels with valid OAuth — ready for uploads and YouTube Data API calls.",
        f"{_KEY} {_BASE}/v1/youtube/channels",
        {
            "count": 1,
            "channels": [
                {
                    "id": "justcavefire",
                    "name": "Just Cave Fire",
                    "youtube_channel_id": "UCxxxxxxxx",
                    "custom_url": "@justcavefire",
                    "category": "korean",
                }
            ],
        },
        details="Channels with `refresh_failed` or missing tokens are omitted. Use `GET /v1/channels` for full auth diagnostics.",
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}",
        "channels",
        "Get one channel",
        "Look up a single channel by config id, display name, @handle, or YouTube channel id.",
        f"{_KEY} {_BASE}/v1/channels/justcavefire",
        {
            "id": "justcavefire",
            "name": "Just Cave Fire",
            "youtube_channel_id": "UCxxxxxxxx",
            "custom_url": "@justcavefire",
            "category": "korean",
            "token_path": "s3://youtuber-uploader/secrets/justcavefire/youtube_token.json",
            "registry_path": "s3://youtuber-uploader/state/justcavefire/upload_registry.txt",
            "auth": {"has_token": True, "valid": True, "status": "ok"},
            "pending_count": 1,
            "uploaded_count": 3,
            "failed_count": 0,
        },
    ),
    _ep(
        "PATCH",
        "/v1/channels/{channel_ref}",
        "channels",
        "Update channel",
        "Assign category and/or publish scheduling defaults (spacing, daily cap, timezone, fallback hour).",
        (
            f"{_KEY} -X PATCH {_BASE}/v1/channels/justcavefire "
            '-H "Content-Type: application/json" '
            '-d \'{"category": "korean", "publish": {"interval_hours": 24, "uploads_per_day": 2}}\''
        ),
        {
            "id": "justcavefire",
            "name": "Just Cave Fire",
            "youtube_channel_id": "UCxxxxxxxx",
            "custom_url": "@justcavefire",
            "category": "korean",
            "publish": {
                "timezone": "America/New_York",
                "hour": 9,
                "interval_hours": 24.0,
                "uploads_per_day": 2,
            },
            "token_path": "s3://youtuber-uploader/secrets/justcavefire/youtube_token.json",
            "registry_path": "s3://youtuber-uploader/state/justcavefire/upload_registry.txt",
            "auth": {"has_token": True, "valid": True, "status": "ok"},
            "pending_count": 1,
            "uploaded_count": 3,
            "failed_count": 0,
        },
        details="Pass `\"category\": \"\"` to clear the category. Pass `\"uploads_per_day\": null` to remove the per-run cap. Category must exist in `GET /v1/categories` first.",
    ),
    _ep(
        "GET",
        "/v1/categories",
        "categories",
        "List saved categories",
        "Return deduplicated assembly/content category labels stored in channels.yaml.",
        f"{_KEY} {_BASE}/v1/categories",
        {"categories": ["korean", "japanese"], "count": 2},
    ),
    _ep(
        "POST",
        "/v1/categories",
        "categories",
        "Create category",
        "Add a new assembly/content category (rejects duplicates).",
        (
            f"{_KEY} -X POST {_BASE}/v1/categories "
            '-H "Content-Type: application/json" -d \'{"name": "korean"}\''
        ),
        {"categories": ["korean"], "count": 1},
    ),
    _ep(
        "DELETE",
        "/v1/categories/{category_name}",
        "categories",
        "Delete category",
        "Remove a category and clear it from any channels using it.",
        f"{_KEY} -X DELETE {_BASE}/v1/categories/korean",
        {"categories": [], "count": 0},
    ),
    _ep(
        "DELETE",
        "/v1/channels/{channel_ref}",
        "channels",
        "Remove channel",
        "Disconnect a channel from the uploader — removes it from config and deletes the OAuth token.",
        f"{_KEY} -X DELETE {_BASE}/v1/channels/justcavefire",
        {
            "channel_id": "justcavefire",
            "name": "Just Cave Fire",
            "removed": True,
            "token_deleted": True,
            "pending_jobs_remaining": 2,
            "message": "Removed channel justcavefire from config (2 pending job(s) remain in storage)",
        },
        details="Queue, uploaded, and registry files in R2 are kept. Reconnect with OAuth to use the same channel id again.",
    ),
    _ep(
        "GET",
        "/v1/jobs",
        "jobs",
        "List jobs across channels",
        "Query the upload queue and history across all channels (or filter by channel).",
        (
            f"{_KEY} '{_BASE}/v1/jobs?status=pending&location=queue'\n"
            f"{_KEY} '{_BASE}/v1/jobs?channel=justcavefire&status=uploaded'"
        ),
        [
            {
                "id": "job_abc123",
                "channel_id": "justcavefire",
                "status": "pending",
                "title": "My Generated Video",
                "storage_folder": "queue",
                "queue_position": 1,
                "queue_prefix": "s3://youtuber-uploader/queue/justcavefire/job_abc123/",
            }
        ],
        details="Query params: `channel`, `status` (pending|uploaded|failed|uploading), `location` (queue|uploaded|all).",
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/jobs",
        "jobs",
        "Stage video into queue/ (multipart)",
        "Primary ingest for AI pipelines — upload a video file + metadata into R2 queue/ without YouTube OAuth.",
        (
            f'curl -X POST {_BASE}/v1/channels/justcavefire/jobs \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -F "video=@./output.mp4" \\\n'
            '  -F "title=My Generated Video" \\\n'
            '  -F "description=Created by my pipeline" \\\n'
            '  -F "privacy=private" \\\n'
            '  -F "is_short=false" \\\n'
            '  -F "tags=ai,generated"'
        ),
        {
            "job_id": "job_abc123",
            "channel_id": "justcavefire",
            "status": "pending",
            "title": "My Generated Video",
            "video_uri": "s3://youtuber-uploader/queue/justcavefire/job_abc123/video.mp4",
            "queue_prefix": "s3://youtuber-uploader/queue/justcavefire/job_abc123/",
            "privacy": "private",
            "is_short": False,
            "tags": ["ai", "generated"],
            "publish_at": "",
            "upload_at": "",
        },
        status_code=201,
        details=(
            "Does not upload to YouTube unless followed by `POST .../runs`. "
            "Optional form fields: `publish_at`, `upload_at` (RFC3339 scheduling). "
            "Cloud Run: prefer register endpoint for files >32 MB."
        ),
    ),
    _ep(
        "POST",
        "/v1/jobs",
        "jobs",
        "Stage video (alias with channel_id in form)",
        "Same as `POST /v1/channels/{id}/jobs` but pass `channel_id` as a form field.",
        (
            f'curl -X POST {_BASE}/v1/jobs \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -F "channel_id=justcavefire" \\\n'
            '  -F "video=@./output.mp4" \\\n'
            '  -F "title=My Video"'
        ),
        {
            "job_id": "job_abc123",
            "channel_id": "justcavefire",
            "status": "pending",
            "title": "My Video",
            "video_uri": "s3://youtuber-uploader/queue/justcavefire/job_abc123/video.mp4",
        },
        status_code=201,
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/jobs/register",
        "jobs",
        "Register job when video already in storage",
        (
            "Register a pending job when ai-music-assembler (or your pipeline) already uploaded "
            "the MP4 to R2. External URIs in ``music-assembly-data`` are kept by reference — "
            "downloaded at upload time. Re-posting the same ``job_id`` is idempotent (200)."
        ),
        (
            f'curl -X POST {_BASE}/v1/channels/nappabeats/jobs/register \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{\n'
            '    "job_id": "mv_20260624_061500",\n'
            '    "title": "Generated title",\n'
            '    "description": "Chapter timestamps…",\n'
            '    "video_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_video.mp4",\n'
            '    "thumbnail_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_thumbnail.png"\n'
            '  }\''
        ),
        {
            "job_id": "mv_20260624_061500",
            "channel_id": "nappabeats",
            "status": "pending",
            "title": "Generated title",
            "video_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_video.mp4",
        },
        status_code=201,
        example_request={
            "job_id": "mv_20260624_061500",
            "title": "Generated title",
            "description": "Chapter timestamps…",
            "video_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_video.mp4",
            "thumbnail_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_thumbnail.png",
            "publish_at": "2026-08-01T12:00:00Z",
            "upload_at": "2026-08-01T06:00:00Z",
            "upload_now": False,
        },
        details=(
            "Scheduling: `publish_at` = YouTube publishAt preset; `upload_at` = queue pickup gate; "
            "`upload_now` = register + dispatch worker immediately (requires OAuth). "
            "Idempotent on `job_id`. Does not upload unless `upload_now` is true."
        ),
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/upload/direct",
        "uploads",
        "Direct upload to YouTube (no queue)",
        "Upload a video straight to YouTube without creating a queue/registry job. Requires channel OAuth.",
        (
            f'curl -X POST {_BASE}/v1/channels/justcavefire/upload/direct \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -F "video=@./output.mp4" \\\n'
            '  -F "title=My Video" \\\n'
            '  -F "description=Optional" \\\n'
            '  -F "privacy=private" \\\n'
            '  -F "publish_at=2026-08-01T12:00:00Z" \\\n'
            '  -F "no_schedule=false"'
        ),
        {
            "channel_id": "justcavefire",
            "youtube_id": "dQw4w9WgXcQ",
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
            "title": "My Video",
            "privacy": "private",
            "publish_at": "2026-08-01T12:00:00Z",
            "status": "uploaded",
        },
        status_code=201,
        details=(
            "Multipart form. Required: `video`, `title`. Optional: `description`, `thumbnail`, `privacy`, "
            "`tags`, `is_short`, `category_id`, `made_for_kids`, `language`, `metadata` (JSON string), "
            "`publish_at`, `no_schedule=true` (immediate publish using privacy), `upload_retries`, `retry_delay`. "
            "Cloud Run body limit 32 MB — use register + runs for large files."
        ),
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}/jobs",
        "jobs",
        "List jobs for one channel",
        "List queue, uploading, uploaded, and failed jobs for a channel with optional filters.",
        (
            f"{_KEY} '{_BASE}/v1/channels/justcavefire/jobs?location=all&status=pending'\n"
            f"{_KEY} '{_BASE}/v1/channels/justcavefire/jobs?status=failed'"
        ),
        [
            {
                "id": "job_abc123",
                "channel_id": "justcavefire",
                "status": "pending",
                "title": "My Video",
                "storage_folder": "queue",
            }
        ],
        details="Query: `status` (pending|uploading|uploaded|failed), `location` (queue|uploaded|all).",
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}/jobs/{job_id}",
        "jobs",
        "Job detail + metadata",
        "Inspect a staged job before upload or debug failures.",
        f"{_KEY} '{_BASE}/v1/channels/justcavefire/jobs/job_abc123?media=true'",
        {
            "job": {
                "id": "job_abc123",
                "channel_id": "justcavefire",
                "status": "pending",
                "title": "My Generated Video",
                "storage_folder": "queue",
            },
            "metadata": {"title": "My Generated Video", "privacy": "private", "is_short": False},
            "media": {
                "video": "/v1/channels/justcavefire/jobs/job_abc123/media/video",
                "thumbnail": "/v1/channels/justcavefire/jobs/job_abc123/media/thumbnail",
                "video_available": True,
                "thumbnail_available": True,
            },
        },
        details="Pass `?media=true` to include preview URL paths.",
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}/jobs/{job_id}/media/{asset}",
        "jobs",
        "Stream video or thumbnail preview",
        "Preview staged media in the dashboard or download for QA.",
        f"{_KEY} -L {_BASE}/v1/channels/justcavefire/jobs/job_abc123/media/video",
        {"note": "307 redirect to presigned R2 URL, or file stream for local storage"},
        details="`asset` must be `video` or `thumbnail`.",
    ),
    _ep(
        "DELETE",
        "/v1/channels/{channel_ref}/jobs/{job_id}",
        "jobs",
        "Remove job from queue",
        "Cancel a staged job — deletes queue/ folder and registry row.",
        f"{_KEY} -X DELETE {_BASE}/v1/channels/justcavefire/jobs/job_abc123",
        {"removed": "job_abc123", "deleted_files": 4},
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}/plan",
        "runs",
        "Preview publish schedule",
        "Dry-run: see computed publishAt times for pending jobs without uploading.",
        f"{_KEY} '{_BASE}/v1/channels/justcavefire/plan?limit=5'",
        [
            {
                "job_id": "job_abc123",
                "title": "My Video",
                "publish_at": "2026-06-21T13:00:00Z",
                "publish_display": "2026-06-21 09:00 EDT",
            }
        ],
        details="Query: `limit`, `no_schedule`, `start`, `interval_hours`. Same logic as `uploader plan`.",
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/runs",
        "runs",
        "Start YouTube upload run (background)",
        "Upload pending queue jobs to YouTube. Requires valid channel OAuth.",
        (
            f'curl -X POST {_BASE}/v1/channels/justcavefire/runs \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"count": 1, "parallel": true, "upload_retries": 5, "ignore_upload_at": false}\''
        ),
        {
            "run_id": "run_a1b2c3d4e5f6",
            "channel_id": "justcavefire",
            "status": "queued",
            "message": "Uploading 1 job(s). Poll GET /v1/runs/run_a1b2c3d4e5f6",
        },
        status_code=202,
        example_request={
            "count": 1,
            "parallel": True,
            "upload_retries": 5,
            "no_schedule": False,
            "job_ids": ["job_abc123"],
            "publish_at": "2026-08-01T12:00:00Z",
            "ignore_upload_at": False,
        },
        details=(
            "Production uploads use `\"parallel\": true` (one worker per job). "
            "Skips jobs with future `upload_at` unless `ignore_upload_at` is true. "
            "Poll `GET /v1/runs/{run_id}` or `GET /v1/uploads/active` when parallel."
        ),
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/jobs/{job_id}/upload",
        "jobs",
        "Upload or re-upload one job",
        "Dispatch a parallel worker for a single job. Re-queues uploaded/failed jobs automatically.",
        (
            f'curl -X POST {_BASE}/v1/channels/justcavefire/jobs/job_abc123/upload \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"parallel": true}\''
        ),
        {
            "run_id": "parallel_a1b2c3",
            "channel_id": "justcavefire",
            "status": "dispatched",
            "message": "Upload started for job_abc123. Poll GET /v1/uploads/active",
        },
        status_code=202,
        example_request={"parallel": True},
        details="Body optional (defaults to parallel upload). Same RunRequest fields supported.",
    ),
    _ep(
        "GET",
        "/v1/runs/{run_id}",
        "runs",
        "Poll upload run status",
        "Track background upload progress and get YouTube URLs when done.",
        f"{_KEY} {_BASE}/v1/runs/run_a1b2c3d4e5f6",
        {
            "run_id": "run_a1b2c3d4e5f6",
            "channel_id": "justcavefire",
            "status": "completed",
            "total": 1,
            "uploaded": 1,
            "failed": 0,
            "urls": ["https://youtu.be/dQw4w9WgXcQ"],
            "errors": [],
        },
    ),
    _ep(
        "GET",
        "/v1/uploads/active",
        "uploads",
        "List in-progress uploads",
        "Dashboard **Uploading now** panel: jobs with status uploading and recent progress.",
        f"{_KEY} {_BASE}/v1/uploads/active",
        {
            "count": 1,
            "uploads": [
                {
                    "channel_id": "justcavefire",
                    "job_id": "job_abc123",
                    "title": "My Video",
                    "upload_phase": "uploading",
                    "upload_progress": 87,
                    "completed": False,
                    "youtube_url": "",
                }
            ],
        },
        details="Recently completed jobs remain visible briefly (grace period) so the UI can show 100% before archive.",
    ),
    _ep(
        "POST",
        "/v1/uploads/reconcile",
        "uploads",
        "Repair stuck uploads",
        "Scan registries for orphaned uploading jobs and archive completed jobs left in queue/.",
        (
            f'curl -X POST "{_BASE}/v1/uploads/reconcile" \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY"\n'
            f'curl -X POST "{_BASE}/v1/uploads/reconcile?dry_run=true&channel=justcavefire" \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY"'
        ),
        {
            "scanned": 3,
            "dry_run": False,
            "actions": [
                {
                    "channel_id": "justcavefire",
                    "job_id": "job_abc123",
                    "action": "archived",
                    "detail": "uploaded in registry but still in queue/",
                }
            ],
        },
        details=(
            "Run via Cloud Scheduler every 10 minutes (see deploy/cloud-scheduler-reconcile.md). "
            "Env: UPLOADER_RECONCILE_STALE_SECONDS (180), UPLOADER_RECONCILE_COMPLETE_SECONDS (90), "
            "UPLOADER_RECONCILE_FAIL_SECONDS (7200). "
            "CLI: `uploader reconcile-uploads [--dry-run] [--channel ID]`."
        ),
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/jobs/{job_id}/cancel-upload",
        "uploads",
        "Cancel in-flight upload",
        "Stop an active upload worker and return the job to pending queue.",
        f'curl -X POST {_BASE}/v1/channels/justcavefire/jobs/job_abc123/cancel-upload -H "X-API-Key: $UPLOADER_API_KEY"',
        {"channel_id": "justcavefire", "job_id": "job_abc123"},
        details="Only for jobs with status `uploading`. Releases worker lock and resets to pending.",
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/jobs/{job_id}/dismiss-upload",
        "uploads",
        "Dismiss stuck Uploading now row",
        "Manually clear a stuck upload: finalize on YouTube, return to queue, or mark failed.",
        (
            f'curl -X POST "{_BASE}/v1/channels/justcavefire/jobs/job_abc123/dismiss-upload" \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY"\n'
            f'curl -X POST "{_BASE}/v1/channels/justcavefire/jobs/job_abc123/dismiss-upload?action=retry" \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY"'
        ),
        {
            "channel_id": "justcavefire",
            "job_id": "job_abc123",
            "action": "finalized",
            "detail": "ytabc123",
        },
        details=(
            "Query `action`: `auto` (default) = finalize if possible else reset pending; "
            "`retry` = force back to queue; `fail` = mark failed."
        ),
    ),
    _ep(
        "GET",
        "/v1/docs/llm",
        "health",
        "LLM / agent API reference (Markdown)",
        "Single self-contained Markdown document with all endpoints, workflows, and schemas for AI agents.",
        f"{_KEY} {_BASE}/v1/docs/llm",
        {"note": "Returns text/markdown body — paste into an LLM context or save to a file"},
        details="Preferred handoff format for another LLM. Also available as JSON catalog at GET /v1/capabilities.",
    ),
    _ep(
        "POST",
        "/v1/runs/all",
        "runs",
        "Upload pending jobs for all channels",
        "Background upload across every configured channel (like `uploader run-all`).",
        (
            f'curl -X POST {_BASE}/v1/runs/all \\\n'
            '  -H "X-API-Key: $UPLOADER_API_KEY" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"count": null, "upload_retries": 5}\''
        ),
        {"status": "queued", "message": "Run-all started in background (poll channels for results)"},
        status_code=202,
        example_request={"count": None, "upload_retries": 5},
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}/youtube/videos",
        "youtube",
        "List videos on YouTube",
        "Read videos already on the authenticated YouTube channel (including scheduled).",
        (
            f"{_KEY} '{_BASE}/v1/channels/justcavefire/youtube/videos'\n"
            f"{_KEY} '{_BASE}/v1/channels/justcavefire/youtube/videos?scheduled_only=true'"
        ),
        [
            {
                "video_id": "dQw4w9WgXcQ",
                "title": "My Scheduled Short",
                "privacy_status": "private",
                "publish_at": "2026-06-21T13:00:00Z",
                "url": "https://youtu.be/dQw4w9WgXcQ",
                "is_scheduled": True,
            }
        ],
        details="Requires valid OAuth. `scheduled_only=true` filters to future publishAt videos.",
    ),
    _ep(
        "GET",
        "/v1/channels/{channel_ref}/youtube/scheduled",
        "youtube",
        "List scheduled videos on YouTube",
        "Future scheduled videos on the channel, with tail publishAt for upload planning.",
        f"{_KEY} '{_BASE}/v1/channels/justcavefire/youtube/scheduled'",
        {
            "channel_id": "justcavefire",
            "count": 2,
            "tail_publish_at": "2026-06-27T13:00:00Z",
            "videos": [
                {
                    "video_id": "abc123",
                    "title": "Scheduled Short 1",
                    "privacy_status": "private",
                    "publish_at": "2026-06-26T13:00:00Z",
                    "url": "https://youtu.be/abc123",
                    "is_scheduled": True,
                },
                {
                    "video_id": "def456",
                    "title": "Scheduled Short 2",
                    "privacy_status": "private",
                    "publish_at": "2026-06-27T13:00:00Z",
                    "url": "https://youtu.be/def456",
                    "is_scheduled": True,
                },
            ],
        },
        details="Use `tail_publish_at` plus channel `interval_hours` to know when the next upload slot starts.",
    ),
    _ep(
        "POST",
        "/v1/oauth/start",
        "oauth",
        "Start OAuth (add channel)",
        "Begin browser OAuth to connect a new YouTube channel.",
        f'curl -X POST {_BASE}/v1/oauth/start -H "X-API-Key: $UPLOADER_API_KEY" '
        '-H "Content-Type: application/json" -d \'{"category": "korean"}\'',
        {
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
            "state": "nonce_value",
            "redirect_uri": "https://your-uploader-host/v1/oauth/callback",
        },
        details="Optional JSON body `{ \"category\": \"korean\" }` assigns an assembly/content category when the channel is saved. Open `auth_url` in a browser.",
    ),
    _ep(
        "POST",
        "/v1/channels/{channel_ref}/oauth/start",
        "oauth",
        "Start OAuth (reauth)",
        "Re-authenticate an existing channel (fixes refresh_failed / reconnect required).",
        f'curl -X POST {_BASE}/v1/channels/justcavefire/oauth/start -H "X-API-Key: $UPLOADER_API_KEY"',
        {
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
            "state": "nonce_value",
            "redirect_uri": "https://your-uploader-host/v1/oauth/callback",
        },
    ),
    _ep(
        "GET",
        "/v1/oauth/callback",
        "oauth",
        "OAuth callback (browser redirect)",
        "Google redirects here after user consent — saves token to R2 and updates channels.yaml.",
        "(Browser redirect only — not called by API clients)",
        {"note": "302 redirect to /?oauth_success=channel_id"},
        auth=False,
        details="Must match `{UPLOADER_API_PUBLIC_URL}/v1/oauth/callback` in Google Cloud Console.",
    ),
    _ep(
        "POST",
        "/v1/storage/init",
        "storage",
        "Initialize bucket layout",
        "Create R2/local folder structure and migrate local config to R2 (run once per bucket).",
        f'curl -X POST {_BASE}/v1/storage/init -H "X-API-Key: $UPLOADER_API_KEY"',
        {
            "created": ["config/", "secrets/", "state/", "queue/", "uploaded/", "logs/"],
            "count": 6,
        },
        details="Equivalent to `uploader storage init`.",
    ),
]

AUTH_NOTE = (
    "Hosted security: set `UPLOADER_API_KEY` (machine clients) and/or `UPLOADER_DASHBOARD_PASSWORD` (browser login). "
    "API clients send `X-API-Key: <token>` or `Authorization: Bearer <token>`. "
    "Dashboard users enter the password on `/` (session cookie). "
    "Public routes: `GET /`, `GET /v1/auth/session`, `/v1/health`, `POST /login`, `/v1/oauth/callback`.\n\n"
    "Docs for LLM agents: `GET /v1/docs/llm` (Markdown). "
    "Interactive: `/docs` (Swagger) and `/redoc`. Machine JSON: `GET /v1/capabilities`. Schema: `/openapi.json`."
)
