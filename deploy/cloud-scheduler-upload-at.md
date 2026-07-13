# Cloud Scheduler — one-shot upload_at dispatch

When a job is registered/staged with a future ``upload_at`` and
``UPLOADER_UPLOAD_AT_SCHEDULER=1``, the API creates a **one-shot** Cloud
Scheduler HTTP job that calls:

```http
POST /v1/channels/{channel_id}/jobs/{job_id}/dispatch-at
X-API-Key: $UPLOADER_API_KEY
```

at that UTC time. The handler uploads that single pending job (same worker path
as ``POST .../jobs/{job_id}/upload``), then deletes the Scheduler job.

## Behavior

| `upload_at` | Scheduler enabled? | Result |
|-------------|--------------------|--------|
| omitted | — | No cron; normal queue |
| in the past (or ≤60s ahead) | any | Status `ready` — no cron; eligible for next `/runs` |
| future | `0` / unset | Status `disabled` — stored as queue gate only |
| future | `1` | Status `scheduled` — Cloud Scheduler one-shot armed |
| + `upload_now: true` | — | Immediate upload; scheduling skipped |

Response fields on register/stage: `upload_at_schedule_status`,
`upload_at_scheduler_job`, `upload_at_schedule_message`.

## Edge cases handled by `dispatch-at`

| Situation | HTTP | Scheduler job |
|-----------|------|---------------|
| Job missing | 200 no-op | Deleted |
| Not pending (uploading/uploaded/failed) | 200 no-op | Deleted |
| Called before `upload_at` (beyond 60s grace) | **409** | **Kept** |
| Channel OAuth missing | **400** | **Kept** (retries can succeed after reauth) |
| Worker dispatch failed | **503** | **Kept** (Cloud Scheduler retries) |
| Success | 200 `dispatched` | Deleted |
| `DELETE .../jobs/{id}` | — | Deleted |

Cloud Scheduler cron is **minute-granularity** (seconds on `upload_at` round
**up** to the next minute). Cron expressions recur yearly; the callback deletes
the job after a successful fire so it does not repeat next year. Late retries
are idempotent.

## Env (API service)

```env
UPLOADER_UPLOAD_AT_SCHEDULER=1
UPLOADER_API_PUBLIC_URL=https://your-uploader-api.run.app
# Required when dashboard/API auth is enabled — Scheduler sends this header:
UPLOADER_API_KEY=...
GOOGLE_CLOUD_PROJECT=youtube-uploader-499603
# Cloud Scheduler location (must be a supported Scheduler region, often us-central1)
UPLOADER_CLOUD_SCHEDULER_LOCATION=us-central1
# Optional OIDC *in addition to* the API key (AuthMiddleware still needs X-API-Key):
# UPLOADER_SCHEDULER_OIDC_SA=reconcile-cron@$PROJECT_ID.iam.gserviceaccount.com
```

The Cloud Run runtime service account needs:

- `roles/cloudscheduler.admin` (or a custom role that can create/delete jobs)
- Permission to invoke Cloud Run if using OIDC

## Enable API

```bash
gcloud services enable cloudscheduler.googleapis.com --project="$PROJECT_ID"
```

## Manual test

```bash
# Register with a near-future upload_at
curl -X POST "$API_URL/v1/channels/justcavefire/jobs/register" \
  -H "X-API-Key: $UPLOADER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Scheduled pickup",
    "video_uri": "s3://bucket/path/video.mp4",
    "upload_at": "2026-08-01T06:00:00Z",
    "publish_at": "2026-08-01T12:00:00Z"
  }'

# Response includes upload_at_schedule_status=scheduled (when enabled)
# and upload_at_scheduler_job=projects/.../jobs/ua-...

# Force the callback early (should 409 if still before upload_at):
curl -X POST "$API_URL/v1/channels/justcavefire/jobs/$JOB_ID/dispatch-at" \
  -H "X-API-Key: $UPLOADER_API_KEY"
```

## Relationship to reconcile / polling crons

| Cron | Purpose |
|------|---------|
| **upload_at one-shots** (this doc) | Per-video; auto-created on register/stage |
| **Reconcile** (`deploy/cloud-scheduler-reconcile.md`) | Every 10 min — repair stuck *Uploading now* |
| **Polling `POST .../runs`** | Optional nightly backlog drain; still useful as a safety net |
