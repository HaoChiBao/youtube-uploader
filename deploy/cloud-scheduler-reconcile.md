# Cloud Scheduler — upload reconcile cron

Repairs stuck **Uploading now** jobs: finalizes uploads that reached YouTube, archives completed jobs, or returns orphaned workers to the pending queue.

## Endpoint

```http
POST /v1/uploads/reconcile
X-API-Key: $UPLOADER_API_KEY
```

Query params:

| Param | Default | Description |
|-------|---------|-------------|
| `dry_run` | `false` | Report actions without changing registry |
| `channel` | (all) | Limit to one channel id |

## Env (API service)

```env
UPLOADER_RECONCILE_STALE_SECONDS=180   # no progress → eligible (default 3 min)
UPLOADER_RECONCILE_FAIL_SECONDS=7200   # mark failed after 2 h with no recovery
```

## Create scheduler job (every 10 minutes)

```bash
export PROJECT_ID=youtube-uploader-499603
export REGION=northamerica-northeast2
export API_URL=https://youtuber-uploader-app-17161979106.northamerica-northeast2.run.app
export SCHEDULER_SA=reconcile-cron@$PROJECT_ID.iam.gserviceaccount.com

gcloud scheduler jobs create http upload-reconcile \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --schedule="*/10 * * * *" \
  --uri="$API_URL/v1/uploads/reconcile" \
  --http-method=POST \
  --headers="X-API-Key=$UPLOADER_API_KEY" \
  --oidc-service-account-email="$SCHEDULER_SA" \
  --oidc-token-audience="$API_URL"
```

Grant the scheduler service account permission to invoke Cloud Run if using OIDC instead of API key.

## CLI (manual / local cron)

```bash
uploader reconcile-uploads
uploader reconcile-uploads --dry-run
uploader reconcile-uploads --channel justcavefire
```

## What it does

1. **Uploading + assets already in `uploaded/`** → mark uploaded, fix registry
2. **Uploading + looks complete (≥93% / done phase) + stale lock** → match title on YouTube → finalize + archive
3. **Uploaded in registry but still in `queue/`** → archive move
4. **Uploading + stale + no YouTube match** → reset to pending (retry)
5. **Uploading + very stale (2h+)** → mark failed
