import json


def to_json(data: dict) -> str:
    """Convert data to pretty-printed JSON string."""
    return json.dumps(data, indent=2)
