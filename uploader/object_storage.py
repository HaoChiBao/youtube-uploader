"""S3 / Cloudflare R2 object storage I/O (S3-compatible API)."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from uploader import bucket_layout


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


def assembly_storage_bucket() -> str:
    """Assembler output bucket (e.g. music-assembly-data)."""
    return _env("ASSEMBLY_R2_BUCKET", "MUSIC_ASSEMBLY_R2_BUCKET")


def assembly_r2_separate_credentials() -> bool:
    """True when ASSEMBLY_R2_ACCESS_KEY_ID is set (dedicated read credentials)."""
    return bool(_env("ASSEMBLY_R2_ACCESS_KEY_ID"))


def assembly_r2_configured() -> bool:
    return bool(assembly_storage_bucket())


def registry_uri(channel_id: str, *, bucket: str | None = None) -> str:
    return bucket_layout.s3_uri(bucket_layout.registry_key(channel_id), bucket=bucket)


def config_uri(*, bucket: str | None = None) -> str:
    return bucket_layout.s3_uri(bucket_layout.config_key(), bucket=bucket)


def token_uri(channel_id: str, *, bucket: str | None = None) -> str:
    return bucket_layout.s3_uri(bucket_layout.token_key(channel_id), bucket=bucket)


def channel_meta_uri(channel_id: str, *, bucket: str | None = None) -> str:
    return bucket_layout.s3_uri(bucket_layout.channel_meta_key(channel_id), bucket=bucket)


def job_uri(
    channel_id: str,
    job_id: str,
    filename: str,
    *,
    bucket: str | None = None,
) -> str:
    return bucket_layout.s3_uri(bucket_layout.job_key(channel_id, job_id, filename), bucket=bucket)


def archive_uri(
    channel_id: str,
    job_id: str,
    filename: str,
    *,
    bucket: str | None = None,
) -> str:
    return bucket_layout.s3_uri(bucket_layout.archive_key(channel_id, job_id, filename), bucket=bucket)


def log_uri(channel_id: str, date_stamp: str, *, bucket: str | None = None) -> str:
    return bucket_layout.s3_uri(bucket_layout.log_key(channel_id, date_stamp), bucket=bucket)


def video_prefix(channel_id: str, job_id: str, *, bucket: str | None = None) -> str:
    b = bucket or storage_bucket()
    if not b:
        raise ValueError("CLOUDFLARE_R2_BUCKET is not set")
    return f"s3://{b}/{bucket_layout.job_prefix_key(channel_id, job_id)}"


_default_s3_client = None
_assembly_s3_client = None


def _build_s3_client(
    *,
    endpoint_names: tuple[str, ...],
    region_names: tuple[str, ...],
    access_key_names: tuple[str, ...],
    secret_key_names: tuple[str, ...],
):
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("S3 support requires boto3. Install with: pip install '.[s3]'") from e

    kwargs: dict = {}
    endpoint = _env(*endpoint_names)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    region = _env(*region_names)
    if region:
        kwargs["region_name"] = region
    access_key = _env(*access_key_names)
    secret_key = _env(*secret_key_names)
    if access_key:
        kwargs["aws_access_key_id"] = access_key
    if secret_key:
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _s3_client():
    global _default_s3_client
    if _default_s3_client is None:
        _default_s3_client = _build_s3_client(
            endpoint_names=("CLOUDFLARE_R2_ENDPOINT_URL", "S3_ENDPOINT_URL", "AWS_ENDPOINT_URL"),
            region_names=("CLOUDFLARE_R2_REGION", "AWS_REGION", "S3_REGION"),
            access_key_names=("CLOUDFLARE_R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
            secret_key_names=("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
        )
    return _default_s3_client


def _assembly_s3_client():
    global _assembly_s3_client
    if _assembly_s3_client is None:
        _assembly_s3_client = _build_s3_client(
            endpoint_names=(
                "ASSEMBLY_R2_ENDPOINT_URL",
                "ASSEMBLY_R2_ENDPOINT",
                "CLOUDFLARE_R2_ENDPOINT_URL",
                "S3_ENDPOINT_URL",
            ),
            region_names=("ASSEMBLY_R2_REGION", "CLOUDFLARE_R2_REGION", "AWS_REGION"),
            access_key_names=("ASSEMBLY_R2_ACCESS_KEY_ID",),
            secret_key_names=("ASSEMBLY_R2_SECRET_ACCESS_KEY",),
        )
    return _assembly_s3_client


def _s3_client_for_bucket(bucket: str):
    assembly_bucket = assembly_storage_bucket()
    if assembly_bucket and bucket == assembly_bucket and assembly_r2_separate_credentials():
        return _assembly_s3_client()
    return _s3_client()


def _s3_client_for_uri(uri: str):
    if not is_s3_uri(uri):
        return _s3_client()
    bucket, _ = parse_s3_uri(uri)
    return _s3_client_for_bucket(bucket)


def _client_error_code(exc: Exception) -> str:
    from botocore.exceptions import ClientError

    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", ""))
    return ""


def assert_object_readable(uri: str) -> None:
    """Verify a local path or s3:// object exists and is readable (raises on 403/404)."""
    if not is_s3_uri(uri):
        from pathlib import Path

        if not Path(uri).is_file():
            raise FileNotFoundError(f"File not found: {uri}")
        return

    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        code = _client_error_code(e)
        if code in ("404", "NoSuchKey", "NotFound"):
            raise FileNotFoundError(f"Object not found: {uri}") from e
        if code in ("403", "AccessDenied", "Forbidden"):
            hint = (
                f"Check R2 credentials for bucket {bucket}. "
                "For ai-music-assembler output, set ASSEMBLY_R2_BUCKET and either "
                "grant the uploader token read access to that bucket or set ASSEMBLY_R2_ACCESS_KEY_ID."
            )
            raise PermissionError(f"Access denied for {uri}. {hint}") from e
        raise


