"""Weather via Open-Meteo (free, no API key): geocode the place, then current conditions + today's range."""

from __future__ import annotations

import httpx

from .registry import P, ToolContext, tool

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "icy fog", 51: "light drizzle", 53: "drizzle",
       55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "heavy showers", 85: "snow showers",
       86: "snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


@tool("get_weather", group="web", description="Current weather and today's forecast for a city or place (temperature, conditions, wind, rain chance).",
      parameters=P(place=("string", "City or place name, e.g. 'Seattle' or 'Paris, France'", True),
                   units=("string", "fahrenheit or celsius (default fahrenheit)", ["fahrenheit", "celsius"], False)), timeout_s=20)
async def get_weather(ctx: ToolContext, place: str, units: str = "fahrenheit"):
    async with httpx.AsyncClient(timeout=12) as c:
        g = await c.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": place, "count": 1, "language": "en", "format": "json"})
        g.raise_for_status()
        res = (g.json().get("results") or [])
        if not res:
            return {"ok": False}, f"I couldn't find a place called {place}"
        loc = res[0]
        unit_t = "fahrenheit" if units != "celsius" else "celsius"
        w = await c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,is_day",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code", "forecast_days": 1,
            "temperature_unit": unit_t, "wind_speed_unit": "mph" if unit_t == "fahrenheit" else "kmh"})
        w.raise_for_status()
    d = w.json()
    cur, day = d["current"], d["daily"]
    sym = "°F" if unit_t == "fahrenheit" else "°C"
    cond = WMO.get(cur["weather_code"], "unknown")
    name = ", ".join(x for x in (loc.get("name"), loc.get("admin1"), loc.get("country")) if x)
    summary = (f"{name}: {round(cur['temperature_2m'])}{sym} and {cond}, feels like {round(cur['apparent_temperature'])}{sym}, "
               f"wind {round(cur['wind_speed_10m'])} {'mph' if sym == '°F' else 'km/h'}. Today {round(day['temperature_2m_min'][0])} to "
               f"{round(day['temperature_2m_max'][0])}{sym}, {day['precipitation_probability_max'][0]}% chance of rain.")
    return {"place": name, "temperature": cur["temperature_2m"], "feels_like": cur["apparent_temperature"], "conditions": cond,
            "humidity": cur["relative_humidity_2m"], "wind": cur["wind_speed_10m"], "high": day["temperature_2m_max"][0],
            "low": day["temperature_2m_min"][0], "rain_chance": day["precipitation_probability_max"][0], "units": unit_t}, summary
