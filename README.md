# YouTube Uploader

Standalone microservice that uploads pre-rendered music videos to YouTube, schedules publish times, and supports multiple YouTube channels. Split from [ai-music-assembler](https://github.com/) which handles video rendering only.

## What it does

- YouTube OAuth (one Google client secret, one refresh token per channel)
- Resumable video upload via YouTube Data API v3
- Custom thumbnail upload (best-effort; never fails the video upload)
- Schedule publish with `publishAt` (RFC3339 UTC)
- Upload registry queue: `pending` → `uploading` → `uploaded` | `failed`
- Batch processing with staggered publish times
- Retry transient failures (timeouts, 408/429/5xx) with linear backoff
- List channel videos; filter to scheduled only
- Multi-channel via `config/channels.yaml`
- Resolve video/thumbnail from local paths, `file://`, or `s3://` URIs

## What it does not do

- No FFmpeg, encoding, or thumbnail generation
- No title/description generation (receives final strings from upstream)

## Quick start

### 1. Install

```bash
pip install .
# Optional S3 support:
pip install '.[s3]'
```

### 2. Configure

```bash
cp config/channels.yaml.example config/channels.yaml
cp .env.example .env
# Edit config/channels.yaml with your channel ids and paths
```

### 3. Google Cloud setup

1. Create a Google Cloud project and enable **YouTube Data API v3**
2. Configure the OAuth consent screen
3. Create an OAuth client (Desktop app for local dev)
4. Register redirect URI: `http://localhost:8080` (no trailing slash)
5. Download the client secret JSON to `secrets/shared/client_secret.json`

### 4. Authorize a channel

```bash
uploader auth --channel channel-a
```

Sign in as the YouTube channel owner. The refresh token is saved to `secrets/channel-a/youtube_token.json`.

### 5. Enqueue and upload

```bash
# Add a test job
uploader enqueue --channel channel-a --id test_01 \
  --video ./test.mp4 --title "Test Upload" --description "Description text"

# Preview schedule
uploader plan --channel channel-a --start "2026-06-21 09:00" --interval-hours 24

# Upload all pending
uploader run --channel channel-a --upload-retries 5

# Verify on YouTube
uploader list --channel channel-a --scheduled-only
```

## CLI commands

| Command | Description |
|---------|-------------|
| `uploader auth --channel X` | OAuth browser flow for channel X |
| `uploader plan --channel X` | Preview publish schedule (dry run) |
| `uploader run --channel X` | Process all pending uploads |
| `uploader list --channel X` | List videos on YouTube |
| `uploader enqueue ...` | Manually append a pending job |

### `uploader run` options

- `--start "YYYY-MM-DD HH:MM"` — first publish time (default: tomorrow 09:00 channel timezone)
- `--interval-hours 24` — hours between each video
- `--limit N` — only process first N pending jobs
- `--no-schedule` — upload without scheduling
- `--upload-retries 5` — retry count for transient errors
- `--retry-delay 30` — base delay between retries (× attempt number)

## Multi-channel

```yaml
# config/channels.yaml
channels:
  - id: channel-a
    token_path: secrets/channel-a/youtube_token.json
    registry_path: state/channel-a/upload_registry.txt
    publish:
      timezone: America/New_York
      hour: 9
      interval_hours: 24

  - id: channel-b
    token_path: secrets/channel-b/youtube_token.json
    registry_path: state/channel-b/upload_registry.txt
    publish:
      hour: 12
      interval_hours: 24

google:
  client_secret_path: secrets/shared/client_secret.json
  oauth_port: 8080
```

Authorize each channel once, then run cron per channel:

```cron
0 3 * * * uploader run --channel channel-a --upload-retries 5 >> /var/log/uploader/a.log 2>&1
0 4 * * * uploader run --channel channel-b --upload-retries 5 >> /var/log/uploader/b.log 2>&1
```

## Registry format

JSON-lines file at `state/{channel_id}/upload_registry.txt`:

```json
{
  "id": "mv_20260617_180732_01",
  "channel_id": "channel-a",
  "status": "pending",
  "title": "YouTube title",
  "description": "Full description or s3://bucket/path/description.txt",
  "video_uri": "s3://bucket/videos/channel-a/mv_.../video.mp4",
  "thumbnail_uri": "s3://bucket/videos/channel-a/mv_.../thumbnail.png",
  "youtube_id": "",
  "publish_at": "",
  "created_at": "2026-06-17T18:07:32Z"
}
```

Upstream (ai-music-assembler) appends `pending` rows; this service owns the lifecycle through `uploaded` or `failed`.

## S3 support

Install the optional extra and set AWS credentials:

```bash
pip install '.[s3]'
export AWS_REGION=us-east-1
```

URIs like `s3://bucket/videos/.../video.mp4` are downloaded to a temp directory before upload.

## Testing

```bash
pip install '.[dev]'
pytest
```

### Manual test flow

1. `uploader auth --channel channel-a`
2. `uploader enqueue --channel channel-a --id test_01 --video ./small-test.mp4 --title "Test" --description "Desc"`
3. `uploader run --channel channel-a --no-schedule`
4. `uploader list --channel channel-a`

## Project layout

```
youtube-uploader/
├── config/channels.yaml.example
├── uploader/           # Core library
├── cli/main.py         # CLI entry point
├── api/app.py          # Phase 2 HTTP API (stub)
└── tests/
```

## Related docs

- [YOUTUBE_UPLOADER.md](./YOUTUBE_UPLOADER.md) — overview and service boundary
- [YOUTUBE_UPLOADER_MICROSERVICE.md](./YOUTUBE_UPLOADER_MICROSERVICE.md) — full build spec
- [YOUTUBE_UPLOADER_BUILD_PROMPT.md](./YOUTUBE_UPLOADER_BUILD_PROMPT.md) — implementation prompt

## License

Private / use per your project terms.
