# YouTube Uploader Microservice — build spec

Export this document to your new GitHub repository. It abstracts everything upload-related from **ai-music-assembler** and defines what the microservice owns vs what stays in the assembler.

---

## Boundary: two services

```mermaid
flowchart LR
  subgraph assembler [ai-music-assembler — existing repo]
    BUILD[generate-music-videos]
    OUT[mv_* folder: mp4, thumbnail, title.txt, description.txt]
    REG_WRITE[Append pending to registry OR POST to uploader API]
  end

  subgraph uploader [youtube-uploader — new repo]
    API[HTTP API or CLI worker]
    REG[Upload registry / queue]
    YT[YouTube Data API v3]
  end

  subgraph storage [Shared object storage]
    S3[(S3 / R2)]
  end

  BUILD --> OUT --> S3
  OUT --> REG_WRITE
  REG_WRITE -->|job payload| API
  S3 -->|download mp4 + thumb| API
  API --> YT
```

| Responsibility | **ai-music-assembler** | **youtube-uploader** |
|----------------|------------------------|----------------------|
| MP3 mix, encode, thumbnail render | ✅ | ❌ |
| Title/description **generation** (OpenAI/Gemini) | ✅ | ❌ (receives final strings) |
| Background images | ✅ | ❌ |
| YouTube OAuth per channel | ❌ | ✅ |
| Resumable video upload | ❌ | ✅ |
| Custom thumbnail upload | ❌ | ✅ |
| Schedule `publishAt` | ❌ | ✅ |
| Upload queue / registry | ❌ (writes `pending`) | ✅ (owns lifecycle) |
| List scheduled videos on channel | ❌ | ✅ |
| Retry on timeout / 429 / 5xx | ❌ | ✅ |
| Multi-channel routing | ❌ | ✅ |

---

## What to copy from this repo

Lift these modules almost verbatim (rename package, remove `music_assembler` imports):

| Source file | New module | Notes |
|-------------|------------|-------|
| `music_assembler/youtube_upload.py` | `uploader/youtube_client.py` | OAuth, `upload_video`, retries, thumbnail prep |
| `music_assembler/youtube_channel.py` | `uploader/channel_list.py` | `list_channel_videos`, `YouTubeVideoInfo` |
| `music_assembler/video_registry.py` | `uploader/registry.py` | Generalize schema (see below) |
| `music_assembler/schedule_music_videos.py` | `uploader/scheduler.py` + CLI | Batch upload loop; drop music-specific paths |
| `music_assembler/progress_bars.py` | `uploader/progress.py` | Optional; CLI only |

**Do not copy:** `pipeline.py`, `ffmpeg_util.py`, `youtube_metadata.py`, `make_music_videos.py`, segmentation, audio, etc.

### Python dependencies (new repo `pyproject.toml`)

```toml
dependencies = [
    "google-api-python-client>=2.100.0",
    "google-auth-oauthlib>=1.2.0",
    "google-auth-httplib2>=0.2.0",
    "Pillow>=10.0.0",          # thumbnail resize only
    "python-dotenv>=1.0.0",    # optional
]

[project.optional-dependencies]
api = ["fastapi>=0.110", "uvicorn>=0.27", "pydantic>=2.0"]
s3 = ["boto3>=1.34"]
```

---

## Core library API (implement first)

These are the functions your CLI, cron worker, and HTTP API wrap.

### 1. OAuth

```python
def get_credentials(
    client_secret: Path,
    token_path: Path,
    *,
    oauth_port: int = 8080,
) -> Credentials:
    """Load/refresh token; browser flow on first run."""

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
```

- One **Google Cloud OAuth client** (Desktop or Web) shared across channels.
- One **`youtube_token.json` per channel** (refresh token).

### 2. Single upload

```python
def upload_video(
    video_path: Path,
    *,
    title: str,
    description: str,
    client_secret: Path,
    token_path: Path,
    privacy: str = "private",       # private | unlisted | public
    category_id: str = "10",          # Music
    tags: list[str] | None = None,
    made_for_kids: bool = False,
    thumbnail_path: Path | None = None,
    publish_at: str | None = None,   # RFC3339 UTC e.g. 2026-06-20T13:00:00Z
    oauth_port: int = 8080,
    on_progress: Callable[[float], None] | None = None,
) -> dict:
    """Returns YouTube insert response; includes 'id'."""
```

**Scheduling rule:** if `publish_at` is set → force `privacyStatus=private` + set `status.publishAt` (YouTube requirement).

**Thumbnail rule:** best-effort; never fail upload if thumbnail fails (verified channel required).

