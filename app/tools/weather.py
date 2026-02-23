"""Weather tool using Open-Meteo API (free, no API key needed).

Provides current weather and hourly forecast up to 7 days.
Works purely with GPS coordinates.
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone

from strands import tool

logger = logging.getLogger(__name__)

# WMO Weather interpretation codes → human-readable descriptions
WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _wmo_description(code: int) -> str:
    return WMO_CODES.get(code, f"Unknown ({code})")


def _senior_recommendations(temp: float, wind: float, weather_code: int) -> str:
    """Generate simple recommendations for seniors."""
    tips = []
    if temp < 0:
        tips.append("Very cold – dress warmly with layers, hat and gloves")
    elif temp < 5:
        tips.append("Cold – wear a warm coat")
    elif temp > 30:
        tips.append("Very hot – drink plenty of water, stay in shade")
    elif temp > 25:
        tips.append("Warm – stay hydrated")

    if wind > 10:
        tips.append("Strong wind – be careful outside")

    if weather_code in (61, 63, 65, 66, 67, 80, 81, 82):
        tips.append("Rain expected – take an umbrella")
    elif weather_code in (71, 73, 75, 85, 86):
        tips.append("Snow expected – be careful on slippery surfaces")
    elif weather_code in (95, 96, 99):
        tips.append("Thunderstorm – better stay indoors")
    elif weather_code in (45, 48):
        tips.append("Foggy – reduced visibility, be careful")

    return "; ".join(tips) if tips else "Nice weather for a walk"


@tool
def get_weather(lat: float, lon: float) -> str:
    """Get current weather and forecast for given GPS coordinates.

    Returns current conditions plus hourly forecast for the next 24 hours
    and a daily summary for the next 3 days. No API key needed.

    Use this when the user asks about:
    - Current weather ("What's the weather like?")
    - Forecast ("Will it rain tomorrow?", "How will the weather be tomorrow morning?")
    - Temperature ("How cold is it?", "What's the temperature?")
    - Whether to take an umbrella, wear a coat, etc.

    Args:
        lat: Latitude (from GPS)
        lon: Longitude (from GPS)

    Returns:
        JSON with current weather, hourly forecast (24h), and daily forecast (3 days).
    """
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            f"weather_code,wind_speed_10m,precipitation"
            f"&hourly=temperature_2m,weather_code,precipitation_probability,"
            f"wind_speed_10m,apparent_temperature"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            f"precipitation_sum,precipitation_probability_max,sunrise,sunset"
            f"&timezone=auto"
            f"&forecast_days=3"
        )

        req = urllib.request.Request(url, headers={"User-Agent": "Sonic2Life/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        # ── Current weather ──
        current = data.get("current", {})
        current_temp = current.get("temperature_2m", 0)
        current_feels = current.get("apparent_temperature", 0)
        current_humidity = current.get("relative_humidity_2m", 0)
        current_code = current.get("weather_code", 0)
        current_wind = current.get("wind_speed_10m", 0)
        current_precip = current.get("precipitation", 0)

        tz_name = data.get("timezone", "UTC")

        result = {
            "timezone": tz_name,
            "current": {
                "temperature": f"{current_temp}°C",
                "feels_like": f"{current_feels}°C",
                "humidity": f"{current_humidity}%",
                "description": _wmo_description(current_code),
                "wind": f"{current_wind} km/h",
                "precipitation": f"{current_precip} mm",
                "recommendation": _senior_recommendations(current_temp, current_wind, current_code),
            },
        }

        # ── Hourly forecast (next 24h, every 3 hours) ──
        hourly = data.get("hourly", {})
        hourly_times = hourly.get("time", [])
        hourly_temps = hourly.get("temperature_2m", [])
        hourly_codes = hourly.get("weather_code", [])
        hourly_precip_prob = hourly.get("precipitation_probability", [])
        hourly_wind = hourly.get("wind_speed_10m", [])
        hourly_feels = hourly.get("apparent_temperature", [])

        # Find current hour index
        now_str = current.get("time", "")[:13]  # "2024-01-15T14"
        start_idx = 0
        for i, t in enumerate(hourly_times):
            if t.startswith(now_str):
                start_idx = i
                break

        forecast_hours = []
        for i in range(start_idx, min(start_idx + 25, len(hourly_times)), 3):
            forecast_hours.append({
                "time": hourly_times[i] if i < len(hourly_times) else "",
                "temp": f"{hourly_temps[i]}°C" if i < len(hourly_temps) else "",
                "feels_like": f"{hourly_feels[i]}°C" if i < len(hourly_feels) else "",
                "description": _wmo_description(hourly_codes[i]) if i < len(hourly_codes) else "",
                "rain_chance": f"{hourly_precip_prob[i]}%" if i < len(hourly_precip_prob) else "",
                "wind": f"{hourly_wind[i]} km/h" if i < len(hourly_wind) else "",
            })

        result["hourly_forecast"] = forecast_hours

        # ── Daily forecast (3 days) ──
        daily = data.get("daily", {})
        daily_times = daily.get("time", [])
        daily_codes = daily.get("weather_code", [])
        daily_max = daily.get("temperature_2m_max", [])
        daily_min = daily.get("temperature_2m_min", [])
        daily_precip = daily.get("precipitation_sum", [])
        daily_precip_prob = daily.get("precipitation_probability_max", [])
        daily_sunrise = daily.get("sunrise", [])
        daily_sunset = daily.get("sunset", [])

        forecast_days = []
        for i in range(len(daily_times)):
            forecast_days.append({
                "date": daily_times[i],
                "description": _wmo_description(daily_codes[i]) if i < len(daily_codes) else "",
                "temp_max": f"{daily_max[i]}°C" if i < len(daily_max) else "",
                "temp_min": f"{daily_min[i]}°C" if i < len(daily_min) else "",
                "precipitation": f"{daily_precip[i]} mm" if i < len(daily_precip) else "",
                "rain_chance": f"{daily_precip_prob[i]}%" if i < len(daily_precip_prob) else "",
                "sunrise": daily_sunrise[i][-5:] if i < len(daily_sunrise) else "",
                "sunset": daily_sunset[i][-5:] if i < len(daily_sunset) else "",
            })

        result["daily_forecast"] = forecast_days

        logger.info(f"🌤️ Weather: {current_temp}°C, {_wmo_description(current_code)}")
        return json.dumps(result, ensure_ascii=False)

    except urllib.error.URLError as e:
        logger.error(f"Weather API error: {e}")
        return json.dumps({"error": f"Could not fetch weather: {e}"})
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return json.dumps({"error": str(e)})
