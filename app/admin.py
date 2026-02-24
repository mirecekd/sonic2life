"""Sonic2Life Admin Panel – FastAPI router for /admin and /api/admin/*."""

import json
import logging
import os
import shutil
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

from app.config import DATA_DIR
from app.tools.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

static_dir = Path(__file__).parent / "static"


# ── Admin Page ────────────────────────────────────────────────────

@router.get("/admin")
async def admin_page():
    """Serve the admin panel HTML."""
    return FileResponse(str(static_dir / "admin.html"))


# ── Settings API ──────────────────────────────────────────────────

@router.get("/api/admin/settings")
async def get_settings():
    """Return all settings as key-value dict."""
    conn = get_db()
    rows = conn.execute("SELECT key, value, description, updated_at FROM settings").fetchall()
    conn.close()
    return {
        "settings": [
            {"key": r["key"], "value": r["value"], "description": r["description"], "updated_at": r["updated_at"]}
            for r in rows
        ]
    }


class SettingUpdate(BaseModel):
    value: str


@router.put("/api/admin/settings/{key}")
async def update_setting(key: str, body: SettingUpdate):
    """Update a single setting by key."""
    conn = get_db()
    conn.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
        (body.value, key),
    )
    conn.commit()
    conn.close()
    logger.info(f"⚙️ Setting updated: {key}")
    return {"status": "ok", "key": key, "value": body.value}


class SettingCreate(BaseModel):
    key: str
    value: str
    description: str = ""


@router.post("/api/admin/settings")
async def create_setting(body: SettingCreate):
    """Create a new setting."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, description, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (body.key, body.value, body.description),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "key": body.key}


# ── Medications API ───────────────────────────────────────────────

@router.get("/api/admin/medications")
async def list_medications():
    """List all medications."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, dosage, schedule_time, days, notes, active, created_at FROM medications ORDER BY schedule_time"
    ).fetchall()
    conn.close()
    return {"medications": [dict(r) for r in rows]}


class MedicationCreate(BaseModel):
    name: str
    dosage: str = ""
    schedule_time: str  # HH:MM
    days: str = "mon,tue,wed,thu,fri,sat,sun"
    notes: str = ""
    active: int = 1


@router.post("/api/admin/medications")
async def create_medication(body: MedicationCreate):
    """Create a new medication."""
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO medications (name, dosage, schedule_time, days, notes, active) VALUES (?, ?, ?, ?, ?, ?)",
        (body.name, body.dosage, body.schedule_time, body.days, body.notes, body.active),
    )
    conn.commit()
    med_id = cursor.lastrowid
    conn.close()
    return {"status": "ok", "id": med_id}


class MedicationUpdate(BaseModel):
    name: Optional[str] = None
    dosage: Optional[str] = None
    schedule_time: Optional[str] = None
    days: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[int] = None


