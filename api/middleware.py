"""HTTP middleware for uploader-api."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from api.auth import auth_enabled, authenticate_request, is_ui_path


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not auth_enabled():
            return await call_next(request)

        auth = authenticate_request(request)
        if auth in ("open", "public", "session", "api_key"):
            return await call_next(request)

        path = request.url.path
        if is_ui_path(path):
            if path == "/login":
                return RedirectResponse(url="/", status_code=302)
            return RedirectResponse(url="/", status_code=302)

        return JSONResponse(
            status_code=401,
            content={
                "detail": (
                    "Authentication required. Send X-API-Key or Authorization: Bearer <UPLOADER_API_KEY>, "
                    "or sign in at / for dashboard access."
                )
            },
        )
