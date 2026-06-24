# YouTube Uploader Microservice — explanation

This document explains how to split **YouTube uploading** out of **ai-music-assembler** into a separate GitHub repository. For the full build spec (API contracts, repo layout, checklist), see **[YOUTUBE_UPLOADER_MICROSERVICE.md](./YOUTUBE_UPLOADER_MICROSERVICE.md)**.

---

## Why split?

**ai-music-assembler** is heavy: FFmpeg encoding, BiRefNet thumbnails, MP3 mixing, OpenAI/Gemini metadata. **YouTube uploading** is a different problem: OAuth, resumable uploads, scheduling, multi-channel tokens, retries, and queue management.

Separating them lets you:

- Run uploads on a **small cloud worker** (I/O bound, not 32 GB RAM)
- Manage **multiple YouTube channels** from one uploader service
- **Cron daily** upload jobs without keeping your Mac awake
- **Retry failures** independently of video rendering

---

## Service boundary

```mermaid
flowchart LR
  subgraph assembler [ai-music-assembler]
    BUILD[generate-music-videos]
    OUT[mp4 + thumbnail + title + description]
  end

  subgraph storage [Object storage — S3 / R2]
    S3[(videos + registry)]
  end

  subgraph uploader [youtube-uploader — new repo]
    RUN[uploader run]
    YT[YouTube API]
  end

  BUILD --> OUT --> S3
  S3 --> RUN --> YT
```

| Responsibility | ai-music-assembler | youtube-uploader |
|----------------|:------------------:|:----------------:|
| MP3 mix + video encode | ✅ | ❌ |
| Thumbnail render (rembg) | ✅ | ❌ |
| Title/description **generation** | ✅ | ❌ |
| Receive final title + description | ❌ | ✅ |
| YouTube OAuth (per channel) | ❌ | ✅ |
| Video upload + custom thumbnail | ❌ | ✅ |
| Schedule `publishAt` | ❌ | ✅ |
| Upload queue / registry | writes `pending` | owns lifecycle |
| List scheduled videos | ❌ | ✅ |
| Retry on timeout / 429 / 5xx | ❌ | ✅ |
| Multi-channel | ❌ | ✅ |

---

## What stays in ai-music-assembler

- `generate-music-videos` — full render pipeline
- `youtube_metadata.py` + `prompts/youtube_metadata.txt`
- Backgrounds, music library, FFmpeg, rembg
- Output folder: `music-video/mv_*/`

**After a video is built**, the assembler only needs to:

1. Upload files to object storage (or keep local paths for now)
2. Append a **pending** row to the upload registry **or** `POST` a job to the uploader API

It should **stop** calling `schedule-music-videos` once the uploader service is live.

---

## What moves to youtube-uploader (new repo)

### Source files to copy from this repo

| This repo | New repo module |
|-----------|-----------------|
| `music_assembler/youtube_upload.py` | `uploader/youtube_client.py` |
| `music_assembler/youtube_channel.py` | `uploader/channel_list.py` |
| `music_assembler/video_registry.py` | `uploader/registry.py` (generalized) |
| `music_assembler/schedule_music_videos.py` | `uploader/scheduler.py` |
| `music_assembler/progress_bars.py` | `uploader/progress.py` (optional) |

**Do not copy:** pipeline, ffmpeg, audio, metadata generation, segmentation.

### Core capabilities

1. **OAuth** — one Google client secret; one refresh token **per channel**
2. **Upload** — resumable insert + optional thumbnail (with retries)
3. **Schedule** — set `publishAt` (RFC3339 UTC); video starts private
4. **Registry** — track `pending` → `uploaded` / `failed`
5. **List** — all channel videos or `--scheduled-only`
6. **Multi-channel** — `channels.yaml` routes token + publish settings

---

## Job handoff (assembler → uploader)

### Endpoints required

