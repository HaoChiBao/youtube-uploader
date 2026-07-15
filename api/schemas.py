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
    long_uploads_status: str = Field(
        default="",
        description="YouTube status.longUploadsStatus: allowed | eligible | disallowed",
    )
    verified: bool = Field(
        default=False,
        description="True when long_uploads_status is allowed (phone-verified proxy)",
    )


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
    upload_at: str = Field(
        default="",
        description="Queue pickup time (RFC3339 UTC). Empty means eligible for the next run.",
    )
    upload_at_schedule_status: str = Field(
        default="",
        description="none | ready | scheduled | disabled | skipped | error — Cloud Scheduler arm state",
    )
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
    publish_at: str = ""
    upload_at: str = ""
    upload_at_schedule_status: str = Field(
        default="",
        description=(
            "none | ready | scheduled | disabled | skipped | error — "
            "whether a Cloud Scheduler one-shot was armed for upload_at"
        ),
    )
    upload_at_scheduler_job: str = Field(
        default="",
        description="Cloud Scheduler job resource name when status is scheduled",
    )
    upload_at_schedule_message: str = ""


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
    publish_at: str | None = Field(
        default=None,
        description="YouTube publishAt (RFC3339 UTC). Video uploads as private until this time.",
    )
    upload_at: str | None = Field(
        default=None,
        description=(
            "Do not dispatch from queue until this time (RFC3339). "
            "When omitted but publish_at is set, defaults to publish_at so the job "
            "auto-uploads at go-live time. "
            "When UPLOADER_UPLOAD_AT_SCHEDULER=1, a Cloud Scheduler one-shot calls "
            "POST .../jobs/{id}/dispatch-at at this time."
        ),
    )
    upload_now: bool = Field(
        default=False,
        description="After register, immediately dispatch upload (requires OAuth). Ignores upload_at.",
    )
    no_schedule: bool = Field(
        default=False,
        description="When upload_now is true, publish immediately using privacy (no YouTube publishAt).",
    )


class DirectUploadOut(BaseModel):
    """Response after uploading directly to YouTube (no queue)."""

    channel_id: str
    youtube_id: str
    youtube_url: str
    title: str
    privacy: str = "private"
    publish_at: str = ""
    status: str = "uploaded"


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
    job_ids: list[str] | None = Field(
        default=None,
        description="Upload only these job IDs (must be pending after optional requeue)",
    )
    publish_at: str | None = Field(
        default=None,
        description="Override YouTube publishAt for this run (RFC3339 UTC). Per-job queue presets win unless set.",
    )
    ignore_upload_at: bool = Field(
        default=False,
        description="Upload pending jobs even if upload_at is in the future",
    )


class RunResponse(BaseModel):
    run_id: str
    channel_id: str
    status: str
    message: str


class DispatchAtResponse(BaseModel):
    channel_id: str
    job_id: str
    status: str
    message: str
    dispatched: bool = False
    run_id: str = ""
    scheduler_cleaned: bool = False


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


class DismissUploadResponse(BaseModel):
    channel_id: str
    job_id: str
    action: str
    detail: str = ""


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


class MetricDeltaOut(BaseModel):
    value: float | None = None
    prior: float | None = None
    delta_pct: float | None = None


class GrowthSeriesOut(BaseModel):
    dates: list[str] = Field(default_factory=list)
    views: list[float] = Field(default_factory=list)
    watch_minutes: list[float] = Field(default_factory=list)
    subs_net: list[float] = Field(default_factory=list)


class VideoVelocityOut(BaseModel):
    video_id: str
    title: str = ""
    url: str = ""
    published_at: str = ""
    privacy_status: str = ""
    channel_id: str = ""
    channel_name: str = ""
    category: str = ""
    age_hours: float = 0
    views_so_far: float = 0
    watch_minutes_so_far: float = 0
    views_24h: float | None = None
    views_72h: float | None = None
    views_7d: float | None = None
    watch_24h: float | None = None
    watch_72h: float | None = None
    watch_7d: float | None = None
    vs_median_24h_pct: float | None = None
    is_underperformer: bool = False
    is_live: bool = True


class CohortSideOut(BaseModel):
    uploads: int = 0
    avg_views_72h: float | None = None
    avg_views_7d: float | None = None
    avg_views_so_far: float | None = None


class CohortCompareOut(BaseModel):
    this_week: CohortSideOut = Field(default_factory=CohortSideOut)
    last_week: CohortSideOut = Field(default_factory=CohortSideOut)
    delta_views_72h_pct: float | None = None
    delta_views_7d_pct: float | None = None


