"""
Agent Worker Service
- Receives agent code + user_input via HTTP
- Uses cached venvs from /venvs/<agent_id>/ when available
- Falls back to system Python only when requirements.txt is empty/missing
- Stateless: temp dir per execution, auto-cleanup
"""

import os
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


app = FastAPI(title="Agent Worker", version="4.0.0")

VENV_BASE = os.getenv("VENV_BASE", "/venvs")


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    success: bool
    venv_used: Optional[str] = None


def _write_files(tmp_dir: str, files: Dict[str, str]) -> None:
    for path, content in files.items():
        filepath = os.path.join(tmp_dir, path)
        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)


def _code_requires_venv(code_files: Optional[Dict[str, str]]) -> bool:
    """If requirements.txt has content, execution must use /venvs/<agent_id>/ (no silent system fallback)."""
    if not code_files:
        return False
    req = code_files.get("requirements.txt", "")
    return bool(req and str(req).strip())


def _find_python(
    agent_id: Optional[str],
    require_venv: bool,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Returns (python_executable, venv_id_or_none, error_message_or_none)."""
    if agent_id:
        venv_python = os.path.join(VENV_BASE, agent_id, "bin", "python")
        if os.path.exists(venv_python):
            return venv_python, agent_id, None
    if require_venv:
        hint = os.path.join(VENV_BASE, agent_id or "<agent_id>", "bin", "python")
        return (
            "",
            None,
            (
                f"requirements.txt is non-empty but no venv at {hint}. "
                "Build via venv-builder /build or /rebuild, or the UI **Dependencies** tab."
            ),
        )
    return sys.executable, None, None


def execute_agent(
    agent_code: Optional[str] = None,
    code_files: Optional[Dict[str, str]] = None,
    entry_point: Optional[str] = None,
    user_input: str = "",
    timeout: int = 30,
    agent_id: Optional[str] = None,
    mode: str = "agent",
) -> Tuple[str, str, Optional[str]]:
    """Execute agent or tool code. Returns (stdout, stderr, venv_id).
    mode='agent': calls run(user_input) -> str
    mode='tool': calls execute(json.loads(user_input)) -> dict, prints as JSON
    """
    need_venv = _code_requires_venv(code_files)
    python_exec, venv_id, py_err = _find_python(agent_id, need_venv)
    if py_err:
        return "", py_err, None

    with tempfile.TemporaryDirectory() as tmp_dir:
        if code_files:
            if not entry_point:
                return "", "entry_point is required when using code_files", None
            _write_files(tmp_dir, code_files)
            script_path = os.path.join(tmp_dir, entry_point)
            if not os.path.exists(script_path):
                return "", f"entry_point '{entry_point}' not found in files", None
            module_name = entry_point.replace("\\", "/").rsplit(".py", 1)[0].replace("/", ".")

            if mode == "tool":
                runner = f'''
import sys, json
sys.path.insert(0, {repr(tmp_dir)})
from {module_name} import execute
params = json.loads(sys.stdin.read())
result = execute(params)
print(json.dumps(result))
'''
            else:
                runner = f'''
import sys
sys.path.insert(0, {repr(tmp_dir)})
from {module_name} import run
print(run(sys.stdin.read()))
'''
            run_script = os.path.join(tmp_dir, "_runner.py")
            with open(run_script, "w") as f:
                f.write(runner)
        else:
            if not agent_code:
                return "", "Either agent_code or code_files must be provided", None
            harness = f'''
import sys

{agent_code}

if __name__ == "__main__":
    user_input = sys.stdin.read()
    result = run(user_input)
    print(result)
'''
            run_script = os.path.join(tmp_dir, "agent.py")
            with open(run_script, "w") as f:
                f.write(harness)

        try:
            # Inherit env so agents see MCP_SERVER_URL, OPENAI_*, etc. from the worker pod.
            result = subprocess.run(
                [python_exec, run_script],
                input=user_input,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp_dir,
                env=os.environ.copy(),
            )
            return result.stdout, result.stderr, venv_id
        except subprocess.TimeoutExpired:
            return "", "Execution timed out", venv_id


@app.post("/execute", response_model=ExecuteResponse)
async def execute(request: Request):
    """Execute agent code. Looks up /venvs/<agent_id>/ for cached venv."""
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    agent_code = body.get("agent_code")
    code_files = body.get("code_files") or body.get("files")
    entry_point = body.get("entry_point")
    user_input = body.get("user_input", "")
    timeout = body.get("timeout", 30)
    agent_id = body.get("agent_id")
    mode = body.get("mode", "agent")

    has_code_files = isinstance(code_files, dict) and len(code_files) > 0
    use_multi_file = bool(entry_point)

    if use_multi_file:
        if not has_code_files:
            cf = code_files
            n = len(cf) if isinstance(cf, dict) else "n/a"
            raise HTTPException(
                status_code=400,
                detail=(
                    "entry_point requires a non-empty code_files object (multipart agent). "
                    f"code_files type={type(cf).__name__} len={n}. "
                    f"Top-level JSON keys: {list(body.keys())}"
                ),
            )
    elif not agent_code:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide either agent_code (string) or code_files (object) + entry_point (e.g. main.py). "
                f"Got entry_point={entry_point!r}, code_files present={has_code_files}."
            ),
        )

    try:
        stdout, stderr, venv_id = execute_agent(
            agent_code=None if use_multi_file else (agent_code or None),
            code_files=code_files if use_multi_file else None,
            entry_point=entry_point,
            user_input=user_input,
            timeout=timeout,
            agent_id=agent_id,
            mode=mode,
        )
        return ExecuteResponse(
            stdout=stdout,
            stderr=stderr,
            success=len(stderr) == 0,
            venv_used=venv_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    venv_count = 0
    if os.path.isdir(VENV_BASE):
        venv_count = len([
            d for d in os.listdir(VENV_BASE)
            if os.path.isdir(os.path.join(VENV_BASE, d)) and not d.startswith(".")
        ])
    return {"version": "5.0", "multifile": True, "tool_mode": True, "agent_venvs": venv_count}
