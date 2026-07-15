"""Aggregate channel YouTube analytics into network / category / drill-in views."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from uploader.channels import AppConfig, ChannelConfig
from uploader.job_views import load_channel_jobs
from uploader.oauth import OAuthSettings
from uploader.registry import STATUS_UPLOADED
from uploader.youtube_analytics import (
    ChannelPerformance,
    DailySeries,
    PeriodTotals,
    VideoVelocity,
    _delta_pct,
    date_windows,
    fetch_channel_performance,
)

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _ttl() -> float:
    raw = os.environ.get("UPLOADER_ANALYTICS_CACHE_TTL", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 6 * 3600.0  # 6 hours


def _cache_get(key: str) -> dict[str, Any] | None:
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if time.monotonic() - stored_at > _ttl():
            del _cache[key]
            return None
        return value


def _cache_set(key: str, value: dict[str, Any]) -> None:
    with _lock:
        _cache[key] = (time.monotonic(), value)


def clear_analytics_cache() -> None:
    with _lock:
        _cache.clear()


def _parse_ts(value: str) -> datetime | None:
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


def count_uploads_in_window(channel: ChannelConfig, *, start, end, base) -> int:
    """Count registry uploads whose uploaded_at falls in [start, end] (dates, UTC)."""
    try:
        bundle = load_channel_jobs(channel, base=base)
        uploaded = bundle.uploaded_jobs
    except Exception:
        return 0
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    n = 0
    for job in uploaded:
        if job.status != STATUS_UPLOADED and job.status != "uploaded":
            continue
        ts = _parse_ts(job.uploaded_at) or _parse_ts(job.publish_at)
        if ts and start_dt <= ts <= end_dt:
            n += 1
    return n


def _metric_block(current: float, prior: float) -> dict[str, Any]:
    return {
        "value": round(current, 2),
        "prior": round(prior, 2),
        "delta_pct": None if _delta_pct(current, prior) is None else round(_delta_pct(current, prior), 2),
    }


def _opt_metric(current: float | None, prior: float | None) -> dict[str, Any]:
    if current is None and prior is None:
        return {"value": None, "prior": None, "delta_pct": None}
    c = float(current or 0.0)
    p = float(prior or 0.0)
    if current is None:
        return {"value": None, "prior": round(p, 2) if prior is not None else None, "delta_pct": None}
    return {
        "value": round(c, 2),
        "prior": round(p, 2) if prior is not None else None,
        "delta_pct": None if prior is None or _delta_pct(c, p) is None else round(_delta_pct(c, p), 2),
    }


def _views_per_upload(views: float, uploads: int) -> float | None:
    if uploads <= 0:
        return None
    return round(views / uploads, 2)


def _subs_per_day(subs_net: float, days: int) -> float | None:
    if days <= 0:
        return None
    return round(subs_net / days, 4)


def _subs_per_1k_views(subs_net: float, views: float) -> float | None:
    if views <= 0:
        return None
    return round((subs_net / views) * 1000.0, 4)


def _series_to_dict(series: DailySeries | None) -> dict[str, Any] | None:
    if series is None or not series.dates:
        return None
    return {
        "dates": list(series.dates),
        "views": [round(v, 2) for v in series.views],
        "watch_minutes": [round(v, 2) for v in series.watch_minutes],
        "subs_net": [round(v, 2) for v in series.subs_net],
    }


def _velocity_to_dict(v: VideoVelocity, *, channel_id: str, channel_name: str, category: str) -> dict[str, Any]:
    return {
        "video_id": v.video_id,
        "title": v.title,
        "url": v.url,
        "published_at": v.published_at,
        "privacy_status": v.privacy_status,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "category": category,
        "age_hours": round(v.age_hours, 2),
        "views_so_far": round(v.views_so_far, 2),
        "watch_minutes_so_far": round(v.watch_minutes_so_far, 2),
        "views_24h": None if v.views_24h is None else round(v.views_24h, 2),
        "views_72h": None if v.views_72h is None else round(v.views_72h, 2),
        "views_7d": None if v.views_7d is None else round(v.views_7d, 2),
        "watch_24h": None if v.watch_24h is None else round(v.watch_24h, 2),
        "watch_72h": None if v.watch_72h is None else round(v.watch_72h, 2),
        "watch_7d": None if v.watch_7d is None else round(v.watch_7d, 2),
        "vs_median_24h_pct": v.vs_median_24h_pct,
        "is_underperformer": v.is_underperformer,
        "is_live": v.is_live,
    }


def channel_to_out(perf: ChannelPerformance, *, days: int = 28) -> dict[str, Any]:
    cur = perf.current
    prior = perf.prior
    subs_net = cur.subscribers_gained - cur.subscribers_lost
    subs_net_prior = prior.subscribers_gained - prior.subscribers_lost
    return {
        "channel_id": perf.channel_id,
        "name": perf.name,
        "category": perf.category or "",
        "youtube_channel_id": perf.youtube_channel_id,
        "status": perf.status_code,
        "message": perf.message,
        "ok": perf.ok,
        "source": perf.source,
        "views": _metric_block(cur.views, prior.views),
        "watch_minutes": _metric_block(cur.watch_minutes, prior.watch_minutes),
        "subs_net": _metric_block(subs_net, subs_net_prior),
        "ctr": _opt_metric(cur.ctr, prior.ctr),
        "avg_view_percentage": _opt_metric(cur.avg_view_percentage, prior.avg_view_percentage),
        "avg_view_duration_seconds": _opt_metric(
            cur.avg_view_duration_seconds, prior.avg_view_duration_seconds
        ),
        "likes": round(cur.likes, 2),
        "comments": round(cur.comments, 2),
        "shares": round(cur.shares, 2),
        "impressions": None if cur.impressions is None else round(cur.impressions, 2),
        "uploads": perf.uploads_in_window,
        "views_per_upload": _views_per_upload(cur.views, perf.uploads_in_window),
        "sparkline": [round(v, 2) for v in perf.sparkline],
        "growth_series": _series_to_dict(perf.series),
        "growth_series_90d": _series_to_dict(perf.series_90d),
        "median_views_24h": None if perf.median_views_24h is None else round(perf.median_views_24h, 2),
        "subs_per_day": _subs_per_day(subs_net, days),
        "subs_per_1k_views": _subs_per_1k_views(subs_net, cur.views),
        "recent_videos": [
            _velocity_to_dict(v, channel_id=perf.channel_id, channel_name=perf.name, category=perf.category or "")
            for v in perf.recent_videos
        ],
        "subscriber_count": perf.subscriber_count,
        "video_count": perf.video_count,
        "top_videos": [
            {
                "video_id": v.video_id,
                "title": v.title,
                "url": v.url,
                "published_at": v.published_at,
                "views": round(v.views, 2),
                "watch_minutes": round(v.watch_minutes, 2),
                "avg_view_percentage": None
                if v.avg_view_percentage is None
                else round(v.avg_view_percentage, 2),
                "ctr": None if v.ctr is None else round(v.ctr, 2),
                "impressions": None if v.impressions is None else round(v.impressions, 2),
                "subscribers_gained": round(v.subscribers_gained, 2),
            }
            for v in perf.top_videos
        ],
    }


def _sum_period(channels: list[ChannelPerformance]) -> tuple[PeriodTotals, PeriodTotals]:
    cur = PeriodTotals()
    prior = PeriodTotals()
    avp_w = avp_n = 0.0
    avp_pw = avp_pn = 0.0
    ctr_w = ctr_n = 0.0
    ctr_pw = ctr_pn = 0.0
    avd_w = avd_n = 0.0
    avd_pw = avd_pn = 0.0
    has_imp = has_imp_p = False

    for ch in channels:
        if not ch.ok:
            continue
        c, p = ch.current, ch.prior
        cur.views += c.views
        cur.watch_minutes += c.watch_minutes
        cur.subscribers_gained += c.subscribers_gained
        cur.subscribers_lost += c.subscribers_lost
        cur.likes += c.likes
        cur.comments += c.comments
        cur.shares += c.shares
        prior.views += p.views
        prior.watch_minutes += p.watch_minutes
        prior.subscribers_gained += p.subscribers_gained
        prior.subscribers_lost += p.subscribers_lost
        if c.impressions is not None:
            has_imp = True
            cur.impressions = (cur.impressions or 0.0) + c.impressions
        if p.impressions is not None:
            has_imp_p = True
            prior.impressions = (prior.impressions or 0.0) + p.impressions
        if c.avg_view_percentage is not None and c.views > 0:
            avp_w += c.avg_view_percentage * c.views
            avp_n += c.views
        if p.avg_view_percentage is not None and p.views > 0:
            avp_pw += p.avg_view_percentage * p.views
            avp_pn += p.views
        if c.ctr is not None and c.impressions:
            ctr_w += c.ctr * c.impressions
            ctr_n += c.impressions
        if p.ctr is not None and p.impressions:
            ctr_pw += p.ctr * p.impressions
            ctr_pn += p.impressions
        if c.avg_view_duration_seconds is not None and c.views > 0:
            avd_w += c.avg_view_duration_seconds * c.views
            avd_n += c.views
        if p.avg_view_duration_seconds is not None and p.views > 0:
            avd_pw += p.avg_view_duration_seconds * p.views
            avd_pn += p.views

    if not has_imp:
        cur.impressions = None
    if not has_imp_p:
        prior.impressions = None
    cur.avg_view_percentage = (avp_w / avp_n) if avp_n else None
    prior.avg_view_percentage = (avp_pw / avp_pn) if avp_pn else None
    cur.ctr = (ctr_w / ctr_n) if ctr_n else None
    prior.ctr = (ctr_pw / ctr_pn) if ctr_pn else None
    cur.avg_view_duration_seconds = (avd_w / avd_n) if avd_n else None
    prior.avg_view_duration_seconds = (avd_pw / avd_pn) if avd_pn else None
    return cur, prior


def _pulse(cur: PeriodTotals, prior: PeriodTotals, *, days: int = 28) -> dict[str, Any]:
    subs = cur.subscribers_gained - cur.subscribers_lost
    subs_p = prior.subscribers_gained - prior.subscribers_lost
    return {
        "views": _metric_block(cur.views, prior.views),
        "watch_minutes": _metric_block(cur.watch_minutes, prior.watch_minutes),
        "subs_net": _metric_block(subs, subs_p),
        "ctr": _opt_metric(cur.ctr, prior.ctr),
        "avg_view_percentage": _opt_metric(cur.avg_view_percentage, prior.avg_view_percentage),
        "subs_per_day": _subs_per_day(subs, days),
        "subs_per_1k_views": _subs_per_1k_views(subs, cur.views),
    }


def _category_health(channels: list[ChannelPerformance]) -> str:
    ok = [c for c in channels if c.ok]
    if not ok:
        return "needs_data"
    # Views-weighted: majority of views decide, else count majority.
    total_views = sum(c.current.views for c in ok) or 1.0
    scores = {"growing": 0.0, "flat": 0.0, "cooling": 0.0, "needs_data": 0.0}
    for c in ok:
        weight = (c.current.views / total_views) if total_views else (1.0 / len(ok))
        key = c.status_code if c.status_code in scores else "needs_data"
        scores[key] += weight
    return max(scores, key=scores.get)


def _category_insights(
    *,
    category: str,
    channels: list[ChannelPerformance],
    network_views: float,
) -> list[str]:
    insights: list[str] = []
    ok = [c for c in channels if c.ok]
    if not ok:
        return ["No Analytics data yet — reconnect channels to grant access."]
    cat_views = sum(c.current.views for c in ok)
    if network_views > 0:
        share = cat_views / network_views * 100.0
        insights.append(f"{share:.0f}% of network views in this window")
    if cat_views > 0:
        ranked = sorted(ok, key=lambda c: c.current.views, reverse=True)
        top = ranked[0]
        share = top.current.views / cat_views * 100.0
        if share >= 60.0 and len(ok) > 1:
            insights.append(f"Carrier risk: {top.name} drives {share:.0f}% of category views")
        elif len(ok) > 1:
            insights.append(f"Top contributor: {top.name} ({share:.0f}% of category views)")

    efficiency = []
    for c in ok:
        if c.uploads_in_window > 0:
            efficiency.append((c, c.current.views / c.uploads_in_window))
    if efficiency:
        efficiency.sort(key=lambda x: x[1], reverse=True)
        best_ch, best_vpu = efficiency[0]
        insights.append(f"Efficiency leader: {best_ch.name} ({best_vpu:,.0f} views/upload)")
        median = sorted(v for _, v in efficiency)[len(efficiency) // 2]
        laggards = [c.name for c, v in efficiency if v < median * 0.8]
        if laggards:
            insights.append(f"Below peer median efficiency: {', '.join(laggards[:3])}")

    high_post_low_eff = [
        c.name
        for c, v in efficiency
        if c.uploads_in_window >= 3 and v < (cat_views / max(1, sum(x.uploads_in_window for x in ok))) * 0.6
    ]
    if high_post_low_eff:
        insights.append(f"Quantity trap watch: {', '.join(high_post_low_eff[:3])}")
    return insights


def _rollup_growth_series(channels: list[ChannelPerformance]) -> DailySeries:
    """Sum daily series across channels, aligned by date."""
    ok = [c for c in channels if c.ok and c.series.dates]
    if not ok:
        return DailySeries()
    all_dates: list[str] = []
    seen: set[str] = set()
    for ch in ok:
        for d in ch.series.dates:
            if d and d not in seen:
                seen.add(d)
                all_dates.append(d)
    all_dates.sort()
    out = DailySeries(dates=all_dates, views=[0.0] * len(all_dates), watch_minutes=[0.0] * len(all_dates), subs_net=[0.0] * len(all_dates))
    idx_by_date = {d: i for i, d in enumerate(all_dates)}
    for ch in ok:
        for i, d in enumerate(ch.series.dates):
            if d not in idx_by_date:
                continue
            j = idx_by_date[d]
            if i < len(ch.series.views):
                out.views[j] += ch.series.views[i]
            if i < len(ch.series.watch_minutes):
                out.watch_minutes[j] += ch.series.watch_minutes[i]
            if i < len(ch.series.subs_net):
                out.subs_net[j] += ch.series.subs_net[i]
    return out


def _rollup_growth_series_90d(channels: list[ChannelPerformance]) -> DailySeries | None:
    ok = [c for c in channels if c.ok and c.series_90d and c.series_90d.dates]
    if not ok:
        return None
    all_dates: list[str] = []
    seen: set[str] = set()
    for ch in ok:
        assert ch.series_90d is not None
        for d in ch.series_90d.dates:
            if d and d not in seen:
                seen.add(d)
                all_dates.append(d)
    all_dates.sort()
    out = DailySeries(dates=all_dates, views=[0.0] * len(all_dates), watch_minutes=[0.0] * len(all_dates), subs_net=[0.0] * len(all_dates))
    idx_by_date = {d: i for i, d in enumerate(all_dates)}
    for ch in ok:
        s = ch.series_90d
        assert s is not None
        for i, d in enumerate(s.dates):
            if d not in idx_by_date:
                continue
            j = idx_by_date[d]
            if i < len(s.views):
                out.views[j] += s.views[i]
            if i < len(s.watch_minutes):
                out.watch_minutes[j] += s.watch_minutes[i]
            if i < len(s.subs_net):
                out.subs_net[j] += s.subs_net[i]
    return out


def _merge_recent_videos(channels: list[ChannelPerformance], *, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ch in channels:
        for v in ch.recent_videos:
            rows.append(
                _velocity_to_dict(v, channel_id=ch.channel_id, channel_name=ch.name, category=ch.category or "")
            )
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    return rows[:limit]


def _collect_underperformers(channels: list[ChannelPerformance], *, limit: int = 20) -> list[dict[str, Any]]:
    rows = _merge_recent_videos(channels, limit=9999)
    under = [r for r in rows if r.get("is_underperformer")]
    return under[:limit]


def _avg_metric(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _cohort_side(videos: list[dict[str, Any]], *, min_age_72h: bool = False) -> dict[str, Any]:
    uploads = len(videos)
    views_72h_vals: list[float | None] = []
    views_7d_vals: list[float | None] = []
    views_so_far_vals: list[float] = []
    for v in videos:
        age = v.get("age_hours") or 0.0
        if min_age_72h:
            if age >= 72 and v.get("views_72h") is not None:
                views_72h_vals.append(v["views_72h"])
            if age >= 168 and v.get("views_7d") is not None:
                views_7d_vals.append(v["views_7d"])
        else:
            if v.get("views_72h") is not None:
                views_72h_vals.append(v["views_72h"])
            if v.get("views_7d") is not None:
                views_7d_vals.append(v["views_7d"])
        views_so_far_vals.append(v.get("views_so_far") or 0.0)
    return {
        "uploads": uploads,
        "avg_views_72h": _avg_metric(views_72h_vals),
        "avg_views_7d": _avg_metric(views_7d_vals),
        "avg_views_so_far": _avg_metric(views_so_far_vals) if views_so_far_vals else None,
    }


def build_cohort_compare(all_videos: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Compare this week (last 7d uploads) vs last week (7-14d ago)."""
    now = now or datetime.now(timezone.utc)
    this_week: list[dict[str, Any]] = []
    last_week: list[dict[str, Any]] = []
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    for v in all_videos:
        pub = _parse_ts(v.get("published_at") or "")
        if not pub:
            continue
        if pub >= week_ago:
            this_week.append(v)
        elif pub >= two_weeks_ago:
            last_week.append(v)

    this_side = _cohort_side(this_week, min_age_72h=True)
    last_side = _cohort_side(last_week, min_age_72h=False)

    delta_72h = None
    if this_side["avg_views_72h"] is not None and last_side["avg_views_72h"] is not None and last_side["avg_views_72h"] > 0:
        delta_72h = round((this_side["avg_views_72h"] / last_side["avg_views_72h"] - 1.0) * 100.0, 2)
    delta_7d = None
    if this_side["avg_views_7d"] is not None and last_side["avg_views_7d"] is not None and last_side["avg_views_7d"] > 0:
        delta_7d = round((this_side["avg_views_7d"] / last_side["avg_views_7d"] - 1.0) * 100.0, 2)

    return {
        "this_week": this_side,
        "last_week": last_side,
        "delta_views_72h_pct": delta_72h,
        "delta_views_7d_pct": delta_7d,
    }


