"""Single-file Markdown API reference for LLM / agent clients."""

from __future__ import annotations

import json
from typing import Any

from api.endpoint_docs import API_ENDPOINTS, API_TAGS, AUTH_NOTE

WORKFLOWS: list[dict[str, str]] = [
    {
        "name": "Assembler two-step (recommended for pipelines)",
        "steps": (
            "1. `POST /v1/channels/{ref}/jobs/register` with `video_uri` (s3://…) — creates pending job, does NOT upload.\n"
            "2. `POST /v1/channels/{ref}/runs` with `{\"parallel\": true, \"count\": 1}` — uploads to YouTube.\n"
            "3. Poll `GET /v1/uploads/active` or `GET /v1/runs/{run_id}` until complete."
        ),
    },
    {
        "name": "Register + immediate upload",
        "steps": (
            "1. `POST /v1/channels/{ref}/jobs/register` with `\"upload_now\": true` (requires channel OAuth).\n"
            "2. Poll `GET /v1/uploads/active`."
        ),
    },
    {
        "name": "Scheduled queue pickup + YouTube publishAt",
        "steps": (
            "1. Register or stage with `upload_at` (when worker may pick up) and/or `publish_at` (YouTube publishAt).\n"
            "2. Cron or manual `POST .../runs` with `parallel: true` — skips jobs whose `upload_at` is still in the future.\n"
            "3. Use `ignore_upload_at: true` on runs to force early dispatch."
        ),
    },
    {
        "name": "Direct upload (no queue)",
        "steps": (
            "1. `POST /v1/channels/{ref}/upload/direct` — multipart `video` + `title` (+ optional metadata).\n"
            "2. Response includes `youtube_id` and `youtube_url` immediately. Requires OAuth."
        ),
    },
    {
        "name": "Stuck Uploading now cleanup",
        "steps": (
            "1. Automatic: Cloud Scheduler calls `POST /v1/uploads/reconcile` every 10 minutes.\n"
            "2. Manual: `POST /v1/uploads/reconcile` or per-job `POST .../jobs/{job_id}/dismiss-upload?action=auto|retry|fail`.\n"
            "3. Poll `GET /v1/uploads/active` — rows disappear when registry is repaired."
        ),
    },
]

SCHEMAS: dict[str, Any] = {
    "JobRegisterRequest": {
        "title": "string (required)",
        "description": "string",
        "video_uri": "string (required) — s3://bucket/key or local path",
        "thumbnail_uri": "string",
        "job_id": "string — idempotent key; same channel+job_id returns 200",
        "privacy": "private | public | unlisted",
        "is_short": "boolean",
        "category_id": "string — YouTube category (default 10)",
        "tags": ["string"],
        "made_for_kids": "boolean",
        "language": "string",
        "metadata": "object — extra fields stored in metadata.json",
        "publish_at": "RFC3339 UTC — YouTube publishAt preset on job",
        "upload_at": "RFC3339 UTC — do not dispatch until this time",
        "upload_now": "boolean — register then dispatch worker immediately",
        "no_schedule": "boolean — with upload_now, publish immediately using privacy",
    },
    "RunRequest": {
        "count": "int | null — jobs from front of queue; null = all ready pending",
        "parallel": "boolean — true = one Cloud Run Job per video (production default)",
        "no_schedule": "boolean — publish immediately, ignore tail scheduling",
        "upload_retries": "int (default 3)",
        "retry_delay": "float seconds (default 30)",
        "privacy": "private | public | unlisted",
        "interval_hours": "float — stagger between videos in batch",
        "uploads_per_day": "int — cap jobs in this run",
        "start": "RFC3339 — anchor for batch scheduling",
        "tags": ["string"],
        "job_ids": ["string — upload only these pending jobs"],
        "publish_at": "RFC3339 — override publishAt for this run",
        "ignore_upload_at": "boolean — dispatch even if upload_at is future",
    },
    "StageJobForm": {
        "video": "file (required)",
        "title": "string (required)",
        "description": "string",
        "thumbnail": "file",
        "privacy": "string",
        "is_short": "true|false",
        "tags": "comma-separated",
        "publish_at": "RFC3339 — YouTube publishAt preset",
        "upload_at": "RFC3339 — queue pickup time",
    },
}


