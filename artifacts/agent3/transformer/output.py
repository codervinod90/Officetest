import json
from tabulate import tabulate


def to_json(data: dict) -> str:
    """Convert data to JSON + table view for CSV data."""
    json_out = json.dumps(data, indent=2)
    if data.get("format") == "csv" and data.get("data"):
        rows = data["data"]
        if rows:
            headers = list(rows[0].keys())
            table = tabulate([r.values() for r in rows], headers=headers, tablefmt="grid")
            return f"{json_out}\n\nTable view:\n{table}"
    return json_out