def _rollup_sparkline(channels: list[ChannelPerformance]) -> list[float]:
    series = [c.sparkline for c in channels if c.ok and c.sparkline]
    if not series:
        return []
    length = max(len(s) for s in series)
    out = [0.0] * length
    for s in series:
        # Right-align shorter sparklines
        offset = length - len(s)
        for i, v in enumerate(s):
            out[offset + i] += v
    return [round(v, 2) for v in out]


def build_category_out(
    category: str,
    channels: list[ChannelPerformance],
    *,
    network_views: float,
    include_channels: bool = True,
    days: int = 28,
) -> dict[str, Any]:
    cur, prior = _sum_period(channels)
    uploads = sum(c.uploads_in_window for c in channels)
    health = _category_health(channels)
    channel_outs = [channel_to_out(c, days=days) for c in channels]
    if include_channels:
        channel_outs.sort(key=lambda c: (c["views_per_upload"] is None, -(c["views_per_upload"] or 0)))
    share = None
    if network_views > 0:
        share = round(cur.views / network_views * 100.0, 2)
    subs_net = cur.subscribers_gained - cur.subscribers_lost
    subs_net_prior = prior.subscribers_gained - prior.subscribers_lost
    return {
        "category": category,
        "label": category if category else "Uncategorized",
        "channel_count": len(channels),
        "health": health,
        "views": _metric_block(cur.views, prior.views),
        "watch_minutes": _metric_block(cur.watch_minutes, prior.watch_minutes),
        "subs_net": _metric_block(subs_net, subs_net_prior),
        "ctr": _opt_metric(cur.ctr, prior.ctr),
        "avg_view_percentage": _opt_metric(cur.avg_view_percentage, prior.avg_view_percentage),
        "uploads": uploads,
        "views_per_upload": _views_per_upload(cur.views, uploads),
        "subs_per_day": _subs_per_day(subs_net, days),
        "subs_per_1k_views": _subs_per_1k_views(subs_net, cur.views),
        "network_view_share_pct": share,
        "carrier_risk": bool(
            cur.views > 0
            and len([c for c in channels if c.ok]) > 1
            and max((c.current.views for c in channels if c.ok), default=0) / cur.views >= 0.6
        ),
        "sparkline": _rollup_sparkline(channels),
        "growth_series": _series_to_dict(_rollup_growth_series(channels)),
        "growth_series_90d": _series_to_dict(_rollup_growth_series_90d(channels)),
        "insights": _category_insights(category=category, channels=channels, network_views=network_views),
        "channels": channel_outs if include_channels else [],
        "top_videos": _merge_top_videos(channels, limit=15),
        "recent_videos": _merge_recent_videos(channels, limit=40),
    }


