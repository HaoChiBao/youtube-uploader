"""Tests for transient upload error detection."""

import errno
import socket
from unittest.mock import patch

from uploader.youtube_client import is_transient_upload_error


class FakeHttpError(Exception):
    def __init__(self, status: int) -> None:
        self.resp = type("R", (), {"status": status})()


def test_timeout_errors() -> None:
    assert is_transient_upload_error(TimeoutError())
    assert is_transient_upload_error(socket.timeout())
    assert is_transient_upload_error(ConnectionError())
    assert is_transient_upload_error(ConnectionResetError())


def test_os_error_errno() -> None:
    assert is_transient_upload_error(OSError(errno.ETIMEDOUT, "timed out"))
    assert is_transient_upload_error(OSError(errno.ECONNRESET, "reset"))
    assert not is_transient_upload_error(OSError(errno.ENOENT, "not found"))


def test_http_status_codes() -> None:
    with patch("uploader.youtube_client._require_google_libs") as mock_libs:
        mock_libs.return_value = (None, None, None, None, FakeHttpError, None)
        for status in (408, 429, 500, 502, 503, 504):
            assert is_transient_upload_error(FakeHttpError(status))
        assert not is_transient_upload_error(FakeHttpError(400))
        assert not is_transient_upload_error(FakeHttpError(403))


def test_non_transient() -> None:
    assert not is_transient_upload_error(ValueError("bad input"))
    assert not is_transient_upload_error(RuntimeError("permanent"))
