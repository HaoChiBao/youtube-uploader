"""Resolve file://, local paths, and s3:// URIs to local files."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlparse


def _is_uri(value: str) -> bool:
    return "://" in value


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an s3:// URI: {uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Invalid s3:// URI: {uri}")
    return bucket, key


def resolve_to_local_path(uri: str, *, temp_dir: Path) -> Path:
    """Download or copy the resource at uri into temp_dir and return the local Path."""
    temp_dir.mkdir(parents=True, exist_ok=True)

    if not _is_uri(uri):
        path = Path(uri).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return path

    parsed = urlparse(uri)
    scheme = parsed.scheme

    if scheme == "file":
        path = Path(parsed.path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return path

    if scheme == "s3":
        bucket, key = _parse_s3_uri(uri)
        filename = Path(key).name or "download"
        dest = temp_dir / filename
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError(
                "S3 support requires boto3. Install with: pip install '.[s3]'"
            ) from e
        client = boto3.client("s3")
        client.download_file(bucket, key, str(dest))
        return dest

    raise ValueError(f"Unsupported URI scheme: {scheme} ({uri})")


def load_description(description: str) -> str:
    """Return inline description text or load from a file/s3:// URI."""
    text = description.strip()
    if not text:
        return ""

    if _is_uri(text):
        with _temp_download(text) as path:
            return path.read_text(encoding="utf-8").strip()

    path = Path(text).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()

    return text


class _temp_download:
    """Context manager that downloads a URI to a temp file."""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        self._tmpdir: Path | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp(prefix="uploader_desc_"))
        self.path = resolve_to_local_path(self.uri, temp_dir=self._tmpdir)
        return self.path

    def __exit__(self, *exc) -> None:
        if self._tmpdir and self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)
