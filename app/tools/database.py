"""SQLite database initialization and access for Sonic2Life."""

import sqlite3
import logging
from pathlib import Path

from app.config import DATABASE_PATH

logger = logging.getLogger(__name__)

_db_initialized = False


def get_db() -> sqlite3.Connection:
    """Get a SQLite connection, creating tables if needed."""
    global _db_initialized
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    if not _db_initialized:
        _init_tables(conn)
        _db_initialized = True

    return conn


def _init_tables(conn: sqlite3.Connection):
    """Create all tables if they don't exist."""
    conn.executescript("""
        -- Medication schedule
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dosage TEXT,
            schedule_time TEXT NOT NULL,  -- HH:MM format
            days TEXT DEFAULT 'mon,tue,wed,thu,fri,sat,sun',  -- comma-separated
            notes TEXT,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Medication log (when taken)
        CREATE TABLE IF NOT EXISTS medication_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication_id INTEGER NOT NULL,
            taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed_by TEXT DEFAULT 'voice',
            FOREIGN KEY (medication_id) REFERENCES medications(id)
        );

        -- Notification responses (persisted)
        CREATE TABLE IF NOT EXISTS notification_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id TEXT NOT NULL,
            action TEXT NOT NULL,
            source TEXT DEFAULT 'banner',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Medication snoozes
        CREATE TABLE IF NOT EXISTS medication_snoozes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication_id INTEGER NOT NULL,
            snooze_until TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (medication_id) REFERENCES medications(id)
        );

        -- User memory/preferences
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            category TEXT DEFAULT 'preference',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Admin settings (runtime configuration, overrides env vars)
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Push notification subscriptions
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            keys_p256dh TEXT NOT NULL,
            keys_auth TEXT NOT NULL,
            subscription_json TEXT NOT NULL,  -- full JSON for pywebpush
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_success_at TIMESTAMP,
            fail_count INTEGER DEFAULT 0
        );

        -- Calendar events / reminders
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            event_time TEXT NOT NULL,        -- ISO 8601 datetime
            reminder_minutes INTEGER DEFAULT 60,  -- minutes before to notify
            morning_brief INTEGER DEFAULT 1, -- include in morning brief
            notified INTEGER DEFAULT 0,      -- already sent reminder?
            brief_sent INTEGER DEFAULT 0,    -- already included in morning brief?
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Emergency contacts
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,           -- short name for voice (e.g. 'Jana')
            fullname TEXT,                -- full name (e.g. 'Jana Novakova')
            relationship TEXT,            -- e.g. 'daughter', 'doctor', 'neighbor'
            phone TEXT NOT NULL,          -- phone number (e.g. '+420123456789')
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    _seed_default_settings(conn)
    conn.commit()
    logger.info("✅ Database tables initialized")


def _seed_default_settings(conn: sqlite3.Connection):
    """Insert default settings if they don't exist yet."""
    defaults = [
        # User profile
        ("user_name", "", "User's first name for voice greeting (e.g. 'Miroslav')"),
        ("user_full_name", "", "User's full name for notifications/messages (e.g. 'Miroslav Dvořák')"),
        ("user_phone", "", "User's phone number for emergency/contacts"),
        # App settings
        ("timezone", "Europe/Prague", "Application timezone for scheduler"),
        ("language", "en", "UI language (en/cs)"),
        ("voice_id", "tiffany", "Nova Sonic voice ID"),
        ("scheduler_enabled", "true", "Enable medication & event reminder scheduler"),
        ("scheduler_interval_minutes", "5", "How often scheduler checks (minutes)"),
        ("notification_advance_minutes", "60", "Default minutes before event to notify"),
        ("system_prompt", "", "Custom system prompt override (empty = use default)"),
    ]
    for key, value, desc in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
            (key, value, desc),
        )
