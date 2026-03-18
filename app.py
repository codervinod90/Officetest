"""
Agent Code Generator & Tester (Base App)
- List agent folders from artifacts/
- Read code from selected agent folder
- Send to Worker for execution
- Display results
"""

import os
from pathlib import Path
from typing import Optional

import requests
import streamlit as st


WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")

AGENT_DESCRIPTIONS = {
    "agent1": "Text Processor – uppercase, reverse, word/char count",
    "agent2": "Math Calculator – evaluates math expressions safely",
    "agent3": "Data Transformer – CSV/key-value to JSON",
}

SAMPLE_INPUTS = {
    "agent1": "Hello World from Agent Tester",
    "agent2": "(10 + 5) * 3 - 8",
    "agent3": "name,age,city\nAlice,30,NYC\nBob,25,LA",
}


def list_agents() -> list[str]:
    """List agent folders inside artifacts/."""
    if not os.path.isdir(ARTIFACTS_DIR):
        return []
    agents = sorted([
        d for d in os.listdir(ARTIFACTS_DIR)
        if os.path.isdir(os.path.join(ARTIFACTS_DIR, d))
    ])
    return agents


def read_folder(folder: str) -> dict[str, str]:
    """Read all files from a folder into a {relative_path: content} dict."""
    files = {}
    base = Path(folder)
    for filepath in sorted(base.rglob("*")):
        if filepath.is_file():
            rel = str(filepath.relative_to(base))
            with open(filepath, "r") as f:
                files[rel] = f.read()
    return files


def execute_via_worker(
    code_files: dict,
    entry_point: str,
    user_input: str = "",
) -> tuple[str, str, bool]:
    """Send code files to worker for execution."""
    payload = {
        "code_files": code_files,
        "entry_point": entry_point,
        "user_input": user_input,
    }
    try:
        resp = requests.post(
            f"{WORKER_URL}/execute",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("stdout", ""), data.get("stderr", ""), data.get("success", True)
    except requests.exceptions.ConnectionError:
        return "", f"Worker unreachable at {WORKER_URL}. Is the worker running?", False
    except requests.exceptions.Timeout:
        return "", "Worker request timed out.", False
    except requests.exceptions.RequestException as e:
        return "", str(e), False


# --- Streamlit UI ---
st.set_page_config(page_title="Agent Tester", page_icon="🤖", layout="wide")
st.title("🤖 Agent Code Tester")

agents = list_agents()

if not agents:
    st.error(f"No agents found in `{ARTIFACTS_DIR}`. Create agent folders there.")
    st.stop()

# Sidebar: select agent and view code
with st.sidebar:
    st.header("1. Select Agent")
    selected = st.selectbox(
        "Agent",
        agents,
        format_func=lambda a: f"{a} – {AGENT_DESCRIPTIONS.get(a, '')}",
    )

    agent_folder = os.path.join(ARTIFACTS_DIR, selected)
    agent_files = read_folder(agent_folder)

    st.divider()
    st.header("Code")
    for path, content in agent_files.items():
        if content.strip():
            with st.expander(path, expanded=(path == "main.py")):
                st.code(content, language="python")

    st.divider()
    st.caption(f"Worker: `{WORKER_URL}`")
    st.caption(f"Folder: `artifacts/{selected}/`")
    st.caption(f"Files: {len(agent_files)}")

# Main area: test
st.header(f"2. Test: {selected}")
st.caption(AGENT_DESCRIPTIONS.get(selected, ""))

user_input = st.text_area(
    "User Input",
    value=SAMPLE_INPUTS.get(selected, ""),
    height=120,
)

run_clicked = st.button("Run Test", type="primary")

if run_clicked:
    if not user_input.strip():
        st.warning("Please enter some input to test.")
    else:
        with st.spinner("Running on worker..."):
            stdout, stderr, success = execute_via_worker(
                code_files=agent_files,
                entry_point="main.py",
                user_input=user_input,
            )

        st.subheader("Output")
        if success and stdout:
            st.code(stdout, language="text")
        if stderr:
            st.error(stderr)
        if not stdout and not stderr:
            st.info("No output.")
