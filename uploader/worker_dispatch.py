"""Dispatch parallel upload workers (Cloud Run Job or local threads)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uploader.channels import AppConfig, get_channel
from uploader.job_claim import release_job_claim, try_claim_job
from uploader.registry import UploadRegistry
from uploader.scheduler import build_channel_upload_plan
from uploader.state_store import config_base_from_path
from uploader.upload_worker import upload_single_job


@dataclass
class DispatchedUpload:
    channel_id: str
    job_id: str
    worker_id: str
    execution: str = ""
    backend: str = ""


@dataclass
class ParallelDispatchResult:
    channel_id: str
    dispatched: list[DispatchedUpload] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def worker_backend() -> str:
    return os.environ.get("UPLOADER_WORKER_BACKEND", "cloudrun").strip().lower() or "cloudrun"


def max_parallel_uploads() -> int:
    raw = os.environ.get("UPLOADER_MAX_PARALLEL_UPLOADS", "5").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


def dispatch_parallel_uploads(
    channel_id: str,
    config: AppConfig,
    *,
    base,
    count: int | None = None,
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
    start: str | None = None,
    interval_hours: float | None = None,
    uploads_per_day: int | None = None,
    run_options: dict[str, Any] | None = None,
    oauth_client_secret: Path | None = None,
    oauth_client_config: dict | None = None,
    oauth_port: int | None = None,
    job_ids: list[str] | None = None,
) -> ParallelDispatchResult:
    """Claim up to `count` pending jobs and start one worker each."""
    channel = get_channel(config, channel_id)
    registry = UploadRegistry(channel.registry_path)
    pending = registry.pending(channel_id=channel.id)
    if job_ids:
        wanted = {j.strip() for j in job_ids if j and j.strip()}
        pending = [e for e in pending if e.id in wanted]
        if not pending:
            return ParallelDispatchResult(channel_id=channel.id)
    elif not pending:
        return ParallelDispatchResult(channel_id=channel.id)

    daily_cap = uploads_per_day if uploads_per_day is not None else channel.publish.uploads_per_day
    limit = count if count is not None else len(pending)
    if daily_cap is not None:
        limit = min(limit, daily_cap)
    limit = min(limit, len(pending), max_parallel_uploads())
    pending = pending[:limit]

    upload_plan = build_channel_upload_plan(
        channel,
        config,
        pending,
        start=start,
        interval_hours=interval_hours,
        no_schedule=no_schedule,
        oauth_client_secret=oauth_client_secret,
        oauth_client_config=oauth_client_config,
        oauth_port=oauth_port,
    )
    plan = upload_plan.items

    result = ParallelDispatchResult(channel_id=channel.id)
    opts = run_options or {}

    for entry, publish_at in plan:
        worker_id = f"wrk_{uuid.uuid4().hex[:12]}"
        claim = try_claim_job(
            registry,
            channel.id,
            entry.id,
            worker_id,
            base=base,
            publish_at=publish_at,
        )
        if not claim.claimed:
            result.skipped.append({"job_id": entry.id, "reason": claim.reason})
            continue
        job_no_schedule = upload_plan.upload_immediately or not publish_at
        try:
            execution = _launch_worker(
                channel.id,
                entry.id,
                worker_id,
                publish_at=publish_at,
                no_schedule=job_no_schedule,
                privacy=privacy,
                upload_retries=upload_retries,
                retry_delay=retry_delay,
                tags=tags,
                **opts,
            )
            result.dispatched.append(
                DispatchedUpload(
                    channel_id=channel.id,
                    job_id=entry.id,
                    worker_id=worker_id,
                    execution=execution,
                    backend=worker_backend(),
                )
            )
        except Exception as e:
            release_job_claim(registry, channel.id, entry.id, worker_id, base=base, reset_to_pending=True)
            result.skipped.append({"job_id": entry.id, "reason": str(e)})

    return result


def _launch_worker(
    channel_id: str,
    job_id: str,
    worker_id: str,
    *,
    publish_at: str = "",
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
) -> str:
    backend = worker_backend()
    if backend == "local":
        _start_local_worker(
            channel_id,
            job_id,
            worker_id,
            publish_at=publish_at,
            no_schedule=no_schedule,
            privacy=privacy,
            upload_retries=upload_retries,
            retry_delay=retry_delay,
            tags=tags,
        )
        return f"local-thread:{worker_id}"

    return _start_cloud_run_job(
        channel_id,
        job_id,
        worker_id,
        publish_at=publish_at,
        no_schedule=no_schedule,
        privacy=privacy,
        upload_retries=upload_retries,
        retry_delay=retry_delay,
        tags=tags,
    )


def _start_local_worker(**kwargs) -> None:
    def _run() -> None:
        import os
        from pathlib import Path

        from uploader.channels import load_config

        config_path = Path(os.environ.get("UPLOADER_CONFIG", "config/channels.yaml")).expanduser().resolve()
        config = load_config(config_path)
        base = config_base_from_path(config_path)
        upload_single_job(
            kwargs["channel_id"],
            kwargs["job_id"],
            config,
            worker_id=kwargs["worker_id"],
            base=base,
            publish_at=kwargs.get("publish_at") or None,
            no_schedule=kwargs.get("no_schedule", False),
            privacy=kwargs.get("privacy"),
            upload_retries=kwargs.get("upload_retries", 3),
            retry_delay=kwargs.get("retry_delay", 30.0),
            tags=kwargs.get("tags"),
            require_claim=True,
        )
        from uploader.cache_signals import bump

        bump("queue")

    thread = threading.Thread(target=_run, name=f"upload-{kwargs['job_id']}", daemon=True)
    thread.start()


def _start_cloud_run_job(
    channel_id: str,
    job_id: str,
    worker_id: str,
    *,
    publish_at: str = "",
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
) -> str:
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("GOOGLE_PROJECT_ID", "").strip()
    )
    region = os.environ.get("UPLOADER_CLOUD_RUN_REGION", "northamerica-northeast2").strip()
    job_name = os.environ.get("UPLOADER_UPLOAD_JOB_NAME", "youtube-uploader-upload").strip()
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required to dispatch Cloud Run upload jobs")
    if not job_name:
        raise RuntimeError("UPLOADER_UPLOAD_JOB_NAME is required")

    env = [
        {"name": "UPLOADER_JOB_CHANNEL", "value": channel_id},
        {"name": "UPLOADER_JOB_ID", "value": job_id},
        {"name": "UPLOADER_WORKER_ID", "value": worker_id},
        {"name": "UPLOADER_UPLOAD_RETRIES", "value": str(upload_retries)},
        {"name": "UPLOADER_RETRY_DELAY", "value": str(retry_delay)},
    ]
    if publish_at:
        env.append({"name": "UPLOADER_JOB_PUBLISH_AT", "value": publish_at})
    if no_schedule:
        env.append({"name": "UPLOADER_NO_SCHEDULE", "value": "1"})
    if privacy:
        env.append({"name": "UPLOADER_JOB_PRIVACY", "value": privacy})
    if tags:
        env.append({"name": "UPLOADER_JOB_TAGS", "value": ",".join(tags)})

    parent = f"projects/{project}/locations/{region}/jobs/{job_name}"
    url = f"https://run.googleapis.com/v2/{parent}:run"

    body = {
        "overrides": {
            "containerOverrides": [
                {
                    "args": [
                        "upload-job",
                        "--channel",
                        channel_id,
                        "--job-id",
                        job_id,
                        "--worker-id",
                        worker_id,
                    ],
                    "env": env,
                }
            ]
        }
    }

    token = _gcp_access_token()
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloud Run Job dispatch failed ({e.code}): {detail}") from e

    return str(payload.get("name", "") or payload.get("metadata", {}).get("name", ""))


def _gcp_access_token() -> str:
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as e:
        raise RuntimeError(
            "google-auth is required for Cloud Run Job dispatch (included with google-api-python-client)"
        ) from e

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    if not credentials.token:
        raise RuntimeError("Could not obtain GCP access token for Cloud Run Job dispatch")
    return credentials.token
