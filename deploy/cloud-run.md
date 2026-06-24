# Deploy to Google Cloud Run

This guide runs **`uploader-api`** (FastAPI dashboard + HTTP API) on Cloud Run. The CLI is not required on the server — use it locally for one-off tasks, or call the API from your pipeline.

**Before you start**

- [Google Cloud project](https://console.cloud.google.com/) with billing enabled
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and logged in
- **Cloudflare R2** configured (all durable state lives in R2; the container is ephemeral)
- Run `uploader storage init` locally at least once so `config/channels.yaml` and bucket layout exist in R2

---

## 1. Enable APIs and pick a region

```bash
export PROJECT_ID=your-gcp-project
export REGION=us-central1
export SERVICE=uploader-api
export REPO=uploader

gcloud config set project "$PROJECT_ID"

gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

---

## 2. Create Artifact Registry and build the image

From the repo root:

```bash
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="YouTube Uploader API"

export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:latest"

gcloud builds submit --tag "$IMAGE"
```

Or build locally and push:

```bash
docker build -t "$IMAGE" .
gcloud auth configure-docker "$REGION-docker.pkg.dev"
docker push "$IMAGE"
```

---

## 3. Google OAuth (Web client)

In [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials):

1. Create or edit a **Web application** OAuth client.
2. Add **Authorized redirect URI** (after first deploy you’ll know the URL):

   `https://YOUR-SERVICE-URL/v1/oauth/callback`

3. Copy `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_PROJECT_ID`.

---

## 4. Deploy to Cloud Run

Use the template in `deploy/cloudrun.env.example`. Fill in secrets, then:

```bash
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=1Gi \
  --cpu=1 \
  --timeout=3600 \
  --concurrency=1 \
  --min-instances=1 \
  --max-instances=3 \
  --startup-probe=httpGet.path=/v1/health,httpGet.port=8080,initialDelaySeconds=5,timeoutSeconds=5,periodSeconds=10,failureThreshold=3 \
  --set-env-vars="UPLOADER_API_PUBLIC_URL=https://PLACEHOLDER,UPLOADER_SESSION_SECURE=1" \
  --set-secrets="GOOGLE_CLIENT_SECRET=google-client-secret:latest,CLOUDFLARE_R2_SECRET_ACCESS_KEY=r2-secret-key:latest"
```

**Important flags**

| Flag | Why |
|------|-----|
| `--allow-unauthenticated` | Lets browsers hit the dashboard; app auth (`UPLOADER_DASHBOARD_PASSWORD` / API key) protects routes |
| `--min-instances=1` | Upload runs use in-process background tasks — avoid scale-to-zero mid-upload |
| `--concurrency=1` | One request at a time per instance (safer for large uploads) |
| `--timeout=3600` | Max 60 minutes (Cloud Run limit) for long YouTube uploads triggered from the API |
| `--memory=1Gi` | Headroom for video buffering during ingest |

Set remaining env vars in the [Cloud Run console](https://console.cloud.google.com/run) → your service → **Edit & deploy new revision** → **Variables & secrets**, or use `--env-vars-file=deploy/cloudrun.env` (do not commit real secrets).

After deploy, copy the service URL and update:

```bash
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-env-vars="UPLOADER_API_PUBLIC_URL=https://YOUR-ACTUAL-URL"
```

Add that same URL to Google OAuth redirect URIs.

---

## 5. Verify

```bash
export URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')

curl -s "$URL/v1/health"
# {"status":"ok","version":"..."}

curl -s "$URL/v1/auth/session"
# {"auth_enabled":true,"authenticated":false}

curl -s -o /dev/null -w "%{http_code}" "$URL/v1/dashboard"
# 401 when auth is enabled
```

Open `$URL` in a browser → black login screen → enter `UPLOADER_DASHBOARD_PASSWORD`.

Pipeline calls:

```bash
curl -H "X-API-Key: YOUR_KEY" "$URL/v1/dashboard"
```

---

## 6. Assembler integration — required endpoints

**Queue (assembler, per video):**

```bash
curl -X POST "$URL/v1/channels/nappabeats/jobs/register" \
  -H "X-API-Key: $UPLOADER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "mv_20260624_061500",
    "title": "Generated title",
    "description": "…",
    "video_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_video.mp4",
    "thumbnail_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_thumbnail.png"
  }'
```

Set Cloud Run env: `ASSEMBLY_R2_BUCKET=music-assembly-data` (and grant read access or `ASSEMBLY_R2_*` keys).

**Upload to YouTube (cron or manual — register does not upload):**

```bash
curl -X POST "$URL/v1/channels/nappabeats/runs" \
  -H "X-API-Key: $UPLOADER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"count": 1, "upload_retries": 5}'
```

Poll: `GET $URL/v1/runs/{run_id}`

Verify assembler bucket: `GET $URL/v1/capabilities` → `assembly_integration.assembly_r2`

---

## 7. Large video uploads (multipart alternative)

Cloud Run limits **HTTP request bodies to 32 MB**. Do not POST huge files to `/v1/channels/{id}/jobs` through Cloud Run.

**Recommended:** assembler writes to R2, then `POST .../jobs/register` (see §6).

---

## 8. Scheduled uploads (optional)

Cloud Run is not ideal as a cron worker. For nightly uploads after assembly:

- **Cloud Scheduler** → HTTP `POST /v1/channels/{id}/runs` with `X-API-Key` and `{"count": 1}`, or
- Run the CLI on a small **Compute Engine** VM on a schedule

Stagger channels to stay under YouTube daily quota (~6 uploads/day default).

---

## 9. Updating

```bash
gcloud builds submit --tag "$IMAGE"
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION"
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| **login.html missing** or blank dashboard | Redeploy with the latest image. The container must set `UPLOADER_STATIC_DIR=/app/api/static` (default in `Dockerfile`) so `index.html` is found after `pip install`. |
| OAuth redirect mismatch | `UPLOADER_API_PUBLIC_URL` must exactly match the Cloud Run URL (no trailing slash). Redirect URI in Google Console must match `{URL}/v1/oauth/callback`. |
| Login works locally but not on Cloud Run | Set `UPLOADER_SESSION_SECURE=1` (HTTPS). |
| Upload run disappears | Ensure `--min-instances=1`; check logs in Cloud Logging. |
| 502 on startup | Confirm R2 env vars; service needs R2 for config when no local disk. |
| Channels empty | Run `uploader storage init` locally or `POST /v1/storage/init` with API key after R2 is configured. |
