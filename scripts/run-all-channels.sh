#!/usr/bin/env bash
# Cron entrypoint: process pending uploads for every channel in channels.yaml.
# Usage: scripts/run-all-channels.sh
# Or stagger per channel (recommended for rate limits):
#   0 3 * * * /path/to/youtube-uploader/scripts/run-channel.sh justcavefire
#   0 4 * * * /path/to/youtube-uploader/scripts/run-channel.sh mmmactually

set -euo pipefail

RETRIES="${UPLOADER_UPLOAD_RETRIES:-5}"
LOG_DIR="${UPLOADER_LOG_DIR:-logs}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/uploader-all-$(date +%Y%m%d).log"

{
  echo "=== $(date -Iseconds) uploader run-all ==="
  uploader run-all --upload-retries "${RETRIES}"
  echo "=== exit $?"
} >> "${LOG_FILE}" 2>&1
