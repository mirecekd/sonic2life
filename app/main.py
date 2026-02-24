"""Sonic2Life - Voice-first life assistant for seniors powered by Amazon Nova 2 Sonic."""

import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, Request
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from pydantic import BaseModel
from typing import Optional

from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, RedirectResponse
from app.config import HOST, PORT
from app.auth import (
    AuthMiddleware, verify_credentials, create_session_token,
    is_auth_enabled, COOKIE_NAME, SESSION_MAX_AGE,
)
from app.agent import get_tool_specs, handle_tool_call, get_mcp_runner
from app.websocket_handler import handle_websocket
from app.admin import router as admin_router
from app.scheduler import start_scheduler, stop_scheduler
from app.push import (
    get_vapid_public_key,
    add_subscription,
    send_notification,
    send_push_notification,
    add_sse_client,
    remove_sse_client,
    record_notification_response,
    get_notification_responses,
    confirm_medication_from_notification,
    snooze_medication_from_notification,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="Sonic2Life", description="Voice-first life assistant for seniors")

# Auth middleware (cookie-based, PWA-compatible)
app.add_middleware(AuthMiddleware)

# Mount admin panel routes
app.include_router(admin_router)


@app.on_event("startup")
async def startup_event():
    """Pre-initialize MCP client and start scheduler."""
    import asyncio
    asyncio.create_task(_warmup_mcp())
    await start_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    """Stop scheduler on shutdown."""
    await stop_scheduler()


async def _warmup_mcp():
    """Background task to warm up MCP server connection."""
    try:
        runner = await get_mcp_runner()
        logging.getLogger(__name__).info("🔌 MCP client pre-initialized")
    except Exception as e:
        logging.getLogger(__name__).warning(f"⚠️ MCP warmup failed (will retry on first call): {e}")

# Serve static files (JS, CSS)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    """Serve the main page."""
    return FileResponse(str(static_dir / "index.html"))


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "sonic2life"}

# ── Authentication ──────────────────────────────────────────
logger = logging.getLogger(__name__)


@app.get("/login")
async def login_page():
    """Serve the login page."""
    if not is_auth_enabled():
        return RedirectResponse("/")
    return FileResponse(static_dir / "login.html")


@app.post("/login")
async def login(request: Request):
    """Handle login form submission."""
    if not is_auth_enabled():
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if verify_credentials(username, password):
        token = create_session_token(username)
        next_url = request.query_params.get("next", "/")
        response = RedirectResponse(next_url, status_code=302)
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        logger.info(f"✅ User '{username}' logged in")
        return response
    else:
        logger.warning(f"❌ Failed login attempt for '{username}'")
        return RedirectResponse("/login?error=1", status_code=302)


@app.get("/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    response = RedirectResponse("/login")
    response.delete_cookie(COOKIE_NAME)
    return response



@app.websocket("/ws/audio")
async def websocket_audio(ws: WebSocket):
    """WebSocket endpoint for bidirectional voice chat."""
    await handle_websocket(
        ws,
        tool_specs=get_tool_specs(),
        tool_handler=handle_tool_call,
    )


# ── Push Notification API ─────────────────────────────────────────

@app.get("/api/push/vapid-key")
async def vapid_key():
    """Return VAPID public key for push subscription."""
    key = get_vapid_public_key()
    if not key:
        return {"public_key": None, "message": "Push notifications not configured"}
    return {"public_key": key}


class PushSubscription(BaseModel):
    endpoint: str
    keys: dict
    expirationTime: Optional[float] = None


@app.post("/api/push/subscribe")
async def push_subscribe(subscription: PushSubscription, request: Request):
    """Store a push subscription in SQLite."""
    user_agent = request.headers.get("user-agent", "")
    add_subscription(subscription.model_dump(), user_agent=user_agent)
    return {"status": "ok"}


class NotificationAction(BaseModel):
    action: str
    title: str


class PushMessage(BaseModel):
    title: str = "Sonic2Life"
    body: str
    tag: str = "sonic2life"
    url: str = "/"
    notification_id: str = ""
    actions: list[NotificationAction] = []


@app.post("/api/push/send")
async def push_send(message: PushMessage):
    """Send a notification to all subscribers via SSE + Web Push."""
    result = await send_notification(
        title=message.title,
        body=message.body,
        tag=message.tag,
        url=message.url,
        notification_id=message.notification_id,
        actions=[a.model_dump() for a in message.actions],
    )
    return {"status": "ok", **result}


# ── SSE (Server-Sent Events) for in-app notifications ────────────

@app.get("/api/push/events")
async def push_events(request: Request):
    """SSE endpoint – frontend connects here to receive in-app notifications in real-time."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    add_sse_client(queue)

    async def event_generator():
        try:
            # Send initial keepalive
            yield "data: {\"type\": \"connected\"}\n\n"

            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    # Wait for notification with timeout (keepalive every 30s)
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        finally:
            remove_sse_client(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Notification Response API ─────────────────────────────────────

class NotificationResponse(BaseModel):
    notification_id: str
    action: str
    source: str = "banner"  # "banner", "system_notification", "voice"


@app.post("/api/push/respond")
async def push_respond(response: NotificationResponse):
    """Record user's response to a notification and execute the action."""
    # Always persist the response
    record_notification_response(
        notification_id=response.notification_id,
        action=response.action,
        source=response.source,
    )

    # Route medication actions
    if response.notification_id.startswith("med_"):
        if response.action == "taken":
            confirm_medication_from_notification(response.notification_id)
        elif response.action == "snooze":
            snooze_medication_from_notification(response.notification_id, minutes=15)

    return {
        "status": "ok",
        "notification_id": response.notification_id,
        "action": response.action,
    }


@app.get("/api/push/responses")
async def push_responses_list():
    """List all notification responses (for debugging)."""
    return get_notification_responses()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
