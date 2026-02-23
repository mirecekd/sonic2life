"""Web Push notification support for Sonic2Life.

VAPID keys are auto-generated on first run and persisted in SQLite.
Push subscriptions are persisted in SQLite.
SSE (Server-Sent Events) used for reliable in-app notification delivery.
"""

import asyncio
import base64
import json
import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

# SSE clients (asyncio.Queue per connected client)
_sse_clients: list[asyncio.Queue] = []

# Notification response log – now persisted to SQLite (see record/get functions below)

# VAPID keys
_vapid_private_key = None
_vapid_public_key = None


def _load_vapid_from_db() -> tuple[str, str]:
    """Try to load VAPID keys from SQLite settings table."""
    try:
        from app.tools.database import get_db
        db = get_db()
        try:
            priv_row = db.execute(
                "SELECT value FROM settings WHERE key = 'vapid_private_key'"
            ).fetchone()
            pub_row = db.execute(
                "SELECT value FROM settings WHERE key = 'vapid_public_key'"
            ).fetchone()
            priv = priv_row["value"] if priv_row else ""
            pub = pub_row["value"] if pub_row else ""
            return priv, pub
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"⚠️ Could not load VAPID keys from DB: {e}")
        return "", ""


def _save_vapid_to_db(private_key: str, public_key: str):
    """Persist VAPID keys to SQLite settings table."""
    try:
        from app.tools.database import get_db
        db = get_db()
        try:
            db.execute(
                """INSERT INTO settings (key, value, description)
                   VALUES ('vapid_private_key', ?, 'VAPID private key for Web Push (auto-generated)')
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (private_key,),
            )
            db.execute(
                """INSERT INTO settings (key, value, description)
                   VALUES ('vapid_public_key', ?, 'VAPID public key for Web Push (auto-generated)')
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (public_key,),
            )
            db.commit()
            logger.info("🔑 VAPID keys saved to database")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"⚠️ Could not save VAPID keys to DB: {e}")


def _ensure_vapid_keys():
    """Load or generate VAPID keys. Priority: env vars → SQLite → generate new."""
    global _vapid_private_key, _vapid_public_key

    if _vapid_private_key and _vapid_public_key:
        return

    # 1) Check env vars (highest priority, allows override)
    _vapid_private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    _vapid_public_key = os.getenv("VAPID_PUBLIC_KEY", "")

    if _vapid_private_key and _vapid_public_key:
        logger.info("🔑 VAPID keys loaded from environment")
        # Also persist to DB so they survive env changes
        _save_vapid_to_db(_vapid_private_key, _vapid_public_key)
        return

    # 2) Try loading from SQLite
    _vapid_private_key, _vapid_public_key = _load_vapid_from_db()

    if _vapid_private_key and _vapid_public_key:
        logger.info("🔑 VAPID keys loaded from database")
        return

    # 3) Generate new keys and persist to SQLite
    try:
        from py_vapid import Vapid
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        vapid = Vapid()
        vapid.generate_keys()

        # pywebpush needs raw 32-byte EC private key as urlsafe base64 (NOT PEM)
        raw_private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        _vapid_private_key = base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode("utf-8")

        # Vapid02 (py_vapid >= 1.9) removed public_key_urlsafe_base64()
        # Extract the uncompressed EC point and encode manually
        pub_bytes = vapid.public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        _vapid_public_key = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("utf-8")

        logger.info("🔑 VAPID keys generated")
        logger.info(f"   Public key: {_vapid_public_key[:20]}...")

        # Persist to SQLite
        _save_vapid_to_db(_vapid_private_key, _vapid_public_key)

    except ImportError:
        logger.warning("⚠️ py_vapid not installed — push notifications disabled")
        logger.warning("   Install with: pip install py-vapid pywebpush")
    except Exception as e:
        logger.warning(f"⚠️ VAPID key generation failed: {e}")


def get_vapid_public_key() -> str:
    """Get the VAPID public key for frontend subscription."""
    _ensure_vapid_keys()
    return _vapid_public_key or ""


def add_subscription(subscription_info: dict, user_agent: str = ""):
    """Store a push subscription in SQLite (upsert by endpoint)."""
    from app.tools.database import get_db

    endpoint = subscription_info.get("endpoint", "")
    keys = subscription_info.get("keys", {})
    keys_p256dh = keys.get("p256dh", "")
    keys_auth = keys.get("auth", "")
    sub_json = json.dumps(subscription_info)

    if not endpoint or not keys_p256dh or not keys_auth:
        logger.warning("📲 Invalid push subscription — missing endpoint or keys")
        return

    db = get_db()
    try:
        db.execute(
            """INSERT INTO push_subscriptions (endpoint, keys_p256dh, keys_auth, subscription_json, user_agent)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET
                   keys_p256dh = excluded.keys_p256dh,
                   keys_auth = excluded.keys_auth,
                   subscription_json = excluded.subscription_json,
                   user_agent = excluded.user_agent,
                   fail_count = 0""",
            (endpoint, keys_p256dh, keys_auth, sub_json, user_agent),
        )
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        logger.info(f"📲 Push subscription saved (total: {count})")
    finally:
        db.close()


