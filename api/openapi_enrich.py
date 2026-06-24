"""Enrich FastAPI OpenAPI schema (/docs, /redoc, /openapi.json) from endpoint_docs."""

from __future__ import annotations

import copy
import json
from typing import Any

from fastapi import FastAPI

from api.endpoint_docs import API_ENDPOINTS


def _default_status_code(method: str, path: str) -> int:
    m = method.upper()
    if m == "POST" and path.endswith("/runs"):
        return 202
    if m == "POST" and path == "/v1/runs/all":
        return 202
    if m == "POST" and "/jobs" in path and not path.endswith("/register"):
        return 201
    if m == "POST" and path.endswith("/jobs/register"):
        return 201
    return 200


def _endpoint_index() -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for ep in API_ENDPOINTS:
        out[(ep["method"].lower(), ep["path"])] = ep
    return out


def build_operation_description(ep: dict[str, Any]) -> str:
    """Markdown description shown in Swagger UI for each operation."""
    parts: list[str] = []

    purpose = ep.get("purpose") or ep.get("description", "")
    if purpose:
        parts.append(f"**Purpose:** {purpose.strip()}")

    details = ep.get("details", "")
    if details:
        parts.append(f"**Details:** {details.strip()}")

    usage = ep.get("usage", "").strip()
    if usage:
        parts.append(f"**How to use:**\n\n{usage}")

    example_request = ep.get("example_request")
    if example_request is not None:
        parts.append(
            "**Example request body:**\n\n```json\n"
            + json.dumps(example_request, indent=2)
            + "\n```"
        )

    example_response = ep.get("example_response")
    if example_response is not None:
        parts.append(
            "**Example response:**\n\n```json\n"
            + json.dumps(example_response, indent=2)
            + "\n```"
        )

    if ep.get("auth", True):
        parts.append(
            "**Authentication:** Required — send `X-API-Key: <token>` or "
            "`Authorization: Bearer <token>`, or use a dashboard session cookie."
        )
    else:
        parts.append("**Authentication:** Public (no API key required).")

    return "\n\n".join(parts)


def _attach_example(responses: dict[str, Any], status_code: int, example: Any) -> None:
    key = str(status_code)
    if key not in responses:
        responses[key] = {"description": "Successful response"}
    content = responses[key].setdefault("content", {})
    json_ct = content.setdefault("application/json", {})
    json_ct["example"] = example


def enrich_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Merge purpose, usage, and response examples into generated OpenAPI."""
    schema = copy.deepcopy(schema)
    by_op = _endpoint_index()
    paths = schema.setdefault("paths", {})

    for (method, path), ep in by_op.items():
        if path not in paths:
            continue
        path_item = paths[path]
        operation = path_item.get(method)
        if not operation:
            continue

        operation["summary"] = ep.get("summary", operation.get("summary", ""))
        operation["description"] = build_operation_description(ep)

        if ep.get("example_response") is not None:
            status = ep.get("status_code") or _default_status_code(method.upper(), path)
            responses = operation.setdefault("responses", {})
            _attach_example(responses, int(status), ep["example_response"])

    # /health alias shares docs with /v1/health
    health_ep = by_op.get(("get", "/v1/health"))
    if health_ep and "/health" in paths and "get" in paths["/health"]:
        paths["/health"]["get"]["summary"] = health_ep.get("summary", "Health check")
        paths["/health"]["get"]["description"] = build_operation_description(health_ep)
        if health_ep.get("example_response") is not None:
            responses = paths["/health"]["get"].setdefault("responses", {})
            _attach_example(responses, 200, health_ep["example_response"])

    # Document catalog entries not exposed as FastAPI routes (e.g. OAuth callback)
    for (method, path), ep in by_op.items():
        if path in paths and paths[path].get(method):
            continue
        tag = ep.get("tag", "health")
        operation: dict[str, Any] = {
            "summary": ep.get("summary", ""),
            "description": build_operation_description(ep),
            "tags": [tag],
            "responses": {"200": {"description": "Successful response"}},
        }
        if ep.get("example_response") is not None:
            status = ep.get("status_code") or _default_status_code(method.upper(), path)
            responses = operation["responses"]
            _attach_example(responses, int(status), ep["example_response"])
        paths.setdefault(path, {})[method] = operation

    schema["info"]["description"] = (
        (schema.get("info", {}).get("description") or "")
        + "\n\n---\n\n"
        + "Each operation below includes **purpose**, **how to use** (curl examples), "
        + "and **example JSON responses**. Machine-readable catalog: `GET /v1/capabilities`."
    )

    return schema


def install_openapi_enrichment(app: FastAPI) -> None:
    """Replace app.openapi so /docs and /redoc show enriched descriptions."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        app.openapi_schema = enrich_openapi_schema(schema)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
