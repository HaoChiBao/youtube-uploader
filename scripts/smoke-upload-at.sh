#!/usr/bin/env bash
# Smoke-test per-job upload_at Cloud Scheduler on a live uploader-api.
#
# Usage:
#   UPLOADER_API_URL=https://… UPLOADER_API_KEY=… \
#   CHANNEL=nappabeats VIDEO_URI=s3://…/video.mp4 \
#   ./scripts/smoke-upload-at.sh
#
# Optional: THUMBNAIL_URI=s3://…/thumbnail.png
#
# Creates a future-scheduled job, asserts Cloud Scheduler was armed,
# checks early dispatch-at returns 409, then deletes the job (and scheduler).

set -euo pipefail

need() {
  [[ -n "${!1:-}" ]] || { echo "error: set $1" >&2; exit 1; }
}

need UPLOADER_API_URL
need UPLOADER_API_KEY
need CHANNEL
need VIDEO_URI

API="${UPLOADER_API_URL%/}"
KEY="$UPLOADER_API_KEY"
export JOB_ID="${JOB_ID:-smoke_upload_at_$(date -u +%Y%m%d_%H%M%S)}"
export FUTURE
export PUBLISH
FUTURE="$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(days=14)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
PUBLISH="$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(days=14,hours=6)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
export FUTURE PUBLISH

BODY="$(JOB_ID="$JOB_ID" FUTURE="$FUTURE" PUBLISH="$PUBLISH" VIDEO_URI="$VIDEO_URI" THUMBNAIL_URI="${THUMBNAIL_URI:-}" python3 - <<'PY'
import json, os
d = {
  "job_id": os.environ["JOB_ID"],
  "title": "SMOKE TEST — delete me (upload_at scheduler)",
  "description": "Automated smoke test; safe to delete",
  "video_uri": os.environ["VIDEO_URI"],
  "upload_at": os.environ["FUTURE"],
  "publish_at": os.environ["PUBLISH"],
  "privacy": "private",
}
thumb = os.environ.get("THUMBNAIL_URI", "").strip()
if thumb:
  d["thumbnail_uri"] = thumb
print(json.dumps(d))
PY
)"

echo "==> POST $API/v1/channels/$CHANNEL/jobs/register (upload_at=$FUTURE)"
RESP="$(curl -fsS -X POST "$API/v1/channels/$CHANNEL/jobs/register" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "$BODY")"
echo "$RESP" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('job_id=', d.get('job_id'))
print('upload_at_schedule_status=', d.get('upload_at_schedule_status'))
print('upload_at_scheduler_job=', d.get('upload_at_scheduler_job'))
print('message=', d.get('upload_at_schedule_message'))
assert d.get('upload_at_schedule_status') == 'scheduled', d
"

echo "==> Early dispatch-at (expect 409)"
CODE="$(curl -sS -o /tmp/smoke_dispatch_at.json -w '%{http_code}' \
  -X POST "$API/v1/channels/$CHANNEL/jobs/$JOB_ID/dispatch-at" \
  -H "X-API-Key: $KEY")"
echo "http=$CODE $(cat /tmp/smoke_dispatch_at.json)"
[[ "$CODE" == "409" ]] || { echo "error: expected 409" >&2; exit 1; }

echo "==> DELETE job $JOB_ID"
curl -fsS -X DELETE "$API/v1/channels/$CHANNEL/jobs/$JOB_ID" -H "X-API-Key: $KEY"
echo
echo "OK — register armed scheduler, early dispatch blocked, delete cleaned up."
