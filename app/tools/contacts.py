"""Emergency Contacts Tool – manage emergency contacts for the user."""

import json
import logging

from strands import tool
from app.tools.database import get_db

logger = logging.getLogger(__name__)


@tool
def add_emergency_contact(
    name: str,
    phone: str,
    fullname: str = "",
    relationship: str = "",
) -> str:
    """Add a new emergency contact.

    Args:
        name: Short name for voice interaction (e.g. 'Jana', 'Dr. Smith')
        phone: Phone number including country code (e.g. '+420123456789')
        fullname: Full name (e.g. 'Jana Novakova'). Optional.
        relationship: Relationship to user (e.g. 'daughter', 'doctor', 'neighbor'). Optional.

    Returns:
        Confirmation message with contact details.
    """
    db = get_db()

    # Check for duplicate by name
    existing = db.execute(
        "SELECT id FROM emergency_contacts WHERE LOWER(name) = LOWER(?) AND active = 1",
        (name,),
    ).fetchone()

    if existing:
        db.close()
        return json.dumps({
            "success": False,
            "message": f"Contact '{name}' already exists. Use update or remove first.",
        }, ensure_ascii=False)

    db.execute(
        """INSERT INTO emergency_contacts (name, fullname, relationship, phone)
           VALUES (?, ?, ?, ?)""",
        (name, fullname, relationship, phone),
    )
    db.commit()
    db.close()

    logger.info(f"📞 Emergency contact added: {name} ({relationship}) {phone}")
    return json.dumps({
        "success": True,
        "message": f"Emergency contact added: {name} ({relationship}) — {phone}",
    }, ensure_ascii=False)


@tool
def get_emergency_contacts(name: str = "") -> str:
    """Get emergency contacts list or a specific contact by name.

    Args:
        name: Optional. If provided, search for a specific contact by name. If empty, returns all contacts.

    Returns:
        List of emergency contacts with their details.
    """
    db = get_db()

    if name:
        rows = db.execute(
            """SELECT id, name, fullname, relationship, phone
               FROM emergency_contacts
               WHERE active = 1 AND LOWER(name) LIKE LOWER(?)
               ORDER BY name""",
            (f"%{name}%",),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT id, name, fullname, relationship, phone
               FROM emergency_contacts
               WHERE active = 1
               ORDER BY name"""
        ).fetchall()

    db.close()

    contacts = [
        {
            "name": r["name"],
            "fullname": r["fullname"] or r["name"],
            "relationship": r["relationship"] or "not specified",
            "phone": r["phone"],
        }
        for r in rows
    ]

    if not contacts:
        msg = f"No emergency contact found matching '{name}'." if name else "No emergency contacts saved yet."
        return json.dumps({"contacts": [], "message": msg}, ensure_ascii=False)

    logger.info(f"📞 Retrieved {len(contacts)} emergency contact(s)")
    return json.dumps({"contacts": contacts}, ensure_ascii=False)


@tool
def remove_emergency_contact(name: str) -> str:
    """Remove an emergency contact by name.

    Args:
        name: Name of the contact to remove.

    Returns:
        Confirmation message.
    """
    db = get_db()
    result = db.execute(
        "UPDATE emergency_contacts SET active = 0 WHERE LOWER(name) = LOWER(?) AND active = 1",
        (name,),
    )
    db.commit()
    affected = result.rowcount
    db.close()

    if affected == 0:
        return json.dumps({
            "success": False,
            "message": f"No active emergency contact found with name '{name}'.",
        }, ensure_ascii=False)

    logger.info(f"📞 Emergency contact removed: {name}")
    return json.dumps({
        "success": True,
        "message": f"Emergency contact '{name}' has been removed.",
    }, ensure_ascii=False)


@tool
def update_emergency_contact(
    name: str,
    new_phone: str = "",
    new_fullname: str = "",
    new_relationship: str = "",
) -> str:
    """Update an existing emergency contact's details.

    Args:
        name: Current name of the contact to update.
        new_phone: New phone number. Leave empty to keep current.
        new_fullname: New full name. Leave empty to keep current.
        new_relationship: New relationship. Leave empty to keep current.

    Returns:
        Confirmation message with updated details.
    """
    db = get_db()

    existing = db.execute(
        "SELECT id, name, fullname, relationship, phone FROM emergency_contacts WHERE LOWER(name) = LOWER(?) AND active = 1",
        (name,),
    ).fetchone()

    if not existing:
        db.close()
        return json.dumps({
            "success": False,
            "message": f"No active emergency contact found with name '{name}'.",
        }, ensure_ascii=False)

    updates = []
    params = []
    if new_phone:
        updates.append("phone = ?")
        params.append(new_phone)
    if new_fullname:
        updates.append("fullname = ?")
        params.append(new_fullname)
    if new_relationship:
        updates.append("relationship = ?")
        params.append(new_relationship)

    if not updates:
        db.close()
        return json.dumps({
            "success": False,
            "message": "No changes specified. Provide new_phone, new_fullname, or new_relationship.",
        }, ensure_ascii=False)

    params.append(existing["id"])
    db.execute(
        f"UPDATE emergency_contacts SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    db.commit()
    db.close()

    logger.info(f"📞 Emergency contact updated: {name}")
    return json.dumps({
        "success": True,
        "message": f"Emergency contact '{name}' updated successfully.",
    }, ensure_ascii=False)
