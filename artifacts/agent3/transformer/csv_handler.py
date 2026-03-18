def parse_csv(text: str) -> list[dict]:
    """Parse CSV text into list of dicts using first row as headers."""
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        values = [v.strip() for v in line.split(",")]
        row = dict(zip(headers, values))
        rows.append(row)
    return rows
