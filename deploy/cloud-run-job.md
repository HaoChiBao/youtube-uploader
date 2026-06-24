# Cloud Run Job — parallel upload workers

Each **upload worker** runs as a separate **Cloud Run Job** task: one queued video per execution, with an R2 lock so two workers never take the same job.

The dashboard **Upload** buttons dispatch workers with `"parallel": true` and poll `GET /v1/uploads/active` for live progress bars.

---

## 1. Build image (same as API)

Use the repo `Dockerfile` — workers run `uploader upload-job` instead of `uploader-api`.

---

## 2. Create the upload job

```bash
export PROJECT_ID=youtube-uploader-499603
export REGION=northamerica-northeast2
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/uploader/uploader-api:latest"
export UPLOAD_JOB=youtube-uploader-upload

gcloud run jobs create "$UPLOAD_JOB" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --image="$IMAGE" \
  --command=uploader \
  --args=upload-job \
  --memory=4Gi \
  --cpu=2 \
  --task-timeout=86400 \
  --max-retries=0 \
  --set-env-vars="UPLOADER_WORKER_BACKEND=cloudrun" \
  --set-secrets="GOOGLE_CLIENT_SECRET=google-client-secret:latest,CLOUDFLARE_R2_SECRET_ACCESS_KEY=r2-secret-key:latest"
```

Copy the same R2 / Google OAuth env vars from the API service (`deploy/cloudrun.env.example`).

**Recommended worker resources**

| Setting | Value | Why |
|---------|-------|-----|
| `memory` | 4Gi+ | Full video in `/tmp` during download + YouTube upload |
| `task-timeout` | 86400 (24h) | Long 1-hour+ encodes can take hours to upload |
| `max-retries` | 0 | Avoid duplicate YouTube uploads on retry |

---

## 3. Grant the API permission to run jobs

The **uploader-api** Cloud Run service account needs:

```bash
export API_SA=$(gcloud run services describe youtuber-uploader-app \
  --region="$REGION" --format='value(spec.template.spec.serviceAccountName)')

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$API_SA" \
  --role="roles/run.developer"
```

(`roles/run.developer` includes `run.jobs.run` and overrides.)

Enable the API if needed:

```bash
gcloud services enable run.googleapis.com
```

---

## 4. Configure the API service

Add to the **API** Cloud Run service env:

```env
UPLOADER_WORKER_BACKEND=cloudrun
UPLOADER_UPLOAD_JOB_NAME=youtube-uploader-upload
UPLOADER_CLOUD_RUN_REGION=northamerica-northeast2
GOOGLE_CLOUD_PROJECT=youtube-uploader-499603
UPLOADER_MAX_PARALLEL_UPLOADS=5
```

---

## 5. Local dev (no Cloud Run Job)

```env
UPLOADER_WORKER_BACKEND=local
```

Parallel uploads spawn **background threads** in the API process (fine for dev; use Cloud Run Jobs in production).

---

## 6. Manual worker test

```bash
uploader upload-job --channel justcavefire --job-id mv_20260624_test
```

Or with env (matches Cloud Run Job overrides):

```bash
export UPLOADER_JOB_CHANNEL=justcavefire
export UPLOADER_JOB_ID=mv_20260624_test
export UPLOADER_WORKER_ID=manual_test
uploader upload-job --channel "$UPLOADER_JOB_CHANNEL" --job-id "$UPLOADER_JOB_ID"
```

---

## How locking works

1. Dispatcher claims job → writes `state/{channel}/locks/{job_id}.lock` on R2 (If-None-Match)
2. Registry row → `status: uploading` + progress fields in `extra`
3. Worker downloads → YouTube upload → archive → `status: uploaded`
4. Lock deleted on success/failure
5. Stale locks (no progress for 10+ min) can be reclaimed

Poll progress: `GET /v1/uploads/active` or dashboard **Uploading now** section.
