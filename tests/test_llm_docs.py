"""Tests for LLM API documentation export."""

from __future__ import annotations

from api.llm_docs import render_llm_api_docs


def test_render_llm_api_docs_includes_key_endpoints() -> None:
    text = render_llm_api_docs(base_url="https://example.test")
    assert "# YouTube Uploader HTTP API" in text
    assert "https://example.test/v1/docs/llm" in text
    assert "POST /v1/channels/{channel_ref}/jobs/register" in text
    assert "POST /v1/channels/{channel_ref}/upload/direct" in text
    assert "POST /v1/channels/{channel_ref}/jobs/{job_id}/dismiss-upload" in text
    assert "JobRegisterRequest" in text
    assert "RunRequest" in text
