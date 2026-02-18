"""
WebSocket handler – bridges browser with Nova 2 Sonic.

Protocol:
  Client → Server:
    - JSON {"type": "start", "engine": "nova", "voice_id": "..."} – begin conversation
    - JSON {"type": "end"}   – end conversation
    - Binary (ArrayBuffer)   – PCM 16-bit 16kHz mono audio (streams continuously)

  Server → Client:
    - JSON {"type": "transcript_user/ai", "text": "..."}
    - JSON {"type": "thinking"} / {"type": "speaking"}
    - JSON {"type": "barge_in"} – user interrupted the model
    - JSON {"type": "done"} – session ended
    - JSON {"type": "error", "text": "..."}
    - JSON {"type": "tool_use", "tool": "..."}
    - Binary – PCM 16-bit 16kHz audio for playback
"""

import asyncio
import json
import logging

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from app.nova_sonic import NovaSonicSession

logger = logging.getLogger(__name__)

OUTPUT_SAMPLE_RATE = 24000
CLIENT_SAMPLE_RATE = 16000


def resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    samples = np.frombuffer(pcm_24k, dtype=np.int16).astype(np.float32)
    ratio = CLIENT_SAMPLE_RATE / OUTPUT_SAMPLE_RATE
    out_len = int(len(samples) * ratio)
    indices = np.linspace(0, len(samples) - 1, out_len)
    resampled = np.interp(indices, np.arange(len(samples)), samples)
    return resampled.astype(np.int16).tobytes()


# Global GPS storage (per-connection, updated by frontend)
_current_gps = {"lat": None, "lon": None, "accuracy": None}


def get_current_gps():
    """Get the latest GPS coordinates from the frontend."""
    return _current_gps.copy()


async def handle_websocket(ws: WebSocket, tool_specs=None, tool_handler=None):
    await ws.accept()
    logger.info("🔌 WebSocket connected")

    session = None
    forwarder = None

    async def cleanup():
        nonlocal session, forwarder
        if forwarder and not forwarder.done():
            forwarder.cancel()
            try:
                await forwarder
            except (asyncio.CancelledError, Exception):
                pass
            forwarder = None
        if session:
            try:
                await session.close()
            except Exception as e:
                logger.warning(f"Close error: {e}")
            session = None

    try:
        while True:
            message = await ws.receive()

            # Binary audio → forward to active Nova Sonic session
            if "bytes" in message:
                if session and session.is_active:
                    audio_len = len(message["bytes"])
                    if not hasattr(handle_websocket, '_audio_count'):
                        handle_websocket._audio_count = 0
                    handle_websocket._audio_count += 1
                    if handle_websocket._audio_count % 50 == 1:
                        logger.info(f"🎤 Audio chunk #{handle_websocket._audio_count}: {audio_len} bytes")
                    await session.send_audio(message["bytes"])
                continue

            # JSON control
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "start":
                    await cleanup()
                    voice_id = data.get("voice_id")
                    logger.info(f"📨 Start requested: voice={voice_id}")

                    session = NovaSonicSession(
                        tool_specs=tool_specs,
                        tool_handler=tool_handler,
                        voice_id=voice_id,
                    )
                    logger.info(f"🔵 Using Nova 2 Sonic (voice={voice_id})")

                    try:
                        await session.start()
                        forwarder = asyncio.create_task(_forward(ws, session))
                        logger.info("✅ Conversation started (with auto-greeting)")
                    except Exception as e:
                        logger.error(f"Start failed: {e}")
                        await ws.send_json({"type": "error", "text": str(e)})
                        session = None

                elif msg_type == "gps":
                    _current_gps["lat"] = data.get("lat")
                    _current_gps["lon"] = data.get("lon")
                    _current_gps["accuracy"] = data.get("accuracy")
                    logger.info(f"📍 GPS updated: {_current_gps['lat']}, {_current_gps['lon']}")

                elif msg_type == "end":
                    await cleanup()
                    await ws.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info("🔌 Disconnected")
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        await cleanup()
        logger.info("🧹 Cleanup done")


async def _forward(ws: WebSocket, session):
    try:
        while session.is_active or not session.output_queue.empty():
            try:
                msg = await asyncio.wait_for(session.output_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg["type"] == "audio":
                pcm = resample_24k_to_16k(msg["data"])
                await ws.send_bytes(pcm)
            elif msg["type"] == "done":
                await ws.send_json({"type": "done"})
                break
            elif msg["type"] == "barge_in":
                await ws.send_json({"type": "barge_in"})
            else:
                await ws.send_json({k: v for k, v in msg.items() if k != "data"})

    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Forward error: {e}")