def _merge_top_videos(channels: list[ChannelPerformance], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ch in channels:
        for v in ch.top_videos:
            rows.append(
                {
                    "video_id": v.video_id,
                    "title": v.title,
                    "url": v.url,
                    "published_at": v.published_at,
                    "channel_id": ch.channel_id,
                    "channel_name": ch.name,
                    "category": ch.category or "",
                    "views": round(v.views, 2),
                    "watch_minutes": round(v.watch_minutes, 2),
                    "avg_view_percentage": None
                    if v.avg_view_percentage is None
                    else round(v.avg_view_percentage, 2),
                    "ctr": None if v.ctr is None else round(v.ctr, 2),
                }
            )
    rows.sort(key=lambda r: r["views"], reverse=True)
    return rows[:limit]


def _fetch_all_channels(
    config: AppConfig,
    oauth: OAuthSettings,
    *,
    days: int,
    base,
    include_top_videos: bool,
    include_growth_90: bool = False,
    include_recent_velocities: bool = False,
) -> list[ChannelPerformance]:
    start, end, _, _ = date_windows(days)
    results: list[ChannelPerformance] = []
    max_workers = min(8, max(1, len(config.channels)))

    def one(ch: ChannelConfig) -> ChannelPerformance:
        uploads = count_uploads_in_window(ch, start=start, end=end, base=base)
        return fetch_channel_performance(
            ch.token_path,
            channel_id=ch.id,
            name=ch.name or ch.id,
            category=ch.category or "",
            youtube_channel_id=ch.youtube_channel_id or "",
            days=days,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
            include_top_videos=include_top_videos,
            include_growth_90=include_growth_90,
            include_recent_velocities=include_recent_velocities,
            uploads_in_window=uploads,
        )

    if not config.channels:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(one, ch): ch.id for ch in config.channels}
        by_id: dict[str, ChannelPerformance] = {}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                by_id[cid] = fut.result()
            except Exception as exc:
                ch = next(c for c in config.channels if c.id == cid)
                by_id[cid] = ChannelPerformance(
                    channel_id=ch.id,
                    name=ch.name or ch.id,
                    category=ch.category or "",
                    youtube_channel_id=ch.youtube_channel_id or "",
                    status_code="error",
                    message=str(exc)[:240],
                )
    # Stable order matching config
    for ch in config.channels:
        results.append(by_id[ch.id])
    return results


