"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class TokenStatus(BaseModel):
    has_token: bool
    valid: bool
    status: str


class ChannelOut(BaseModel):
    id: str
    name: str
    youtube_channel_id: str = ""
    custom_url: str = ""
    token_path: str
    registry_path: str
    auth: TokenStatus
    pending_count: int = 0


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
    error: str = ""


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
    no_schedule: bool = False
    upload_retries: int = 3
    retry_delay: float = 30.0
    privacy: str | None = None
    interval_hours: float | None = None
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


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str
    redirect_uri: str


class YouTubeVideoOut(BaseModel):
    video_id: str
    title: str
    privacy_status: str
    publish_at: str | None = None
    url: str
    is_scheduled: bool = False


class ChannelListResponse(BaseModel):
    config_uri: str
    storage: str
    channels: list[ChannelOut]


class DashboardResponse(BaseModel):
    config_uri: str
    storage: str
    channels: list[ChannelOut]
    jobs: list[JobOut]
    cached: bool = False


class CapabilitiesOut(BaseModel):
    cli_commands: list[dict]
    youtube_features: list[dict]
    api_endpoints: list[dict]
