"""Memory Tool – persistent user preferences and remembered facts."""

import json
import logging

from strands import tool
from app.tools.database import get_db

logger = logging.getLogger(__name__)


@tool
def remember(key: str, value: str, category: str = "preference") -> str:
    """Remember a piece of information about the user.

    Args:
        key: What to remember (e.g. "favorite_color", "grandchild_name", "allergy")
        value: The value to remember (e.g. "blue", "Tomáš", "penicillin")
        category: Category of memory: "preference", "health", "contact", "personal"

    Returns:
        Confirmation message.
    """
    db = get_db()
    db.execute(
        """INSERT INTO memory (key, value, category, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value = ?, category = ?, updated_at = CURRENT_TIMESTAMP""",
        (key, value, category, value, category),
    )
    db.commit()
    db.close()

    logger.info(f"🧠 Remembered: {key} = {value} [{category}]")
    return json.dumps({
        "success": True,
        "message": f"Zapamatováno: {key} = {value}",
    }, ensure_ascii=False)


@tool
def recall(key: str = "", category: str = "") -> str:
    """Recall stored information about the user.

    Args:
        key: Specific key to recall. If empty, returns all memories (optionally filtered by category).
        category: Filter by category ("preference", "health", "contact", "personal"). Empty for all.

    Returns:
        The remembered information.
    """
    db = get_db()

    if key:
        row = db.execute(
            "SELECT key, value, category, updated_at FROM memory WHERE key = ?",
            (key,),
        ).fetchone()
        db.close()

        if not row:
            return json.dumps({"found": False, "message": f"Nemám žádnou informaci o '{key}'."}, ensure_ascii=False)

        logger.info(f"🧠 Recalled: {key} = {row['value']}")
        return json.dumps({
            "found": True,
            "key": row["key"],
            "value": row["value"],
            "category": row["category"],
        }, ensure_ascii=False)
    else:
        if category:
            rows = db.execute(
                "SELECT key, value, category FROM memory WHERE category = ? ORDER BY key",
                (category,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT key, value, category FROM memory ORDER BY category, key"
            ).fetchall()
        db.close()

        memories = [{"key": r["key"], "value": r["value"], "category": r["category"]} for r in rows]

        if not memories:
            return json.dumps({"memories": [], "message": "Zatím si nic nepamatuji."}, ensure_ascii=False)

        logger.info(f"🧠 Recalled {len(memories)} memories")
        return json.dumps({"memories": memories}, ensure_ascii=False)


@tool
def forget(key: str) -> str:
    """Forget a piece of stored information.

    Args:
        key: The key to forget.

    Returns:
        Confirmation message.
    """
    db = get_db()
    result = db.execute("DELETE FROM memory WHERE key = ?", (key,))
    db.commit()
    affected = result.rowcount
    db.close()

    if affected == 0:
        return json.dumps({"success": False, "message": f"Nic o '{key}' jsem nevěděl/a."}, ensure_ascii=False)

    logger.info(f"🧠 Forgot: {key}")
    return json.dumps({"success": True, "message": f"Zapomenuto: {key}"}, ensure_ascii=False)