def build_analytics_overview(
    config: AppConfig,
    oauth: OAuthSettings,
    *,
    days: int = 28,
    base=None,
    refresh: bool = False,
    include_top_videos: bool = False,
) -> dict[str, Any]:
    days = 7 if days == 7 else 28
    cache_key = f"overview:v2:{days}:tops={int(include_top_videos)}"
    if not refresh:
        cached = _cache_get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out

    perfs = _fetch_all_channels(
        config,
        oauth,
        days=days,
        base=base,
        include_top_videos=include_top_videos,
        include_growth_90=True,
        include_recent_velocities=True,
    )
    start, end, prior_start, prior_end = date_windows(days)
    net_cur, net_prior = _sum_period(perfs)
    network_views = net_cur.views

    # Group by category
    by_cat: dict[str, list[ChannelPerformance]] = {}
    for p in perfs:
        key = (p.category or "").strip()
        by_cat.setdefault(key, []).append(p)

    # Prefer configured category order, then any extras, then uncategorized last
    ordered_keys: list[str] = []
    for name in config.categories or []:
        if name in by_cat and name not in ordered_keys:
            ordered_keys.append(name)
    for key in sorted(k for k in by_cat if k and k not in ordered_keys):
        ordered_keys.append(key)
    if "" in by_cat:
        ordered_keys.append("")

    categories = [
        build_category_out(k, by_cat[k], network_views=network_views, include_channels=True, days=days)
        for k in ordered_keys
    ]
    # Sort scoreboard by views delta (movement), then views
    categories.sort(
        key=lambda c: (
            c["views"]["delta_pct"] is None,
            -(c["views"]["delta_pct"] if c["views"]["delta_pct"] is not None else -9999),
            -c["views"]["value"],
        )
    )

    channel_outs = [channel_to_out(p, days=days) for p in perfs]
    health_counts = {"growing": 0, "flat": 0, "cooling": 0, "needs_data": 0}
    for c in channel_outs:
        key = c["status"] if c["status"] in health_counts else "needs_data"
        if c["status"] in ("needs_reauth", "error"):
            key = "needs_data"
        health_counts[key] += 1

    with_eff = [c for c in channel_outs if c["ok"] and c["views_per_upload"] is not None]
    with_eff_sorted = sorted(with_eff, key=lambda c: c["views_per_upload"], reverse=True)
    leaderboard_top = with_eff_sorted[:5]
    leaderboard_bottom = list(reversed(with_eff_sorted[-5:])) if len(with_eff_sorted) > 5 else list(reversed(with_eff_sorted))

    breakouts = [
        c
        for c in channel_outs
        if c["ok"] and c["views"]["delta_pct"] is not None and c["views"]["delta_pct"] >= 50
    ]
    breakouts.sort(key=lambda c: c["views"]["delta_pct"], reverse=True)

    cooling = [
        c
        for c in channel_outs
        if c["ok"] and c["views"]["delta_pct"] is not None and c["views"]["delta_pct"] <= -40
    ]
    cooling.sort(key=lambda c: c["views"]["delta_pct"])

    all_recent = _merge_recent_videos(perfs, limit=9999)
    growth_d28 = _series_to_dict(_rollup_growth_series(perfs))
    growth_d90 = _series_to_dict(_rollup_growth_series_90d(perfs))
    growth_curves: dict[str, Any] = {}
    if growth_d28:
        growth_curves["d28"] = growth_d28
    if growth_d90:
        growth_curves["d90"] = growth_d90

    payload = {
        "days": days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "prior_start_date": prior_start.isoformat(),
        "prior_end_date": prior_end.isoformat(),
        "refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cached": False,
        "network": _pulse(net_cur, net_prior, days=days),
        "health": health_counts,
        "categories": categories,
        "channels": channel_outs,
        "leaderboard_top": leaderboard_top,
        "leaderboard_bottom": leaderboard_bottom,
        "breakouts": breakouts[:8],
        "cooling": cooling[:8],
        "needs_reauth_count": sum(1 for c in channel_outs if c["status"] == "needs_reauth"),
        "growth_curves": growth_curves,
        "new_uploads": _merge_recent_videos(perfs, limit=40),
        "underperformers": _collect_underperformers(perfs, limit=20),
        "cohorts": build_cohort_compare(all_recent),
    }
    _cache_set(cache_key, payload)
    return payload


