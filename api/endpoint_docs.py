"""Canonical HTTP API endpoint catalog (README, /v1/capabilities, OpenAPI)."""

from __future__ import annotations

API_TAGS: list[dict] = [
    {"name": "health", "description": "Liveness and version info."},
    {"name": "dashboard", "description": "Cached snapshot of channels and jobs for the web UI."},
    {"name": "channels", "description": "Configured YouTube channels, auth status, and publish settings."},
    {"name": "jobs", "description": "Upload queue: stage videos, list pending/history, preview media, remove jobs."},
    {"name": "runs", "description": "Background YouTube upload runs and progress polling."},
    {"name": "oauth", "description": "Browser OAuth to add or re-authenticate YouTube channels."},
    {"name": "storage", "description": "Initialize Cloudflare R2 / local bucket layout."},
    {"name": "youtube", "description": "Read videos already published or scheduled on YouTube."},
]

API_ENDPOINTS: list[dict] = [
    {
        "method": "GET",
        "path": "/v1/health",
        "tag": "health",
        "summary": "Health check",
        "description": (
            "Returns `{ status: \"ok\", version }`. Use for load balancers and uptime checks. "
            "Alias: `GET /health`."
        ),
        "auth": False,
    },
    {
        "method": "GET",
        "path": "/login",
        "tag": "health",
        "summary": "Dashboard sign-in page",
        "description": "HTML login form. Public when auth is enabled.",
        "auth": False,
    },
    {
        "method": "POST",
        "path": "/login",
        "tag": "health",
        "summary": "Dashboard sign-in",
        "description": "JSON body `{ \"password\": \"...\" }` — accepts dashboard password or API token. Sets session cookie.",
        "auth": False,
    },
    {
        "method": "POST",
        "path": "/logout",
        "tag": "health",
        "summary": "Sign out",
        "description": "Clears dashboard session cookie.",
        "auth": False,
    },
    {
        "method": "GET",
        "path": "/v1/auth/session",
        "tag": "health",
        "summary": "Browser session status",
        "description": "Public. Returns whether auth is enabled and whether the current request has a valid session or API key.",
        "auth": False,
    },
    {
        "method": "GET",
        "path": "/v1/auth/status",
        "tag": "health",
        "summary": "Auth configuration status",
        "description": "Returns whether API key / dashboard password auth is enabled (no secrets exposed).",
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/capabilities",
        "tag": "health",
        "summary": "CLI and API inventory",
        "description": (
            "Lists every CLI command, YouTube feature flag, and HTTP route with implementation status. "
            "Useful for tooling and assembler integration discovery."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/dashboard",
        "tag": "dashboard",
        "summary": "Channels + queue + uploaded jobs (cached)",
        "description": (
            "Preferred single request for the web UI: all channels (auth, pending/uploaded/failed counts), "
            "`queue_jobs` (FIFO pending), and `uploaded_jobs` (history). "
            "Pass `?refresh=true` to bypass the in-memory cache and reload from R2."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/channels",
        "tag": "channels",
        "summary": "List all channels",
        "description": (
            "Returns `{ config_uri, storage, channels[] }`. Each channel includes OAuth status, "
            "token/registry paths, and queue counts."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/channels/{channel_ref}",
        "tag": "channels",
        "summary": "Get one channel",
        "description": (
            "Channel detail by config id, display name, `@handle`, or YouTube channel id. "
            "404 if not found."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/jobs",
        "tag": "jobs",
        "summary": "List jobs across channels",
        "description": (
            "Filter with `?channel=`, `?status=pending|uploaded|failed|uploading`, and "
            "`?location=queue|uploaded|all`. Default: pending jobs in queue/. "
            "Each job includes `storage_folder`, `queue_prefix`, and FIFO `queue_position` when pending."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/channels/{channel_ref}/jobs",
        "tag": "jobs",
        "summary": "Stage video into queue/ (multipart)",
        "description": (
            "**Primary ingest endpoint for AI video pipelines.** "
            "Upload a video file + metadata; writes to `queue/{channel}/{job_id}/` on R2 and appends a "
            "`pending` registry row. Does not require YouTube OAuth. "
            "Form fields: `video` (required), `title` (required), `description`, `thumbnail`, `job_id`, "
            "`privacy`, `is_short`, `category_id`, `tags` (comma-separated), `made_for_kids`, `language`, "
            "`metadata` (JSON string). Returns `201` + `StagedJobOut`. "
            "Errors: 400 empty/missing file, 404 channel, 409 duplicate job_id, 422 invalid metadata, 502 storage."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/jobs",
        "tag": "jobs",
        "summary": "Stage video (alias with channel_id in form)",
        "description": (
            "Same as `POST /v1/channels/{id}/jobs` but accepts `channel_id` as a form field instead of a URL path. "
            "Convenient for generic HTTP clients."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/channels/{channel_ref}/jobs/register",
        "tag": "jobs",
        "summary": "Register job when video already in storage",
        "description": (
            "**Use when your pipeline uploads directly to R2** before calling the uploader. "
            "JSON body: `title`, `video_uri` (required), `description`, `thumbnail_uri`, `job_id`, "
            "metadata fields (`privacy`, `is_short`, `tags`, …). Validates files exist, copies to canonical "
            "`queue/` path if needed, writes metadata sidecars, appends registry row. Returns `201` + `StagedJobOut`."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/channels/{channel_ref}/jobs/{job_id}",
        "tag": "jobs",
        "summary": "Job detail + metadata",
        "description": (
            "Returns registry row, parsed `metadata.json`, and optional media preview URLs when `?media=true`. "
            "Use to inspect a staged job before upload or debug failed uploads."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/channels/{channel_ref}/jobs/{job_id}/media/{asset}",
        "tag": "jobs",
        "summary": "Stream video or thumbnail preview",
        "description": (
            "`asset` is `video` or `thumbnail`. Redirects to a presigned R2 URL (307) or serves local file. "
            "Used by the dashboard lazy preview."
        ),
        "auth": True,
    },
    {
        "method": "DELETE",
        "path": "/v1/channels/{channel_ref}/jobs/{job_id}",
        "tag": "jobs",
        "summary": "Remove job from queue",
        "description": (
            "Deletes the job folder under `queue/` and removes the registry row. "
            "Cannot remove jobs with status `uploading`. Equivalent to `uploader queue remove`."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/channels/{channel_ref}/plan",
        "tag": "runs",
        "summary": "Preview publish schedule",
        "description": (
            "Dry-run schedule for pending jobs: job id, title, computed `publish_at`, and human-readable display. "
            "Query: `limit`, `no_schedule`, `start`, `interval_hours`. Same logic as `uploader plan`."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/channels/{channel_ref}/runs",
        "tag": "runs",
        "summary": "Start YouTube upload run (background)",
        "description": (
            "Uploads pending jobs from the front of the queue to YouTube. Requires valid channel OAuth. "
            "JSON body: `count` (null = all pending), `no_schedule`, `privacy`, `upload_retries`, "
            "`retry_delay`, `tags`, `start`, `interval_hours`. Returns `202` + `run_id`; poll `GET /v1/runs/{run_id}`."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/runs/{run_id}",
        "tag": "runs",
        "summary": "Poll upload run status",
        "description": (
            "Returns `status`, `uploaded`, `failed`, `total`, YouTube URLs, and per-job errors when complete."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/runs/all",
        "tag": "runs",
        "summary": "Upload pending jobs for all channels",
        "description": (
            "Background run across every configured channel. Same body as single-channel runs. "
            "Equivalent to `uploader run-all`."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/channels/{channel_ref}/youtube/videos",
        "tag": "youtube",
        "summary": "List videos on YouTube",
        "description": (
            "Lists videos on the authenticated channel. Pass `?scheduled_only=true` for future publishAt videos. "
            "Requires valid OAuth."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/oauth/start",
        "tag": "oauth",
        "summary": "Start OAuth (add channel)",
        "description": (
            "Returns Google authorization URL + PKCE state. User completes OAuth in browser; "
            "callback saves token and channel to R2. Register redirect URI: "
            "`{UPLOADER_API_PUBLIC_URL}/v1/oauth/callback`."
        ),
        "auth": True,
    },
    {
        "method": "POST",
        "path": "/v1/channels/{channel_ref}/oauth/start",
        "tag": "oauth",
        "summary": "Start OAuth (reauth)",
        "description": (
            "Re-authenticate an existing channel. Same flow as add; may add a new channel entry if a "
            "different Google account is used."
        ),
        "auth": True,
    },
    {
        "method": "GET",
        "path": "/v1/oauth/callback",
        "tag": "oauth",
        "summary": "OAuth callback (browser redirect)",
        "description": "Google redirects here after user consent. Not called directly by API clients.",
        "auth": False,
    },
    {
        "method": "POST",
        "path": "/v1/storage/init",
        "tag": "storage",
        "summary": "Initialize bucket layout",
        "description": (
            "Creates R2/local prefixes (`config/`, `secrets/`, `state/`, `queue/`, `uploaded/`, `logs/`). "
            "Migrates local config/tokens/registries when R2 is configured. Equivalent to `uploader storage init`."
        ),
        "auth": True,
    },
]

AUTH_NOTE = (
    "Hosted security: set `UPLOADER_API_KEY` (machine clients) and/or `UPLOADER_DASHBOARD_PASSWORD` (browser login). "
    "API clients send `X-API-Key: <token>` or `Authorization: Bearer <token>`. "
    "Dashboard users enter the password on `/` (session cookie). "
    "Public routes: `GET /`, `GET /v1/auth/session`, `/v1/health`, `POST /login`, `/v1/oauth/callback`."
)
