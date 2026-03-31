import jmespath


def execute(params: dict) -> dict:
    data = params.get("data")
    expression = params.get("expression", "")

    if data is None:
        return {"error": "data is required"}
    if not expression:
        return {"error": "expression is required"}

    try:
        result = jmespath.search(expression, data)
        return {
            "expression": expression,
            "result": result,
            "result_type": type(result).__name__,
        }
    except jmespath.exceptions.ParseError as e:
        return {"error": f"Invalid JMESPath expression: {e}"}
