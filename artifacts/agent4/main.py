"""
Travel Report Agent
Takes a city name, calls MCP tools (weather_lookup + text_analyzer),
and produces a combined travel report.
"""

import json
import os
import urllib.request


MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8002")


def call_tool(tool_name: str, params: dict) -> dict:
    """Call an MCP tool via the REST API."""
    url = f"{MCP_SERVER_URL}/tools/{tool_name}/call"
    data = json.dumps({"params": params}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def run(user_input: str) -> str:
    city = user_input.strip()
    if not city:
        return "Please provide a city name."

    lines = [f"=== Travel Report: {city} ===", ""]

    # 1) Call weather_lookup tool
    try:
        weather = call_tool("weather_lookup", {"city": city, "units": "celsius"})
        if "error" in weather:
            lines.append(f"[Weather] Error: {weather['error']}")
        else:
            lines.append(f"Weather in {weather['city']}:")
            lines.append(f"  Temperature : {weather['temperature']}°C")
            lines.append(f"  Condition   : {weather['condition']}")
            lines.append(f"  Humidity    : {weather['humidity']}%")
            lines.append(f"  Wind        : {weather['wind_kmh']} km/h")
    except Exception as e:
        lines.append(f"[Weather] Failed to call tool: {e}")

    lines.append("")

    # 2) Build a travel blurb and analyze it with text_analyzer
    blurb = (
        f"{city} is a wonderful destination for travelers. "
        f"The city offers a unique blend of culture, cuisine, and adventure. "
        f"Visitors enjoy exploring local markets, historic landmarks, and scenic views. "
        f"Whether you are looking for relaxation or excitement, {city} has something for everyone."
    )
    lines.append(f"Travel Blurb: {blurb}")
    lines.append("")

    try:
        analysis = call_tool("text_analyzer", {"text": blurb, "top_n": 5})
        if "error" in analysis:
            lines.append(f"[Text Analysis] Error: {analysis['error']}")
        else:
            lines.append("Blurb Analysis:")
            lines.append(f"  Words     : {analysis['word_count']}")
            lines.append(f"  Sentences : {analysis['sentence_count']}")
            lines.append(f"  Unique    : {analysis['unique_words']}")
            lines.append(f"  Avg length: {analysis['avg_word_length']} chars")
            top = ", ".join(f"{w['word']}({w['count']})" for w in analysis.get("top_words", []))
            lines.append(f"  Top words : {top}")
    except Exception as e:
        lines.append(f"[Text Analysis] Failed to call tool: {e}")

    return "\n".join(lines)
