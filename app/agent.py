"""
Strands Agent sub-agent for Nova 2 Sonic tool calling.

Architecture:
  Nova Sonic (voice) → calls "askAgent" tool → Strands Agent (text, multi-tool)
  
  Nova Sonic handles voice I/O, Strands Agent handles reasoning + tool orchestration.
  This gives us:
    - Multi-tool chaining (agent can call multiple tools per request)
    - Strong reasoning (Nova 2 Lite text model)
    - Easy tool addition (just add to AGENT_TOOLS list)
"""

import asyncio
import json
import logging
from functools import partial

from app.config import AWS_REGION

logger = logging.getLogger(__name__)

# ── Tool spec for Nova 2 Sonic ────────────────────────────────────
# Nova Sonic sees ONE tool: askAgent. The Strands Agent handles everything else.

TOOL_SPECS = [
    {
        "toolSpec": {
            "name": "askAgent",
            "description": (
                "Ask a specialized AI research agent to help with complex tasks. "
                "The agent can: search the web (DuckDuckGo), search AWS documentation, "
                "perform calculations, get current date/time, call AWS services, "
                "make HTTP API requests, manage medications and schedule, manage emergency contacts, send SMS to emergency contacts, "
                "manage calendar events and appointments (add, cancel, reschedule, "
                "get today's schedule, get upcoming events), remember user preferences, "
                "analyze photos from the user's camera (identify objects, read text, "
                "describe scenes, identify medications), "
                "and reason through multi-step problems. "
                "Use this for ANY question that needs external information, "
                "calculations, calendar/schedule management, photo analysis, or research."
            ),
            "inputSchema": {
                "json": json.dumps({
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task or question for the agent to research and answer",
                        }
                    },
                    "required": ["task"],
                })
            },
        }
    },
]


# ── Strands Agent singleton ───────────────────────────────────────

_strands_agent = None
_agent_lock = asyncio.Lock()


def _load_user_profile_for_agent() -> dict:
    """Load user profile settings from SQLite for agent prompt personalization."""
    try:
        from app.tools.database import get_db
        conn = get_db()
        profile = {}
        for key in ("user_name", "user_full_name", "user_phone"):
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            profile[key] = row["value"] if row and row["value"] else ""
        conn.close()
        return profile
    except Exception as e:
        logger.warning(f"⚠️ Could not load user profile for agent: {e}")
        return {}


def _build_agent_system_prompt() -> str:
    """Build comprehensive system prompt for the Strands Agent."""
    profile = _load_user_profile_for_agent()
    user_name = profile.get("user_name", "")
    user_full_name = profile.get("user_full_name", "")
    user_phone = profile.get("user_phone", "")

    user_block = ""
    if user_name:
        user_block = (
            f"\nUSER PROFILE:\n"
            f"- Name: {user_name}\n"
            f"{f'- Full name: {user_full_name}' + chr(10) if user_full_name else ''}"
            f"{f'- Phone: {user_phone}' + chr(10) if user_phone else ''}"
            f"Use their name when confirming important actions.\n"
        )

    return (
        "You are Sonic2Life's reasoning engine – a backend agent that processes requests "
        "from a voice assistant for seniors living independently. "
        "Your responses will be READ ALOUD by a voice model, so keep them SHORT (2-3 sentences max).\n"
        + user_block +
        "\nRULES:\n"
        "1. LANGUAGE: ALWAYS respond in the SAME language as the user's question. "
        "Translate tool outputs into the user's language.\n"
        "2. BREVITY: Max 2-3 sentences. No markdown, no bullet points – plain spoken text.\n"
        "3. ACCURACY: Use tools for real data. Never guess dates, times, or facts.\n"
        "4. CONFIRMATION: When adding events or medications, confirm the key details back to the user.\n"
        "\nAVAILABLE TOOLS:\n"
        "Medication: get_medication_schedule, add_medication(name, schedule_time, dosage, days, notes), "
        "confirm_medication_taken(medication_name), remove_medication(medication_name), "
        "get_medication_history(medication_name, days)\n"
        "Calendar: get_upcoming_events(days), get_todays_schedule, "
        "add_event(title, event_time, description, reminder_minutes, morning_brief), "
        "cancel_event(event_title), update_event_time(event_title, new_time)\n"
        "Memory: remember(key, value, category), recall(key, category), forget(key)\n"
        "Emergency Contacts: add_emergency_contact(name, phone, fullname, relationship), "
        "get_emergency_contacts(name), remove_emergency_contact(name), "
        "update_emergency_contact(name, new_phone, new_fullname, new_relationship)\n"
        "SMS: send_emergency_sms(contact_name, message) – send SMS/text message to an emergency contact via Amazon SNS. "
        "Use this when the user says 'text my son', 'send a message to...', 'SMS to...', etc. "
        "WORKFLOW: First call get_emergency_contacts('') to get ALL contacts. Find the right one by "
        "matching the relationship field (e.g. 'son', 'daughter', 'doctor') or name. "
        "Then call send_emergency_sms with the contact's name and the message. "
        "After sending, mention other saved contacts and ask if the user wants to notify them too.\n"
        "Weather: get_weather(lat, lon) – current weather + hourly/daily forecast (Open-Meteo, no API key). Use GPS from [CONTEXT]. Can answer about today, tomorrow, next days.\n"
        "Web search: web_search(query, max_results) – search the internet for current info\n"
        "Vision: analyze_photo(question) – analyze the user's camera photo using Nova 2 Lite vision\n"
        "Location: reverse_geocode, search_places – via MCP\n"
        "Other: calculator, current_time, http_request, think\n"
    )


