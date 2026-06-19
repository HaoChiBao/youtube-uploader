"""S3 / Cloudflare R2 object storage helpers (S3-compatible API)."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def is_s3_uri(value: str) -> bool:
    return str(value).startswith("s3://")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an s3:// URI: {uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Invalid s3:// URI: {uri}")
    return bucket, key


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def storage_bucket() -> str:
    """Default bucket from CLOUDFLARE_R2_BUCKET."""
    return _env("CLOUDFLARE_R2_BUCKET", "UPLOADER_STORAGE_BUCKET")


def registry_uri(channel_id: str, *, bucket: str | None = None) -> str:
    """Canonical upload queue path for a channel."""
    b = bucket or storage_bucket()
    if not b:
        raise ValueError("CLOUDFLARE_R2_BUCKET is not set")
    return f"s3://{b}/state/{channel_id}/upload_registry.txt"


def video_prefix(channel_id: str, job_id: str, *, bucket: str | None = None) -> str:
    """Canonical folder for one rendered video job."""
    b = bucket or storage_bucket()
    if not b:
        raise ValueError("CLOUDFLARE_R2_BUCKET is not set")
    return f"s3://{b}/videos/{channel_id}/{job_id}/"


def _s3_client():
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("S3 support requires boto3. Install with: pip install '.[s3]'") from e

    kwargs: dict = {}
    endpoint = _env("CLOUDFLARE_R2_ENDPOINT_URL", "S3_ENDPOINT_URL", "AWS_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    region = _env("CLOUDFLARE_R2_REGION", "AWS_REGION", "S3_REGION")
    if region:
        kwargs["region_name"] = region
    access_key = _env("CLOUDFLARE_R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    secret_key = _env("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
    if access_key:
        kwargs["aws_access_key_id"] = access_key
    if secret_key:
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def read_text(uri: str) -> str:
    if not is_s3_uri(uri):
        from pathlib import Path

        path = Path(uri)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    bucket, key = parse_s3_uri(uri)
    client = _s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as e:
        from botocore.exceptions import ClientError

        if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") in (
            "404",
            "NoSuchKey",
            "NotFound",
        ):
            return ""
        raise
    body = response["Body"].read()
    return body.decode("utf-8")


def write_text(uri: str, text: str) -> None:
    if not is_s3_uri(uri):
        from pathlib import Path

        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    bucket, key = parse_s3_uri(uri)
    client = _s3_client()
    client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))


def append_line(uri: str, line: str) -> None:
    """Append one line to a text object (read-modify-write)."""
    existing = read_text(uri)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    write_text(uri, existing + line.rstrip("\n") + "\n")
