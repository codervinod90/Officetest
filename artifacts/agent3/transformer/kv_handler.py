def parse_key_value(text: str) -> dict:
    """Parse key=value or key:value pairs."""
    result = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
        elif ":" in line:
            key, _, value = line.partition(":")
        else:
            continue
        result[key.strip()] = value.strip()
    return result
