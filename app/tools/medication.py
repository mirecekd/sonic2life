"""Medication Manager – CRUD operations for medication schedule and logging."""

import json
import logging
from datetime import datetime

from strands import tool
from app.tools.database import get_db

logger = logging.getLogger(__name__)


@tool
def get_medication_schedule() -> str:
    """Get the current medication schedule. Returns all active medications with their schedule times.

    Returns:
        JSON list of medications with name, dosage, schedule_time, and days.
    """
    db = get_db()
    rows = db.execute(
        "SELECT id, name, dosage, schedule_time, days, notes FROM medications WHERE active = 1 ORDER BY schedule_time"
    ).fetchall()
    db.close()

    if not rows:
        return json.dumps({"medications": [], "message": "No medications scheduled."}, ensure_ascii=False)

    meds = []
    for r in rows:
        meds.append({
            "id": r["id"],
            "name": r["name"],
            "dosage": r["dosage"],
            "time": r["schedule_time"],
            "days": r["days"],
            "notes": r["notes"],
        })

    logger.info(f"💊 Returning {len(meds)} medications")
    return json.dumps({"medications": meds}, ensure_ascii=False)


@tool
def add_medication(name: str, schedule_time: str, dosage: str = "", days: str = "mon,tue,wed,thu,fri,sat,sun", notes: str = "") -> str:
    """Add a new medication to the schedule.

    Args:
        name: Name of the medication (e.g. "Warfarin", "Metformin")
        schedule_time: Time to take it in HH:MM format (e.g. "08:00", "20:00")
        dosage: Dosage information (e.g. "1 tableta", "5mg")
        days: Comma-separated days (e.g. "mon,wed,fri"). Default is every day.
        notes: Additional notes (e.g. "po jídle", "nalačno")

    Returns:
        Confirmation message.
    """
    db = get_db()
    db.execute(
        "INSERT INTO medications (name, dosage, schedule_time, days, notes) VALUES (?, ?, ?, ?, ?)",
        (name, dosage, schedule_time, days, notes),
    )
    db.commit()
    db.close()

    logger.info(f"💊 Added medication: {name} at {schedule_time}")
    return json.dumps({
        "success": True,
        "message": f"Medication '{name}' added at {schedule_time}.",
    }, ensure_ascii=False)


@tool
def confirm_medication_taken(medication_name: str) -> str:
    """Confirm that a medication was taken. Logs the current time.

    Args:
        medication_name: Name of the medication that was taken.

    Returns:
        Confirmation message.
    """
    db = get_db()

    # Find the medication
    row = db.execute(
        "SELECT id, name FROM medications WHERE name LIKE ? AND active = 1",
        (f"%{medication_name}%",),
    ).fetchone()

    if not row:
        db.close()
        return json.dumps({
            "success": False,
            "message": f"Medication '{medication_name}' not found in schedule.",
        }, ensure_ascii=False)

    # Log it
    db.execute(
        "INSERT INTO medication_log (medication_id, confirmed_by) VALUES (?, 'voice')",
        (row["id"],),
    )
    db.commit()
    db.close()

    now = datetime.now().strftime("%H:%M")
    logger.info(f"💊 Confirmed: {row['name']} taken at {now}")
    return json.dumps({
        "success": True,
        "message": f"Confirmed: {row['name']} taken at {now}.",
    }, ensure_ascii=False)


@tool
def remove_medication(medication_name: str) -> str:
    """Remove a medication from the schedule (deactivate it).

    Args:
        medication_name: Name of the medication to remove.

    Returns:
        Confirmation message.
    """
    db = get_db()
    result = db.execute(
        "UPDATE medications SET active = 0 WHERE name LIKE ? AND active = 1",
        (f"%{medication_name}%",),
    )
    db.commit()
    affected = result.rowcount
    db.close()

    if affected == 0:
        return json.dumps({
            "success": False,
            "message": f"Medication '{medication_name}' not found.",
        }, ensure_ascii=False)

    logger.info(f"💊 Removed medication: {medication_name}")
    return json.dumps({
        "success": True,
        "message": f"Medication '{medication_name}' removed from schedule.",
    }, ensure_ascii=False)


@tool
def get_medication_history(medication_name: str = "", days: int = 7) -> str:
    """Get medication taking history.

    Args:
        medication_name: Optional filter by medication name. Empty for all.
        days: Number of days to look back. Default 7.

    Returns:
        History of medication confirmations.
    """
    db = get_db()

    if medication_name:
        rows = db.execute(
            """SELECT m.name, ml.taken_at
               FROM medication_log ml
               JOIN medications m ON ml.medication_id = m.id
               WHERE m.name LIKE ? AND ml.taken_at >= datetime('now', ?)
               ORDER BY ml.taken_at DESC""",
            (f"%{medication_name}%", f"-{days} days"),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT m.name, ml.taken_at
               FROM medication_log ml
               JOIN medications m ON ml.medication_id = m.id
               WHERE ml.taken_at >= datetime('now', ?)
               ORDER BY ml.taken_at DESC""",
            (f"-{days} days",),
        ).fetchall()
    db.close()

    history = [{"name": r["name"], "taken_at": r["taken_at"]} for r in rows]

    logger.info(f"💊 History: {len(history)} entries")
    return json.dumps({"history": history, "days": days}, ensure_ascii=False)