1. **`POST /v1/channels/{channel_ref}/jobs/register`** — assembler queues the job (`pending`)
2. **`POST /v1/channels/{channel_ref}/runs`** — uploads oldest pending job(s) to YouTube
3. **`GET /v1/runs/{run_id}`** — poll run status (optional but recommended)

One-time: **`POST /v1/oauth/start`** (browser) so `auth.valid` is true before step 2.

Each finished video becomes one registry entry:

```json
{
  "id": "mv_20260624_061500",
  "channel_id": "nappabeats",
  "status": "pending",
  "title": "Final YouTube title (from assembler metadata step)",
  "description": "Full description text with chapters",
  "video_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_video.mp4",
  "thumbnail_uri": "s3://music-assembly-data/music-video/nappabeats/mv_20260624_061500/mv_20260624_061500_thumbnail.png"
}
```

On **`POST .../runs`**, the uploader downloads from `video_uri` (assembler bucket at run time), uploads to YouTube, sets schedule, marks `uploaded`.

---

## Multi-channel setup

Channels are registered dynamically — no hardcoded `channel-a` / `channel-b`:

```bash
uploader channel add    # OAuth in browser; id = @handle or channel name
uploader channel list
```

```yaml
# config/channels.yaml (auto-populated by channel add)
channels:
  - id: justcavefire
    name: Cavefire
    youtube_channel_id: UC...
    custom_url: '@justcavefire'
    token_path: s3://bucket/secrets/justcavefire/youtube_token.json   # or local path
    registry_path: s3://bucket/state/justcavefire/upload_registry.txt
    publish:
      timezone: America/New_York
      hour: 9
      interval_hours: 24

google:
  oauth_port: 8765
```

Google OAuth credentials go in `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`). Authorize each channel once; tokens persist in R2 when `CLOUDFLARE_R2_BUCKET` is set.

---

## How it runs in production

```mermaid
flowchart TB
  CRON[Daily cron] --> RUN[uploader run --channel X]
  RUN --> S3[Read registry + mp4 from S3]
  RUN --> YT[YouTube Data API v3]
```

| Component | Uploader needs | Assembler needs |
|-----------|----------------|-----------------|
| Worker VM | 2–4 vCPU, 4–8 GB RAM | 32 GB RAM for encode |
| Storage | Read videos + registry | Write videos + pending jobs |
| Scheduler | Cron per channel | Cron for daily **build** |
| Secrets | OAuth tokens per channel | OpenAI/Gemini keys only |

---

## Cloudflare R2 bucket layout

When `CLOUDFLARE_R2_BUCKET` is configured, durable state lives in R2:

```
{bucket}/config/channels.yaml
{bucket}/secrets/{channel_id}/youtube_token.json
{bucket}/state/{channel_id}/channel.meta.json
{bucket}/state/{channel_id}/upload_registry.txt
{bucket}/queue/{channel_id}/{job_id}/
  video.mp4, thumbnail.png, title.txt, description.txt
  metadata.json, privacy.txt, is_short.txt, manifest.json
{bucket}/uploaded/{channel_id}/{job_id}/   # archived after YouTube upload
```

Initialize with `uploader storage init`. Stage with `uploader queue add`. Defaults: `.env` `UPLOADER_DEFAULT_*` → `channels.yaml` `defaults:` → per-channel → CLI flags.

## CLI commands (youtube-uploader repo)

Activate `.venv` then use `uploader …` (entry point: `pyproject.toml` → `cli.main:main`).

### Channels & auth

| Command | Description |
|---------|-------------|
| `uploader channel add` | OAuth in browser; save channel by @handle or name |
| `uploader channel list` | List configured channels |
| `uploader channel reauth <ref>` | Re-authenticate one channel |
| `uploader channels` | Alias for `channel list` |

### Storage

| Command | Description |
|---------|-------------|
| `uploader storage init` | Create R2/local layout; migrate existing data |

### Queue & scheduler

