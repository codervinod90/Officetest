"""
End-to-end checks across worker → (MCP-internal) → venv builder.

Order is independent per test; each test skips if its required service is down.
"""

from __future__ import annotations

import json

import pytest
import requests

pytestmark = pytest.mark.e2e


def test_worker_execute_stdlib_agent(worker_up: str) -> None:
    """Worker runs inline multifile agent (no venv)."""
    payload = {
        "code_files": {
            "main.py": "def run(user_input: str) -> str:\n    return 'e2e:' + user_input.strip()\n",
        },
        "entry_point": "main.py",
        "user_input": "hello",
        "timeout": 15,
    }
    r = requests.post(f"{worker_up}/execute", json=payload, timeout=20)
    r.raise_for_status()
    body = r.json()
    assert body.get("success") is True, body
    assert "e2e:hello" in (body.get("stdout") or "")


def test_worker_execute_tool_mode(worker_up: str) -> None:
    """Worker tool mode: execute(params) → JSON stdout."""
    payload = {
        "code_files": {
            "main.py": (
                "def execute(params: dict) -> dict:\n"
                "    return {'doubled': (params.get('n', 0) * 2)}\n"
            ),
        },
        "entry_point": "main.py",
        "user_input": json.dumps({"n": 21}),
        "timeout": 15,
        "mode": "tool",
        "agent_id": "e2e_inline_tool",
    }
    r = requests.post(f"{worker_up}/execute", json=payload, timeout=20)
    r.raise_for_status()
    body = r.json()
    assert body.get("success") is True, body
    out = json.loads((body.get("stdout") or "").strip())
    assert out == {"doubled": 42}


def test_mcp_lists_tools(mcp_up: str) -> None:
    r = requests.get(f"{mcp_up}/tools", timeout=10)
    r.raise_for_status()
    data = r.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)


def test_mcp_call_message_echo(mcp_up: str) -> None:
    """MCP REST → worker executes tool folder (full backend chain)."""
    listed = requests.get(f"{mcp_up}/tools", timeout=10)
    listed.raise_for_status()
    names = {t.get("name") for t in listed.json().get("tools", [])}
    if "message_echo" not in names:
        pytest.skip("message_echo not registered on MCP (seed TOOLS_DIR / PVC)")

    r = requests.post(
        f"{mcp_up}/tools/message_echo/call",
        json={"params": {"msg": "e2e-ping"}},
        timeout=90,
    )
    r.raise_for_status()
    result = r.json()
    assert "error" not in result, result
    assert result.get("echo") == "e2e-ping"


def test_venv_builder_list(venv_builder_up: str) -> None:
    r = requests.get(f"{venv_builder_up}/list", timeout=10)
    r.raise_for_status()
    data = r.json()
    assert "venvs" in data
    assert isinstance(data["venvs"], list)


def test_venv_build_and_worker_uses_venv(
    worker_up: str,
    venv_builder_up: str,
) -> None:
    """
    Build a tiny venv, then worker execute with agent_id (may use system python if
    venv path not shared between builder and worker — skip when worker cannot see venv).
    """
    agent_id = "e2e_requests_probe"
    req = "requests>=2.28.0\n"
    br = requests.post(
        f"{venv_builder_up}/build",
        json={"agent_id": agent_id, "requirements": req},
        timeout=180,
    )
    if br.status_code >= 400:
        pytest.skip(f"Venv build not available: {br.status_code} {br.text[:200]}")

    code = (
        "def run(user_input: str) -> str:\n"
        "    import requests\n"
        "    return 'ok:' + str(requests.__version__)\n"
    )
    payload = {
        "code_files": {"main.py": code},
        "entry_point": "main.py",
        "user_input": "x",
        "timeout": 30,
        "agent_id": agent_id,
    }
    er = requests.post(f"{worker_up}/execute", json=payload, timeout=45)
    er.raise_for_status()
    body = er.json()
    if not body.get("success") or "No module named 'requests'" in (body.get("stderr") or ""):
        pytest.skip(
            "Worker does not share venv volume with venv-builder (expected in split local runs)."
        )
    assert "ok:" in (body.get("stdout") or "")
