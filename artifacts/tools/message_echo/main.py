def execute(params: dict) -> dict:
    msg = params.get("msg", "")
    return {"echo": msg}
