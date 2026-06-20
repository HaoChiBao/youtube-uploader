# Deploy uploader-api to Google Cloud Run (build + push + deploy)
# Usage: edit variables below, then: .\scripts\deploy-cloudrun.ps1

$ErrorActionPreference = "Stop"

$PROJECT_ID = "your-gcp-project"
$REGION = "us-central1"
$SERVICE = "uploader-api"
$REPO = "uploader"
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/${SERVICE}:latest"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "Project: $PROJECT_ID  Region: $REGION  Service: $SERVICE"

gcloud config set project $PROJECT_ID

$repoExists = gcloud artifacts repositories describe $REPO --location=$REGION 2>$null
if (-not $repoExists) {
    gcloud artifacts repositories create $REPO `
        --repository-format=docker `
        --location=$REGION `
        --description="YouTube Uploader API"
}

Write-Host "Building and pushing image..."
gcloud builds submit --tag $IMAGE

Write-Host "Deploying to Cloud Run..."
gcloud run deploy $SERVICE `
    --image=$IMAGE `
    --region=$REGION `
    --platform=managed `
    --allow-unauthenticated `
    --port=8080 `
    --memory=1Gi `
    --cpu=1 `
    --timeout=3600 `
    --concurrency=1 `
    --min-instances=1 `
    --max-instances=3 `
    --startup-probe="httpGet.path=/v1/health,httpGet.port=8080,initialDelaySeconds=5,timeoutSeconds=5,periodSeconds=10,failureThreshold=3"

$url = gcloud run services describe $SERVICE --region=$REGION --format="value(status.url)"
Write-Host ""
Write-Host "Deployed: $url"
Write-Host "Set UPLOADER_API_PUBLIC_URL=$url in Cloud Run env vars."
Write-Host "Add OAuth redirect: ${url}/v1/oauth/callback"
