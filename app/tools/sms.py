"""SMS Tool – send emergency SMS messages via Amazon SNS."""

import json
import logging

import boto3
from botocore.exceptions import ClientError
from strands import tool

from app.config import AWS_REGION
from app.tools.database import get_db

logger = logging.getLogger(__name__)

# SNS client (lazy init)
_sns_client = None


def _get_sns_client():
    """Get or create the SNS client."""
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns", region_name=AWS_REGION)
    return _sns_client


def _log_sms(contact_id: int | None, contact_name: str, phone: str,
             message: str, sns_message_id: str | None,
             status: str, error_detail: str | None = None):
    """Log an SMS send attempt to the database."""
    try:
        db = get_db()
        db.execute(
            """INSERT INTO sms_log
               (contact_id, contact_name, phone, message, sns_message_id, status, error_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (contact_id, contact_name, phone, message, sns_message_id, status, error_detail),
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"Failed to log SMS: {e}")


@tool
def send_emergency_sms(contact_name: str, message: str) -> str:
    """Send an SMS message to an emergency contact via Amazon SNS.

    Looks up the contact by name in the emergency contacts database,
    then sends the SMS message to their phone number.

    Args:
        contact_name: Name of the emergency contact to send SMS to (e.g. 'Jana', 'Dr. Smith').
        message: The text message to send. Keep it short and clear.

    Returns:
        Confirmation with delivery details, or error if contact not found or SMS failed.
    """
    db = get_db()

    # Look up contact by name (case-insensitive, partial match)
    row = db.execute(
        """SELECT id, name, fullname, phone, relationship
           FROM emergency_contacts
           WHERE active = 1 AND LOWER(name) LIKE LOWER(?)
           ORDER BY name LIMIT 1""",
        (f"%{contact_name}%",),
    ).fetchone()

    if not row:
        # Try exact match
        row = db.execute(
            """SELECT id, name, fullname, phone, relationship
               FROM emergency_contacts
               WHERE active = 1 AND LOWER(name) = LOWER(?)
               LIMIT 1""",
            (contact_name,),
        ).fetchone()

    db.close()

    if not row:
        # List available contacts for the agent to suggest
        db2 = get_db()
        all_contacts = db2.execute(
            "SELECT name, relationship FROM emergency_contacts WHERE active = 1 ORDER BY name"
        ).fetchall()
        db2.close()

        if all_contacts:
            names = ", ".join(f"{c['name']} ({c['relationship']})" for c in all_contacts)
            return json.dumps({
                "success": False,
                "message": f"No emergency contact found matching '{contact_name}'. "
                           f"Available contacts: {names}",
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": "No emergency contacts saved yet. "
                           "Please add contacts first using add_emergency_contact.",
            }, ensure_ascii=False)

    contact_id = row["id"]
    name = row["name"]
    fullname = row["fullname"] or name
    phone = row["phone"]
    relationship = row["relationship"] or "contact"

    # Validate phone number (basic check)
    if not phone or len(phone) < 8:
        _log_sms(contact_id, name, phone or "?", message, None, "error", "Invalid phone number")
        return json.dumps({
            "success": False,
            "message": f"Contact '{name}' has an invalid phone number: '{phone}'. "
                       f"Please update it first.",
        }, ensure_ascii=False)

    # Send via Amazon SNS
    try:
        sns = _get_sns_client()
        response = sns.publish(
            PhoneNumber=phone,
            Message=message,
            MessageAttributes={
                "AWS.SNS.SMS.SenderID": {
                    "DataType": "String",
                    "StringValue": "Sonic2Life",
                },
                "AWS.SNS.SMS.SMSType": {
                    "DataType": "String",
                    "StringValue": "Transactional",
                },
            },
        )

        message_id = response.get("MessageId", "unknown")
        logger.info(f"📱 SMS sent to {name} ({phone}): MessageId={message_id}")

        _log_sms(contact_id, name, phone, message, message_id, "sent")

        return json.dumps({
            "success": True,
            "message": f"SMS sent to {fullname} ({relationship}) at {phone}.",
            "sns_message_id": message_id,
        }, ensure_ascii=False)

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        logger.error(f"📱 SNS error sending to {name} ({phone}): {error_code} - {error_msg}")

        _log_sms(contact_id, name, phone, message, None, "failed", f"{error_code}: {error_msg}")

        return json.dumps({
            "success": False,
            "message": f"Failed to send SMS to {name}: {error_msg}",
            "error_code": error_code,
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"📱 Unexpected error sending SMS to {name}: {e}")

        _log_sms(contact_id, name, phone, message, None, "error", str(e))

        return json.dumps({
            "success": False,
            "message": f"Unexpected error sending SMS to {name}: {str(e)}",
        }, ensure_ascii=False)
