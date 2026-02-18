"""Event / Calendar Manager – CRUD operations for appointments, reminders, and scheduled events."""

import json
import logging
from datetime import datetime, timedelta

from strands import tool
from app.tools.database import get_db

logger = logging.getLogger(__name__)


@tool
def get_upcoming_events(days: int = 7) -> str:
    """Get upcoming events and appointments for the next N days.

    Args:
        days: Number of days to look ahead. Default 7.

    Returns:
        JSON list of upcoming events with title, time, description, and reminder info.
    """
    db = get_db()
    rows = db.execute(
        """SELECT id, title, description, event_time, reminder_minutes, morning_brief
           FROM events
           WHERE active = 1 AND event_time >= datetime('now') AND event_time <= datetime('now', ?)
           ORDER BY event_time""",
        (f"+{days} days",),
    ).fetchall()
    db.close()

    if not rows:
        return json.dumps({
            "events": [],
            "message": f"No events scheduled for the next {days} days.",
        }, ensure_ascii=False)

    events = []
    for r in rows:
        events.append({
            "id": r["id"],
            "title": r["title"],
            "description": r["description"] or "",
            "event_time": r["event_time"],
            "reminder_minutes_before": r["reminder_minutes"],
            "morning_brief": bool(r["morning_brief"]),
        })

    logger.info(f"📅 Returning {len(events)} upcoming events")
    return json.dumps({"events": events, "days_ahead": days}, ensure_ascii=False)


@tool
def get_todays_schedule() -> str:
    """Get today's schedule – all events and medication times for today.

    Returns:
        JSON with today's events and medications combined into a timeline.
    """
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    current_day = datetime.now().strftime("%a").lower()

    # Get today's events
    events = db.execute(
        """SELECT id, title, description, event_time, reminder_minutes
           FROM events
           WHERE active = 1 AND date(event_time) = ?
           ORDER BY event_time""",
        (today,),
    ).fetchall()

    # Get today's medications (filter by day of week)
    meds = db.execute(
        "SELECT id, name, dosage, schedule_time, days, notes FROM medications WHERE active = 1 ORDER BY schedule_time"
    ).fetchall()

    db.close()

    timeline = []

    for e in events:
        try:
            dt = datetime.fromisoformat(e["event_time"])
            time_str = dt.strftime("%H:%M")
        except Exception:
            time_str = "?"
        timeline.append({
            "time": time_str,
            "type": "event",
            "title": e["title"],
            "description": e["description"] or "",
        })

    for m in meds:
        # Check if today is a scheduled day for this medication
        med_days = [d.strip() for d in (m["days"] or "").split(",")]
        if current_day not in med_days:
            continue
        timeline.append({
            "time": m["schedule_time"],
            "type": "medication",
            "title": f"💊 {m['name']}",
            "description": f"{m['dosage'] or ''} {m['notes'] or ''}".strip(),
        })

    # Sort by time
    timeline.sort(key=lambda x: x["time"])

    logger.info(f"📅 Today's schedule: {len(timeline)} items")
    return json.dumps({
        "date": today,
        "day": datetime.now().strftime("%A"),
        "schedule": timeline,
    }, ensure_ascii=False)


@tool
def add_event(title: str, event_time: str, description: str = "", reminder_minutes: int = 60, morning_brief: bool = True) -> str:
    """Add a new event or appointment to the calendar.

    Args:
        title: Title of the event (e.g. "Doctor appointment", "Dentist", "Family visit")
        event_time: Date and time in format "YYYY-MM-DD HH:MM" (e.g. "2026-02-19 11:00")
        description: Optional description or details about the event
        reminder_minutes: How many minutes before the event to send a reminder. Default 60.
        morning_brief: Whether to include this event in the morning briefing. Default True.

    Returns:
        Confirmation message.
    """
    # Parse and validate the time
    try:
        dt = datetime.strptime(event_time, "%Y-%m-%d %H:%M")
        iso_time = dt.isoformat()
    except ValueError:
        try:
            # Try ISO format directly
            dt = datetime.fromisoformat(event_time)
            iso_time = dt.isoformat()
        except ValueError:
            return json.dumps({
                "success": False,
                "message": f"Invalid date/time format: '{event_time}'. Use 'YYYY-MM-DD HH:MM' format.",
            }, ensure_ascii=False)

    db = get_db()
    cursor = db.execute(
        "INSERT INTO events (title, description, event_time, reminder_minutes, morning_brief) VALUES (?, ?, ?, ?, ?)",
        (title, description, iso_time, reminder_minutes, 1 if morning_brief else 0),
    )
    db.commit()
    event_id = cursor.lastrowid
    db.close()

    logger.info(f"📅 Added event: {title} at {event_time}")
    return json.dumps({
        "success": True,
        "id": event_id,
        "message": f"Event '{title}' scheduled for {event_time}. Reminder will be sent {reminder_minutes} minutes before.",
    }, ensure_ascii=False)


@tool
def cancel_event(event_title: str) -> str:
    """Cancel/remove an event from the calendar by title.

    Args:
        event_title: Title (or partial title) of the event to cancel.

    Returns:
        Confirmation message.
    """
    db = get_db()
    result = db.execute(
        "UPDATE events SET active = 0 WHERE title LIKE ? AND active = 1",
        (f"%{event_title}%",),
    )
    db.commit()
    affected = result.rowcount
    db.close()

    if affected == 0:
        return json.dumps({
            "success": False,
            "message": f"No active event found matching '{event_title}'.",
        }, ensure_ascii=False)

    logger.info(f"📅 Cancelled event: {event_title}")
    return json.dumps({
        "success": True,
        "message": f"Event '{event_title}' has been cancelled.",
    }, ensure_ascii=False)


@tool
def update_event_time(event_title: str, new_time: str) -> str:
    """Reschedule an event to a new date/time.

    Args:
        event_title: Title (or partial title) of the event to reschedule.
        new_time: New date and time in format "YYYY-MM-DD HH:MM"

    Returns:
        Confirmation message.
    """
    try:
        dt = datetime.strptime(new_time, "%Y-%m-%d %H:%M")
        iso_time = dt.isoformat()
    except ValueError:
        try:
            dt = datetime.fromisoformat(new_time)
            iso_time = dt.isoformat()
        except ValueError:
            return json.dumps({
                "success": False,
                "message": f"Invalid date/time format: '{new_time}'. Use 'YYYY-MM-DD HH:MM'.",
            }, ensure_ascii=False)

    db = get_db()
    # Reset notification flags since time changed
    result = db.execute(
        "UPDATE events SET event_time = ?, notified = 0, brief_sent = 0 WHERE title LIKE ? AND active = 1",
        (iso_time, f"%{event_title}%"),
    )
    db.commit()
    affected = result.rowcount
    db.close()

    if affected == 0:
        return json.dumps({
            "success": False,
            "message": f"No active event found matching '{event_title}'.",
        }, ensure_ascii=False)

    logger.info(f"📅 Rescheduled event: {event_title} to {new_time}")
    return json.dumps({
        "success": True,
        "message": f"Event '{event_title}' rescheduled to {new_time}.",
    }, ensure_ascii=False)
