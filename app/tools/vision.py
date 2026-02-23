"""Vision tool – analyze photos using Amazon Nova 2 Lite (multimodal).

Flow:
  1. User taps camera button → captures photo → sent via WebSocket
  2. Photo stored in websocket_handler._current_photo
  3. Agent calls analyze_photo(question) → Nova 2 Lite Converse API
  4. Returns text description spoken by Nova Sonic
"""

import base64
import json
import logging

import boto3
from strands import tool

from app.config import AWS_REGION

logger = logging.getLogger(__name__)

# Nova 2 Lite model for vision (multimodal)
VISION_MODEL_ID = "amazon.nova-lite-v1:0"


@tool
def analyze_photo(question: str = "What do you see in this image? Describe it clearly and concisely.") -> str:
    """Analyze the most recent photo taken by the user's camera.

    The user can take a photo using the camera button in the app.
    This tool sends the photo to Amazon Nova 2 Lite vision model for analysis.

    Use this when:
    - User asks "what is this?", "what do you see?", "can you read this?"
    - User takes a photo and wants to know what's in it
    - User wants to identify medication, read text, describe a scene
    - User asks about something they're looking at

    Args:
        question: What to ask about the photo. Default is a general description.
                  Examples: "What medication is this?", "Read the text in this image",
                  "What building is this?", "What are the opening hours?"

    Returns:
        Text description of what the vision model sees in the photo.
    """
    from app.websocket_handler import get_current_photo

    photo_data = get_current_photo()
    if not photo_data:
        return json.dumps({
            "error": "No photo available. Ask the user to take a photo first using the camera button."
        })

    try:
        # Decode base64 to bytes
        if "," in photo_data:
            # Strip data:image/jpeg;base64, prefix if present
            photo_data = photo_data.split(",", 1)[1]

        image_bytes = base64.b64decode(photo_data)
        logger.info(f"📸 Analyzing photo: {len(image_bytes)} bytes, question: {question[:80]}")

        # Call Nova 2 Lite via Converse API
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

        response = client.converse(
            modelId=VISION_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "image": {
                                "format": "jpeg",
                                "source": {
                                    "bytes": image_bytes
                                }
                            }
                        },
                        {
                            "text": question
                        }
                    ]
                }
            ],
            inferenceConfig={
                "maxTokens": 512,
                "temperature": 0.3,
            },
            system=[
                {
                    "text": (
                        "You are a helpful vision assistant for an elderly person. "
                        "Describe what you see clearly and concisely in 2-3 sentences. "
                        "Focus on the most important and useful information. "
                        "If you see text, read it out. If you see medication, identify it. "
                        "If you see a building or place, describe it. "
                        "Respond in the same language as the user's question."
                    )
                }
            ]
        )

        # Extract text from response
        output = response.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])

        result_text = ""
        for block in content:
            if "text" in block:
                result_text += block["text"]

        if not result_text:
            result_text = "I could not analyze the photo. Please try taking another one."

        logger.info(f"📸 Vision result: {result_text[:200]}")
        return json.dumps({"description": result_text}, ensure_ascii=False)

    except Exception as e:
        logger.error(f"📸 Vision error: {e}")
        return json.dumps({"error": f"Photo analysis failed: {str(e)}"})
