import yaml


def format_result(expression: str, value) -> str:
    """Format the calculation result as YAML."""
    if isinstance(value, float) and value == int(value):
        value = int(value)
    result = {"expression": expression, "result": value}
    return yaml.dump(result, default_flow_style=False).strip()