def build_category_detail(
    config: AppConfig,
    oauth: OAuthSettings,
    category: str,
    *,
    days: int = 28,
    base=None,
    refresh: bool = False,
) -> dict[str, Any] | None:
    days = 7 if days == 7 else 28
    want = (category or "").strip()
    cache_key = f"category:v2:{want.casefold()}:{days}"
    if not refresh:
        cached = _cache_get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out

    start, end, prior_start, prior_end = date_windows(days)
    matched = [
        ch
        for ch in config.channels
        if (ch.category or "").strip().casefold() == want.casefold()
    ]
    if not matched and want:
        # Allow literal empty / "uncategorized"
        if want.casefold() in ("uncategorized", "(none)", "none"):
            matched = [ch for ch in config.channels if not (ch.category or "").strip()]
            want = ""
        else:
            return None
    if not matched and want == "":
        matched = [ch for ch in config.channels if not (ch.category or "").strip()]
        if not matched:
            return None

    max_workers = min(8, max(1, len(matched)))

    def one(ch: ChannelConfig) -> ChannelPerformance:
        uploads = count_uploads_in_window(ch, start=start, end=end, base=base)
        return fetch_channel_performance(
            ch.token_path,
            channel_id=ch.id,
            name=ch.name or ch.id,
            category=ch.category or "",
            youtube_channel_id=ch.youtube_channel_id or "",
            days=days,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
            include_top_videos=True,
            top_video_limit=12,
            include_growth_90=True,
            include_recent_velocities=True,
            uploads_in_window=uploads,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(one, ch): ch for ch in matched}
        by_id: dict[str, ChannelPerformance] = {}
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                by_id[ch.id] = fut.result()
            except Exception as exc:
                by_id[ch.id] = ChannelPerformance(
                    channel_id=ch.id,
                    name=ch.name or ch.id,
                    category=ch.category or "",
                    status_code="error",
                    message=str(exc)[:240],
                )
    ordered = [by_id[ch.id] for ch in matched]

    # Network views for share % — use overview cache when possible
    network_views = 0.0
    overview = _cache_get(f"overview:v2:{days}:tops=0")
    if overview:
        network_views = float(overview.get("network", {}).get("views", {}).get("value") or 0)
    else:
        network_views = sum(p.current.views for p in ordered if p.ok)

    cat = build_category_out(want, ordered, network_views=network_views or 1.0, include_channels=True, days=days)
    payload = {
        "days": days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "prior_start_date": prior_start.isoformat(),
        "prior_end_date": prior_end.isoformat(),
        "refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cached": False,
        "network_views": network_views,
        "category": cat,
    }
    _cache_set(cache_key, payload)
    return payload


