def format_result(expression: str, value) -> str:
    """Format the calculation result."""
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return f"{expression} = {value}"
