"""Sonic2Life - Voice-first life assistant for seniors powered by Amazon Nova 2 Sonic."""

import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path

from pydantic import BaseModel
from typing import Optional

from app.config import HOST, PORT
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
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="Sonic2Life", description="Voice-first life assistant for seniors")

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
async def push_subscribe(subscription: PushSubscription):
    """Store a push subscription from the frontend."""
    add_subscription(subscription.model_dump())
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
    """Record user's response to a notification (e.g., medication confirmation)."""
    record_notification_response(
        notification_id=response.notification_id,
        action=response.action,
        source=response.source,
    )
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