### 3. Upload with retries

```python
def upload_video_with_retry(
    video_path: Path,
    *,
    max_attempts: int = 3,
    retry_delay_sec: float = 30.0,  # linear: delay * attempt
    on_retry: Callable[[int, int, Exception], None] | None = None,
    **upload_kwargs,
) -> dict:

def is_transient_upload_error(exc: BaseException) -> bool:
    """Timeouts, connection errors, HTTP 408/429/5xx."""
```

### 4. List channel videos

```python
@dataclass
class YouTubeVideoInfo:
    video_id: str
    title: str
    privacy_status: str
    publish_at: str | None
    url: str

    @property
    def is_scheduled(self) -> bool: ...

def list_channel_videos(
    client_secret: Path,
    token_path: Path,
    *,
    scheduled_only: bool = False,
) -> list[YouTubeVideoInfo]:
    """channels.list → uploads playlist → videos.list (batched by 50)."""
```

---

## Upload registry (generalized schema)

Replace music-specific fields with storage-agnostic URIs. Store in S3 or Postgres.

### JSON-lines entry (compatible with assembler today)

```json
{
  "id": "mv_20260617_180732_01",
  "channel_id": "justcavefire",
  "status": "pending",
  "title": "𝐏𝐥𝐚𝐲𝐥𝐢𝐬𝐭 …",
  "description": "Full description text OR s3://bucket/path/description.txt",
  "video_uri": "s3://bucket/queue/justcavefire/mv_…/video.mp4",
  "thumbnail_uri": "s3://bucket/queue/justcavefire/mv_…/thumbnail.png",
  "youtube_id": "",
  "youtube_url": "",
  "publish_at": "",
  "created_at": "2026-06-17T18:07:32Z",
  "uploaded_at": "",
  "error": "",
  "extra": {}
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `id` | ✅ | Unique job id from assembler |
| `channel_id` | ✅ | Routes OAuth token + config |
| `status` | ✅ | `pending` → `uploading` → `uploaded` \| `failed` |
| `title` | ✅ | Final YouTube title (assembler generates) |
| `description` | ✅ | Inline text or URI to `.txt` |
| `video_uri` | ✅ | `file://`, `s3://`, `gs://`, or local path |
| `thumbnail_uri` | optional | |
| `publish_at` | optional | Set by scheduler before upload |
| `youtube_id` | set on success | |

### Registry operations

```python
class UploadRegistry:
    def pending(self, channel_id: str | None = None) -> list[UploadEntry]: ...
    def mark_uploading(self, entry_id: str) -> None: ...
    def mark_uploaded(self, entry_id: str, *, youtube_id: str, publish_at: str = "") -> None: ...
    def mark_failed(self, entry_id: str, *, error: str) -> None: ...
    def append(self, entry: UploadEntry) -> None: ...
```

**Storage backends (pick one for v1):**

1. **JSON-lines file** in S3 (`state/{channel}/upload_registry.txt`) — same as today
2. **PostgreSQL** — better for API + concurrent workers
3. **SQS queue** — message per job; registry optional

---

## Job payload (assembler → uploader contract)

When the assembler finishes a video, use one of two HTTP ingest paths (documented in `api/endpoint_docs.py` and README):

### Option A — multipart upload (simplest)

```bash
POST /v1/channels/justcavefire/jobs
Content-Type: multipart/form-data

video=@output.mp4
title=My Generated Video
description=...
privacy=private
is_short=false
tags=ai,generated
```

Returns `201` with `job_id`, `video_uri`, `queue_prefix`. No YouTube OAuth required.

### Option B — register when files already on R2

```json
POST /v1/channels/justcavefire/jobs/register
{
  "job_id": "mv_20260617_180732_01",
  "title": "...",
  "description": "...",
  "video_uri": "s3://my-bucket/queue/justcavefire/mv_20260617_180732_01/video.mp4",
  "thumbnail_uri": "s3://my-bucket/queue/justcavefire/mv_20260617_180732_01/thumbnail.png",
  "privacy": "private",
  "is_short": false,
  "tags": ["lofi", "chill"],
  "category_id": "10",
  "made_for_kids": false
}
```

The service validates files exist, writes metadata sidecars, and appends a `pending` registry row.

### Option C — direct R2 + cron (no HTTP)

Assembler writes to `queue/{channel_id}/{job_id}/` and appends to `upload_registry.txt`; uploader cron runs `uploader run`.

**Assembler changes (minimal):**

