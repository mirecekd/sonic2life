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
                "The agent can: search the web, search AWS documentation, "
                "perform calculations, get current date/time, call AWS services, "
                "make HTTP API requests, and reason through multi-step problems. "
                "Use this for ANY question that needs external information, "
                "calculations, or research."
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
        system_prompt=(
            "ABSOLUTE RULE #1 – LANGUAGE: You MUST respond in the SAME language as the user's question. "
            "If the question is in English, your ENTIRE response MUST be in English. "
            "If the question is in Czech, respond in Czech. "
            "If the question is in German, respond in German. "
            "NEVER switch language based on tool output data (e.g. Czech place names from geocoding). "
            "Always TRANSLATE tool results into the question's language. "
            "For example, if asked in English about a Czech location, respond in English: "
            "'You are in Vřesina, at Sportovní 7/7, 747 20 Vřesina, Czechia.' "
            "RULE #2 – BREVITY: Answer concisely in 2-3 sentences. "
            "Keep answers short and factual – they will be read aloud by a voice assistant. "
            "Use tools when needed to get accurate, up-to-date information."
        ),
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
