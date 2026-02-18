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

        -- User memory/preferences
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            category TEXT DEFAULT 'preference',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    logger.info("✅ Database tables initialized")
