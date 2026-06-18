"""CLI entry point for the YouTube uploader microservice."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from uploader import __version__
from uploader.channels import get_channel, load_config
from uploader.channel_list import list_channel_videos
from uploader.registry import UploadEntry, UploadRegistry
from uploader.scheduler import compute_publish_schedule, parse_start, run_channel
from uploader.youtube_client import get_credentials


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="uploader",
        description="Upload pre-rendered videos to YouTube with scheduling and multi-channel support.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to channels.yaml (default: config/channels.yaml or UPLOADER_CONFIG).",
    )

    sub = p.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Run OAuth browser flow for a channel.")
    auth.add_argument("--channel", required=True, help="Channel id from channels.yaml.")

    plan = sub.add_parser("plan", help="Preview publish schedule for pending jobs (no upload).")
    plan.add_argument("--channel", required=True)
    plan.add_argument("--start", default=None, metavar="'YYYY-MM-DD HH:MM'")
    plan.add_argument("--interval-hours", type=float, default=None)
    plan.add_argument("--limit", type=int, default=None)
    plan.add_argument("--no-schedule", action="store_true")

    run = sub.add_parser("run", help="Process all pending uploads for a channel.")
    run.add_argument("--channel", required=True)
    run.add_argument("--start", default=None, metavar="'YYYY-MM-DD HH:MM'")
    run.add_argument("--interval-hours", type=float, default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--no-schedule", action="store_true")
    run.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    run.add_argument("--upload-retries", type=int, default=3, metavar="N")
    run.add_argument("--retry-delay", type=float, default=30.0, metavar="SEC")
    run.add_argument("--tags", default=None, help="Comma-separated tags (overrides channel default).")

    lst = sub.add_parser("list", help="List videos on the YouTube channel.")
    lst.add_argument("--channel", required=True)
    lst.add_argument("--scheduled-only", action="store_true")

    enq = sub.add_parser("enqueue", help="Append a pending job to the registry (for testing).")
    enq.add_argument("--channel", required=True)
    enq.add_argument("--id", required=True, help="Unique job id.")
    enq.add_argument("--video", required=True, help="Video path or URI.")
    enq.add_argument("--title", required=True)
    enq.add_argument("--description", default="", help="Inline text or path/URI to .txt.")
    enq.add_argument("--thumbnail", default="", help="Thumbnail path or URI.")

    return p


def _cmd_auth(args, config) -> int:
    channel = get_channel(config, args.channel)
    if not config.google.client_secret_path.is_file():
        print(
            f"error: client secret not found: {config.google.client_secret_path}",
            file=sys.stderr,
        )
        return 2
    print(f"Starting OAuth for channel {channel.id} ({channel.name})…", file=sys.stderr)
    print("A browser window will open. Sign in as the YouTube channel owner.", file=sys.stderr)
    get_credentials(
        config.google.client_secret_path,
        channel.token_path,
        oauth_port=config.google.oauth_port,
    )
    print(f"Token saved to {channel.token_path}")
    return 0


def _cmd_plan(args, config) -> int:
    channel = get_channel(config, args.channel)
    registry = UploadRegistry(channel.registry_path)
    pending = registry.pending(channel_id=args.channel)
    if args.limit is not None:
        pending = pending[: max(0, args.limit)]
    if not pending:
        print(f"No pending jobs in {registry.path}.")
        return 0

    ivl = args.interval_hours if args.interval_hours is not None else channel.publish.interval_hours
    start_dt = parse_start(
        args.start,
        timezone_name=channel.publish.timezone,
        default_hour=channel.publish.hour,
    )
    plan = compute_publish_schedule(
        pending, start_dt, ivl, no_schedule=args.no_schedule
    )

    print(f"{len(plan)} pending job(s) in {registry.path}:")
    for entry, publish_at in plan:
        if args.no_schedule:
            when = "now (no schedule)"
        else:
            publish_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            when = publish_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        title = entry.title or "(no title)"
        print(f"  {entry.id}  ->  publish {when}")
        print(f"      title: {title}")
    return 0


def _cmd_run(args, config) -> int:
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    print(f"uploader run: channel={args.channel}", file=sys.stderr, flush=True)
    result = run_channel(
        args.channel,
        config,
        start=args.start,
        interval_hours=args.interval_hours,
        limit=args.limit,
        no_schedule=args.no_schedule,
        privacy=args.privacy,
        upload_retries=args.upload_retries,
        retry_delay=args.retry_delay,
        tags=tags,
    )
    if result.total == 0:
        channel = get_channel(config, args.channel)
        print(f"No pending jobs in {channel.registry_path}. Nothing to do.")
        return 0

    print(f"\nUploaded {result.uploaded}/{result.total} ({result.failed} failed).")
    if result.errors:
        for job_id, err in result.errors:
            print(f"  FAILED {job_id}: {err}", file=sys.stderr)
    return 0 if result.uploaded > 0 or result.failed == 0 else 1


def _cmd_list(args, config) -> int:
    print("uploader list: loading Google client…", file=sys.stderr, flush=True)
    channel = get_channel(config, args.channel)
    videos = list_channel_videos(
        config.google.client_secret_path,
        channel.token_path,
        scheduled_only=args.scheduled_only,
        oauth_port=config.google.oauth_port,
    )
    label = "scheduled" if args.scheduled_only else "all"
    print(f"{len(videos)} {label} video(s) on channel {channel.id}:")
    for v in videos:
        extra = ""
        if v.publish_at:
            extra = f"  publish_at={v.publish_at}"
        print(f"  {v.video_id}  [{v.privacy_status}]{extra}")
        print(f"    {v.title}")
        print(f"    {v.url}")
    return 0


def _cmd_enqueue(args, config) -> int:
    channel = get_channel(config, args.channel)
    registry = UploadRegistry(channel.registry_path)
    existing = registry.get(args.id)
    if existing:
        print(f"error: job id already exists: {args.id}", file=sys.stderr)
        return 2

    entry = UploadEntry(
        id=args.id,
        channel_id=args.channel,
        title=args.title,
        description=args.description,
        video_uri=args.video,
        thumbnail_uri=args.thumbnail,
    )
    registry.append(entry)
    print(f"Enqueued {args.id} -> {registry.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    print("uploader: starting…", file=sys.stderr, flush=True)
    load_dotenv(find_dotenv(usecwd=True))
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    handlers = {
        "auth": _cmd_auth,
        "plan": _cmd_plan,
        "run": _cmd_run,
        "list": _cmd_list,
        "enqueue": _cmd_enqueue,
    }
    return handlers[args.command](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
