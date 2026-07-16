"""Fetch YouTube Analytics + Data API performance metrics for a channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from uploader.youtube_client import (
    _require_google_libs,
    credentials_have_analytics,
    get_credentials,
)

# Metrics that work at channel×day for most channels.
_CHANNEL_DAY_METRICS = (
    "views,estimatedMinutesWatched,averageViewPercentage,averageViewDuration,"
    "subscribersGained,subscribersLost,likes,comments,shares"
)
_CHANNEL_DAY_METRICS_WITH_CTR = (
    _CHANNEL_DAY_METRICS + ",impressions,impressionClickThroughRate"
)

# Top videos in the window (no day dimension).
_VIDEO_METRICS = (
    "views,estimatedMinutesWatched,averageViewPercentage,"
    "impressions,impressionClickThroughRate,subscribersGained"
)


@dataclass
class PeriodTotals:
    views: float = 0.0
    watch_minutes: float = 0.0
    avg_view_percentage: float | None = None
    avg_view_duration_seconds: float | None = None
    subscribers_gained: float = 0.0
    subscribers_lost: float = 0.0
    likes: float = 0.0
    comments: float = 0.0
    shares: float = 0.0
    impressions: float | None = None
    ctr: float | None = None  # 0–100


@dataclass
class DailySeries:
    dates: list[str] = field(default_factory=list)  # YYYY-MM-DD
    views: list[float] = field(default_factory=list)
    watch_minutes: list[float] = field(default_factory=list)
    subs_net: list[float] = field(default_factory=list)


@dataclass
class VideoVelocity:
    video_id: str
    title: str = ""
    url: str = ""
    published_at: str = ""
    privacy_status: str = ""
    age_hours: float = 0.0
    views_so_far: float = 0.0
    watch_minutes_so_far: float = 0.0
    views_24h: float | None = None
    views_72h: float | None = None
    views_7d: float | None = None
    watch_24h: float | None = None
    watch_72h: float | None = None
    watch_7d: float | None = None
    vs_median_24h_pct: float | None = None
    is_underperformer: bool = False
    is_live: bool = True  # False if still private scheduled


@dataclass
class VideoPerformance:
    video_id: str
    title: str = ""
    url: str = ""
    published_at: str = ""
    views: float = 0.0
    watch_minutes: float = 0.0
    avg_view_percentage: float | None = None
    ctr: float | None = None
    impressions: float | None = None
    subscribers_gained: float = 0.0


@dataclass
class ChannelPerformance:
    """Resolved performance for one channel over a window + prior window."""

    channel_id: str
    name: str
    category: str
    youtube_channel_id: str = ""
    custom_url: str = ""
    thumbnail_url: str = ""
    ok: bool = False
    source: str = "none"  # analytics_api | none
    status_code: str = "needs_data"  # growing|flat|cooling|needs_data|needs_reauth|error
    message: str = ""
    current: PeriodTotals = field(default_factory=PeriodTotals)
    prior: PeriodTotals = field(default_factory=PeriodTotals)
    sparkline: list[float] = field(default_factory=list)  # daily views in current window
    series: DailySeries = field(default_factory=DailySeries)
    series_90d: DailySeries | None = None
    recent_videos: list[VideoVelocity] = field(default_factory=list)
    median_views_24h: float | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    uploads_in_window: int = 0
    top_videos: list[VideoPerformance] = field(default_factory=list)


def date_windows(days: int, *, end: date | None = None) -> tuple[date, date, date, date]:
    """Return (start, end, prior_start, prior_end) inclusive dates (UTC)."""
    if days not in (7, 28, 90):
        days = 28
    end_d = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start_d = end_d - timedelta(days=days - 1)
    prior_end = start_d - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    return start_d, end_d, prior_start, prior_end


def _delta_pct(current: float, prior: float) -> float | None:
    if prior == 0:
        return None if current == 0 else 100.0
    return ((current - prior) / prior) * 100.0


def classify_health(
    *,
    views: float,
    views_prior: float,
    subs_net: float,
    subs_net_prior: float,
    has_data: bool,
) -> str:
    """growing | flat | cooling | needs_data."""
    if not has_data or (views <= 0 and views_prior <= 0 and abs(subs_net) < 0.5):
        return "needs_data"
    views_delta = _delta_pct(views, views_prior)
    subs_delta = _delta_pct(subs_net, subs_net_prior)
    growing = (views_delta is not None and views_delta >= 10.0) or (
        subs_delta is not None and subs_delta >= 10.0
    )
    cooling = (views_delta is not None and views_delta <= -10.0) and not growing
    if growing:
        return "growing"
    if cooling:
        return "cooling"
    return "flat"


def _load_creds(
    token_path: str | Path,
    *,
    client_secret: Path | None,
    client_config: dict | None,
    oauth_port: int,
):
    return get_credentials(
        token_path,
        client_secret=client_secret,
        client_config=client_config,
        oauth_port=oauth_port,
    )


def _build_services(creds):
    Request, Credentials, Flow, build, HttpError, Media = _require_google_libs()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    return youtube, analytics, HttpError


def _channel_snapshot(youtube) -> tuple[str, int | None, int | None, str]:
    """Return (youtube_channel_id, subscriber_count, video_count, thumbnail_url)."""
    resp = (
        youtube.channels()
        .list(part="id,snippet,statistics", mine=True)
        .execute()
    )
    items = resp.get("items") or []
    if not items:
        return "", None, None, ""
    item = items[0]
    stats = item.get("statistics") or {}
    snippet = item.get("snippet") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = ""
    for key in ("medium", "high", "default"):
        entry = thumbs.get(key) or {}
        if entry.get("url"):
            thumb = entry["url"]
            break
    sub = stats.get("subscriberCount")
    vids = stats.get("videoCount")
    return (
        item.get("id") or "",
        int(sub) if sub is not None else None,
        int(vids) if vids is not None else None,
        thumb,
    )


def _query_report(analytics, HttpError, *, ids: str, start: date, end: date, metrics: str, dimensions: str = "", filters: str = "", sort: str = "", max_results: int | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "ids": ids,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "metrics": metrics,
    }
    if dimensions:
        kwargs["dimensions"] = dimensions
    if filters:
        kwargs["filters"] = filters
    if sort:
        kwargs["sort"] = sort
    if max_results is not None:
        kwargs["maxResults"] = max_results
    try:
        return analytics.reports().query(**kwargs).execute()
    except HttpError:
        raise


def _parse_totals_row(headers: list[dict], row: list[Any] | None) -> PeriodTotals:
    totals = PeriodTotals()
    if not row:
        return totals
    by_name = {h.get("name"): i for i, h in enumerate(headers)}

    def num(name: str) -> float:
        idx = by_name.get(name)
        if idx is None or idx >= len(row) or row[idx] is None:
            return 0.0
        try:
            return float(row[idx])
        except (TypeError, ValueError):
            return 0.0

    def opt(name: str) -> float | None:
        idx = by_name.get(name)
        if idx is None or idx >= len(row) or row[idx] is None:
            return None
        try:
            return float(row[idx])
        except (TypeError, ValueError):
            return None

    totals.views = num("views")
    totals.watch_minutes = num("estimatedMinutesWatched")
    totals.subscribers_gained = num("subscribersGained")
    totals.subscribers_lost = num("subscribersLost")
    totals.likes = num("likes")
    totals.comments = num("comments")
    totals.shares = num("shares")
    avp = opt("averageViewPercentage")
    totals.avg_view_percentage = avp
    avd = opt("averageViewDuration")
    totals.avg_view_duration_seconds = avd
    impressions = opt("impressions")
    ctr = opt("impressionClickThroughRate")
    totals.impressions = impressions
    # API returns CTR as a fraction (0–1) or already percent depending on report; normalize.
    if ctr is not None:
        totals.ctr = ctr * 100.0 if ctr <= 1.0 else ctr
    return totals


def _sum_day_rows(headers: list[dict], rows: list[list[Any]]) -> tuple[PeriodTotals, DailySeries]:
    """Aggregate day rows; return totals + daily series (ordered by day)."""
    by_name = {h.get("name"): i for i, h in enumerate(headers)}
    day_idx = by_name.get("day")
    ordered = rows
    if day_idx is not None:
        ordered = sorted(rows, key=lambda r: str(r[day_idx]) if day_idx < len(r) else "")

    series = DailySeries()
    views = watch = gained = lost = likes = comments = shares = 0.0
    impressions_sum = 0.0
    has_impressions = False
    avp_weighted = 0.0
    avp_weight = 0.0
    avd_weighted = 0.0
    avd_weight = 0.0
    ctr_weighted = 0.0
    ctr_weight = 0.0

    def cell(row: list[Any], name: str) -> float | None:
        idx = by_name.get(name)
        if idx is None or idx >= len(row) or row[idx] is None:
            return None
        try:
            return float(row[idx])
        except (TypeError, ValueError):
            return None

    for row in ordered:
        v = cell(row, "views") or 0.0
        w = cell(row, "estimatedMinutesWatched") or 0.0
        g = cell(row, "subscribersGained") or 0.0
        lo = cell(row, "subscribersLost") or 0.0
        if day_idx is not None and day_idx < len(row):
            series.dates.append(str(row[day_idx]))
        else:
            series.dates.append("")
        series.views.append(v)
        series.watch_minutes.append(w)
        series.subs_net.append(g - lo)
        views += v
        watch += w
        gained += g
        lost += lo
        likes += cell(row, "likes") or 0.0
        comments += cell(row, "comments") or 0.0
        shares += cell(row, "shares") or 0.0
        avp = cell(row, "averageViewPercentage")
        if avp is not None and v > 0:
            avp_weighted += avp * v
            avp_weight += v
        avd = cell(row, "averageViewDuration")
        if avd is not None and v > 0:
            avd_weighted += avd * v
            avd_weight += v
        imp = cell(row, "impressions")
        if imp is not None:
            has_impressions = True
            impressions_sum += imp
        ctr = cell(row, "impressionClickThroughRate")
        if ctr is not None and imp is not None and imp > 0:
            ctr_val = ctr * 100.0 if ctr <= 1.0 else ctr
            ctr_weighted += ctr_val * imp
            ctr_weight += imp

    totals = PeriodTotals(
        views=views,
        watch_minutes=watch,
        subscribers_gained=gained,
        subscribers_lost=lost,
        likes=likes,
        comments=comments,
        shares=shares,
        avg_view_percentage=(avp_weighted / avp_weight) if avp_weight else None,
        avg_view_duration_seconds=(avd_weighted / avd_weight) if avd_weight else None,
        impressions=impressions_sum if has_impressions else None,
        ctr=(ctr_weighted / ctr_weight) if ctr_weight else None,
    )
    return totals, series


def _fetch_period(
    analytics,
    HttpError,
    *,
    channel_yt_id: str,
    start: date,
    end: date,
) -> tuple[PeriodTotals, DailySeries]:
    ids = f"channel=={channel_yt_id}" if channel_yt_id else "channel==MINE"
    # Prefer metrics with CTR; fall back if the API rejects impressions columns.
    for metrics in (_CHANNEL_DAY_METRICS_WITH_CTR, _CHANNEL_DAY_METRICS):
        try:
            resp = _query_report(
                analytics,
                HttpError,
                ids=ids,
                start=start,
                end=end,
                metrics=metrics,
                dimensions="day",
            )
            headers = resp.get("columnHeaders") or []
            rows = resp.get("rows") or []
            if not rows:
                # Try totals-only query (no day rows for inactive channels).
                resp2 = _query_report(
                    analytics,
                    HttpError,
                    ids=ids,
                    start=start,
                    end=end,
                    metrics=metrics.replace(",impressions,impressionClickThroughRate", "").replace(
                        "impressions,impressionClickThroughRate,", ""
                    )
                    if "impression" in metrics
                    else metrics,
                )
                # If we had CTR metrics and empty days, still try plain metrics totals.
                headers2 = resp2.get("columnHeaders") or []
                rows2 = resp2.get("rows") or []
                totals = _parse_totals_row(headers2, rows2[0] if rows2 else None)
                return totals, DailySeries()
            return _sum_day_rows(headers, rows)
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
            if status in (400, 403) and metrics == _CHANNEL_DAY_METRICS_WITH_CTR:
                continue
            raise
    return PeriodTotals(), DailySeries()


def _parse_published_at(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _video_is_live(snippet: dict, status: dict) -> bool:
    """Return False for scheduled private videos not yet published."""
    privacy = (status.get("privacyStatus") or "").lower()
    publish_at = snippet.get("publishedAt") or ""
    if privacy == "private":
        scheduled = status.get("publishAt") or ""
        if scheduled:
            dt = _parse_published_at(scheduled)
            if dt and dt > datetime.now(timezone.utc):
                return False
    return True


def _list_recent_channel_videos(youtube, *, limit: int = 25) -> list[dict[str, Any]]:
    """List recent uploads from the channel uploads playlist."""
    try:
        ch_resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    except Exception:
        return []
    items = ch_resp.get("items") or []
    if not items:
        return []
    uploads_id = (items[0].get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads_id:
        return []

    video_ids: list[str] = []
    page_token = None
    while len(video_ids) < limit:
        kwargs: dict[str, Any] = {
            "part": "contentDetails",
            "playlistId": uploads_id,
            "maxResults": min(50, limit - len(video_ids)),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            pl_resp = youtube.playlistItems().list(**kwargs).execute()
        except Exception:
            break
        for item in pl_resp.get("items") or []:
            vid = (item.get("contentDetails") or {}).get("videoId")
            if vid:
                video_ids.append(vid)
        page_token = pl_resp.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        return []

    out: list[dict[str, Any]] = []
    for offset in range(0, len(video_ids), 50):
        batch = video_ids[offset : offset + 50]
        try:
            vresp = youtube.videos().list(part="snippet,status,statistics", id=",".join(batch)).execute()
        except Exception:
            continue
        for item in vresp.get("items") or []:
            snippet = item.get("snippet") or {}
            status = item.get("status") or {}
            stats = item.get("statistics") or {}
            if not _video_is_live(snippet, status):
                continue
            out.append(
                {
                    "video_id": item.get("id") or "",
                    "title": snippet.get("title") or "",
                    "published_at": snippet.get("publishedAt") or "",
                    "privacy_status": status.get("privacyStatus") or "",
                    "views_so_far": float(stats.get("viewCount") or 0),
                    "watch_minutes_so_far": 0.0,
                }
            )
    return out[:limit]


def _fetch_video_day_series(
    analytics,
    HttpError,
    *,
    channel_yt_id: str,
    video_id: str,
    start: date,
    end: date,
) -> list[tuple[str, float, float]]:
    """Return (date, views, watch_minutes) rows for one video."""
    ids = f"channel=={channel_yt_id}" if channel_yt_id else "channel==MINE"
    metrics = "views,estimatedMinutesWatched"
    try:
        resp = _query_report(
            analytics,
            HttpError,
            ids=ids,
            start=start,
            end=end,
            metrics=metrics,
            dimensions="day",
            filters=f"video=={video_id}",
        )
    except HttpError:
        return []
    headers = resp.get("columnHeaders") or []
    rows = resp.get("rows") or []
    by_name = {h.get("name"): i for i, h in enumerate(headers)}
    day_idx = by_name.get("day")
    ordered = rows
    if day_idx is not None:
        ordered = sorted(rows, key=lambda r: str(r[day_idx]) if day_idx < len(r) else "")

    def cell(row: list[Any], name: str) -> float:
        idx = by_name.get(name)
        if idx is None or idx >= len(row) or row[idx] is None:
            return 0.0
        try:
            return float(row[idx])
        except (TypeError, ValueError):
            return 0.0

    result: list[tuple[str, float, float]] = []
    for row in ordered:
        d = str(row[day_idx]) if day_idx is not None and day_idx < len(row) else ""
        result.append((d, cell(row, "views"), cell(row, "estimatedMinutesWatched")))
    return result


def _build_velocity_from_days(
    *,
    published_at: str,
    day_rows: list[tuple[str, float, float]],
    lifetime_views: float,
    now: datetime | None = None,
) -> dict[str, float | None]:
    """Compute 24h/72h/7d checkpoints from day-indexed rows (day 0 = publish day)."""
    now = now or datetime.now(timezone.utc)
    pub = _parse_published_at(published_at)
    age_hours = 0.0
    if pub:
        age_hours = (now - pub).total_seconds() / 3600.0

    views_by_day = [v for _, v, _ in day_rows]
    watch_by_day = [w for _, _, w in day_rows]

    def sum_days(days: list[float], n: int) -> float:
        return sum(days[:n]) if days else 0.0

    out: dict[str, float | None] = {
        "age_hours": age_hours,
        "views_so_far": lifetime_views,
        "watch_minutes_so_far": sum(watch_by_day),
        "views_24h": None,
        "views_72h": None,
        "views_7d": None,
        "watch_24h": None,
        "watch_72h": None,
        "watch_7d": None,
    }

    if age_hours >= 24 and len(views_by_day) >= 1:
        out["views_24h"] = sum_days(views_by_day, 1)
        out["watch_24h"] = sum_days(watch_by_day, 1)
    if age_hours >= 72 and len(views_by_day) >= 3:
        out["views_72h"] = sum_days(views_by_day, 3)
        out["watch_72h"] = sum_days(watch_by_day, 3)
    if age_hours >= 168 and len(views_by_day) >= 7:
        out["views_7d"] = sum_days(views_by_day, 7)
        out["watch_7d"] = sum_days(watch_by_day, 7)

    return out


def _compute_median_views_24h(velocities: list[VideoVelocity]) -> float | None:
    """Median views_24h from videos with age >= 48h and complete views_24h."""
    values = [
        v.views_24h
        for v in velocities
        if v.age_hours >= 48 and v.views_24h is not None
    ]
    if not values:
        return None
    values.sort()
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def _mark_underperformers(velocities: list[VideoVelocity], median_views_24h: float | None) -> None:
    """Flag videos aged 48h–168h underperforming vs channel median 24h views."""
    if median_views_24h is None or median_views_24h <= 0:
        return
    threshold = median_views_24h * 0.5
    for v in velocities:
        if not (48 <= v.age_hours <= 168):
            continue
        compare = v.views_72h if v.views_72h is not None else v.views_so_far
        if compare < threshold:
            v.is_underperformer = True


def _fetch_recent_velocities(
    analytics,
    youtube,
    HttpError,
    *,
    channel_yt_id: str,
    days: int = 28,
    max_videos: int = 12,
) -> tuple[list[VideoVelocity], float | None]:
    """Fetch velocity metrics for recent live uploads (last 28d)."""
    end_d = datetime.now(timezone.utc).date() - timedelta(days=1)
    cutoff = end_d - timedelta(days=days - 1)
    recent = _list_recent_channel_videos(youtube, limit=25)
    filtered: list[dict[str, Any]] = []
    for v in recent:
        pub = _parse_published_at(v.get("published_at") or "")
        if pub and pub.date() >= cutoff:
            filtered.append(v)
        if len(filtered) >= max_videos:
            break

    velocities: list[VideoVelocity] = []
    for v in filtered:
        pub = _parse_published_at(v.get("published_at") or "")
        if not pub:
            continue
        pub_date = pub.date()
        series_end = min(end_d, pub_date + timedelta(days=6))
        day_rows = _fetch_video_day_series(
            analytics,
            HttpError,
            channel_yt_id=channel_yt_id,
            video_id=v["video_id"],
            start=pub_date,
            end=series_end,
        )
        metrics = _build_velocity_from_days(
            published_at=v["published_at"],
            day_rows=day_rows,
            lifetime_views=v.get("views_so_far") or 0.0,
        )
        velocities.append(
            VideoVelocity(
                video_id=v["video_id"],
                title=v.get("title") or "",
                url=f"https://youtu.be/{v['video_id']}",
                published_at=v.get("published_at") or "",
                privacy_status=v.get("privacy_status") or "",
                age_hours=metrics["age_hours"] or 0.0,
                views_so_far=metrics["views_so_far"] or 0.0,
                watch_minutes_so_far=metrics["watch_minutes_so_far"] or 0.0,
                views_24h=metrics["views_24h"],
                views_72h=metrics["views_72h"],
                views_7d=metrics["views_7d"],
                watch_24h=metrics["watch_24h"],
                watch_72h=metrics["watch_72h"],
                watch_7d=metrics["watch_7d"],
                is_live=True,
            )
        )

    median = _compute_median_views_24h(velocities)
    if median is not None:
        for v in velocities:
            if v.views_24h is not None and median > 0:
                v.vs_median_24h_pct = round((v.views_24h / median - 1.0) * 100.0, 2)
    _mark_underperformers(velocities, median)
    return velocities, median


def _fetch_top_videos(
    analytics,
    youtube,
    HttpError,
    *,
    channel_yt_id: str,
    start: date,
    end: date,
    limit: int = 10,
) -> list[VideoPerformance]:
    ids = f"channel=={channel_yt_id}" if channel_yt_id else "channel==MINE"
    metrics_candidates = (_VIDEO_METRICS, "views,estimatedMinutesWatched,averageViewPercentage,subscribersGained")
    resp = None
    for metrics in metrics_candidates:
        try:
            resp = _query_report(
                analytics,
                HttpError,
                ids=ids,
                start=start,
                end=end,
                metrics=metrics,
                dimensions="video",
                sort="-views",
                max_results=limit,
            )
            break
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
            if status in (400, 403) and metrics == _VIDEO_METRICS:
                continue
            return []
    if not resp:
        return []

    headers = resp.get("columnHeaders") or []
    rows = resp.get("rows") or []
    by_name = {h.get("name"): i for i, h in enumerate(headers)}
    video_ids: list[str] = []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        vid_idx = by_name.get("video")
        if vid_idx is None or vid_idx >= len(row):
            continue
        vid = str(row[vid_idx])
        video_ids.append(vid)

        def cell(name: str) -> float | None:
            idx = by_name.get(name)
            if idx is None or idx >= len(row) or row[idx] is None:
                return None
            try:
                return float(row[idx])
            except (TypeError, ValueError):
                return None

        ctr_raw = cell("impressionClickThroughRate")
        ctr = None
        if ctr_raw is not None:
            ctr = ctr_raw * 100.0 if ctr_raw <= 1.0 else ctr_raw
        parsed.append(
            {
                "video_id": vid,
                "views": cell("views") or 0.0,
                "watch_minutes": cell("estimatedMinutesWatched") or 0.0,
                "avg_view_percentage": cell("averageViewPercentage"),
                "ctr": ctr,
                "impressions": cell("impressions"),
                "subscribers_gained": cell("subscribersGained") or 0.0,
            }
        )

    titles: dict[str, tuple[str, str]] = {}
    for offset in range(0, len(video_ids), 50):
        batch = video_ids[offset : offset + 50]
        if not batch:
            continue
        try:
            vresp = youtube.videos().list(part="snippet", id=",".join(batch)).execute()
        except Exception:
            continue
        for item in vresp.get("items") or []:
            vid = item.get("id") or ""
            snippet = item.get("snippet") or {}
            titles[vid] = (
                snippet.get("title") or vid,
                snippet.get("publishedAt") or "",
            )

    out: list[VideoPerformance] = []
    for p in parsed:
        title, published = titles.get(p["video_id"], (p["video_id"], ""))
        out.append(
            VideoPerformance(
                video_id=p["video_id"],
                title=title,
                url=f"https://youtu.be/{p['video_id']}",
                published_at=published,
                views=p["views"],
                watch_minutes=p["watch_minutes"],
                avg_view_percentage=p["avg_view_percentage"],
                ctr=p["ctr"],
                impressions=p["impressions"],
                subscribers_gained=p["subscribers_gained"],
            )
        )
    return out


def fetch_channel_performance(
    token_path: str | Path,
    *,
    channel_id: str,
    name: str,
    category: str = "",
    youtube_channel_id: str = "",
    custom_url: str = "",
    days: int = 28,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
    include_top_videos: bool = False,
    top_video_limit: int = 10,
    uploads_in_window: int = 0,
    include_growth_90: bool = False,
    include_recent_velocities: bool = False,
) -> ChannelPerformance:
    """Load Analytics for one channel. Never opens a browser."""
    result = ChannelPerformance(
        channel_id=channel_id,
        name=name,
        category=category,
        youtube_channel_id=youtube_channel_id,
        custom_url=custom_url or "",
        uploads_in_window=uploads_in_window,
    )
    try:
        creds = _load_creds(
            token_path,
            client_secret=client_secret,
            client_config=client_config,
            oauth_port=oauth_port,
        )
    except Exception as exc:
        result.status_code = "error"
        result.message = str(exc)[:240]
        return result

    if not credentials_have_analytics(creds):
        result.status_code = "needs_reauth"
        result.message = "Reconnect this channel to grant YouTube Analytics access"
        # Still try lifetime snapshot via Data API.
        try:
            youtube, _analytics, _HttpError = _build_services(creds)
            ytid, subs, vcount, thumb = _channel_snapshot(youtube)
            result.youtube_channel_id = ytid or youtube_channel_id
            result.subscriber_count = subs
            result.video_count = vcount
            result.thumbnail_url = thumb
        except Exception:
            pass
        return result

    start, end, prior_start, prior_end = date_windows(days)
    try:
        youtube, analytics, HttpError = _build_services(creds)
        ytid, subs, vcount, thumb = _channel_snapshot(youtube)
        result.youtube_channel_id = ytid or youtube_channel_id
        result.subscriber_count = subs
        result.video_count = vcount
        result.thumbnail_url = thumb

        current, series = _fetch_period(
            analytics, HttpError, channel_yt_id=result.youtube_channel_id, start=start, end=end
        )
        prior, _ = _fetch_period(
            analytics,
            HttpError,
            channel_yt_id=result.youtube_channel_id,
            start=prior_start,
            end=prior_end,
        )
        result.current = current
        result.prior = prior
        result.series = series
        result.sparkline = series.views
        result.ok = True
        result.source = "analytics_api"
        subs_net = current.subscribers_gained - current.subscribers_lost
        subs_net_prior = prior.subscribers_gained - prior.subscribers_lost
        result.status_code = classify_health(
            views=current.views,
            views_prior=prior.views,
            subs_net=subs_net,
            subs_net_prior=subs_net_prior,
            has_data=True,
        )
        if include_growth_90:
            _, series_90 = _fetch_period(
                analytics,
                HttpError,
                channel_yt_id=result.youtube_channel_id,
                start=end - timedelta(days=89),
                end=end,
            )
            result.series_90d = series_90
        if include_recent_velocities:
            recent, median = _fetch_recent_velocities(
                analytics,
                youtube,
                HttpError,
                channel_yt_id=result.youtube_channel_id,
                days=28,
                max_videos=12,
            )
            result.recent_videos = recent
            result.median_views_24h = median
        if include_top_videos:
            result.top_videos = _fetch_top_videos(
                analytics,
                youtube,
                HttpError,
                channel_yt_id=result.youtube_channel_id,
                start=start,
                end=end,
                limit=top_video_limit,
            )
    except Exception as exc:
        result.ok = False
        result.source = "none"
        result.status_code = "error"
        result.message = str(exc)[:240]
    return result


def fetch_video_window_metrics(
    token_path: str | Path,
    video_id: str,
    *,
    youtube_channel_id: str = "",
    days: int = 28,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
) -> VideoPerformance | None:
    """Analytics window totals for a single video (views, CTR, watch, etc.)."""
    if not video_id:
        return None
    try:
        creds = _load_creds(
            token_path,
            client_secret=client_secret,
            client_config=client_config,
            oauth_port=oauth_port,
        )
    except Exception:
        return None
    if not credentials_have_analytics(creds):
        return None
    start, end, _ps, _pe = date_windows(days)
    try:
        youtube, analytics, HttpError = _build_services(creds)
        if not youtube_channel_id:
            ytid, _s, _v, _t = _channel_snapshot(youtube)
            youtube_channel_id = ytid
        ids = f"channel=={youtube_channel_id}" if youtube_channel_id else "channel==MINE"
        metrics_candidates = (
            _VIDEO_METRICS,
            "views,estimatedMinutesWatched,averageViewPercentage,subscribersGained",
        )
        resp = None
        for metrics in metrics_candidates:
            try:
                resp = _query_report(
                    analytics,
                    HttpError,
                    ids=ids,
                    start=start,
                    end=end,
                    metrics=metrics,
                    dimensions="video",
                    filters=f"video=={video_id}",
                    max_results=1,
                )
                break
            except HttpError as exc:
                status = int(getattr(exc.resp, "status", 0) or 0)
                if status in (400, 403) and metrics == _VIDEO_METRICS:
                    continue
                return None
        if not resp:
            return None
        headers = resp.get("columnHeaders") or []
        rows = resp.get("rows") or []
        if not rows:
            return VideoPerformance(video_id=video_id, url=f"https://youtu.be/{video_id}")
        by_name = {h.get("name"): i for i, h in enumerate(headers)}
        row = rows[0]

        def cell(name: str) -> float | None:
            idx = by_name.get(name)
            if idx is None or idx >= len(row) or row[idx] is None:
                return None
            try:
                return float(row[idx])
            except (TypeError, ValueError):
                return None

        ctr_raw = cell("impressionClickThroughRate")
        ctr = None
        if ctr_raw is not None:
            ctr = ctr_raw * 100.0 if ctr_raw <= 1.0 else ctr_raw
        return VideoPerformance(
            video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            views=cell("views") or 0.0,
            watch_minutes=cell("estimatedMinutesWatched") or 0.0,
            avg_view_percentage=cell("averageViewPercentage"),
            ctr=ctr,
            impressions=cell("impressions"),
            subscribers_gained=cell("subscribersGained") or 0.0,
        )
    except Exception:
        return None


# Re-export for tests / callers
__all__ = [
    "ChannelPerformance",
    "DailySeries",
    "PeriodTotals",
    "VideoPerformance",
    "VideoVelocity",
    "classify_health",
    "date_windows",
    "fetch_channel_performance",
    "fetch_video_window_metrics",
    "_build_velocity_from_days",
    "_compute_median_views_24h",
    "_delta_pct",
    "_mark_underperformers",
]
