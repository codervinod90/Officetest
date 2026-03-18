"""
Agent Worker Service
- Receives agent code (single file or multi-file with folders) + user_input via HTTP
- Executes in isolated subprocess
- Cleans up temp files after each request
- Stateless: ready for next request
"""

import os
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


app = FastAPI(title="Agent Worker", version="1.0.0")


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    success: bool


def _write_files(tmp_dir: str, files: Dict[str, str]) -> None:
    """Write files dict to tmp_dir, creating subdirs as needed."""
    for path, content in files.items():
        filepath = os.path.join(tmp_dir, path)
        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)


def execute_agent(
    agent_code: Optional[str] = None,
    code_files: Optional[Dict[str, str]] = None,
    entry_point: Optional[str] = None,
    user_input: str = "",
    timeout: int = 30,
) -> Tuple[str, str]:
    """Execute agent code. Supports single file or multi-file with folders.
    Entry point must define run(user_input: str) -> str."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        if code_files:
            # Multi-file: write all files, create runner that imports run() from entry_point
            if not entry_point:
                return "", "entry_point is required when using code_files"
            _write_files(tmp_dir, code_files)
            script_path = os.path.join(tmp_dir, entry_point)
            if not os.path.exists(script_path):
                return "", f"entry_point '{entry_point}' not found in files"
            # Module name: src/agent.py -> src.agent
            module_name = entry_point.replace("\\", "/").rsplit(".py", 1)[0].replace("/", ".")
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
            # Single file: wrap agent_code in harness
            if not agent_code:
                return "", "Either agent_code or files must be provided"
            harness = f'''
import sys

{agent_code}

if __name__ == "__main__":
    user_input = sys.stdin.read()
    result = run(user_input)
    print(result)
'''
            script_path = os.path.join(tmp_dir, "agent.py")
            with open(script_path, "w") as f:
                f.write(harness)
            run_script = script_path

        try:
            result = subprocess.run(
                [sys.executable, run_script],
                input=user_input,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp_dir,
            )
            return result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return "", "Execution timed out"


@app.post("/execute", response_model=ExecuteResponse)
async def execute(request: Request):
    """Execute agent code with user input. Supports single file or multi-file with folders.
    Uses raw JSON body to avoid FastAPI/Pydantic field parsing issues with 'files'."""
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    
    agent_code = body.get("agent_code")
    code_files = body.get("code_files") or body.get("files")  # support both keys
    entry_point = body.get("entry_point")
    user_input = body.get("user_input", "")
    timeout = body.get("timeout", 30)
    
    # Use entry_point as primary discriminator: if present, MUST use multi-file mode
    has_code_files = isinstance(code_files, dict) and len(code_files) > 0
    use_multi_file = bool(entry_point)  # entry_point present => multi-file mode
    
    if use_multi_file:
        if not has_code_files:
            raise HTTPException(
                status_code=400,
                detail=f"entry_point requires code_files. Received keys: {list(body.keys())}",
            )
    elif not agent_code:
        raise HTTPException(status_code=400, detail="Either agent_code or (code_files+entry_point) must be provided")
    
    try:
        stdout, stderr = execute_agent(
            agent_code=None if use_multi_file else (agent_code or None),
            code_files=code_files if use_multi_file else None,
            entry_point=entry_point,
            user_input=user_input,
            timeout=timeout,
        )
        return ExecuteResponse(
            stdout=stdout,
            stderr=stderr,
            success=len(stderr) == 0,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    """Health check for load balancer / K8s."""
    return {"status": "ok"}


@app.get("/version")
def version():
    """Returns worker version. Use to verify worker was restarted with latest code."""
    return {"version": "2.0", "multifile": True}
