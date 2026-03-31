import hashlib


CITIES = {
    "new york": {"temp_c": 18, "condition": "partly cloudy", "humidity": 65, "wind_kmh": 12},
    "london": {"temp_c": 14, "condition": "rainy", "humidity": 82, "wind_kmh": 20},
    "tokyo": {"temp_c": 22, "condition": "sunny", "humidity": 55, "wind_kmh": 8},
    "paris": {"temp_c": 16, "condition": "overcast", "humidity": 70, "wind_kmh": 15},
    "sydney": {"temp_c": 25, "condition": "sunny", "humidity": 48, "wind_kmh": 18},
    "mumbai": {"temp_c": 32, "condition": "humid", "humidity": 88, "wind_kmh": 6},
}


def _generate_weather(city: str) -> dict:
    """Generate deterministic weather for unknown cities based on name hash."""
    h = int(hashlib.md5(city.encode()).hexdigest()[:8], 16)
    conditions = ["sunny", "cloudy", "rainy", "partly cloudy", "windy", "foggy"]
    return {
        "temp_c": 10 + (h % 25),
        "condition": conditions[h % len(conditions)],
        "humidity": 30 + (h % 60),
        "wind_kmh": 2 + (h % 30),
    }


def execute(params: dict) -> dict:
    city = params.get("city", "").strip()
    if not city:
        return {"error": "city is required"}

    units = params.get("units", "celsius")
    data = CITIES.get(city.lower(), _generate_weather(city.lower()))

    temp = data["temp_c"]
    if units == "fahrenheit":
        temp = round(temp * 9 / 5 + 32, 1)

    return {
        "city": city,
        "temperature": temp,
        "units": units,
        "condition": data["condition"],
        "humidity": data["humidity"],
        "wind_kmh": data["wind_kmh"],
    }