@router.put("/api/admin/medications/{med_id}")
async def update_medication(med_id: int, body: MedicationUpdate):
    """Update a medication."""
    conn = get_db()
    updates = []
    params = []
    for field in ["name", "dosage", "schedule_time", "days", "notes", "active"]:
        val = getattr(body, field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if updates:
        params.append(med_id)
        conn.execute(f"UPDATE medications SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()
    return {"status": "ok", "id": med_id}


@router.delete("/api/admin/medications/{med_id}")
async def delete_medication(med_id: int):
    """Delete a medication."""
    conn = get_db()
    conn.execute("DELETE FROM medications WHERE id = ?", (med_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "id": med_id}


@router.get("/api/admin/medication-log")
async def list_medication_log():
    """List medication log entries."""
    conn = get_db()
    rows = conn.execute("""
        SELECT ml.id, ml.medication_id, m.name as medication_name, ml.taken_at, ml.confirmed_by
        FROM medication_log ml
        LEFT JOIN medications m ON ml.medication_id = m.id
        ORDER BY ml.taken_at DESC
        LIMIT 100
    """).fetchall()
    conn.close()
    return {"log": [dict(r) for r in rows]}


# ── Notification Responses API ────────────────────────────────────

@router.get("/api/admin/notification-responses")
async def list_notification_responses():
    """List all notification responses (persisted in SQLite)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, notification_id, action, source, created_at
        FROM notification_responses
        ORDER BY created_at DESC
        LIMIT 200
    """).fetchall()
    conn.close()
    return {"responses": [dict(r) for r in rows]}


@router.get("/api/admin/medication-snoozes")
async def list_medication_snoozes():
    """List active medication snoozes."""
    conn = get_db()
    rows = conn.execute("""
        SELECT ms.id, ms.medication_id, m.name as medication_name,
               ms.snooze_until, ms.created_at
        FROM medication_snoozes ms
        LEFT JOIN medications m ON ms.medication_id = m.id
        ORDER BY ms.created_at DESC
        LIMIT 100
    """).fetchall()
    conn.close()
    return {"snoozes": [dict(r) for r in rows]}


@router.delete("/api/admin/medication-snoozes/{snooze_id}")
async def delete_medication_snooze(snooze_id: int):
    """Delete a medication snooze."""
    conn = get_db()
    conn.execute("DELETE FROM medication_snoozes WHERE id = ?", (snooze_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted", "id": snooze_id}


# ── Memory / Preferences API ─────────────────────────────────────

@router.get("/api/admin/memory")
async def list_memory():
    """List all memory entries."""
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value, category, updated_at FROM memory ORDER BY category, key"
    ).fetchall()
    conn.close()
    return {"memory": [dict(r) for r in rows]}


class MemoryCreate(BaseModel):
    key: str
    value: str
    category: str = "preference"


@router.post("/api/admin/memory")
async def create_memory(body: MemoryCreate):
    """Create or update a memory entry."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO memory (key, value, category, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (body.key, body.value, body.category),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "key": body.key}


@router.delete("/api/admin/memory/{key}")
async def delete_memory(key: str):
    """Delete a memory entry."""
    conn = get_db()
    conn.execute("DELETE FROM memory WHERE key = ?", (key,))
    conn.commit()
    conn.close()
    return {"status": "ok", "key": key}


# ── Events / Calendar API ────────────────────────────────────────

@router.get("/api/admin/events")
async def list_events():
    """List all events."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, description, event_time, reminder_minutes, morning_brief, notified, brief_sent, active, created_at FROM events ORDER BY event_time"
    ).fetchall()
    conn.close()
    return {"events": [dict(r) for r in rows]}


class EventCreate(BaseModel):
    title: str
    description: str = ""
    event_time: str  # ISO 8601 datetime
    reminder_minutes: int = 60
    morning_brief: int = 1


@router.post("/api/admin/events")
async def create_event(body: EventCreate):
    """Create a new event."""
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO events (title, description, event_time, reminder_minutes, morning_brief) VALUES (?, ?, ?, ?, ?)",
        (body.title, body.description, body.event_time, body.reminder_minutes, body.morning_brief),
    )
    conn.commit()
    event_id = cursor.lastrowid
    conn.close()
    return {"status": "ok", "id": event_id}


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    event_time: Optional[str] = None
    reminder_minutes: Optional[int] = None
    morning_brief: Optional[int] = None
    active: Optional[int] = None
    notified: Optional[int] = None
    brief_sent: Optional[int] = None


@router.put("/api/admin/events/{event_id}")
async def update_event(event_id: int, body: EventUpdate):
    """Update an event."""
    conn = get_db()
    updates = []
    params = []
    for field in ["title", "description", "event_time", "reminder_minutes", "morning_brief", "active", "notified", "brief_sent"]:
        val = getattr(body, field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if updates:
        params.append(event_id)
        conn.execute(f"UPDATE events SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()
    return {"status": "ok", "id": event_id}


@router.delete("/api/admin/events/{event_id}")
async def delete_event(event_id: int):
    """Delete an event."""
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "id": event_id}


# ── Dashboard API ─────────────────────────────────────────────────

@router.get("/api/admin/dashboard")
async def dashboard():
    """Return dashboard statistics."""
    conn = get_db()

    med_count = conn.execute("SELECT COUNT(*) as c FROM medications WHERE active = 1").fetchone()["c"]
    event_count = conn.execute("SELECT COUNT(*) as c FROM events WHERE active = 1").fetchone()["c"]
    memory_count = conn.execute("SELECT COUNT(*) as c FROM memory").fetchone()["c"]
    push_count = conn.execute("SELECT COUNT(*) as c FROM push_subscriptions").fetchone()["c"]
    push_stale = conn.execute("SELECT COUNT(*) as c FROM push_subscriptions WHERE fail_count > 0").fetchone()["c"]

    last_log = conn.execute("""
        SELECT ml.taken_at, m.name as medication_name
        FROM medication_log ml
        LEFT JOIN medications m ON ml.medication_id = m.id
        ORDER BY ml.taken_at DESC LIMIT 1
    """).fetchone()

    upcoming_events = conn.execute("""
        SELECT id, title, event_time, reminder_minutes
        FROM events
        WHERE active = 1 AND event_time >= datetime('now')
        ORDER BY event_time LIMIT 5
    """).fetchall()

    # Scheduler settings
    sched_enabled = conn.execute("SELECT value FROM settings WHERE key = 'scheduler_enabled'").fetchone()
    sched_interval = conn.execute("SELECT value FROM settings WHERE key = 'scheduler_interval_minutes'").fetchone()

    conn.close()

    # Data dir stats
    data_path = Path(DATA_DIR)
    files_in_data = [f.name for f in data_path.iterdir() if f.is_file()] if data_path.exists() else []
    data_size_mb = sum(f.stat().st_size for f in data_path.iterdir() if f.is_file()) / (1024 * 1024) if data_path.exists() else 0

    return {
        "active_medications": med_count,
        "active_events": event_count,
        "memory_entries": memory_count,
        "push_subscriptions": push_count,
        "push_stale": push_stale,
        "last_medication_log": dict(last_log) if last_log else None,
        "upcoming_events": [dict(e) for e in upcoming_events],
        "scheduler": {
            "enabled": sched_enabled["value"] if sched_enabled else "false",
            "interval_minutes": sched_interval["value"] if sched_interval else "5",
        },
        "data_dir": {
            "path": str(data_path.resolve()),
            "files_count": len(files_in_data),
            "size_mb": round(data_size_mb, 2),
        },
    }


# ── File Management API (data/ workdir) ──────────────────────────

@router.get("/api/admin/files")
async def list_files():
    """List all files in the data directory."""
    data_path = Path(DATA_DIR)
    if not data_path.exists():
        return {"files": []}

    files = []
    for f in sorted(data_path.iterdir()):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"files": files}


@router.post("/api/admin/files/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the data directory."""
    data_path = Path(DATA_DIR)
    data_path.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = Path(file.filename).name  # strip any path components
    dest = data_path / safe_name

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info(f"📁 File uploaded: {safe_name}")
    return {"status": "ok", "filename": safe_name, "size": dest.stat().st_size}


@router.get("/api/admin/files/download/{filename}")
async def download_file(filename: str):
    """Download a file from the data directory."""
    data_path = Path(DATA_DIR)
    file_path = data_path / Path(filename).name  # sanitize

    if not file_path.exists() or not file_path.is_file():
        return {"status": "error", "message": "File not found"}

    return FileResponse(
        str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@router.delete("/api/admin/files/{filename}")
async def delete_file(filename: str):
    """Delete a file from the data directory."""
    data_path = Path(DATA_DIR)
    file_path = data_path / Path(filename).name  # sanitize

    if not file_path.exists() or not file_path.is_file():
        return {"status": "error", "message": "File not found"}

    # Don't allow deleting the database
    if file_path.name == "sonic2life.db":
        return {"status": "error", "message": "Cannot delete the database file"}

    file_path.unlink()
    logger.info(f"🗑️ File deleted: {filename}")
    return {"status": "ok", "filename": filename}


@router.get("/api/admin/files/db-backup")
async def backup_database():
    """Download a backup of the SQLite database."""
    from app.config import DATABASE_PATH
    db_path = Path(DATABASE_PATH)

    if not db_path.exists():
        return {"status": "error", "message": "Database not found"}

    backup_name = f"sonic2life_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    return FileResponse(
        str(db_path),
        filename=backup_name,
        media_type="application/octet-stream",
    )


# ── Push Subscriptions ────────────────────────────────────────────────

@router.get("/api/admin/push-subscriptions")
async def list_push_subscriptions():
    """List all push subscriptions."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT id, endpoint, user_agent, created_at, last_success_at, fail_count
               FROM push_subscriptions ORDER BY created_at DESC"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "endpoint": r["endpoint"],
                "endpoint_short": r["endpoint"][:80] + "..." if len(r["endpoint"]) > 80 else r["endpoint"],
                "user_agent": r["user_agent"] or "",
                "created_at": r["created_at"],
                "last_success_at": r["last_success_at"],
                "fail_count": r["fail_count"],
            }
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/api/admin/push-subscriptions/all")
async def delete_all_push_subscriptions():
    """Delete ALL push subscriptions."""
    db = get_db()
    try:
        count = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        db.execute("DELETE FROM push_subscriptions")
        db.commit()
        logger.info(f"📲 All push subscriptions deleted via admin ({count} removed)")
        return {"status": "ok", "deleted": count}
    finally:
        db.close()


@router.delete("/api/admin/push-subscriptions/stale")
async def delete_stale_push_subscriptions():
    """Delete push subscriptions with fail_count > 0."""
    db = get_db()
    try:
        count = db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE fail_count > 0").fetchone()[0]
        db.execute("DELETE FROM push_subscriptions WHERE fail_count > 0")
        db.commit()
        logger.info(f"📲 Stale push subscriptions deleted via admin ({count} removed)")
        return {"status": "ok", "deleted": count}
    finally:
        db.close()


@router.delete("/api/admin/push-subscriptions/{sub_id}")
async def delete_push_subscription(sub_id: int):
    """Delete a push subscription by ID."""
    db = get_db()
    try:
        db.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub_id,))
        db.commit()
        logger.info(f"📲 Push subscription {sub_id} deleted via admin")
        return {"status": "ok"}
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────



# ── Emergency Contacts API ────────────────────────────────────────

@router.get("/api/admin/contacts")
async def list_contacts():
    """Return all emergency contacts."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, fullname, relationship, phone, active, created_at FROM emergency_contacts ORDER BY name"
    ).fetchall()
    conn.close()
    return {
        "contacts": [
            {"id": r["id"], "name": r["name"], "fullname": r["fullname"], "relationship": r["relationship"],
             "phone": r["phone"], "active": r["active"], "created_at": r["created_at"]}
            for r in rows
        ]
    }


class ContactCreate(BaseModel):
    name: str
    phone: str
    fullname: str = ""
    relationship: str = ""


@router.post("/api/admin/contacts")
async def create_contact(body: ContactCreate):
    """Create a new emergency contact."""
    conn = get_db()
    conn.execute(
        "INSERT INTO emergency_contacts (name, fullname, relationship, phone) VALUES (?, ?, ?, ?)",
        (body.name, body.fullname, body.relationship, body.phone),
    )
    conn.commit()
    conn.close()
    logger.info(f"📞 Contact created: {body.name}")
    return {"status": "ok", "name": body.name}


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    fullname: Optional[str] = None
    relationship: Optional[str] = None
    phone: Optional[str] = None


@router.put("/api/admin/contacts/{contact_id}")
async def update_contact(contact_id: int, body: ContactUpdate):
    """Update an emergency contact."""
    conn = get_db()
    updates = []
    params = []
    for field in ["name", "fullname", "relationship", "phone"]:
        val = getattr(body, field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if not updates:
        conn.close()
        return {"status": "error", "message": "No fields to update"}
    params.append(contact_id)
    conn.execute(f"UPDATE emergency_contacts SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    logger.info(f"📞 Contact updated: id={contact_id}")
    return {"status": "ok", "id": contact_id}


@router.delete("/api/admin/contacts/{contact_id}")
async def delete_contact(contact_id: int):
    """Delete an emergency contact."""
    conn = get_db()
    conn.execute("DELETE FROM emergency_contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()
    logger.info(f"📞 Contact deleted: id={contact_id}")
    return {"status": "ok", "id": contact_id}

@router.get("/api/admin/sms-log")
async def list_sms_log():
    """Return all SMS log entries."""
    conn = get_db()
    rows = conn.execute(
        """SELECT id, contact_name, phone, message, sns_message_id, status, error_detail, created_at
           FROM sms_log ORDER BY created_at DESC LIMIT 100"""
    ).fetchall()
    conn.close()
    return {
        "sms_log": [
            {"id": r["id"], "contact_name": r["contact_name"], "phone": r["phone"],
             "message": r["message"], "sns_message_id": r["sns_message_id"],
             "status": r["status"], "error_detail": r["error_detail"],
             "created_at": r["created_at"]}
            for r in rows
        ]
    }


@router.delete("/api/admin/sms-log/{log_id}")
async def delete_sms_log_entry(log_id: int):
    """Delete an SMS log entry."""
    conn = get_db()
    conn.execute("DELETE FROM sms_log WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "id": log_id}



def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