def _create_strands_agent():
    """Create and configure the Strands Agent with all available tools."""
    from strands import Agent
    from strands.models.bedrock import BedrockModel
    from strands.tools.mcp import MCPClient
    from mcp import stdio_client, StdioServerParameters

    # Import available Strands tools
    tools = []

    try:
        from strands_tools import calculator
        tools.append(calculator)
        logger.info("  ✅ calculator")
    except ImportError:
        logger.warning("  ⚠️ calculator not available")

    try:
        from strands_tools import current_time
        tools.append(current_time)
        logger.info("  ✅ current_time")
    except ImportError:
        logger.warning("  ⚠️ current_time not available")

    try:
        from strands_tools import http_request
        tools.append(http_request)
        logger.info("  ✅ http_request")
    except ImportError:
        logger.warning("  ⚠️ http_request not available")

    #try:
    #    from strands_tools import use_aws
    #    tools.append(use_aws)
    #    logger.info("  ✅ use_aws")
    #except ImportError:
    #    logger.warning("  ⚠️ use_aws not available")

    try:
        from strands_tools import think
        tools.append(think)
        logger.info("  ✅ think")
    except ImportError:
        logger.warning("  ⚠️ think not available")

    # Sonic2Life custom tools
    try:
        from app.tools.web_search import web_search
        tools.append(web_search)
        logger.info("  ✅ web_search")
    except Exception as e:
        logger.warning(f"  ⚠️ web_search not available: {e}")

    try:
        from app.tools.vision import analyze_photo
        tools.append(analyze_photo)
        logger.info("  ✅ analyze_photo (vision)")
    except Exception as e:
        logger.warning(f"  ⚠️ analyze_photo not available: {e}")

    try:
        from app.tools.weather import get_weather
        tools.append(get_weather)
        logger.info("  ✅ get_weather")
    except Exception as e:
        logger.warning(f"  ⚠️ get_weather not available: {e}")

    try:
        from app.tools.medication import (
            get_medication_schedule,
            add_medication,
            confirm_medication_taken,
            remove_medication,
            get_medication_history,
        )
        tools.extend([
            get_medication_schedule,
            add_medication,
            confirm_medication_taken,
            remove_medication,
            get_medication_history,
        ])
        logger.info("  ✅ medication tools (5)")
    except Exception as e:
        logger.warning(f"  ⚠️ medication tools not available: {e}")

    try:
        from app.tools.memory import remember, recall, forget
        tools.extend([remember, recall, forget])
        logger.info("  ✅ memory tools (3)")
    except Exception as e:
        logger.warning(f"  ⚠️ memory tools not available: {e}")

    try:
        from app.tools.contacts import (
            add_emergency_contact,
            get_emergency_contacts,
            remove_emergency_contact,
            update_emergency_contact,
        )
        tools.extend([
            add_emergency_contact,
            get_emergency_contacts,
            remove_emergency_contact,
            update_emergency_contact,
        ])
        logger.info("  ✅ emergency contacts tools (4)")
    except Exception as e:
        logger.warning(f"  ⚠️ emergency contacts tools not available: {e}")

    try:
        from app.tools.sms import send_emergency_sms
        tools.append(send_emergency_sms)
        logger.info("  ✅ send_emergency_sms (SNS)")
    except Exception as e:
        logger.warning(f"  ⚠️ send_emergency_sms not available: {e}")

    try:
        from app.tools.events import (
            get_upcoming_events,
            get_todays_schedule,
            add_event,
            cancel_event,
            update_event_time,
        )
        tools.extend([
            get_upcoming_events,
            get_todays_schedule,
            add_event,
            cancel_event,
            update_event_time,
        ])
        logger.info("  ✅ event/calendar tools (5)")
    except Exception as e:
        logger.warning(f"  ⚠️ event/calendar tools not available: {e}")

    # Add MCP tools (AWS documentation search)
    mcp_client = None
    try:
        mcp_client = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command="uvx",
                    args=["fastmcp", "run", "https://knowledge-mcp.global.api.aws"],
                )
            )
        )
        mcp_client.start()
        mcp_tools = mcp_client.list_tools_sync()
        tools.extend(mcp_tools)
        tool_names = [t.name if hasattr(t, 'name') else str(t) for t in mcp_tools]
        logger.info(f"  ✅ MCP AWS Knowledge tools: {tool_names}")
    except Exception as e:
        logger.warning(f"  ⚠️ MCP AWS Knowledge tools failed: {e}")

    # Add Amazon Location MCP tools (reverse geocoding, places search, routing)
    location_mcp_client = None
    try:
        import os as _os
        location_env = _os.environ.copy()
        location_env["AWS_REGION"] = AWS_REGION
        location_mcp_client = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command="uvx",
                    args=["awslabs.aws-location-mcp-server@latest"],
                    env=location_env,
                )
            )
        )
        location_mcp_client.start()
        location_tools = location_mcp_client.list_tools_sync()
        tools.extend(location_tools)
        tool_names = [t.name if hasattr(t, 'name') else str(t) for t in location_tools]
        logger.info(f"  ✅ MCP Location tools: {tool_names}")
    except Exception as e:
        logger.warning(f"  ⚠️ MCP Location tools failed: {e}")

    # Create the text model for reasoning
    model = BedrockModel(
        model_id="global.amazon.nova-2-lite-v1:0",
        region_name=AWS_REGION,
    )

    agent = Agent(
        model=model,
        tools=tools,
        system_prompt=_build_agent_system_prompt(),
    )

    logger.info("🤖 Strands Agent created with %d tools", len(tools))

    # Store MCP client reference for cleanup
    agent._mcp_client = mcp_client

    return agent


