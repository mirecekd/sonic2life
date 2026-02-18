"""Sonic2Life Admin Panel – FastAPI router for /admin and /api/admin/*."""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

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

    return {
        "active_medications": med_count,
        "active_events": event_count,
        "memory_entries": memory_count,
        "last_medication_log": dict(last_log) if last_log else None,
        "upcoming_events": [dict(e) for e in upcoming_events],
        "scheduler": {
            "enabled": sched_enabled["value"] if sched_enabled else "false",
            "interval_minutes": sched_interval["value"] if sched_interval else "5",
        },
    }
