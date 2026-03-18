def safe_eval(expression: str) -> dict:
    """Evaluate a math expression safely (no builtins)."""
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return {"value": result, "error": None}
    except ZeroDivisionError:
        return {"value": None, "error": "Division by zero"}
    except Exception as e:
        return {"value": None, "error": str(e)}
