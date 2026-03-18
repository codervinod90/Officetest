from calculator.parser import parse_expression
from calculator.evaluator import safe_eval
from calculator.formatter import format_result


def run(user_input: str) -> str:
    expr = user_input.strip()
    parsed = parse_expression(expr)
    if parsed["error"]:
        return f"Error: {parsed['error']}"
    result = safe_eval(parsed["expression"])
    if result["error"]:
        return f"Error: {result['error']}"
    return format_result(expr, result["value"])
