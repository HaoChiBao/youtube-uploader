"""Background run tracking."""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass, field

from uploader.scheduler import RunResult


@dataclass
class TrackedRun:
    run_id: str
    channel_id: str
    status: str = "queued"
    result: RunResult | None = None
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_RUNS: dict[str, TrackedRun] = {}


def create_run(channel_id: str) -> TrackedRun:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    tracked = TrackedRun(run_id=run_id, channel_id=channel_id)
    _RUNS[run_id] = tracked
    return tracked


def get_run(run_id: str) -> TrackedRun | None:
    return _RUNS.get(run_id)


def set_running(tracked: TrackedRun) -> None:
    with tracked.lock:
        tracked.status = "running"


def set_complete(tracked: TrackedRun, result: RunResult) -> None:
    with tracked.lock:
        tracked.result = result
        tracked.status = "completed" if result.failed == 0 else "completed_with_errors"


def set_failed(tracked: TrackedRun, error: str) -> None:
    with tracked.lock:
        tracked.error = error
        tracked.status = "failed"
