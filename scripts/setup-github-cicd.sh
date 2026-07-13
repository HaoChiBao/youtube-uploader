#!/usr/bin/env bash
# One-shot setup: GCP Workload Identity Federation + GitHub production env/secrets
# for .github/workflows/deploy-cloud-run.yml
#
# Prerequisites (run on your machine, not in CI):
#   - gcloud authenticated with Owner/Editor on the project
#   - gh authenticated with admin on HaoChiBao/youtube-uploader
#
# Usage:
#   ./scripts/setup-github-cicd.sh
#   PROJECT_ID=... GITHUB_REPO=... ./scripts/setup-github-cicd.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-youtube-uploader-499603}"
REGION="${REGION:-northamerica-northeast2}"
SERVICE="${SERVICE:-youtuber-uploader-app}"
GITHUB_ORG="${GITHUB_ORG:-HaoChiBao}"
GITHUB_REPO="${GITHUB_REPO:-youtube-uploader}"
REPO_SLUG="${GITHUB_ORG}/${GITHUB_REPO}"
DEPLOY_SA_NAME="${DEPLOY_SA_NAME:-github-deploy}"
POOL_ID="${POOL_ID:-github}"
PROVIDER_ID="${PROVIDER_ID:-github}"
AR_REPO="${AR_REPO:-uploader}"
GH_ENVIRONMENT="${GH_ENVIRONMENT:-production}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' is required on PATH" >&2
    exit 1
  }
}

need gcloud
need gh
need curl
need python3

echo "==> Checking auth"
ACTIVE="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1 || true)"
if [[ -z "${ACTIVE}" ]]; then
  echo "error: no active gcloud account. Run: gcloud auth login" >&2
  exit 1
fi
echo "    gcloud: ${ACTIVE}"

if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "error: gh is not logged in. Run: gh auth login" >&2
  exit 1
fi
echo "    gh: ok for ${REPO_SLUG}"

gcloud config set project "${PROJECT_ID}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Enabling APIs"
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project="${PROJECT_ID}"

echo "==> Ensuring Artifact Registry repo '${AR_REPO}' in ${REGION}"
if ! gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="YouTube Uploader API"
fi

echo "==> Ensuring deploy service account ${DEPLOY_SA}"
if ! gcloud iam service-accounts describe "${DEPLOY_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOY_SA_NAME}" \
    --display-name="GitHub Actions Cloud Run deploy"
fi

bind_project_role() {
  local role="$1"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
}

echo "==> Granting deploy SA roles"
bind_project_role "roles/artifactregistry.writer"
bind_project_role "roles/run.admin"

RUNTIME_SA="$(gcloud run services describe "${SERVICE}" \
  --region="${REGION}" \
  --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
if [[ -z "${RUNTIME_SA}" ]]; then
  RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  echo "    (service ${SERVICE} missing or has no custom SA — using ${RUNTIME_SA})"
else
  echo "    runtime SA: ${RUNTIME_SA}"
fi

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${DEPLOY_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet >/dev/null

echo "==> Ensuring Workload Identity Pool '${POOL_ID}'"
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --location=global \
    --display-name="GitHub Actions"
fi

POOL_RESOURCE="$(gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --location=global --format='value(name)')"

echo "==> Ensuring OIDC provider '${PROVIDER_ID}'"
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="GitHub" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='${REPO_SLUG}'"
else
  gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='${REPO_SLUG}'" \
    --quiet
fi

PROVIDER_RESOURCE="$(gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --location=global --workload-identity-pool="${POOL_ID}" --format='value(name)')"

echo "==> Allowing GitHub repo ${REPO_SLUG} to impersonate deploy SA"
gcloud iam service-accounts add-iam-policy-binding "${DEPLOY_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository/${REPO_SLUG}" \
  --quiet >/dev/null

echo "==> Creating GitHub Environment '${GH_ENVIRONMENT}'"
# gh api PUT creates/updates an environment
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${REPO_SLUG}/environments/${GH_ENVIRONMENT}" \
  -f wait_timer=0 \
  -F reviewers='[]' \
  -F deployment_branch_policy='null' \
  >/dev/null

echo "==> Setting GitHub Actions secrets (repo-level)"
gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER \
  --repo "${REPO_SLUG}" \
  --body "${PROVIDER_RESOURCE}"
gh secret set GCP_SERVICE_ACCOUNT \
  --repo "${REPO_SLUG}" \
  --body "${DEPLOY_SA}"

# Also set as environment secrets so environment: production can resolve them
echo "==> Setting GitHub Environment secrets on '${GH_ENVIRONMENT}'"
gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER \
  --repo "${REPO_SLUG}" \
  --env "${GH_ENVIRONMENT}" \
  --body "${PROVIDER_RESOURCE}"
gh secret set GCP_SERVICE_ACCOUNT \
  --repo "${REPO_SLUG}" \
  --env "${GH_ENVIRONMENT}" \
  --body "${DEPLOY_SA}"

echo "==> Setting optional GitHub variables (defaults for the workflow)"
gh variable set GCP_PROJECT_ID --repo "${REPO_SLUG}" --body "${PROJECT_ID}" || true
gh variable set GCP_REGION --repo "${REPO_SLUG}" --body "${REGION}" || true
gh variable set CLOUD_RUN_SERVICE --repo "${REPO_SLUG}" --body "${SERVICE}" || true

echo
echo "Done."
echo "  Provider: ${PROVIDER_RESOURCE}"
echo "  Deploy SA: ${DEPLOY_SA}"
echo "  Environment: ${GH_ENVIRONMENT}"
echo
echo "Next:"
echo "  1. Merge PR #2 (CI/CD workflow) if not already merged."
echo "  2. Actions → Deploy Cloud Run → Run workflow (or push to main)."
echo "  3. Confirm GET /v1/health on the Cloud Run URL succeeds."
