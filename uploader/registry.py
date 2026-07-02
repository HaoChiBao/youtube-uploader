"""JSON-lines upload registry for pending → uploaded/failed job lifecycle."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from uploader.object_storage import append_line, is_s3_uri, read_text, write_text

STATUS_PENDING = "pending"
STATUS_UPLOADING = "uploading"
STATUS_UPLOADED = "uploaded"
STATUS_FAILED = "failed"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class UploadEntry:
    id: str
    channel_id: str
    status: str = STATUS_PENDING
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
    extra: dict = field(default_factory=dict)

    # Legacy fields from ai-music-assembler (backward compatibility)
    video: str = ""
    thumbnail: str = ""
    dir: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "UploadEntry":
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        kwargs = {k: d[k] for k in fields if k in d}
        extra = dict(kwargs.pop("extra", {}) or {})
        for k, v in d.items():
            if k not in fields:
                extra[k] = v
        if extra:
            kwargs["extra"] = extra
        return cls(**kwargs)

    def resolved_video_uri(self) -> str:
        """Return video_uri, falling back to legacy video field."""
        return self.video_uri or self.video

    def resolved_thumbnail_uri(self) -> str:
        """Return thumbnail_uri, falling back to legacy thumbnail field."""
        return self.thumbnail_uri or self.thumbnail


def _parse_lines(text: str) -> list[UploadEntry]:
    entries: list[UploadEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(UploadEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries


class UploadRegistry:
    """Read/append/update JSON-lines registry (local file or s3:// URI)."""

    def __init__(self, path: str | Path) -> None:
        self.location = str(path)
        self._remote = is_s3_uri(self.location)
        self.path = Path(path) if not self._remote else None

    def load(self) -> list[UploadEntry]:
        if self._remote:
            return _parse_lines(read_text(self.location))
        if not self.path.is_file():
            return []
        return _parse_lines(self.path.read_text(encoding="utf-8"))

    def _write_all(self, entries: list[UploadEntry]) -> None:
        body = "".join(e.to_json() + "\n" for e in entries)
        if self._remote:
            write_text(self.location, body)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(body, encoding="utf-8")
        from uploader.cache_signals import bump

        bump("queue")

    def append(self, entry: UploadEntry) -> None:
        if not entry.created_at:
            entry.created_at = _utc_now_iso()
        line = entry.to_json() + "\n"
        if self._remote:
            append_line(self.location, line)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
        from uploader.cache_signals import bump

        bump("queue")

    def pending(self, channel_id: str | None = None) -> list[UploadEntry]:
        entries = [e for e in self.load() if e.status == STATUS_PENDING]
        if channel_id is not None:
            entries = [e for e in entries if e.channel_id == channel_id]
        return entries

    def uploading(self, channel_id: str | None = None) -> list[UploadEntry]:
        entries = [e for e in self.load() if e.status == STATUS_UPLOADING]
        if channel_id is not None:
            entries = [e for e in entries if e.channel_id == channel_id]
        return entries

    def all_uploading(self) -> list[UploadEntry]:
        return self.uploading()

    @staticmethod
    def upload_progress_fields(entry: UploadEntry) -> dict:
        extra = entry.extra or {}
        return {
            "upload_worker_id": str(extra.get("upload_worker_id", "") or ""),
            "upload_phase": str(extra.get("upload_phase", "") or ""),
            "upload_progress": float(extra.get("upload_progress", 0) or 0),
            "upload_message": str(extra.get("upload_message", "") or ""),
            "upload_started_at": str(extra.get("upload_started_at", "") or ""),
            "upload_updated_at": str(extra.get("upload_updated_at", "") or ""),
        }

    @staticmethod
    def upload_stale(entry: UploadEntry, *, stale_seconds: int = 600) -> bool:
        """True when an uploading job has no recent progress (reclaimable)."""
        extra = entry.extra or {}
        updated = extra.get("upload_updated_at") or extra.get("upload_started_at") or ""
        if not updated:
            return True
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - ts).total_seconds() > stale_seconds

    def get(self, entry_id: str) -> UploadEntry | None:
        for e in self.load():
            if e.id == entry_id:
                return e
        return None

    def _update_entry(self, entry_id: str, updater) -> None:
        entries = self.load()
        for e in entries:
            if e.id == entry_id:
                updater(e)
                self._write_all(entries)
                return

    def mark_uploading(
        self,
        entry_id: str,
        *,
        worker_id: str = "",
        publish_at: str = "",
    ) -> None:
        def _upd(e: UploadEntry) -> None:
            e.status = STATUS_UPLOADING
            e.error = ""
            if publish_at:
                e.publish_at = publish_at
            extra = dict(e.extra or {})
            if worker_id:
                extra["upload_worker_id"] = worker_id
            extra["upload_phase"] = "starting"
            extra["upload_progress"] = 0.0
            extra["upload_message"] = "Starting upload worker"
            now = _utc_now_iso()
            extra["upload_started_at"] = now
            extra["upload_updated_at"] = now
            e.extra = extra

        self._update_entry(entry_id, _upd)

    def set_upload_progress(
        self,
        entry_id: str,
        *,
        phase: str,
        progress: float,
        message: str = "",
    ) -> None:
        pct = max(0.0, min(100.0, float(progress)))

        def _upd(e: UploadEntry) -> None:
            extra = dict(e.extra or {})
            extra["upload_phase"] = phase
            extra["upload_progress"] = pct
            if message:
                extra["upload_message"] = message
            extra["upload_updated_at"] = _utc_now_iso()
            e.extra = extra

        self._update_entry(entry_id, _upd)

    def reset_upload_to_pending(self, entry_id: str) -> None:
        """Return an uploading job to pending and clear progress metadata."""

        def _upd(e: UploadEntry) -> None:
            e.status = STATUS_PENDING
            e.error = ""
            extra = dict(e.extra or {})
            for key in (
                "upload_worker_id",
                "upload_phase",
                "upload_progress",
                "upload_message",
                "upload_started_at",
                "upload_updated_at",
            ):
                extra.pop(key, None)
            e.extra = extra

        self._update_entry(entry_id, _upd)

    def prepare_for_reupload(self, entry_id: str) -> None:
        """Reset a job to pending for a fresh upload (re-upload or retry)."""

        def _upd(e: UploadEntry) -> None:
            e.status = STATUS_PENDING
            e.youtube_id = ""
            e.youtube_url = ""
            e.uploaded_at = ""
            e.error = ""
            e.publish_at = ""
            extra = dict(e.extra or {})
            for key in (
                "upload_worker_id",
                "upload_phase",
                "upload_progress",
                "upload_message",
                "upload_started_at",
                "upload_updated_at",
            ):
                extra.pop(key, None)
            e.extra = extra

        self._update_entry(entry_id, _upd)

    def clear_upload_progress(self, entry_id: str) -> None:
        def _upd(e: UploadEntry) -> None:
            extra = dict(e.extra or {})
            for key in (
                "upload_worker_id",
                "upload_phase",
                "upload_progress",
                "upload_message",
                "upload_started_at",
                "upload_updated_at",
            ):
                extra.pop(key, None)
            e.extra = extra

        self._update_entry(entry_id, _upd)

    def mark_uploaded(
        self, entry_id: str, *, youtube_id: str, publish_at: str = ""
    ) -> None:
        def _upd(e: UploadEntry) -> None:
            e.status = STATUS_UPLOADED
            e.youtube_id = youtube_id
            e.youtube_url = f"https://youtu.be/{youtube_id}" if youtube_id else ""
            if publish_at:
                e.publish_at = publish_at
            e.uploaded_at = _utc_now_iso()
            e.error = ""
            extra = dict(e.extra or {})
            for key in (
                "upload_worker_id",
                "upload_phase",
                "upload_progress",
                "upload_message",
                "upload_started_at",
                "upload_updated_at",
            ):
                extra.pop(key, None)
            extra["upload_phase"] = "done"
            extra["upload_progress"] = 100.0
            e.extra = extra

        self._update_entry(entry_id, _upd)

    def mark_failed(self, entry_id: str, *, error: str) -> None:
        def _upd(e: UploadEntry) -> None:
            e.status = STATUS_FAILED
            e.error = error

        self._update_entry(entry_id, _upd)

    def update_storage_uris(
        self,
        entry_id: str,
        *,
        video_uri: str,
        thumbnail_uri: str = "",
    ) -> None:
        def _upd(e: UploadEntry) -> None:
            e.video_uri = video_uri
            if thumbnail_uri:
                e.thumbnail_uri = thumbnail_uri

        self._update_entry(entry_id, _upd)

    def remove(self, entry_id: str) -> UploadEntry:
        """Remove a registry row by job id. Returns the removed entry."""
        entries = self.load()
        for i, e in enumerate(entries):
            if e.id == entry_id:
                removed = entries.pop(i)
                self._write_all(entries)
                return removed
        raise ValueError(f"Job not found in registry: {entry_id}")
