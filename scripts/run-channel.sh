#!/usr/bin/env bash
# Cron entrypoint: process pending uploads for one channel.
# Usage: scripts/run-channel.sh channel-a
# Crontab example:
#   0 3 * * * /path/to/youtube-uploader/scripts/run-channel.sh channel-a

set -euo pipefail

CHANNEL="${1:-${UPLOADER_DEFAULT_CHANNEL:-channel-a}}"
RETRIES="${UPLOADER_UPLOAD_RETRIES:-5}"
LOG_DIR="${UPLOADER_LOG_DIR:-logs}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/uploader-${CHANNEL}-$(date +%Y%m%d).log"

{
  echo "=== $(date -Iseconds) uploader run --channel ${CHANNEL} ==="
  uploader run --channel "${CHANNEL}" --upload-retries "${RETRIES}"
  echo "=== exit $?"
} >> "${LOG_FILE}" 2>&1
