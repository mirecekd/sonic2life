"""
Nova 2 Sonic bidirectional streaming client.

Nova Sonic is fully bidirectional with built-in VAD:
- Audio streams continuously from client to model
- Model detects when user stops talking and auto-responds
- No need for client-side VAD or turn management
- One audio content block stays open the entire conversation
"""

import asyncio
import json
import base64
import uuid
import logging

from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.models import (
    InvokeModelWithBidirectionalStreamInputChunk,
    BidirectionalInputPayloadPart,
)
from aws_sdk_bedrock_runtime.config import Config
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

from app.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_SESSION_TOKEN,
    AWS_REGION,
    NOVA_SONIC_MODEL_ID,
    NOVA_SONIC_VOICE_ID,
    NOVA_SONIC_SYSTEM_PROMPT,
)
from app.tools.database import get_db

logger = logging.getLogger(__name__)

# Suppress noisy InvalidStateError from awscrt on stream close (cosmetic, not a real error)
logging.getLogger("awscrt").setLevel(logging.ERROR)

import warnings
warnings.filterwarnings("ignore", message=".*InvalidStateError.*CANCELLED.*")


def _load_user_profile() -> dict:
    """Load user profile settings from SQLite for prompt personalization."""
    try:
        conn = get_db()
        profile = {}
        for key in ("user_name", "user_full_name", "user_phone", "system_prompt"):
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            profile[key] = row["value"] if row and row["value"] else ""
        conn.close()
        return profile
    except Exception as e:
        logger.warning(f"⚠️ Could not load user profile: {e}")
        return {}


def _build_system_prompt(profile: dict) -> str:
    """Build the Nova Sonic system prompt with user personalization."""
    # Allow full override from admin settings
    if profile.get("system_prompt"):
        return profile["system_prompt"]

    # Allow env var override
    import os
    env_prompt = os.getenv("NOVA_SONIC_SYSTEM_PROMPT", "")
    if env_prompt:
        return env_prompt

    user_name = profile.get("user_name", "")
    user_full_name = profile.get("user_full_name", "")
    user_phone = profile.get("user_phone", "")

    # Build personalization block
    user_block = ""
    if user_name:
        user_block = (
            f"USER PROFILE: The user's name is {user_name}"
            f"{f' ({user_full_name})' if user_full_name else ''}. "
            f"{f'Phone: {user_phone}. ' if user_phone else ''}"
            f"Address them by name occasionally – in greetings, confirmations, "
            f"and important moments. Do NOT use their name in every sentence. "
        )

    return (
        # Language rule
        "CRITICAL LANGUAGE RULE: You MUST detect the language the user speaks and ALWAYS reply "
        "in THAT SAME language. If the user speaks English, reply in English. "
        "If Czech, reply in Czech. If German, reply in German. "
        "NEVER default to any particular language – always match the user's language "
        "from their very first word. "
        # Identity
        "You are Sonic2Life – a kind, patient voice assistant for seniors living independently. "
        # User profile
        + user_block +
        # Personality
        "Your traits: "
        "- Speak SLOWLY, CLEARLY and in an EASY-TO-UNDERSTAND way. "
        "- Use short sentences (max 2-3 at a time). "
        "- Be warm, empathetic and patient – like a good grandchild. "
        "- Never rush. If the user does not understand, rephrase. "
        "- Address the user politely (formal 'you'). "
        # Tools
        "- You have access to the askAgent tool – a powerful assistant that can: "
        "  search the web, find location and nearby places (has access to the user's GPS), "
        "  check weather and forecast, manage medications, remember user preferences, "
        "  manage calendar events and appointments, manage emergency contacts, "
        "  SEND SMS/TEXT MESSAGES to contacts, analyze photos, "
        "  calculate, check date/time, make HTTP requests and solve complex problems. "
        "- You MUST use askAgent for: "
        "  * Location questions ('where am I', 'what is nearby', 'find the nearest...') "
        "  * Weather questions ('what is the weather', 'will it rain tomorrow') "
        "  * Medication management ('what meds do I have', 'add a medication', 'I took my pill') "
        "  * Calendar/events ('schedule a doctor appointment', 'what is my schedule today', "
        "    'when is my next appointment', 'cancel the dentist', 'reschedule to Friday') "
        "  * Today's schedule ('what do I have today', 'what is planned for today') "
        "  * Memory ('remember that I like...', 'what do you know about me') "
        "  * Emergency contacts ('who are my contacts', 'add my son as contact') "
        "  * Sending messages ('text my son', 'send SMS to...', 'message my daughter') "
        "  * Any question that needs facts, real-time information or calculations "
        "- When you need to use askAgent, just call it directly without announcing it first. "
        "  The user interface will show a visual indicator that you are working. "
        "  After getting the result, speak the answer naturally in the SAME LANGUAGE the user is speaking. "
        "  IMPORTANT: Even if the tool returns data in a different language, YOU must always "
        "  respond in the language the user used in their last message. "
        # Health
        "- If the user says they feel unwell or have a health problem, "
        "  ask for details and offer to contact a close person or emergency services. "
    )


