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

## Edge cases handled by `dispatch-at`

- Job already uploaded / uploading / missing → no-op (200), scheduler cleaned
- Called before `upload_at` (beyond 60s grace) → **409**, scheduler **kept**
- Channel OAuth missing → 400
- Deleting the queue job cancels its Scheduler job

Cloud Scheduler cron recurs yearly; the callback always deletes the job after a
successful (or terminal) fire so it does not repeat next year. Late retries are
idempotent.

## Env (API service)

```env
UPLOADER_UPLOAD_AT_SCHEDULER=1
UPLOADER_API_PUBLIC_URL=https://your-uploader-api.run.app
UPLOADER_API_KEY=...
GOOGLE_CLOUD_PROJECT=youtube-uploader-499603
# Cloud Scheduler location (must be a supported Scheduler region, often us-central1)
UPLOADER_CLOUD_SCHEDULER_LOCATION=us-central1
# Optional OIDC (in addition to or instead of API key header)
# UPLOADER_SCHEDULER_OIDC_SA=reconcile-cron@$PROJECT_ID.iam.gserviceaccount.com
```

The Cloud Run runtime service account needs:

- `roles/cloudscheduler.admin` (or a custom role that can create/delete jobs)
- Permission to invoke itself if using OIDC

## IAM / queue setup

No Cloud Tasks queue is required. Ensure the Cloud Scheduler API is enabled:

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

## Relationship to reconcile cron

The every-10-minute **reconcile** cron (`deploy/cloud-scheduler-reconcile.md`) is
unrelated — it repairs stuck *Uploading now* rows. upload_at one-shots are
per-video and created automatically on register/stage.