def build_channel_detail(
    config: AppConfig,
    oauth: OAuthSettings,
    channel: ChannelConfig,
    *,
    days: int = 28,
    base=None,
    refresh: bool = False,
) -> dict[str, Any]:
    days = 7 if days == 7 else 28
    cache_key = f"channel:v2:{channel.id}:{days}"
    if not refresh:
        cached = _cache_get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out

    start, end, prior_start, prior_end = date_windows(days)
    uploads = count_uploads_in_window(channel, start=start, end=end, base=base)
    perf = fetch_channel_performance(
        channel.token_path,
        channel_id=channel.id,
        name=channel.name or channel.id,
        category=channel.category or "",
        youtube_channel_id=channel.youtube_channel_id or "",
        days=days,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
        include_top_videos=True,
        top_video_limit=15,
        include_growth_90=True,
        include_recent_velocities=True,
        uploads_in_window=uploads,
    )
    channel_out = channel_to_out(perf, days=days)

    # Peer median from cached overview when available (avoids N extra Analytics calls).
    peer_median = None
    vs_peer = None
    overview = _cache_get(f"overview:v2:{days}:tops=0") or _cache_get(f"overview:v2:{days}:tops=1")
    if overview:
        cat = channel.category or ""
        peer_vpus = [
            c["views_per_upload"]
            for c in overview.get("channels") or []
            if c.get("category") == cat
            and c.get("channel_id") != channel.id
            and c.get("ok")
            and c.get("views_per_upload") is not None
        ]
        if peer_vpus:
            peer_vpus.sort()
            peer_median = round(peer_vpus[len(peer_vpus) // 2], 2)
            if channel_out["views_per_upload"] and peer_median:
                vs_peer = round((channel_out["views_per_upload"] / peer_median - 1.0) * 100.0, 2)

    all_recent = _merge_recent_videos([perf], limit=9999)
    growth_d28 = _series_to_dict(perf.series)
    growth_d90 = _series_to_dict(perf.series_90d)
    growth_curves: dict[str, Any] = {}
    if growth_d28:
        growth_curves["d28"] = growth_d28
    if growth_d90:
        growth_curves["d90"] = growth_d90

    payload = {
        "days": days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "prior_start_date": prior_start.isoformat(),
        "prior_end_date": prior_end.isoformat(),
        "refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cached": False,
        "channel": channel_out,
        "peer_median_views_per_upload": peer_median,
        "vs_peer_median_pct": vs_peer,
        "growth_curves": growth_curves,
        "new_uploads": all_recent[:40],
        "underperformers": _collect_underperformers([perf], limit=20),
        "cohorts": build_cohort_compare(all_recent),
    }
    _cache_set(cache_key, payload)
    return payload