def _build_greeting_prompt(profile: dict) -> str:
    """Build personalized greeting prompt."""
    user_name = profile.get("user_name", "")
    if user_name:
        return (
            f"[SYSTEM: The user just connected. Greet them warmly BY NAME. "
            f"Say something like 'Hello {user_name}! How can I help you today?' "
            f"Keep it short and friendly. Remember to match "
            f"the user's language once they start speaking.]"
        )
    return (
        "[SYSTEM: The user just connected. Greet them warmly and ask "
        "how you can help today. Say something like 'Hello! How can I "
        "help you today?' Keep it short and friendly. Remember to match "
        "the user's language once they start speaking.]"
    )


class NovaSonicSession:
    """
    Manages one bidirectional streaming session with Nova 2 Sonic.

    Simple lifecycle (matching official AWS sample):
        1. start()       – open stream, init session, open audio content block
        2. send_audio()  – continuously feed PCM audio (model auto-detects speech/silence)
        3. close()       – tear down everything
    """

    def __init__(self, tool_specs=None, tool_handler=None, voice_id=None):
        self.tool_specs = tool_specs or []
        self.tool_handler = tool_handler
        self.voice_id = voice_id or NOVA_SONIC_VOICE_ID

        # Load user profile and build personalized prompts
        self._user_profile = _load_user_profile()
        self._system_prompt = _build_system_prompt(self._user_profile)
        self._greeting_prompt = _build_greeting_prompt(self._user_profile)
        logger.info(f"👤 User profile loaded: {self._user_profile.get('user_name', '(not set)')}")

        self._stream = None
        self._client = None
        self._is_active = False

        self._prompt_name = str(uuid.uuid4())
        self._audio_content_name = str(uuid.uuid4())

        self.output_queue: asyncio.Queue = asyncio.Queue()
        self._response_task = None
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._audio_sender_task = None

        # Track content generation stage to avoid duplicate text
        self._current_generation_stage = None  # "SPECULATIVE" or "FINAL"
        self._current_role = None

    @property
    def is_active(self):
        return self._is_active

    async def start(self):
        """Open the bidirectional stream and set up the session."""
        logger.info(f"🚀 Starting Nova 2 Sonic session (voice={self.voice_id})...")

        # Set credentials into env vars for EnvironmentCredentialsResolver
        import os
        if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
            raise RuntimeError(
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in .env or environment"
            )
        os.environ["AWS_ACCESS_KEY_ID"] = AWS_ACCESS_KEY_ID
        os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET_ACCESS_KEY
        # Only set session token if it has a real value; remove it otherwise
        # (empty string confuses AWS SDK into thinking it's a bad temp credential)
        if AWS_SESSION_TOKEN and AWS_SESSION_TOKEN.strip():
            os.environ["AWS_SESSION_TOKEN"] = AWS_SESSION_TOKEN
            logger.info("✅ AWS credentials loaded (with session token)")
        else:
            os.environ.pop("AWS_SESSION_TOKEN", None)
            logger.info("✅ AWS credentials loaded (permanent keys, no session token)")

        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{AWS_REGION}.amazonaws.com",
            region=AWS_REGION,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        self._client = BedrockRuntimeClient(config=config)

        self._stream = await self._client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=NOVA_SONIC_MODEL_ID)
        )
        self._is_active = True

        # Start response listener
        self._response_task = asyncio.create_task(self._process_responses())
        # Start audio sender
        self._audio_sender_task = asyncio.create_task(self._process_audio_queue())

        # Session start
        await self._send_event({
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
                    }
                }
            }
        })

        # Prompt start
        prompt_start = {
            "event": {
                "promptStart": {
                    "promptName": self._prompt_name,
                    "textOutputConfiguration": {"mediaType": "text/plain"},
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 24000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "voiceId": self.voice_id,
                        "encoding": "base64",
                        "audioType": "SPEECH",
                    },
                    "toolUseOutputConfiguration": {"mediaType": "application/json"},
                }
            }
        }
        if self.tool_specs:
            prompt_start["event"]["promptStart"]["toolConfiguration"] = {"tools": self.tool_specs}
        else:
            prompt_start["event"]["promptStart"]["toolConfiguration"] = {"tools": []}
        await self._send_event(prompt_start)

        # System prompt
        system_content = str(uuid.uuid4())
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self._prompt_name,
                    "contentName": system_content,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "SYSTEM",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        })
        await self._send_event({
            "event": {
                "textInput": {
                    "promptName": self._prompt_name,
                    "contentName": system_content,
                    "content": self._system_prompt,
                }
            }
        })
        await self._send_event({
            "event": {
                "contentEnd": {
                    "promptName": self._prompt_name,
                    "contentName": system_content,
                }
            }
        })

        # Send greeting text prompt BEFORE opening audio block
        # (must be sent first so model speaks before waiting for audio)
        greeting_content = str(uuid.uuid4())
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self._prompt_name,
                    "contentName": greeting_content,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "USER",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        })
        await self._send_event({
            "event": {
                "textInput": {
                    "promptName": self._prompt_name,
                    "contentName": greeting_content,
                    "content": self._greeting_prompt,
                }
            }
        })
        await self._send_event({
            "event": {
                "contentEnd": {
                    "promptName": self._prompt_name,
                    "contentName": greeting_content,
                }
            }
        })
        logger.info("👋 Greeting prompt sent – model will speak first")

        # Open audio content block – stays open the ENTIRE conversation
        # (opened AFTER greeting so they don't conflict)
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self._prompt_name,
                    "contentName": self._audio_content_name,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 16000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64",
                    },
                }
            }
        })

        logger.info("✅ Session ready – streaming audio continuously")

    async def send_greeting_prompt(self):
        """Send a hidden text prompt to make the model greet the user first."""
        if not self._is_active:
            return
        greeting_content = str(uuid.uuid4())
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self._prompt_name,
                    "contentName": greeting_content,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "USER",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        })
        await self._send_event({
            "event": {
                "textInput": {
                    "promptName": self._prompt_name,
                    "contentName": greeting_content,
                    "content": (
                        "[SYSTEM: The user just connected. Greet them warmly and ask "
                        "how you can help today. Say something like 'Hello! How can I "
                        "help you today?' Keep it short and friendly. Remember to match "
                        "the user's language once they start speaking.]"
                    ),
                }
            }
        })
        await self._send_event({
            "event": {
                "contentEnd": {
                    "promptName": self._prompt_name,
                    "contentName": greeting_content,
                }
            }
        })
        logger.info("👋 Greeting prompt sent – model will speak first")

    async def send_photo_context(self, description: str):
        """Inject photo analysis result into the conversation.

        The model will automatically speak the description to the user.
        """
        if not self._is_active:
            logger.warning("📸 Cannot inject photo context – session not active")
            return

        photo_content = str(uuid.uuid4())
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self._prompt_name,
                    "contentName": photo_content,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "USER",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        })
        await self._send_event({
            "event": {
                "textInput": {
                    "promptName": self._prompt_name,
                    "contentName": photo_content,
                    "content": (
                        f"[SYSTEM: The user just took a photo with their camera. "
                        f"Here is what the photo shows:\n\n{description}\n\n"
                        f"Tell the user what you see in the photo in a friendly, clear way. "
                        f"Keep it concise (2-3 sentences). If you see text, read it out. "
                        f"If you see medication, identify it. Ask if they want to know more.]"
                    ),
                }
            }
        })
        await self._send_event({
            "event": {
                "contentEnd": {
                    "promptName": self._prompt_name,
                    "contentName": photo_content,
                }
            }
        })
        logger.info("📸 Photo context injected – model will describe the photo")

    async def send_audio(self, pcm_bytes: bytes):
        """Queue a PCM audio chunk for sending."""
        if not self._is_active:
            return
        b64 = base64.b64encode(pcm_bytes).decode("utf-8")
        await self._audio_queue.put(b64)

    async def close(self):
        """Tear down the session. Safe to call multiple times."""
        was_active = self._is_active
        self._is_active = False

        if was_active:
            logger.info("🔚 Closing session...")
            # Close audio content, prompt, session
            try:
                await self._send_event({"event": {"contentEnd": {"promptName": self._prompt_name, "contentName": self._audio_content_name}}})
                await self._send_event({"event": {"promptEnd": {"promptName": self._prompt_name}}})
                await self._send_event({"event": {"sessionEnd": {}}})
            except Exception:
                pass

        if self._stream:
            try:
                await self._stream.input_stream.close()
            except Exception:
                pass
            self._stream = None

        for task in [self._response_task, self._audio_sender_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        self._response_task = None
        self._audio_sender_task = None

        if was_active:
            logger.info("✅ Session closed")

    # ── Internal ──────────────────────────────────────────────────

    async def _send_event(self, event_data: dict):
        if not self._stream or not self._is_active:
            return
        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=json.dumps(event_data).encode("utf-8"))
        )
        await self._stream.input_stream.send(chunk)

    async def _process_audio_queue(self):
        while self._is_active:
            try:
                b64 = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                if self._is_active:
                    await self._send_event({
                        "event": {
                            "audioInput": {
                                "promptName": self._prompt_name,
                                "contentName": self._audio_content_name,
                                "content": b64,
                            }
                        }
                    })
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Audio send error: {e}")

    async def _process_responses(self):
        logger.info("🎧 Listening for responses...")
        _resp_count = 0

        while self._is_active:
            try:
                output = await asyncio.wait_for(self._stream.await_output(), timeout=60.0)
                result = await output[1].receive()

                _resp_count += 1
                if not (result.value and result.value.bytes_):
                    logger.debug(f"📥 Empty response #{_resp_count} (value={result.value})")
                    continue

                logger.info(f"📥 Response #{_resp_count}: {len(result.value.bytes_)} bytes")

                data = json.loads(result.value.bytes_.decode("utf-8"))
                if "event" not in data:
                    continue

                event_name = list(data["event"].keys())[0]

                # Audio output → forward
                if event_name == "audioOutput":
                    audio_b64 = data["event"]["audioOutput"].get("content", "")
                    if audio_b64:
                        pcm = base64.b64decode(audio_b64)
                        await self.output_queue.put({"type": "audio", "data": pcm})

                # Text output – only show SPECULATIVE text (FINAL is a duplicate)
                elif event_name == "textOutput":
                    text = data["event"]["textOutput"].get("content", "")
                    role = data["event"]["textOutput"].get("role", "ASSISTANT")
                    if text:
                        # Check for barge-in signal
                        if '{ "interrupted" : true }' in text:
                            logger.info("⚡ Barge-in detected")
                            await self.output_queue.put({"type": "barge_in"})
                        elif role == "USER":
                            # User transcripts from FINAL stage only (more accurate)
                            if self._current_generation_stage == "FINAL":
                                await self.output_queue.put({"type": "transcript_user", "text": text})
                        elif role == "ASSISTANT":
                            # Assistant text from SPECULATIVE only (FINAL is duplicate)
                            if self._current_generation_stage == "SPECULATIVE":
                                await self.output_queue.put({"type": "transcript_ai", "text": text})

                # Content start – track generation stage
                elif event_name == "contentStart":
                    cs = data["event"]["contentStart"]
                    content_type = cs.get("type", "")
                    self._current_role = cs.get("role", "")

                    # Parse generation stage (SPECULATIVE = streaming preview, FINAL = confirmed)
                    additional = cs.get("additionalModelFields", "")
                    if additional:
                        try:
                            fields = json.loads(additional) if isinstance(additional, str) else additional
                            self._current_generation_stage = fields.get("generationStage", "FINAL")
                        except (json.JSONDecodeError, AttributeError):
                            self._current_generation_stage = "FINAL"
                    else:
                        self._current_generation_stage = "FINAL"

                    if content_type == "AUDIO" and self._current_role == "ASSISTANT":
                        await self.output_queue.put({"type": "speaking"})
                    elif content_type == "TEXT" and self._current_role == "ASSISTANT":
                        # Only notify "thinking" for SPECULATIVE (avoid double)
                        if self._current_generation_stage == "SPECULATIVE":
                            await self.output_queue.put({"type": "thinking"})

                # Tool use
                elif event_name == "toolUse":
                    tool_name = data["event"]["toolUse"].get("toolName", "")
                    tool_use_id = data["event"]["toolUse"].get("toolUseId", "")
                    tool_content = data["event"]["toolUse"].get("content", "")
                    await self.output_queue.put({"type": "tool_use", "tool": tool_name})
                    self._pending_tool = {"name": tool_name, "id": tool_use_id, "content": tool_content}

                # Content end
                elif event_name == "contentEnd":
                    ct = data["event"]["contentEnd"].get("type", "")
                    if ct == "TOOL" and hasattr(self, "_pending_tool") and self._pending_tool:
                        await self._handle_tool_result(data["event"]["contentEnd"].get("promptName", self._prompt_name))

                # Usage events - ignore
                elif event_name == "usageEvent":
                    pass

                # Completion events - ignore (model manages turns internally)
                elif event_name in ("completionStart", "completionEnd"):
                    logger.info(f"📥 {event_name}")

                else:
                    logger.info(f"📥 Unknown event: {event_name}")

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except StopAsyncIteration:
                logger.info("Stream ended")
                break
            except Exception as e:
                logger.error(f"Response error: {e}")
                break

        self._is_active = False
        await self.output_queue.put({"type": "done"})

    async def _handle_tool_result(self, prompt_name):
        tool = self._pending_tool
        self._pending_tool = None
        if not tool:
            return

        logger.info(f"🔧 Executing tool: {tool['name']}")
        result = "no result"
        if self.tool_handler:
            try:
                result = await self.tool_handler(tool["name"], tool["content"])
            except Exception as e:
                result = {"error": str(e)}

        # Ensure result is a JSON string (Nova Sonic expects application/json)
        if isinstance(result, dict):
            result_json = json.dumps(result)
        elif isinstance(result, str):
            result_json = json.dumps({"result": result})
        else:
            result_json = json.dumps({"result": str(result)})

        tool_content_name = str(uuid.uuid4())
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": tool_content_name,
                    "interactive": False,
                    "type": "TOOL",
                    "role": "TOOL",
                    "toolResultInputConfiguration": {
                        "toolUseId": tool["id"],
                        "type": "TEXT",
                        "textInputConfiguration": {"mediaType": "text/plain"},
                    },
                }
            }
        })
        await self._send_event({
            "event": {"toolResult": {"promptName": prompt_name, "contentName": tool_content_name, "content": result_json}}
        })
        await self._send_event({
            "event": {"contentEnd": {"promptName": prompt_name, "contentName": tool_content_name}}
        })
        logger.info(f"✅ Tool result sent for: {tool['name']}")
