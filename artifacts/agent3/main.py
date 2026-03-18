from transformer.detector import detect_format
from transformer.csv_handler import parse_csv
from transformer.kv_handler import parse_key_value
from transformer.output import to_json


def run(user_input: str) -> str:
    text = user_input.strip()
    fmt = detect_format(text)

    if fmt == "csv":
        data = parse_csv(text)
        return to_json({"format": "csv", "rows": len(data), "data": data})
    elif fmt == "key_value":
        data = parse_key_value(text)
        sorted_data = dict(sorted(data.items()))
        return to_json({"format": "key_value", "count": len(sorted_data), "data": sorted_data})
    else:
        words = text.split()
        return to_json({"format": "plain_text", "words": sorted(words), "count": len(words)})