- After `generate-music-videos`, call **Option A or B** (or write directly per Option C).
- Stop calling `schedule-music-videos` locally; use `POST /v1/channels/{id}/runs` or cron `uploader run`.

---

## Multi-channel config

Channels are registered via `uploader channel add` (dynamic id from @handle or channel name):

```yaml
# config/channels.yaml
channels:
  - id: justcavefire
    name: Cavefire
    youtube_channel_id: UCxxxx
    custom_url: '@justcavefire'
    token_path: s3://bucket/secrets/justcavefire/youtube_token.json
    registry_path: s3://bucket/state/justcavefire/upload_registry.txt
    default_tags: [lofi, chill, study]
    category_id: "10"
    publish:
      timezone: America/New_York
      hour: 9
      interval_hours: 24

google:
  oauth_port: 8765
```

OAuth app credentials: `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env` (preferred) or `client_secret_path` in yaml.

---

## Cloudflare R2 bucket layout

Canonical paths (`uploader/bucket_layout.py`):

```
{bucket}/config/channels.yaml
{bucket}/secrets/{channel_id}/youtube_token.json
{bucket}/state/{channel_id}/channel.meta.json
{bucket}/state/{channel_id}/upload_registry.txt
{bucket}/queue/{channel_id}/{job_id}/
  video.mp4, thumbnail.png, title.txt, description.txt
  metadata.json, privacy.txt, is_short.txt, manifest.json
{bucket}/uploaded/{channel_id}/{job_id}/   # moved after YouTube upload
{bucket}/logs/{channel_id}/               # cron logs
```

When `CLOUDFLARE_R2_*` env vars are set, `state_store.py` persists config + tokens to R2. `uploader storage init` creates layout. `uploader queue add` calls `job_store.stage_job()` which writes full metadata via `job_metadata.py`. Defaults via `job_defaults.py` (`.env` → `channels.yaml` `defaults:` → channel → CLI).

Assembler job URIs: `bucket_layout.default_job_uris(channel_id, job_id, base)`.

---

## Microservice surfaces

Implement in phases.

### Phase 1 — CLI worker (fastest path)

```bash
# Register channel (OAuth in browser)
uploader channel add

# Initialize R2 bucket layout + migrate local data
uploader storage init

# Stage video into queue/ + register as pending
uploader queue add --channel justcavefire --video ./clip.mp4 --title "..." --description "..."

# Inspect queue
uploader queue list --channel justcavefire

# Upload one (or N) pending jobs — oldest first
uploader queue upload --channel justcavefire
uploader queue upload --channel justcavefire --count 3

# Or upload all pending for one channel / all channels
uploader run --channel justcavefire
uploader run-all --upload-retries 5

# Preview publish schedule (default: tomorrow 9 AM ET + 24h interval)
uploader plan --channel justcavefire

# List scheduled on YouTube
uploader list --channel justcavefire --scheduled-only
```

**Daily cron (on worker VM):**

```cron
0 3 * * * /path/to/scripts/run-channel.sh justcavefire
0 4 * * * /path/to/scripts/run-channel.sh mmmactually
# or: scripts/run-all-channels.sh
```

### Phase 2 — HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/jobs` | Enqueue upload (assembler calls this) |
| `GET` | `/v1/jobs/{id}` | Job status |
| `GET` | `/v1/jobs?channel=&status=pending` | List queue |
| `POST` | `/v1/channels/{id}/run` | Process pending batch now |
| `GET` | `/v1/channels/{id}/scheduled` | Proxy to `list_channel_videos(scheduled_only=True)` |
| `POST` | `/v1/channels/{id}/auth/start` | OAuth URL for channel setup |

Auth between services: API key or mTLS (assembler → uploader).

### Phase 3 — Worker pulls from queue

- SQS / Redis queue; horizontal upload workers
- One encode-sized video upload per worker; no parallel uploads per channel (quota + bandwidth)

---

## Scheduler logic (port from `schedule_music_videos.py`)

For each `pending` entry in order:

1. Resolve `description` (inline or fetch from URI).
2. Download `video_uri` + `thumbnail_uri` to temp dir (if remote).
3. Compute `publish_at`:
   - **Explicit:** from job or `--start` + index × `--interval-hours`
   - **Auto slot:** next free day at channel’s publish hour (query YouTube scheduled list to avoid collisions — optional v2)
4. Call `upload_video_with_retry(...)`.
5. `mark_uploaded` or `mark_failed`.
6. Delete temp files.

**Publish time format:** local `--start` → convert to RFC3339 UTC:

