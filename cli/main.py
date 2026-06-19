"""CLI entry point for the YouTube uploader microservice."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from uploader import __version__
from uploader.channel_store import add_and_authenticate_channel, reauthenticate_channel
from uploader.channels import ChannelConfig, get_channel, load_config, resolve_channel
from uploader.channel_list import list_channel_videos
from uploader.metadata import default_test_description, default_test_title
from uploader.oauth import oauth_is_configured, resolve_oauth_settings
from uploader.registry import UploadEntry, UploadRegistry
from uploader.scheduler import compute_publish_schedule, parse_start, run_channel
from uploader.storage import resolve_to_local_path
from uploader.youtube_client import get_credentials, upload_video


_CHANNEL_HELP = "Channel reference: config id, display name, @handle, or YouTube channel id."


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

    channel = sub.add_parser("channel", help="Add, list, or re-authenticate YouTube channels.")
    channel_sub = channel.add_subparsers(dest="channel_command", required=True)

    ch_add = channel_sub.add_parser(
        "add",
        help="Sign in via browser and save channel (id = @handle or channel name).",
    )

    channel_sub.add_parser("list", help="List saved YouTube channels.")

    ch_reauth = channel_sub.add_parser("reauth", help="Re-authenticate a saved channel.")
    ch_reauth.add_argument("ref", help=_CHANNEL_HELP)

    sub.add_parser("channels", help="Alias for: uploader channel list")

    plan = sub.add_parser("plan", help="Preview publish schedule for pending jobs (no upload).")
    plan.add_argument("--channel", required=True, help=_CHANNEL_HELP)
    plan.add_argument("--start", default=None, metavar="'YYYY-MM-DD HH:MM'")
    plan.add_argument("--interval-hours", type=float, default=None)
    plan.add_argument("--limit", type=int, default=None)
    plan.add_argument("--no-schedule", action="store_true")

    run = sub.add_parser("run", help="Process all pending uploads for a channel.")
    run.add_argument("--channel", required=True, help=_CHANNEL_HELP)
    run.add_argument("--start", default=None, metavar="'YYYY-MM-DD HH:MM'")
    run.add_argument("--interval-hours", type=float, default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--no-schedule", action="store_true")
    run.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    run.add_argument("--upload-retries", type=int, default=3, metavar="N")
    run.add_argument("--retry-delay", type=float, default=30.0, metavar="SEC")
    run.add_argument("--tags", default=None, help="Comma-separated tags (overrides channel default).")

    lst = sub.add_parser("list", help="List videos on the YouTube channel.")
    lst.add_argument("--channel", required=True, help=_CHANNEL_HELP)
    lst.add_argument("--scheduled-only", action="store_true")

    enq = sub.add_parser("enqueue", help="Append a pending job to the registry (for testing).")
    enq.add_argument("--channel", required=True, help=_CHANNEL_HELP)
    enq.add_argument("--id", required=True, help="Unique job id.")
    enq.add_argument("--video", required=True, help="Video path or URI.")
    enq.add_argument("--title", required=True)
    enq.add_argument("--description", default="", help="Inline text or path/URI to .txt.")
    enq.add_argument("--thumbnail", default="", help="Thumbnail path or URI.")

    upl = sub.add_parser("upload", help="Upload a single video directly (no registry).")
    upl.add_argument("--channel", required=True, help=_CHANNEL_HELP)
    upl.add_argument("--video", required=True, help="Video path or URI.")
    upl.add_argument(
        "--title",
        default=None,
        help="Video title (default: datetime-based test title).",
    )
    upl.add_argument(
        "--description",
        default=None,
        help="Description text (default: generated test description).",
    )
    upl.add_argument("--thumbnail", default="", help="Thumbnail path or URI.")
    upl.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    upl.add_argument("--no-schedule", action="store_true", help="Upload without scheduling publishAt.")
    upl.add_argument(
        "--reauth",
        action="store_true",
        help="Re-run OAuth with account picker before uploading.",
    )

    tst = sub.add_parser(
        "test",
        help="Quick test upload with default title/description (private).",
    )
    tst.add_argument("--channel", required=True, help=_CHANNEL_HELP)
    tst.add_argument("--video", required=True, help="Video path or URI.")
    tst.add_argument("--thumbnail", default="", help="Thumbnail path or URI.")
    tst.add_argument(
        "--reauth",
        action="store_true",
        help="Re-run OAuth with account picker before uploading.",
    )

    return p


def _oauth_for_config(config):
    return resolve_oauth_settings(
        config.google.client_secret_path,
        oauth_port=config.google.oauth_port,
    )


def _ensure_oauth(channel, oauth, config, *, force_reauth: bool = False) -> int | None:
    """Run OAuth if needed. Returns exit code on error, else None."""
    if not oauth_is_configured(config.google.client_secret_path):
        print("error: Google OAuth not configured in .env", file=sys.stderr)
        return 2

    needs_auth = force_reauth or not channel.token_path.is_file()
    if needs_auth:
        action = "Re-authenticating" if force_reauth else "No token found — starting"
        print(f"{action} OAuth for {channel.id} ({channel.name})…", file=sys.stderr)
        print(
            "Browser will open — choose the Google account for this YouTube channel.",
            file=sys.stderr,
        )
        get_credentials(
            channel.token_path,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
            force_reauth=force_reauth,
        )
    return None


def _resolve_upload_metadata(channel, title: str | None, description: str | None) -> tuple[str, str]:
    resolved_title = title or default_test_title(
        channel_id=channel.id,
        channel_name=channel.name,
    )
    if description is None:
        resolved_description = default_test_description(
            channel_id=channel.id,
            channel_name=channel.name,
            timezone_name=channel.publish.timezone,
        )
    elif description and Path(description).is_file():
        resolved_description = Path(description).read_text(encoding="utf-8")
    else:
        resolved_description = description
    return resolved_title, resolved_description


def _config_path_from_args(args) -> Path:
    if getattr(args, "config", None):
        return args.config.expanduser().resolve()
    import os

    env_path = os.environ.get("UPLOADER_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path("config/channels.yaml").resolve()


def _print_channel_saved(ch: ChannelConfig) -> None:
    handle = f" (@{ch.custom_url})" if ch.custom_url else ""
    print(f"Saved channel: {ch.name}{handle}")
    print(f"  Reference id:       {ch.id}")
    print(f"  YouTube channel id: {ch.youtube_channel_id}")
    print(f"  Token:              {ch.token_path}")
    print(f"  Registry:           {ch.registry_path}")
    print(f"\nUse --channel {ch.id!r} (or the channel name) for uploads.")


def _cmd_channel_add(args, config) -> int:
    oauth = _oauth_for_config(config)
    if not oauth_is_configured(config.google.client_secret_path):
        print("error: Google OAuth not configured in .env", file=sys.stderr)
        return 2
    print("Opening browser — choose the Google account for this YouTube channel.", file=sys.stderr)
    try:
        channel = add_and_authenticate_channel(
            oauth,
            config_path=_config_path_from_args(args),
            force_reauth=True,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _print_channel_saved(channel)
    return 0


def _cmd_channel_list(args, config) -> int:
    if not config.channels:
        print("No channels saved yet. Run: uploader channel add")
        return 0
    print(f"{len(config.channels)} saved channel(s):")
    for ch in config.channels:
        token_status = "authorized" if ch.token_path.is_file() else "not authorized"
        handle = f" @{ch.custom_url.lstrip('@')}" if ch.custom_url else ""
        print(f"  {ch.id}  —  {ch.name}{handle}  [{token_status}]")
        if ch.youtube_channel_id:
            print(f"    youtube_id: {ch.youtube_channel_id}")
        print(f"    token:      {ch.token_path}")
    return 0


def _cmd_channel_reauth(args, config) -> int:
    oauth = _oauth_for_config(config)
    if not oauth_is_configured(config.google.client_secret_path):
        print("error: Google OAuth not configured in .env", file=sys.stderr)
        return 2
    try:
        channel = resolve_channel(config, args.ref)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Re-authenticating {channel.name} ({channel.id})…", file=sys.stderr)
    try:
        channel = reauthenticate_channel(
            channel,
            oauth,
            config_path=_config_path_from_args(args),
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _print_channel_saved(channel)
    return 0


def _cmd_channels(args, config) -> int:
    return _cmd_channel_list(args, config)


def _cmd_plan(args, config) -> int:
    try:
        channel = resolve_channel(config, args.channel)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    registry = UploadRegistry(channel.registry_path)
    pending = registry.pending(channel_id=channel.id)
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
    try:
        resolve_channel(config, args.channel)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
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
    oauth = _oauth_for_config(config)
    videos = list_channel_videos(
        channel.token_path,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        scheduled_only=args.scheduled_only,
        oauth_port=oauth.oauth_port,
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
        channel_id=channel.id,
        title=args.title,
        description=args.description,
        video_uri=args.video,
        thumbnail_uri=args.thumbnail,
    )
    registry.append(entry)
    print(f"Enqueued {args.id} -> {registry.path}")
    return 0


def _cmd_upload(args, config) -> int:
    import shutil
    import tempfile

    channel = get_channel(config, args.channel)
    oauth = _oauth_for_config(config)
    if not oauth_is_configured(config.google.client_secret_path):
        print("error: Google OAuth not configured in .env", file=sys.stderr)
        return 2

    err = _ensure_oauth(channel, oauth, config, force_reauth=args.reauth)
    if err is not None:
        return err

    title, description = _resolve_upload_metadata(channel, args.title, args.description)

    tmp_root = Path(tempfile.mkdtemp(prefix="uploader_upload_"))
    try:
        video_path = resolve_to_local_path(args.video, temp_dir=tmp_root)

        thumb_path = None
        if args.thumbnail:
            try:
                thumb_path = resolve_to_local_path(args.thumbnail, temp_dir=tmp_root)
            except (FileNotFoundError, ValueError):
                thumb_path = None

        print(f"Title: {title}", file=sys.stderr)
        print(f"Uploading {video_path.name} to YouTube ({channel.id})…", file=sys.stderr)

        def on_progress(p: float) -> None:
            print(f"\rupload {p * 100:.0f}%", end="", file=sys.stderr, flush=True)

        response = upload_video(
            video_path,
            title=title,
            description=description,
            token_path=channel.token_path,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            privacy=args.privacy,
            category_id=channel.category_id,
            tags=channel.default_tags or None,
            made_for_kids=channel.made_for_kids,
            thumbnail_path=thumb_path,
            publish_at=None,
            oauth_port=oauth.oauth_port,
            on_progress=on_progress,
        )
        print(file=sys.stderr)
        video_id = response.get("id", "")
        url = f"https://youtu.be/{video_id}"
        print(f"Uploaded: {url}")
        if response.get("_thumbnail_warning"):
            print(f"warning: thumbnail skipped — {response['_thumbnail_warning']}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _cmd_test(args, config) -> int:
    """Shorthand: private upload with auto-generated metadata."""
    upload_args = argparse.Namespace(
        channel=args.channel,
        video=args.video,
        title=None,
        description=None,
        thumbnail=args.thumbnail,
        privacy="private",
        no_schedule=True,
        reauth=args.reauth,
    )
    return _cmd_upload(upload_args, config)


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
        "channel": {
            "add": _cmd_channel_add,
            "list": _cmd_channel_list,
            "reauth": _cmd_channel_reauth,
        },
        "channels": _cmd_channels,
        "plan": _cmd_plan,
        "run": _cmd_run,
        "list": _cmd_list,
        "enqueue": _cmd_enqueue,
        "upload": _cmd_upload,
        "test": _cmd_test,
    }

    if args.command == "channel":
        return handlers["channel"][args.channel_command](args, config)
    return handlers[args.command](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