| Command | Description |
|---------|-------------|
| `uploader queue add …` | Stage job folder to `queue/` + `pending` registry row |
| `uploader queue list [--channel X]` | Pending count + job ids (one channel or all) |
| `uploader queue upload --channel X [--count N]` | Upload oldest N pending jobs (default N=1) |
| `uploader queue remove --channel X --id JOB_ID` | Delete queue folder + registry row |
| `uploader plan --channel X` | Preview publish schedule (dry run) |
| `uploader run --channel X [--limit N]` | Upload all (or N) pending jobs for one channel |
| `uploader run-all` | Upload pending jobs for every channel |
| `uploader list --channel X [--scheduled-only]` | List videos on YouTube |

**Default scheduling** (unless `--no-schedule`): first job publishes **tomorrow at channel `publish.hour`** (from `channels.yaml`, default 9 AM in `publish.timezone`), then + `interval_hours` (default 24) per additional job. Preview with `plan`.

**Example — stage, inspect, upload one:**

```bash
uploader queue add --channel justcavefire \
  --video ./my-video.mp4 --title "My Title" --description "…"

uploader queue list --channel justcavefire
uploader plan --channel justcavefire
uploader queue upload --channel justcavefire          # 1 job
uploader queue upload --channel justcavefire --count 3  # up to 3
uploader run --channel justcavefire --upload-retries 5  # all pending
uploader list --channel justcavefire --scheduled-only
```

### Direct upload & testing

| Command | Description |
|---------|-------------|
| `uploader test --channel X --video PATH` | Quick private test upload |
| `uploader upload --channel X --video PATH` | Single direct upload (bypass queue) |
| `uploader enqueue …` | Append registry row (when URIs already exist) |

**Daily cron example:**

```cron
0 3 * * * /path/to/scripts/run-channel.sh justcavefire
0 4 * * * /path/to/scripts/run-channel.sh mmmactually
# or: scripts/run-all-channels.sh
```

---

## Implementation phases

| Phase | Deliverable |
|-------|-------------|
| **1** | CLI worker: `queue add/list/upload/remove`, `run`, R2, metadata + scheduling |
| **2** | Assembler writes S3 URIs + pending jobs; cron on one VM |
| **3** | HTTP API — **Review 1 done:** dashboard, OAuth, R2 config, cached reads. **Review 2 partial:** `POST /v1/channels/{id}/jobs` (multipart stage), `POST .../jobs/register`, optional `UPLOADER_API_KEY`. **Remaining:** PATCH metadata, direct upload, Postgres, hosted deploy |
| **4** | Idempotency, quota tracking, alerts on failure |

---

## Google Cloud / YouTube requirements

- Google Cloud project with **YouTube Data API v3** enabled
- OAuth consent screen + OAuth client (Desktop for dev, Web for prod)
- One **verified YouTube channel** per `channel_id` (for custom thumbnails)
- Upload quota: ~6 videos/day default per project unless extended

---

## Commands today vs after split

**Today (everything in this repo):**

```bash
generate-music-videos -n 1 --thumbnail-text "OMYO" --workers 2
schedule-music-videos --start "2026-06-21 09:00" --interval-hours 24
list-youtube-videos --scheduled-only
```

**After split:**

```bash
# ai-music-assembler
generate-music-videos -n 1 --thumbnail-text "OMYO" --workers 2
# → sync to S3 + write pending registry row

# youtube-uploader (new repo)
uploader run --channel justcavefire --upload-retries 5
uploader run-all
uploader list --channel justcavefire --scheduled-only
```

---

## Related docs in this repo

| File | Contents |
|------|----------|
| **[YOUTUBE_UPLOADER_MICROSERVICE.md](./YOUTUBE_UPLOADER_MICROSERVICE.md)** | Full build spec: API shapes, repo layout, checklist, YouTube API details |
| **[FUTURE_PLAN.md](./FUTURE_PLAN.md)** | End-to-end hosting (storage, cron, multi-channel platform) |
| `music-video/video_registry.txt` | Example pending/uploaded registry format today |