```python
def to_rfc3339_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

---

## Secrets & Google Cloud setup

### Required accounts

| Item | Purpose |
|------|---------|
| Google Cloud project | YouTube Data API v3 |
| YouTube channel(s) | Destination |
| Object storage | Shared with assembler (video files) |
| Secrets manager | Prod tokens |

### One-time per Google Cloud project

1. Enable **YouTube Data API v3**
2. OAuth consent screen (External or Internal)
3. Create OAuth client:
   - **Dev:** Web app (browser on laptop) → `uploader channel add`
   - **Prod:** same client; refresh tokens in R2 or Secrets Manager
4. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` in `.env`

### Per YouTube channel

1. Run `uploader channel add` (browser login as that channel’s Google account)
2. Token stored at `secrets/{channel_id}/youtube_token.json` (local or R2)
3. **Verified channel** for custom thumbnails

### Environment variables

```bash
GOOGLE_CLIENT_SECRET_PATH=/secrets/shared/google_oauth_client.json
CLOUDFLARE_R2_BUCKET=your-bucket
CLOUDFLARE_R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
CLOUDFLARE_R2_REGION=auto
CLOUDFLARE_R2_ACCESS_KEY_ID=...
CLOUDFLARE_R2_SECRET_ACCESS_KEY=...
UPLOADER_API_KEY=...                    # if HTTP API
```

---

## Infrastructure (uploader only)

```mermaid
flowchart TB
  subgraph assembler_repo [ai-music-assembler]
    GMV[generate-music-videos]
  end

  subgraph shared [Cloudflare R2]
    CFG[config/channels.yaml]
    SEC[secrets/{channel}/youtube_token.json]
    REG[state/{channel}/upload_registry.txt]
    QUEUE[queue/{channel}/{job_id}/]
    UPLOADED[uploaded/{channel}/{job_id}/]
  end

  subgraph uploader_repo [youtube-uploader service]
    CRON[cron or Cloud Scheduler]
    WORKER[uploader run]
    API[FastAPI optional]
  end

  GMV -->|writes mp4 + metadata + pending job| QUEUE
  GMV -->|append pending| REG
  CRON --> WORKER
  WORKER -->|read| REG & QUEUE & SEC
  WORKER -->|archive on success| UPLOADED
  WORKER -->|upload| YT[YouTube API]
```

| Component | Spec |
|-----------|------|
| **Worker VM** | Small OK: 2 vCPU, 4–8 GB RAM (upload is I/O bound; ~200–400 MB/video) |
| **Storage** | Read videos from S3; sync registry R/W |
| **Scheduler** | Cron per channel, staggered |
| **Secrets** | AWS Secrets Manager / GCP Secret Manager |
| **Logs** | Structured JSON; alert on `failed` status |
| **Network** | Stable egress; large upload timeouts (15–60 min per video) |

**Not required on uploader:** FFmpeg, rembg, Gemini, OpenAI, 32 GB RAM.

---

## Suggested new repo layout

```
youtube-uploader/
├── pyproject.toml
├── README.md
├── config/
│   └── channels.yaml.example
├── uploader/
│   ├── youtube_client.py
│   ├── channel_store.py       # dynamic channel add
│   ├── channel_list.py
│   ├── bucket_layout.py       # canonical R2 paths
│   ├── object_storage.py      # S3/R2 I/O
│   ├── state_store.py         # durable config + tokens
│   ├── registry.py
│   ├── scheduler.py           # run_channel + run_all_channels
│   ├── storage.py
│   ├── channels.py
│   ├── cache_signals.py       # API cache invalidation
│   └── progress.py
├── cli/main.py
├── api/
│   ├── app.py                 # FastAPI (local MVP + dashboard)
│   ├── cache.py               # Dashboard/config cache
│   ├── deps.py, schemas.py, capabilities.py
│   ├── oauth_sessions.py
│   └── static/index.html
├── scripts/
│   ├── run-channel.sh / .ps1
│   └── run-all-channels.sh / .ps1
└── tests/
    ├── test_registry.py
    ├── test_api.py
    ├── test_cache_signals.py
    ├── test_scheduler.py
    ├── test_bucket_layout.py
    └── test_state_store.py
```

---

## Implementation checklist

### Phase 1 — Library + CLI