def get_all_subscriptions() -> list[dict]:
    """Load all push subscriptions from SQLite."""
    from app.tools.database import get_db

    db = get_db()
    try:
        rows = db.execute("SELECT endpoint, subscription_json FROM push_subscriptions").fetchall()
        subs = []
        for row in rows:
            try:
                subs.append(json.loads(row["subscription_json"]))
            except (json.JSONDecodeError, KeyError):
                logger.warning(f"📲 Skipping invalid subscription: {row['endpoint'][:50]}...")
        return subs
    finally:
        db.close()


def remove_subscription(endpoint: str):
    """Remove a push subscription from SQLite by endpoint."""
    from app.tools.database import get_db

    db = get_db()
    try:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        db.commit()
        logger.info(f"📲 Push subscription removed: {endpoint[:50]}...")
    finally:
        db.close()


def _increment_fail_count(endpoint: str):
    """Increment fail counter; remove subscription if too many failures."""
    from app.tools.database import get_db

    max_failures = 3
    db = get_db()
    try:
        db.execute(
            "UPDATE push_subscriptions SET fail_count = fail_count + 1 WHERE endpoint = ?",
            (endpoint,),
        )
        db.commit()
        row = db.execute(
            "SELECT fail_count FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        ).fetchone()
        if row and row["fail_count"] >= max_failures:
            db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
            db.commit()
            logger.warning(f"📲 Subscription removed after {max_failures} failures: {endpoint[:50]}...")
    finally:
        db.close()


def _mark_success(endpoint: str):
    """Reset fail count and update last success timestamp."""
    from app.tools.database import get_db

    db = get_db()
    try:
        db.execute(
            "UPDATE push_subscriptions SET fail_count = 0, last_success_at = CURRENT_TIMESTAMP WHERE endpoint = ?",
            (endpoint,),
        )
        db.commit()
    finally:
        db.close()


# ── SSE (Server-Sent Events) ─────────────────────────────────────────

def add_sse_client(queue: asyncio.Queue):
    """Register a new SSE client."""
    _sse_clients.append(queue)
    logger.info(f"📡 SSE client connected (total: {len(_sse_clients)})")


def remove_sse_client(queue: asyncio.Queue):
    """Remove an SSE client."""
    if queue in _sse_clients:
        _sse_clients.remove(queue)
    logger.info(f"📡 SSE client disconnected (total: {len(_sse_clients)})")


async def broadcast_to_sse(data: dict):
    """Send a notification to all connected SSE clients."""
    if not _sse_clients:
        return 0

    sent = 0
    dead = []
    for queue in _sse_clients:
        try:
            queue.put_nowait(data)
            sent += 1
        except asyncio.QueueFull:
            dead.append(queue)

    for q in dead:
        _sse_clients.remove(q)

    if sent > 0:
        logger.info(f"📡 SSE broadcast to {sent} client(s)")
    return sent


# ── Notification Response ─────────────────────────────────────────────

def record_notification_response(notification_id: str, action: str, source: str):
    """Record a user's response to a notification (persisted to SQLite)."""
    from app.tools.database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO notification_responses (notification_id, action, source) VALUES (?, ?, ?)",
        (notification_id, action, source),
    )
    conn.commit()
    conn.close()
    logger.info(f"📋 Notification response: id={notification_id} action={action} source={source}")


def get_notification_responses() -> list:
    """Get all notification responses (persisted in SQLite)."""
    from app.tools.database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT notification_id, action, source, created_at FROM notification_responses ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Medication Snooze ─────────────────────────────────────────────

def add_medication_snooze(medication_id: int, minutes: int = 15):
    """Snooze a medication reminder for N minutes."""
    from app.tools.database import get_db
    from datetime import datetime, timedelta
    snooze_until = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO medication_snoozes (medication_id, snooze_until) VALUES (?, ?)",
        (medication_id, snooze_until),
    )
    conn.commit()
    conn.close()
    logger.info(f"⏰ Medication {medication_id} snoozed until {snooze_until}")