def assembly_r2_status() -> dict:
    """Health snapshot for assembler cross-bucket reads (GET /v1/capabilities)."""
    bucket = assembly_storage_bucket()
    if not bucket:
        return {
            "configured": False,
            "bucket": "",
            "reachable": False,
            "uses_separate_credentials": False,
            "message": "Set ASSEMBLY_R2_BUCKET to enable assembler URI registration.",
        }

    uses_separate = assembly_r2_separate_credentials()
    reachable = False
    message = ""
    try:
        client = _assembly_s3_client() if uses_separate else _s3_client()
        client.list_objects_v2(Bucket=bucket, MaxKeys=1)
        reachable = True
        message = "Assembler bucket is reachable."
    except Exception as e:
        message = f"Cannot reach assembler bucket {bucket}: {e}"

    return {
        "configured": True,
        "bucket": bucket,
        "reachable": reachable,
        "uses_separate_credentials": uses_separate,
        "message": message,
    }


def exists(uri: str) -> bool:
    if not is_s3_uri(uri):
        from pathlib import Path

        return Path(uri).is_file()
    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        code = _client_error_code(e)
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        if code in ("403", "AccessDenied", "Forbidden"):
            return False
        raise


def read_text(uri: str) -> str:
    if not is_s3_uri(uri):
        from pathlib import Path

        path = Path(uri)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as e:
        code = _client_error_code(e)
        if code in ("404", "NoSuchKey", "NotFound"):
            return ""
        if code in ("403", "AccessDenied", "Forbidden"):
            raise PermissionError(
                f"R2 access denied for {uri}. Check credentials for bucket {bucket}."
            ) from e
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
    client = _s3_client_for_bucket(bucket)
    client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))


def append_line(uri: str, line: str) -> None:
    """Append one line to a text object (read-modify-write)."""
    existing = read_text(uri)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    write_text(uri, existing + line.rstrip("\n") + "\n")


def write_bytes(uri: str, data: bytes, *, content_type: str | None = None) -> None:
    if not is_s3_uri(uri):
        from pathlib import Path

        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    kwargs: dict = {"Bucket": bucket, "Key": key, "Body": data}
    if content_type:
        kwargs["ContentType"] = content_type
    client.put_object(**kwargs)


def upload_file(local_path: str | Path, uri: str, *, content_type: str | None = None) -> None:
    """Upload a local file to a local path or s3:// URI."""
    from pathlib import Path

    src = Path(local_path)
    if not src.is_file():
        raise FileNotFoundError(f"Local file not found: {src}")
    write_bytes(uri, src.read_bytes(), content_type=content_type)


def list_keys(prefix_uri: str) -> list[str]:
    """List object keys under an s3:// prefix (returns keys only, not full URIs)."""
    if not is_s3_uri(prefix_uri):
        from pathlib import Path

        base = prefix_uri.rstrip("/\\")
        root = Path(base)
        if not root.is_dir():
            return []
        keys: list[str] = []
        for path in root.rglob("*"):
            if path.is_file():
                keys.append(str(path.relative_to(root.parent)).replace("\\", "/"))
        return keys

    bucket, prefix = parse_s3_uri(prefix_uri.rstrip("/") + "/")
    client = _s3_client_for_bucket(bucket)
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            response = client.list_objects_v2(**kwargs)
        except Exception as e:
            code = _client_error_code(e)
            if code in ("404", "NoSuchKey", "NotFound", "NoSuchBucket"):
                return []
            raise
        for item in response.get("Contents") or []:
            key = item.get("Key", "")
            if key and not key.endswith("/"):
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
    return keys


