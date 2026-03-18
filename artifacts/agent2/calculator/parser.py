import re

ALLOWED_CHARS = re.compile(r'^[\d\s\+\-\*/\.\(\)%]+$')


def parse_expression(expr: str) -> dict:
    """Validate and return a cleaned math expression."""
    expr = expr.strip()
    if not expr:
        return {"expression": "", "error": "Empty expression"}
    if not ALLOWED_CHARS.match(expr):
        return {"expression": expr, "error": "Contains invalid characters. Only numbers and +-*/().% allowed"}
    return {"expression": expr, "error": None}