class AnalyticsVideoOut(BaseModel):
    video_id: str
    title: str = ""
    url: str = ""
    published_at: str = ""
    channel_id: str = ""
    channel_name: str = ""
    category: str = ""
    views: float = 0
    watch_minutes: float = 0
    avg_view_percentage: float | None = None
    ctr: float | None = None
    impressions: float | None = None
    subscribers_gained: float = 0


class ChannelAnalyticsOut(BaseModel):
    channel_id: str
    name: str
    category: str = ""
    youtube_channel_id: str = ""
    status: str = "needs_data"
    message: str = ""
    ok: bool = False
    source: str = "none"
    views: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    watch_minutes: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    subs_net: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    ctr: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    avg_view_percentage: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    avg_view_duration_seconds: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    likes: float = 0
    comments: float = 0
    shares: float = 0
    impressions: float | None = None
    uploads: int = 0
    views_per_upload: float | None = None
    sparkline: list[float] = Field(default_factory=list)
    growth_series: GrowthSeriesOut | None = None
    growth_series_90d: GrowthSeriesOut | None = None
    median_views_24h: float | None = None
    subs_per_day: float | None = None
    subs_per_1k_views: float | None = None
    recent_videos: list[VideoVelocityOut] = Field(default_factory=list)
    subscriber_count: int | None = None
    video_count: int | None = None
    top_videos: list[AnalyticsVideoOut] = Field(default_factory=list)


class CategoryAnalyticsOut(BaseModel):
    category: str = ""
    label: str = ""
    channel_count: int = 0
    health: str = "needs_data"
    views: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    watch_minutes: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    subs_net: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    ctr: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    avg_view_percentage: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    uploads: int = 0
    views_per_upload: float | None = None
    network_view_share_pct: float | None = None
    carrier_risk: bool = False
    sparkline: list[float] = Field(default_factory=list)
    growth_series: GrowthSeriesOut | None = None
    growth_series_90d: GrowthSeriesOut | None = None
    subs_per_day: float | None = None
    subs_per_1k_views: float | None = None
    insights: list[str] = Field(default_factory=list)
    channels: list[ChannelAnalyticsOut] = Field(default_factory=list)
    top_videos: list[AnalyticsVideoOut] = Field(default_factory=list)
    recent_videos: list[VideoVelocityOut] = Field(default_factory=list)


class AnalyticsHealthOut(BaseModel):
    growing: int = 0
    flat: int = 0
    cooling: int = 0
    needs_data: int = 0


class AnalyticsNetworkOut(BaseModel):
    views: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    watch_minutes: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    subs_net: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    ctr: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    avg_view_percentage: MetricDeltaOut = Field(default_factory=MetricDeltaOut)
    subs_per_day: float | None = None
    subs_per_1k_views: float | None = None


class AnalyticsOverviewResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    prior_start_date: str
    prior_end_date: str
    refreshed_at: str
    cached: bool = False
    network: AnalyticsNetworkOut
    health: AnalyticsHealthOut
    categories: list[CategoryAnalyticsOut] = Field(default_factory=list)
    channels: list[ChannelAnalyticsOut] = Field(default_factory=list)
    leaderboard_top: list[ChannelAnalyticsOut] = Field(default_factory=list)
    leaderboard_bottom: list[ChannelAnalyticsOut] = Field(default_factory=list)
    breakouts: list[ChannelAnalyticsOut] = Field(default_factory=list)
    cooling: list[ChannelAnalyticsOut] = Field(default_factory=list)
    needs_reauth_count: int = 0
    growth_curves: dict[str, GrowthSeriesOut] = Field(default_factory=dict)
    new_uploads: list[VideoVelocityOut] = Field(default_factory=list)
    underperformers: list[VideoVelocityOut] = Field(default_factory=list)
    cohorts: CohortCompareOut = Field(default_factory=CohortCompareOut)


class CategoryAnalyticsResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    prior_start_date: str
    prior_end_date: str
    refreshed_at: str
    cached: bool = False
    network_views: float = 0
    category: CategoryAnalyticsOut


class ChannelAnalyticsResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    prior_start_date: str
    prior_end_date: str
    refreshed_at: str
    cached: bool = False
    channel: ChannelAnalyticsOut
    peer_median_views_per_upload: float | None = None
    vs_peer_median_pct: float | None = None
    growth_curves: dict[str, GrowthSeriesOut] = Field(default_factory=dict)
    new_uploads: list[VideoVelocityOut] = Field(default_factory=list)
    underperformers: list[VideoVelocityOut] = Field(default_factory=list)
    cohorts: CohortCompareOut = Field(default_factory=CohortCompareOut)
