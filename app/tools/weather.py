"""Weather tool using OpenWeatherMap API. Optional – disabled if OWM_API_KEY not set."""

import json
import logging
import urllib.request
import urllib.error

from strands import tool
from app.config import OWM_API_KEY

logger = logging.getLogger(__name__)

OWM_BASE = "https://api.openweathermap.org/data/2.5/weather"


@tool
def get_weather(lat: float, lon: float) -> str:
    """Get current weather for given GPS coordinates.

    Args:
        lat: Latitude
        lon: Longitude

    Returns:
        Weather description with temperature, humidity, and recommendation.
    """
    if not OWM_API_KEY:
        return json.dumps({"error": "Weather service not configured (OWM_API_KEY not set)"})

    try:
        url = (
            f"{OWM_BASE}?lat={lat}&lon={lon}"
            f"&appid={OWM_API_KEY}&units=metric&lang=cs"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Sonic2Life/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        temp = data["main"]["temp"]
        feels_like = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        description = data["weather"][0]["description"]
        city = data.get("name", "neznámé místo")
        wind = data["wind"]["speed"]

        # Simple recommendations for seniors
        recommendations = []
        if temp < 5:
            recommendations.append("Je chladno, oblečte se teple")
        elif temp > 30:
            recommendations.append("Je velké horko, pijte hodně vody a zůstaňte ve stínu")
        if wind > 10:
            recommendations.append("Fouká silný vítr")
        if "rain" in description.lower() or "déšť" in description.lower():
            recommendations.append("Vezměte si deštník")

        result = {
            "location": city,
            "temperature": f"{temp}°C",
            "feels_like": f"{feels_like}°C",
            "description": description,
            "humidity": f"{humidity}%",
            "wind": f"{wind} m/s",
            "recommendations": "; ".join(recommendations) if recommendations else "Příjemné počasí na procházku",
        }

        logger.info(f"🌤️ Weather: {city} {temp}°C {description}")
        return json.dumps(result, ensure_ascii=False)

    except urllib.error.URLError as e:
        logger.error(f"Weather API error: {e}")
        return json.dumps({"error": f"Nepodařilo se zjistit počasí: {e}"})
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return json.dumps({"error": str(e)})
