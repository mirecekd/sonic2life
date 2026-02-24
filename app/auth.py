"""App-level authentication middleware.

Simple session-cookie auth that replaces nginx Basic Auth.
This approach is PWA-compatible: Service Worker sends cookies
automatically (same-origin), so asset caching and manifest.json
work without issues.

Single-user design — credentials from environment variables.
"""

import hashlib
import hmac
import logging
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from app.config import AUTH_USERNAME, AUTH_PASSWORD

logger = logging.getLogger(__name__)

# Session secret — regenerated on each server start (sessions expire on restart)
_SESSION_SECRET = secrets.token_hex(32)

# Session duration: 30 days (seconds)
SESSION_MAX_AGE = 30 * 24 * 60 * 60

# Cookie name
COOKIE_NAME = "s2l_session"

# Paths that don't require authentication
PUBLIC_PATHS = [
    "/static/",
    "/login",
    "/api/push/vapid-key",
    "/api/push/subscribe",
    "/api/push/respond",
    "/api/push/events",
    "/favicon.ico",
]

# Paths that are API endpoints (return 401 JSON instead of redirect)
API_PATHS = [
    "/api/",
    "/ws",
]


def _is_public(path: str) -> bool:
    """Check if a path is public (no auth required)."""
    return any(path.startswith(p) for p in PUBLIC_PATHS)


def _is_api(path: str) -> bool:
    """Check if a path is an API endpoint."""
    return any(path.startswith(p) for p in API_PATHS)


def create_session_token(username: str) -> str:
    """Create a signed session token."""
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    signature = hmac.new(
        _SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(token: str) -> bool:
    """Verify a session token is valid and not expired."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False

        username, timestamp_str, signature = parts

        # Verify signature
        payload = f"{username}:{timestamp_str}"
        expected = hmac.new(
            _SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            return False

        # Check expiration
        created = int(timestamp_str)
        if time.time() - created > SESSION_MAX_AGE:
            return False

        return True

    except (ValueError, TypeError):
        return False


def verify_credentials(username: str, password: str) -> bool:
    """Verify login credentials against env vars."""
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        # Auth not configured — allow access
        return True
    return (
        hmac.compare_digest(username, AUTH_USERNAME)
        and hmac.compare_digest(password, AUTH_PASSWORD)
    )


def is_auth_enabled() -> bool:
    """Check if authentication is configured."""
    return bool(AUTH_USERNAME and AUTH_PASSWORD)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that checks session cookie on every request.

    Public paths (static assets, push endpoints) are always accessible.
    API paths return 401 JSON. Other paths redirect to /login.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Auth not configured — pass through
        if not is_auth_enabled():
            return await call_next(request)

        # Public paths — no auth needed
        if _is_public(path):
            return await call_next(request)

        # Check session cookie
        token = request.cookies.get(COOKIE_NAME)
        if token and verify_session_token(token):
            return await call_next(request)

        # Not authenticated
        if _is_api(path):
            return JSONResponse(
                {"error": "Authentication required"},
                status_code=401,
            )

        # Redirect to login page
        return RedirectResponse(f"/login?next={path}")
