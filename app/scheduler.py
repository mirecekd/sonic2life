"""Sonic2Life Scheduler – Background task for medication & event reminders."""

import asyncio
import logging
from datetime import datetime, timedelta

from app.tools.database import get_db
from app.push import send_notification, is_medication_snoozed

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None


def _get_setting(conn, key: str, default: str = "") -> str:
    """Get a single setting value from DB."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _get_timezone_offset():
    """Get timezone offset. For simplicity, uses pytz if available, else UTC."""
    try:
        import zoneinfo
        from app.tools.database import get_db
        conn = get_db()
        tz_name = _get_setting(conn, "timezone", "Europe/Prague")
        conn.close()
        tz = zoneinfo.ZoneInfo(tz_name)
        now = datetime.now(tz)
        return now, tz
    except Exception:
        return datetime.utcnow(), None


async def _check_medications():
    """Check if any medications are due and send notifications."""
    try:
        now, tz = _get_timezone_offset()
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%a").lower()

        conn = get_db()
        meds = conn.execute(
            "SELECT id, name, dosage, schedule_time, days, notes FROM medications WHERE active = 1"
        ).fetchall()

        for med in meds:
            # Check if today is a scheduled day
            days = [d.strip() for d in med["days"].split(",")]
            if current_day not in days:
                continue

            # Check if it's time (within 2-minute window)
            sched_time = med["schedule_time"]
            try:
                sched_h, sched_m = map(int, sched_time.split(":"))
                now_h, now_m = map(int, current_time.split(":"))
                diff_minutes = abs((now_h * 60 + now_m) - (sched_h * 60 + sched_m))
            except ValueError:
                continue

            if diff_minutes > 2:
                continue

            # Check if already notified today
            today_str = now.strftime("%Y-%m-%d")
            already = conn.execute(
                "SELECT id FROM medication_log WHERE medication_id = ? AND taken_at LIKE ?",
                (med["id"], f"{today_str}%"),
            ).fetchone()

            if already:
                continue

            # Check if snoozed
            if is_medication_snoozed(med["id"]):
                logger.info(f"💤 Medication '{med['name']}' is snoozed, skipping")
                continue

            # Send notification!
            dosage_text = f" ({med['dosage']})" if med["dosage"] else ""
            body = f"💊 Time to take: {med['name']}{dosage_text}"
            if med["notes"]:
                body += f"\n{med['notes']}"

            notification_id = f"med_{med['id']}_{today_str}"
            await send_notification(
                title="Medication Reminder",
                body=body,
                tag="medication",
                notification_id=notification_id,
                actions=[
                    {"action": "taken", "title": "✅ Taken"},
                    {"action": "snooze", "title": "⏰ Snooze 15min"},
                ],
            )
            logger.info(f"💊 Medication reminder sent: {med['name']} at {sched_time}")

        # ── Check for expired snoozes (re-send reminder after snooze period) ──
        today_str = now.strftime("%Y-%m-%d")
        expired_snoozes = conn.execute("""
            SELECT DISTINCT ms.medication_id, m.name, m.dosage, m.notes
            FROM medication_snoozes ms
            JOIN medications m ON ms.medication_id = m.id
            WHERE ms.snooze_until <= ?
              AND ms.snooze_until >= ?
              AND m.active = 1
              AND ms.medication_id NOT IN (
                  SELECT medication_id FROM medication_log
                  WHERE taken_at LIKE ?
              )
        """, (
            now.isoformat(),
            (now - timedelta(minutes=6)).isoformat(),  # within last scheduler cycle
            f"{today_str}%",
        )).fetchall()

        for med in expired_snoozes:
            # Don't re-send if currently snoozed again
            if is_medication_snoozed(med["medication_id"]):
                continue

            dosage_text = f" ({med['dosage']})" if med["dosage"] else ""
            body = f"💊 Reminder (after snooze): {med['name']}{dosage_text}"
            if med["notes"]:
                body += f"\n{med['notes']}"

            notification_id = f"med_{med['medication_id']}_{today_str}_snooze"
            await send_notification(
                title="Medication Reminder",
                body=body,
                tag="medication",
                notification_id=notification_id,
                actions=[
                    {"action": "taken", "title": "✅ Taken"},
                    {"action": "snooze", "title": "⏰ Snooze 15min"},
                ],
            )
            logger.info(f"💊 Post-snooze reminder sent: {med['name']}")

        conn.close()
    except Exception as e:
        logger.error(f"❌ Medication check error: {e}")


async def _check_events():
    """Check for upcoming events that need reminders or morning brief."""
    try:
        now, tz = _get_timezone_offset()
        conn = get_db()

        # ── Pre-event reminders ──
        events = conn.execute("""
            SELECT id, title, description, event_time, reminder_minutes
            FROM events
            WHERE active = 1 AND notified = 0
        """).fetchall()

        for event in events:
            try:
                event_dt = datetime.fromisoformat(event["event_time"])
                if tz:
                    import zoneinfo
                    if event_dt.tzinfo is None:
                        event_dt = event_dt.replace(tzinfo=tz)
                reminder_dt = event_dt - timedelta(minutes=event["reminder_minutes"])

                if now >= reminder_dt and now < event_dt:
                    # Time to notify!
                    mins_until = int((event_dt - now).total_seconds() / 60)
                    body = f"📅 {event['title']} in {mins_until} minutes"
                    if event["description"]:
                        body += f"\n{event['description']}"

                    await send_notification(
                        title="Event Reminder",
                        body=body,
                        tag="event",
                        notification_id=f"event_{event['id']}",
                    )

                    # Mark as notified
                    conn.execute(
                        "UPDATE events SET notified = 1 WHERE id = ?",
                        (event["id"],),
                    )
                    conn.commit()
                    logger.info(f"📅 Event reminder sent: {event['title']}")

            except (ValueError, TypeError) as e:
                logger.warning(f"⚠️ Invalid event time for '{event['title']}': {e}")

        # ── Morning brief (check if between 6:00-9:00 and not yet sent) ──
        current_hour = now.hour
        if 6 <= current_hour <= 9:
            today_str = now.strftime("%Y-%m-%d")
            brief_events = conn.execute("""
                SELECT id, title, description, event_time, reminder_minutes
                FROM events
                WHERE active = 1 AND morning_brief = 1 AND brief_sent = 0
                    AND date(event_time) = ?
            """, (today_str,)).fetchall()

            if brief_events:
                body_lines = ["🌅 Today's schedule:"]
                for ev in brief_events:
                    try:
                        ev_dt = datetime.fromisoformat(ev["event_time"])
                        time_str = ev_dt.strftime("%H:%M")
                    except Exception:
                        time_str = "?"
                    body_lines.append(f"• {time_str} – {ev['title']}")

                await send_notification(
                    title="Morning Brief",
                    body="\n".join(body_lines),
                    tag="morning_brief",
                    notification_id=f"brief_{today_str}",
                )

                # Mark all as brief_sent
                for ev in brief_events:
                    conn.execute("UPDATE events SET brief_sent = 1 WHERE id = ?", (ev["id"],))
                conn.commit()
                logger.info(f"🌅 Morning brief sent with {len(brief_events)} events")

        conn.close()
    except Exception as e:
        logger.error(f"❌ Event check error: {e}")


async def _scheduler_loop():
    """Main scheduler loop – runs periodically."""
    logger.info("⏰ Scheduler started")

    while True:
        try:
            conn = get_db()
            enabled = _get_setting(conn, "scheduler_enabled", "true")
            interval = int(_get_setting(conn, "scheduler_interval_minutes", "5"))
            conn.close()

            if enabled.lower() == "true":
                await _check_medications()
                await _check_events()
            else:
                logger.debug("⏸️ Scheduler disabled, sleeping...")

            await asyncio.sleep(interval * 60)

        except asyncio.CancelledError:
            logger.info("⏰ Scheduler stopped")
            break
        except Exception as e:
            logger.error(f"❌ Scheduler loop error: {e}")
            await asyncio.sleep(60)  # Retry in 1 minute on error


async def start_scheduler():
    """Start the background scheduler task."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("⏰ Scheduler task created")


async def stop_scheduler():
    """Stop the background scheduler task."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
    logger.info("⏰ Scheduler task stopped")


def get_scheduler_status() -> dict:
    """Return current scheduler status."""
    return {
        "running": _scheduler_task is not None and not _scheduler_task.done(),
    }
