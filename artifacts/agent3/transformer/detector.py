def detect_format(text: str) -> str:
    """Detect if input is CSV, key-value pairs, or plain text."""
    lines = text.strip().splitlines()
    if any("," in line for line in lines) and len(lines) > 1:
        return "csv"
    if any("=" in line or ":" in line for line in lines):
        return "key_value"
    return "plain_text"