- [x] New repo, copy core modules, fix imports
- [x] Generalized `UploadEntry` + `UploadRegistry` (file + s3:// backed)
- [x] `storage.py`: resolve `s3://` and `file://` to local Path
- [x] CLI: `channel add`, `queue add/list/upload/remove`, `run`, `run-all`, `plan`, `list`, `storage init`
- [x] Dynamic `channels.yaml` + `channel_store.py`
- [x] Cloudflare R2 durable state (`bucket_layout`, `state_store`, `object_storage`)
- [x] Windows `tzdata` dependency for `ZoneInfo` scheduling

### Phase 2 — Assembler integration

- [x] Assembler contract documented: `queue/{channel_id}/{job_id}/` + `metadata.json`
- [ ] Assembler writes video jobs to `queue/{channel_id}/{job_id}/` + pending registry rows
- [x] Document contract in youtube-uploader README + `api/endpoint_docs.py` (assembler cross-link pending — YAN-14)
- [ ] Cron on one VM: assembler builds overnight → uploader runs at 06:00
- [ ] Remove `schedule-music-videos` from assembler default workflow

### Phase 3 — HTTP API + multi-channel

- [x] FastAPI local server (`uploader-api`) + OpenAPI docs
- [x] Web OAuth redirect flow (add channel + reauth) with PKCE
- [x] Dashboard UI at `/` — channels, queue, upload triggers
- [x] `GET /v1/dashboard` — cached channels + pending jobs (Review 1)
- [x] R2 `config/channels.yaml` as source of truth; fast reads (skip migrate on routine load)
- [x] `GET /v1/channels`, `/v1/jobs`, job detail, DELETE remove, plan, runs
- [x] `GET /v1/health`, `GET /v1/capabilities`
- [x] `GET /v1/channels/{id}/youtube/videos`
- [x] FastAPI `POST /v1/channels/{id}/jobs` + `POST /v1/jobs` (multipart stage from assembler)
- [x] `POST /v1/channels/{id}/jobs/register` (R2 URI register)
- [x] Optional `UPLOADER_API_KEY` on ingest routes (`X-API-Key` header)
- [x] API endpoint catalog — `api/endpoint_docs.py`, README reference, OpenAPI tags
- [ ] Postgres registry option
- [ ] Secrets manager for tokens
- [ ] Hosted deploy
- [ ] Alerting on failures

### Phase 4 — Hardening

- [ ] Idempotency: skip if `youtube_id` already set
- [ ] Resume partial uploads (YouTube resumable protocol already in client)
- [ ] Quota tracking (YouTube daily upload limit)
- [ ] Admin UI or Slack notification on failure

---

## YouTube API reference (implementation details)

| Operation | API call |
|-----------|----------|
| Upload video | `videos().insert(part="snippet,status", media_body=resumable)` |
| Set thumbnail | `thumbnails().set(videoId=…)` |
| List uploads | `channels().list(mine=True)` → `playlistItems().list(uploads playlist)` |
| Video metadata | `videos().list(part="snippet,status", id=…)` |
| Schedule publish | `status.publishAt` + `privacyStatus=private` on insert |

**Quota:** ~1,600 units per upload; default 10,000 units/day per project — ~6 uploads/day unless quota extension requested.

**Chunk size:** 8 MB (`MediaFileUpload(..., chunksize=8*1024*1024, resumable=True)`).

---

## What assembler keeps (no duplication)

- `generate-music-videos` — full render pipeline
- `youtube_metadata.py` — title/description generation
- `prompts/youtube_metadata.txt`
- Backgrounds, music library, ffmpeg, rembg
- Local `music-video/mv_*/` output structure

**Assembler output handoff:** each completed run produces files + one registry row (or HTTP POST) with **final** title, description, video URI, thumbnail URI, and `channel_id`.

---

## Quick reference — commands after split

**Assembler (existing repo):**

```bash
generate-music-videos -n 1 --thumbnail-text "OMYO" --workers 2
# → writes to S3 + appends pending row (you add S3 sync)
```

**Uploader (new repo):**

```bash
uploader channel add
uploader storage init
uploader queue add --channel justcavefire --video ./clip.mp4 --title "..." --description "..."
uploader queue list --channel justcavefire
uploader queue upload --channel justcavefire --count 1
uploader plan --channel justcavefire
uploader run --channel justcavefire --upload-retries 5
uploader run-all --upload-retries 5
uploader list --channel justcavefire --scheduled-only
```

---

## Files in this repo to use as source

When copying, start from these paths in **ai-music-assembler**:

- `music_assembler/youtube_upload.py`
- `music_assembler/youtube_channel.py`
- `music_assembler/video_registry.py`
- `music_assembler/schedule_music_videos.py` (scheduler loop only)
- `music_assembler/progress_bars.py` (optional)

Example registry row today: `music-video/video_registry.txt`.