async def get_strands_agent():
    """Get or create the singleton Strands Agent (thread-safe)."""
    global _strands_agent
    async with _agent_lock:
        if _strands_agent is None:
            logger.info("🚀 Creating Strands Agent with tools:")
            loop = asyncio.get_event_loop()
            _strands_agent = await loop.run_in_executor(None, _create_strands_agent)
    return _strands_agent


# ── Tool handler for Nova 2 Sonic ─────────────────────────────────

async def handle_tool_call(tool_name: str, tool_content: str) -> str:
    """
    Route a Nova 2 Sonic tool call to the Strands Agent.

    This is passed as `tool_handler` to NovaSonicSession.
    """
    logger.info(f"🔧 Tool call: {tool_name}")
    logger.debug(f"🔧 Tool content: {tool_content}")

    tool_name_lower = tool_name.lower()

    if tool_name_lower == "askagent":
        try:
            args = json.loads(tool_content) if isinstance(tool_content, str) else tool_content
        except json.JSONDecodeError:
            args = {"task": str(tool_content)}

        task = args.get("task", str(tool_content))

        # Inject GPS context if available
        from app.websocket_handler import get_current_gps
        gps = get_current_gps()
        if gps.get("lat") is not None and gps.get("lon") is not None:
            task = (
                f"{task}\n\n"
                f"[CONTEXT: User's current GPS location: lat={gps['lat']}, lon={gps['lon']}, "
                f"accuracy={gps.get('accuracy', 'unknown')}m. "
                f"Use this location for any location-related queries.]\n"
                f"[LANGUAGE: You MUST respond in the SAME language as the question above. "
                f"Do NOT switch to Czech just because tool data is in Czech.]"
            )
        else:
            task = (
                f"{task}\n\n"
                f"[LANGUAGE: You MUST respond in the SAME language as the question above. "
                f"Do NOT switch to Czech just because tool data is in Czech.]"
            )

        logger.info(f"🤖 Delegating to Strands Agent: {task[:100]}...")

        agent = await get_strands_agent()

        # Run Strands Agent in executor (it's synchronous)
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, partial(agent, task))

            # Extract text from agent response
            if hasattr(result, 'message'):
                response_text = str(result.message)
            elif hasattr(result, 'text'):
                response_text = str(result.text)
            else:
                response_text = str(result)

            # Clean up response (remove any tool artifacts)
            if len(response_text) > 2000:
                response_text = response_text[:2000] + "..."

            logger.info(f"🤖 Agent response: {response_text[:200]}...")
            return {"answer": response_text}

        except Exception as e:
            logger.error(f"🤖 Agent error: {e}")
            return {"error": str(e)}

    # Unknown tool
    logger.warning(f"⚠️ Unknown tool: {tool_name}")
    return {"error": f"Unknown tool: {tool_name}"}


def get_tool_specs():
    """Return the tool specs for Nova 2 Sonic session configuration."""
    return TOOL_SPECS


# Keep backward compatibility for MCP pre-init
async def get_mcp_runner():
    """Pre-initialize by creating the Strands Agent (which starts MCP internally)."""
    return await get_strands_agent()