def is_medication_snoozed(medication_id: int) -> bool:
    """Check if a medication is currently snoozed."""
    from app.tools.database import get_db
    from datetime import datetime
    conn = get_db()
    row = conn.execute(
        "SELECT snooze_until FROM medication_snoozes WHERE medication_id = ? ORDER BY snooze_until DESC LIMIT 1",
        (medication_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False
    try:
        snooze_until = datetime.fromisoformat(row["snooze_until"])
        return datetime.now() < snooze_until
    except (ValueError, TypeError):
        return False


def confirm_medication_from_notification(notification_id: str):
    """Extract medication ID from notification_id and confirm it as taken."""
    from app.tools.database import get_db
    # notification_id format: "med_{med_id}_{date}"
    parts = notification_id.split("_")
    if len(parts) < 3 or parts[0] != "med":
        logger.warning(f"⚠️ Cannot parse medication notification_id: {notification_id}")
        return

    try:
        med_id = int(parts[1])
    except ValueError:
        logger.warning(f"⚠️ Invalid medication ID in notification_id: {notification_id}")
        return

    conn = get_db()
    row = conn.execute("SELECT id, name FROM medications WHERE id = ?", (med_id,)).fetchone()
    if not row:
        conn.close()
        logger.warning(f"⚠️ Medication ID {med_id} not found")
        return

    # Log to medication_log
    conn.execute(
        "INSERT INTO medication_log (medication_id, confirmed_by) VALUES (?, 'notification')",
        (med_id,),
    )
    conn.commit()
    conn.close()
    logger.info(f"💊 Medication '{row['name']}' confirmed via notification button")


def snooze_medication_from_notification(notification_id: str, minutes: int = 15):
    """Extract medication ID from notification_id and snooze it."""
    parts = notification_id.split("_")
    if len(parts) < 3 or parts[0] != "med":
        logger.warning(f"⚠️ Cannot parse medication notification_id: {notification_id}")
        return

    try:
        med_id = int(parts[1])
    except ValueError:
        logger.warning(f"⚠️ Invalid medication ID in notification_id: {notification_id}")
        return

    add_medication_snooze(med_id, minutes)


# ── Send Notification ─────────────────────────────────────────────────

async def send_notification(
    title: str,
    body: str,
    tag: str = "sonic2life",
    url: str = "/",
    notification_id: str = "",
    actions: list[dict] | None = None,
) -> dict:
    """Send notification via BOTH SSE (in-app) and Web Push (system).

    Returns dict with counts: {"sse": N, "push": N, "notification_id": "..."}
    """
    if not notification_id:
        notification_id = f"notif_{uuid.uuid4().hex[:8]}"

    # Payload for both channels
    payload = {
        "title": title,
        "body": body,
        "tag": tag,
        "url": url,
        "notification_id": notification_id,
        "actions": actions or [],
    }

    # 1) SSE broadcast (for open app windows – reliable, instant)
    sse_sent = await broadcast_to_sse(payload)

    # 2) Web Push (for closed app / background – async)
    push_sent = _send_web_push(payload)

    logger.info(f"📲 Notification sent: SSE={sse_sent} Push={push_sent} id={notification_id}")
    return {"sse": sse_sent, "push": push_sent, "notification_id": notification_id}


def _send_web_push(payload: dict) -> int:
    """Send Web Push to all subscribers from SQLite."""
    _ensure_vapid_keys()

    if not _vapid_private_key or not _vapid_public_key:
        return 0

    subscriptions = get_all_subscriptions()
    if not subscriptions:
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return 0

    # Web Push payload (include actions for system notification buttons)
    push_data = json.dumps({
        "title": payload.get("title", "Sonic2Life"),
        "body": payload.get("body", ""),
        "tag": payload.get("tag", "sonic2life"),
        "url": payload.get("url", "/"),
        "notification_id": payload.get("notification_id", ""),
        "icon": "/static/icons/icon-192.png",
        "badge": "/static/icons/badge-96.png",
        "actions": payload.get("actions", []),
    })

    vapid_claims = {
        "sub": "mailto:sonic2life@example.com",
    }

    sent = 0

    for sub in subscriptions:
        endpoint = sub.get("endpoint", "")
        try:
            webpush(
                headers={"Urgency": "high"},
                ttl=86400,
                subscription_info=sub,
                data=push_data,
                vapid_private_key=_vapid_private_key,
                vapid_claims=vapid_claims.copy(),
            )
            sent += 1
            _mark_success(endpoint)
        except Exception as e:
            logger.warning(f"📲 Push failed for endpoint: {e}")
            _increment_fail_count(endpoint)

    return sent


# ── Legacy function (kept for backwards compat) ──────────────────────

def send_push_notification(title: str, body: str, tag: str = "sonic2life", url: str = "/"):
    """Legacy sync wrapper – sends Web Push only (no SSE)."""
    return _send_web_push({
        "title": title,
        "body": body,
        "tag": tag,
        "url": url,
        "notification_id": "",
        "actions": [],
    })
