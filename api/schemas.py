"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class LoginRequest(BaseModel):
    password: str = ""


class TokenStatus(BaseModel):
    has_token: bool
    valid: bool
    status: str


class PublishConfigOut(BaseModel):
    timezone: str = "America/New_York"
    hour: int = 9
    interval_hours: float = 24.0
    uploads_per_day: int | None = None


class ChannelOut(BaseModel):
    id: str
    name: str
    youtube_channel_id: str = ""
    custom_url: str = ""
    category: str = ""
    token_path: str
    registry_path: str
    auth: TokenStatus
    publish: PublishConfigOut = Field(default_factory=PublishConfigOut)
    pending_count: int = 0
    uploaded_count: int = 0
    failed_count: int = 0


class JobOut(BaseModel):
    id: str
    channel_id: str
    status: str
    title: str = ""
    description: str = ""
    video_uri: str = ""
    thumbnail_uri: str = ""
    youtube_id: str = ""
    youtube_url: str = ""
    publish_at: str = ""
    created_at: str = ""
    uploaded_at: str = ""
    error: str = ""
    storage_folder: str = "missing"
    queue_position: int | None = None
    queue_prefix: str = ""
    uploaded_prefix: str = ""
    upload_worker_id: str = ""
    upload_phase: str = ""
    upload_progress: float = 0.0
    upload_message: str = ""


class JobMediaOut(BaseModel):
    thumbnail: str = ""
    video: str = ""
    thumbnail_available: bool = False
    video_available: bool = False


class JobDetailResponse(BaseModel):
    job: JobOut
    metadata: dict | None = None
    media: JobMediaOut | None = None


class StagedJobOut(BaseModel):
    """Response after staging a job into queue/."""

    job_id: str
    channel_id: str
    status: str = "pending"
    title: str = ""
    description: str = ""
    video_uri: str = ""
    thumbnail_uri: str = ""
    metadata_uri: str = ""
    queue_prefix: str = ""
    uploaded_prefix: str = ""
    registry_path: str = ""
    privacy: str = "private"
    is_short: bool = False
    tags: list[str] = Field(default_factory=list)


class JobRegisterRequest(BaseModel):
    """Register a job when video files already exist in storage."""

    title: str
    description: str = ""
    video_uri: str
    thumbnail_uri: str = ""
    job_id: str | None = None
    privacy: str | None = None
    is_short: bool | None = None
    category_id: str | None = None
    tags: list[str] | None = None
    made_for_kids: bool | None = None
    language: str | None = None
    metadata: dict | None = None


class PlanItemOut(BaseModel):
    job_id: str
    title: str
    publish_at: str
    publish_display: str


class RunRequest(BaseModel):
    count: int | None = Field(
        default=None,
        ge=1,
        description="Jobs to upload from front of queue; null = all pending",
    )
    parallel: bool = Field(
        default=False,
        description="When true, dispatch one Cloud Run Job (or local thread) per job for parallel uploads",
    )
    no_schedule: bool = False
    upload_retries: int = 3
    retry_delay: float = 30.0
    privacy: str | None = None
    interval_hours: float | None = None
    uploads_per_day: int | None = Field(
        default=None,
        ge=1,
        description="Max jobs to upload in one run; defaults to channel publish.uploads_per_day",
    )
    start: str | None = None
    tags: list[str] | None = None


class RunResponse(BaseModel):
    run_id: str
    channel_id: str
    status: str
    message: str


class RunStatusOut(BaseModel):
    run_id: str
    channel_id: str
    status: str
    total: int = 0
    uploaded: int = 0
    failed: int = 0
    urls: list[str] = []
    errors: list[dict] = []


class ActiveUploadOut(BaseModel):
    channel_id: str
    job_id: str
    title: str = ""
    status: str = "uploading"
    upload_phase: str = ""
    upload_progress: float = 0.0
    upload_message: str = ""
    upload_worker_id: str = ""
    publish_at: str = ""
    completed: bool = False
    youtube_url: str = ""


class ActiveUploadsResponse(BaseModel):
    uploads: list[ActiveUploadOut]
    count: int = 0


class CancelUploadResponse(BaseModel):
    channel_id: str
    job_id: str


class ReconcileActionOut(BaseModel):
    channel_id: str
    job_id: str
    action: str
    detail: str = ""


class ReconcileUploadsResponse(BaseModel):
    scanned: int = 0
    actions: list[ReconcileActionOut] = Field(default_factory=list)
    dry_run: bool = False


class ParallelRunResponse(BaseModel):
    channel_id: str
    dispatched: list[dict] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)
    message: str = ""


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str
    redirect_uri: str


class OAuthStartRequest(BaseModel):
    category: str = Field(
        default="",
        description="Assembly/content category for this channel (e.g. korean)",
    )


class PublishConfigUpdate(BaseModel):
    timezone: str | None = Field(default=None, min_length=1)
    hour: int | None = Field(default=None, ge=0, le=23)
    interval_hours: float | None = Field(default=None, gt=0, le=168)
    uploads_per_day: int | None = Field(
        default=None,
        ge=1,
        description="Max jobs per upload run; omit field to leave unchanged, null to clear cap",
    )


class ChannelUpdateRequest(BaseModel):
    category: str | None = Field(
        default=None,
        description="Assembly/content category; empty string clears it",
    )
    publish: PublishConfigUpdate | None = Field(
        default=None,
        description="Publish scheduling defaults for this channel",
    )


class YouTubeVideoOut(BaseModel):
    video_id: str
    title: str
    privacy_status: str
    publish_at: str | None = None
    url: str
    is_scheduled: bool = False


class ScheduledVideosResponse(BaseModel):
    channel_id: str
    count: int = 0
    tail_publish_at: str | None = None
    videos: list[YouTubeVideoOut] = Field(default_factory=list)


class ChannelListResponse(BaseModel):
    config_uri: str
    storage: str
    categories: list[str] = Field(default_factory=list)
    channels: list[ChannelOut]


class AuthenticatedYouTubeChannelOut(BaseModel):
    """YouTube channel with valid OAuth — ready for uploads and YouTube API reads."""

    id: str
    name: str
    youtube_channel_id: str = ""
    custom_url: str = ""
    category: str = ""


class AuthenticatedYouTubeChannelsResponse(BaseModel):
    channels: list[AuthenticatedYouTubeChannelOut]
    count: int


class ChannelRemovalOut(BaseModel):
    channel_id: str
    name: str
    removed: bool = True
    token_deleted: bool
    pending_jobs_remaining: int
    message: str


class CategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Assembly/content category label (e.g. korean)")


class CategoryListResponse(BaseModel):
    categories: list[str]
    count: int


class DashboardResponse(BaseModel):
    config_uri: str
    storage: str
    categories: list[str] = Field(default_factory=list)
    channels: list[ChannelOut]
    queue_jobs: list[JobOut]
    uploading_jobs: list[JobOut] = Field(default_factory=list)
    uploaded_jobs: list[JobOut]
    jobs: list[JobOut] = Field(default_factory=list, description="Alias for queue_jobs (compat)")
    cached: bool = False


class CapabilitiesOut(BaseModel):
    cli_commands: list[dict]
    youtube_features: list[dict]
    api_endpoints: list[dict]
    auth_note: str = ""
    assembly_integration: dict = Field(default_factory=dict)