def copy_object(src_uri: str, dest_uri: str) -> None:
    """Copy an object (local file or s3://) to another location."""
    if not is_s3_uri(src_uri) and not is_s3_uri(dest_uri):
        from pathlib import Path
        import shutil

        dest = Path(dest_uri)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_uri, dest)
        return

    if is_s3_uri(src_uri) and is_s3_uri(dest_uri):
        src_bucket, src_key = parse_s3_uri(src_uri)
        dest_bucket, dest_key = parse_s3_uri(dest_uri)
        src_client = _s3_client_for_bucket(src_bucket)
        dest_client = _s3_client_for_bucket(dest_bucket)
        if src_client is dest_client:
            src_client.copy_object(
                CopySource={"Bucket": src_bucket, "Key": src_key},
                Bucket=dest_bucket,
                Key=dest_key,
            )
        else:
            body = src_client.get_object(Bucket=src_bucket, Key=src_key)["Body"].read()
            dest_client.put_object(Bucket=dest_bucket, Key=dest_key, Body=body)
        return

    write_bytes(dest_uri, _read_bytes(src_uri))


def delete_object(uri: str) -> None:
    if not is_s3_uri(uri):
        from pathlib import Path

        Path(uri).unlink(missing_ok=True)
        return
    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    client.delete_object(Bucket=bucket, Key=key)


def _read_bytes(uri: str) -> bytes:
    if not is_s3_uri(uri):
        from pathlib import Path

        return Path(uri).read_bytes()
    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def delete_prefix(prefix: str) -> list[str]:
    """Delete all objects under a local directory or s3:// prefix. Returns deleted paths/URIs."""
    prefix = prefix.rstrip("/") + "/"
    if not is_s3_uri(prefix):
        from pathlib import Path
        import shutil

        root = Path(prefix.rstrip("/"))
        if not root.is_dir():
            return []
        deleted: list[str] = []
        for path in root.rglob("*"):
            if path.is_file():
                deleted.append(str(path))
        shutil.rmtree(root)
        return deleted

    bucket, key_prefix = parse_s3_uri(prefix)
    deleted: list[str] = []
    for key in list_keys(prefix):
        if not key.startswith(key_prefix):
            continue
        uri = f"s3://{bucket}/{key}"
        delete_object(uri)
        deleted.append(uri)
    return deleted


def move_prefix(src_prefix: str, dest_prefix: str) -> list[str]:
    """Move all objects from src prefix to dest prefix. Returns dest URIs."""
    src_prefix = src_prefix.rstrip("/") + "/"
    dest_prefix = dest_prefix.rstrip("/") + "/"
    if not is_s3_uri(src_prefix):
        from pathlib import Path
        import shutil

        src_root = Path(src_prefix.rstrip("/"))
        dest_root = Path(dest_prefix.rstrip("/"))
        if not src_root.is_dir():
            return []
        dest_root.parent.mkdir(parents=True, exist_ok=True)
        if dest_root.exists():
            shutil.rmtree(dest_root)
        shutil.move(str(src_root), str(dest_root))
        moved: list[str] = []
        for path in dest_root.rglob("*"):
            if path.is_file():
                moved.append(str(path))
        return moved

    src_bucket, src_key_prefix = parse_s3_uri(src_prefix)
    dest_bucket, dest_key_prefix = parse_s3_uri(dest_prefix)
    keys = list_keys(src_prefix)
    moved_uris: list[str] = []
    for key in keys:
        if not key.startswith(src_key_prefix):
            continue
        suffix = key[len(src_key_prefix) :]
        dest_key = f"{dest_key_prefix}{suffix}"
        src_uri = f"s3://{src_bucket}/{key}"
        dest_uri = f"s3://{dest_bucket}/{dest_key}"
        copy_object(src_uri, dest_uri)
        delete_object(src_uri)
        moved_uris.append(dest_uri)
    return moved_uris


def presigned_get_url(uri: str, *, expires: int = 900) -> str:
    """Temporary HTTPS URL for browser media preview (R2/S3 only)."""
    if not is_s3_uri(uri):
        raise ValueError(f"Not an s3:// URI: {uri}")
    bucket, key = parse_s3_uri(uri)
    client = _s3_client_for_bucket(bucket)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def guess_media_type(path_or_key: str) -> str:
    name = path_or_key.lower()
    if name.endswith(".mp4"):
        return "video/mp4"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"