def render_llm_api_docs(*, base_url: str) -> str:
    """Return a self-contained Markdown document for LLM agents."""
    base = base_url.rstrip("/")
    lines: list[str] = [
        "# YouTube Uploader HTTP API — LLM / Agent Reference",
        "",
        f"**Base URL:** `{base}`",
        "",
        "Use this document as the single source of truth for calling the uploader API. "
        "All paths are relative to the base URL unless noted.",
        "",
        "## Authentication",
        "",
        AUTH_NOTE.replace("your-uploader-host", base.replace("https://", "").replace("http://", "")),
        "",
        "**Header for machine clients:**",
        "",
        "```",
        "X-API-Key: $UPLOADER_API_KEY",
        "```",
        "",
        "Public routes (no key): `GET /v1/health`, `GET /v1/auth/session`, `POST /login`, `GET /v1/oauth/callback`.",
        "",
        "## Resolving `channel_ref`",
        "",
        "Path parameter `{channel_ref}` accepts any of:",
        "",
        "- Config channel id (e.g. `justcavefire`)",
        "- Display name",
        "- YouTube `@handle`",
        "- YouTube channel id (`UC…`)",
        "",
        "## Common workflows",
        "",
    ]

    for wf in WORKFLOWS:
        lines.extend([f"### {wf['name']}", "", wf["steps"], ""])

    lines.extend(
        [
            "## Request body / form schemas",
            "",
        ]
    )
    for name, fields in SCHEMAS.items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(fields, indent=2))
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "## Endpoint catalog",
            "",
            "Grouped by area. Each entry includes method, path, purpose, curl example, and notes.",
            "",
        ]
    )

    tags_by_name = {t["name"]: t["description"] for t in API_TAGS}
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for ep in API_ENDPOINTS:
        by_tag.setdefault(ep["tag"], []).append(ep)

    tag_order = [t["name"] for t in API_TAGS]
    for tag in tag_order:
        eps = by_tag.get(tag)
        if not eps:
            continue
        desc = tags_by_name.get(tag, "")
        lines.append(f"### {tag}")
        if desc:
            lines.append("")
            lines.append(desc)
        lines.append("")
        for ep in eps:
            usage = ep.get("usage", "").replace("https://your-uploader-host", base)
            lines.append(f"#### `{ep['method']} {ep['path']}` — {ep['summary']}")
            lines.append("")
            purpose = ep.get("purpose") or ep.get("description", "")
            if purpose:
                lines.append(purpose)
                lines.append("")
            details = ep.get("details", "")
            if details:
                lines.append(f"**Details:** {details}")
                lines.append("")
            if ep.get("auth", True):
                lines.append("**Auth:** required (`X-API-Key` or session cookie)")
            else:
                lines.append("**Auth:** public")
            if ep.get("status_code"):
                lines.append(f"**Status:** `{ep['status_code']}`")
            lines.append("")
            if usage:
                lines.append("**Example:**")
                lines.append("")
                lines.append("```bash")
                lines.append(usage)
                lines.append("```")
                lines.append("")
            if ep.get("example_request") is not None:
                lines.append("**Example request body:**")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(ep["example_request"], indent=2))
                lines.append("```")
                lines.append("")
            if ep.get("example_response") is not None:
                lines.append("**Example response:**")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(ep["example_response"], indent=2))
                lines.append("```")
                lines.append("")

    lines.extend(
        [
            "## Other machine-readable docs",
            "",
            f"| URL | Format | Use |",
            f"|-----|--------|-----|",
            f"| `{base}/v1/capabilities` | JSON | Structured endpoint list + CLI map |",
            f"| `{base}/v1/docs/llm` | Markdown | This document (refresh for latest) |",
            f"| `{base}/openapi.json` | OpenAPI 3 | Schema-aware clients |",
            f"| `{base}/docs` | Swagger UI | Interactive browser docs |",
            f"| `{base}/redoc` | ReDoc | Readable browser docs |",
            "",
            "## Job lifecycle",
            "",
            "`pending` → `uploading` → `uploaded` | `failed`",
            "",
            "Registry file per channel: `state/{channel_id}/upload_registry.txt` (JSON lines on R2).",
            "Storage: `queue/{channel}/{job_id}/` before upload, `uploaded/` after success.",
            "",
        ]
    )

    return "\n".join(lines)
