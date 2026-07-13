# GitHub Actions → Cloud Run CI/CD

Push (or merge) to **`main`** builds the Docker image, pushes it to Artifact
Registry, and deploys the production Cloud Run service
(`youtuber-uploader-app`) plus the upload worker Job when present.

Workflow file: `.github/workflows/deploy-cloud-run.yml`

## What gets deployed

| Resource | Default |
|----------|---------|
| Project | `youtube-uploader-499603` |
| Region | `northamerica-northeast2` |
| Cloud Run service | `youtuber-uploader-app` |
| Cloud Run Job (workers) | `youtube-uploader-upload` |
| Image | `northamerica-northeast2-docker.pkg.dev/…/uploader/uploader-api:<sha>` |

Env vars / secrets already on the Cloud Run service are **preserved** — the
workflow only updates the container image.

## One-time setup (Workload Identity Federation)

**Fast path (recommended):** from a machine where you are logged into `gcloud` and `gh`
with admin access:

```bash
./scripts/setup-github-cicd.sh
```

That script creates the deploy SA, WIF pool/provider, IAM bindings, GitHub
Environment `production`, and the `GCP_*` secrets/variables.

Or follow the manual steps below.

### 1. Enable APIs

```bash
export PROJECT_ID=youtube-uploader-499603
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
export REGION=northamerica-northeast2
export GITHUB_ORG=HaoChiBao          # GitHub org or user
export GITHUB_REPO=youtube-uploader  # repo name

gcloud config set project "$PROJECT_ID"
gcloud services enable \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com
```

### 2. Create a deploy service account

```bash
gcloud iam service-accounts create github-deploy \
  --display-name="GitHub Actions Cloud Run deploy"

export DEPLOY_SA="github-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

# Push images
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}" \
  --role="roles/artifactregistry.writer"

# Deploy / update Cloud Run service + jobs
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}" \
  --role="roles/run.admin"

# Allow the deploy SA to act as the Cloud Run runtime SA
export RUNTIME_SA="$(gcloud run services describe youtuber-uploader-app \
  --region="$REGION" --format='value(spec.template.spec.serviceAccountName)')"
# If empty, runtime uses the default compute SA:
# RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${DEPLOY_SA}" \
  --role="roles/iam.serviceAccountUser"
```

### 3. Create Workload Identity Pool + provider

```bash
gcloud iam workload-identity-pools create github \
  --location=global \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global \
  --workload-identity-pool=github \
  --display-name="GitHub" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_ORG}/${GITHUB_REPO}'"

export POOL_ID="$(gcloud iam workload-identity-pools describe github \
  --location=global --format='value(name)')"
export PROVIDER_ID="$(gcloud iam workload-identity-pools providers describe github \
  --location=global --workload-identity-pool=github --format='value(name)')"

echo "PROVIDER: $PROVIDER_ID"
# projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github
```

### 4. Allow GitHub to impersonate the deploy SA

Restrict to this repo’s `main` branch (and optional `workflow_dispatch` from main):

```bash
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"
```

For a tighter binding (main only), use:

```bash
# Optional: also map attribute.ref in the provider (already in attribute-mapping above)
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"
```

### 5. GitHub secrets & environment

In the repo → **Settings → Secrets and variables → Actions**:

| Type | Name | Value |
|------|------|--------|
| Secret | `GCP_WORKLOAD_IDENTITY_PROVIDER` | Full provider resource name from step 3 (`projects/…/providers/github`) |
| Secret | `GCP_SERVICE_ACCOUNT` | `github-deploy@youtube-uploader-499603.iam.gserviceaccount.com` |

Create a GitHub **Environment** named `production` (the workflow uses
`environment: production`). Optionally require reviewers before deploy.

Optional repository **variables** (override defaults):

| Variable | Default |
|----------|---------|
| `GCP_PROJECT_ID` | `youtube-uploader-499603` |
| `GCP_REGION` | `northamerica-northeast2` |
| `CLOUD_RUN_SERVICE` | `youtuber-uploader-app` |
| `CLOUD_RUN_UPLOAD_JOB` | `youtube-uploader-upload` |
| `ARTIFACT_REGISTRY_REPO` | `uploader` |
| `ARTIFACT_IMAGE_NAME` | `uploader-api` |

### 6. Confirm Artifact Registry exists

```bash
gcloud artifacts repositories describe uploader --location="$REGION" \
  || gcloud artifacts repositories create uploader \
       --repository-format=docker \
       --location="$REGION" \
       --description="YouTube Uploader API"
```

## Usage

1. Merge a PR into `main` (or push to `main`).
2. GitHub Actions → **Deploy Cloud Run** runs automatically.
3. Health check hits `GET /v1/health` on the live URL.
4. Manual re-deploy: Actions → **Deploy Cloud Run** → **Run workflow**.

## Notes

- **Secrets stay in Cloud Run** — do not put R2/OAuth keys in GitHub for this workflow.
- **PR CI** (`.github/workflows/ci.yml`) runs pytest; it does not deploy.
- First deploy after enabling WIF: run the workflow manually once to confirm auth.
- If the upload Job does not exist yet, the workflow skips that step (see `deploy/cloud-run-job.md`).
