"""
Agent Code Generator & Tester
- Generate tab: LLM-only drafting of MCP tools and OpenAI+MCP agents (agent5 template)
- New agents from `write_agent_bundle` always get a `requirements.txt` file (non-empty for OpenAI+MCP clones)
- Test tab: run the selected agent on the worker; agents call MCP tools as the model decides
- Tool call log: parses agent5 `[MCP trace]` footer to show whether tools ran for each test input
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import requests
import streamlit as st
from openai import OpenAI


WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
VENV_BUILDER_URL = os.getenv("VENV_BUILDER_URL", "http://localhost:8001")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8002")
_default_artifacts = os.path.join(os.path.dirname(__file__), "artifacts")
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", _default_artifacts)
_default_tools = os.path.join(_default_artifacts, "tools")
TOOLS_DIR = os.getenv("TOOLS_DIR", _default_tools)

# agent5-style agents need a worker venv; system Python on the worker image has no httpx/openai.
DEFAULT_OPENAI_MCP_REQUIREMENTS = "openai>=1.0\nhttpx>=0.27.0\n"


def _main_py_needs_openai_venv(main_py: str) -> bool:
    s = main_py or ""
    return (
        "import httpx" in s
        or "from httpx" in s
        or "import openai" in s
        or "from openai" in s
    )


AGENT_DESCRIPTIONS = {
    "agent1": "Text Processor – strips HTML, uppercase, reverse, stats",
    "agent2": "Math Calculator – evaluates expressions, outputs YAML",
    "agent3": "Data Transformer – CSV/key-value to JSON + table view",
    "agent4": "Travel Reporter – calls MCP tools (weather + text analyzer)",
    "agent5": "LLM Agent – GPT dynamically discovers and calls MCP tools",
}

SAMPLE_INPUTS = {
    "agent1": "<h1>Hello</h1> <p>World from Agent Tester</p>",
    "agent2": "(10 + 5) * 3 - 8",
    "agent3": "name,age,city\nAlice,30,NYC\nBob,25,LA",
    "agent4": "Tokyo",
    "agent5": "What is the weather in Tokyo and Paris? Also analyze this text: Artificial intelligence is transforming how we build software.",
}


def list_agents() -> list[str]:
    if not os.path.isdir(ARTIFACTS_DIR):
        return []
    return sorted([
        d for d in os.listdir(ARTIFACTS_DIR)
        if os.path.isdir(os.path.join(ARTIFACTS_DIR, d))
        and d != "tools"
        and not d.startswith(".")
    ])


def load_agent_meta(agent_id: str) -> dict:
    """Optional per-agent UI hints without redeploying the app image (K8s: place meta.json in agent folder on /agents PVC)."""
    path = os.path.join(ARTIFACTS_DIR, agent_id, "meta.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def agent_description(agent_id: str) -> str:
    meta = load_agent_meta(agent_id)
    return meta.get("description") or AGENT_DESCRIPTIONS.get(agent_id, "")


def agent_sample_input(agent_id: str) -> str:
    meta = load_agent_meta(agent_id)
    return meta.get("sample_input") or SAMPLE_INPUTS.get(agent_id, "")


def list_mcp_tools() -> list[dict]:
    try:
        resp = requests.get(f"{MCP_SERVER_URL}/tools", timeout=10)
        resp.raise_for_status()
        return resp.json().get("tools", [])
    except Exception:
        return []


def read_folder(folder: str) -> dict[str, str]:
    files = {}
    base = Path(folder)
    for filepath in sorted(base.rglob("*")):
        if filepath.is_file():
            rel = str(filepath.relative_to(base))
            try:
                files[rel] = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return files


def ensure_venv(agent_id: str, requirements: str) -> dict:
    """Build venv if it doesn't exist. Returns build result or cached status."""
    try:
        resp = requests.post(
            f"{VENV_BUILDER_URL}/build",
            json={"agent_id": agent_id, "requirements": requirements},
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": f"Venv builder unreachable at {VENV_BUILDER_URL}"}
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def rebuild_venv(agent_id: str, requirements: str) -> dict:
    """Delete and recreate venv (fixes stale/cached venv vs updated requirements.txt)."""
    try:
        resp = requests.post(
            f"{VENV_BUILDER_URL}/rebuild",
            json={"agent_id": agent_id, "requirements": requirements},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": f"Venv builder unreachable at {VENV_BUILDER_URL}"}
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def list_cached_venvs() -> dict:
    try:
        resp = requests.get(f"{VENV_BUILDER_URL}/list", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"venvs": []}


def execute_via_worker(
    code_files: dict,
    entry_point: str,
    user_input: str = "",
    agent_id: Optional[str] = None,
    timeout: int = 30,
) -> tuple[str, str, bool, Optional[str]]:
    payload = {
        "code_files": code_files,
        "entry_point": entry_point,
        "user_input": user_input,
        "timeout": timeout,
    }
    if agent_id:
        payload["agent_id"] = agent_id

    try:
        resp = requests.post(
            f"{WORKER_URL}/execute",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout + 10,
        )
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
                detail = err_body.get("detail", err_body)
            except Exception:
                detail = (resp.text or "")[:4000]
            return "", f"Worker HTTP {resp.status_code}: {detail}", False, None
        data = resp.json()
        return data.get("stdout", ""), data.get("stderr", ""), data.get("success", True), data.get("venv_used")
    except requests.exceptions.ConnectionError:
        return "", f"Worker unreachable at {WORKER_URL}. Is the worker running?", False, None
    except requests.exceptions.Timeout:
        return "", "Worker request timed out.", False, None
    except requests.exceptions.RequestException as e:
        return "", str(e), False, None


MCP_TRACE_MARKER = "\n\n---\n[MCP trace] Tools invoked: "


def parse_mcp_trace_from_stdout(stdout: str) -> dict:
    """Split agent stdout into the model answer and agent5 MCP tool trace (if present)."""
    if MCP_TRACE_MARKER not in stdout:
        return {
            "has_trace": False,
            "answer": stdout,
            "tools_called": None,
            "note": None,
        }
    i = stdout.index(MCP_TRACE_MARKER)
    answer = stdout[:i].rstrip()
    tail = stdout[i + len(MCP_TRACE_MARKER) :].strip()
    m = re.match(r"^(.+?)\.\s+(.*)$", tail, re.DOTALL)
    if m:
        names_part, note = m.group(1).strip(), m.group(2).strip()
    else:
        names_part = tail.rstrip(".").strip()
        note = ""
    if names_part == "(none)" or not names_part:
        tools: list[str] = []
    else:
        tools = [t.strip() for t in names_part.split(",") if t.strip()]
    return {
        "has_trace": True,
        "answer": answer,
        "tools_called": tools,
        "note": note or None,
    }


def _valid_slug(s: str) -> bool:
    """Folder / tool id: lowercase letter, then letters, digits, underscores."""
    return bool(re.match(r"^[a-z][a-z0-9_]{0,62}$", s or ""))


def _strip_json_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _llm_configured() -> bool:
    prov = (os.getenv("LLM_PROVIDER") or "azure").strip().lower()
    if prov == "azure":
        return bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))
    return bool(os.getenv("OPENAI_API_KEY"))


def _openai_client_and_model() -> tuple[OpenAI, str]:
    prov = (os.getenv("LLM_PROVIDER") or "azure").strip().lower()
    if prov == "azure":
        client = OpenAI(
            base_url=os.getenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid/openai/v1/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        )
        model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    else:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return client, model


def llm_complete_json(system: str, user: str) -> dict:
    if not _llm_configured():
        raise RuntimeError("LLM API keys not configured.")
    client, model = _openai_client_and_model()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(_strip_json_fences(raw))


def _mcp_tools_context() -> str:
    tools = list_mcp_tools()
    if not tools:
        return "(No tools returned from MCP — describe generic patterns only.)"
    lines = []
    for t in tools[:48]:
        n = t.get("name", "")
        d = (t.get("description") or "").replace("\n", " ")[:220]
        lines.append(f"- {n}: {d}")
    return "\n".join(lines)


def llm_draft_tool(user_idea: str, avoid_ids: list[str]) -> dict:
    taken = ", ".join(avoid_ids[:80]) if avoid_ids else "(none)"
    system = f"""You are an expert Python engineer building MCP tools.
The worker loads main.py and calls execute(params: dict) -> dict; return value must be JSON-serializable.
Output ONLY valid JSON with keys:
- tool_id: string, snake_case, starts with a letter, max 40 chars
- description: string
- inputSchema: JSON Schema with type "object", "properties", "required"
- main_py: Python source defining def execute(params: dict) -> dict:
- requirements_txt: pip lines or empty string if stdlib only

Validate inputs, handle missing keys. No filesystem or subprocess unless essential and documented.
Do not use these tool_id values: {taken}."""
    user = f"Tool idea:\n{user_idea}\n\nRegistered MCP tools (for context):\n{_mcp_tools_context()}"
    return llm_complete_json(system, user)


def llm_draft_agent5_meta(user_idea: str, avoid_ids: list[str]) -> dict:
    taken = ", ".join(avoid_ids[:80]) if avoid_ids else "(none)"
    system = f"""Output ONLY valid JSON with string keys agent_id, description, sample_input.
agent_id: snake_case, unique, not in: {taken}
Code will be copied from a fixed OpenAI function-calling + MCP template. You only supply metadata and a rich sample_input that exercises multiple MCP tools when possible."""
    user = f"Agent purpose:\n{user_idea}\n\nTools:\n{_mcp_tools_context()}"
    return llm_complete_json(system, user)


def _load_from_agent5(rel: str) -> Optional[str]:
    for root in (Path(ARTIFACTS_DIR), Path(__file__).resolve().parent / "artifacts"):
        p = root / "agent5" / rel
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return None


def write_tool_from_llm_payload(data: dict, main_py_override: Optional[str] = None) -> str:
    tool_id = (data.get("tool_id") or "").strip().lower()
    if not _valid_slug(tool_id):
        raise ValueError("Invalid tool_id from model (snake_case, start with letter).")
    description = (data.get("description") or "").strip() or f"Tool {tool_id}"
    schema = data.get("inputSchema") or data.get("input_schema")
    if not isinstance(schema, dict):
        raise ValueError("inputSchema must be a JSON object.")
    if schema.get("type") != "object":
        raise ValueError('inputSchema.type must be "object".')
    main_py = (main_py_override if main_py_override is not None else (data.get("main_py") or "")).strip()
    if "def execute" not in main_py:
        raise ValueError("main_py must define execute(params: dict).")
    compile(main_py, f"{tool_id}/main.py", "exec")
    base = os.path.join(TOOLS_DIR, tool_id)
    if os.path.exists(base):
        raise FileExistsError(f"Tool folder already exists: {tool_id}")
    os.makedirs(base, exist_ok=True)
    manifest = {"name": tool_id, "description": description, "inputSchema": schema}
    with open(os.path.join(base, "tool.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    with open(os.path.join(base, "main.py"), "w") as f:
        f.write(main_py + ("\n" if not main_py.endswith("\n") else ""))
    req = (data.get("requirements_txt") or "").strip()
    if req:
        with open(os.path.join(base, "requirements.txt"), "w") as f:
            f.write(req + "\n")
    return tool_id


def write_agent_bundle(
    agent_id: str,
    description: str,
    sample_input: str,
    main_py: str,
    requirements_txt: str = "",
) -> None:
    aid = agent_id.strip().lower()
    if not _valid_slug(aid):
        raise ValueError("Invalid agent_id.")
    if "def run" not in main_py:
        raise ValueError("main_py must define run(user_input: str).")
    compile(main_py, f"{aid}/main.py", "exec")
    base = os.path.join(ARTIFACTS_DIR, aid)
    if os.path.exists(base):
        raise FileExistsError(f"Agent folder already exists: {aid}")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "main.py"), "w") as f:
        f.write(main_py + ("\n" if not main_py.endswith("\n") else ""))
    meta = {
        "description": (description or "").strip() or f"Generated agent {aid}",
        "sample_input": (sample_input or "").strip() or "hello",
    }
    with open(os.path.join(base, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    req_out = (requirements_txt or "").strip()
    if not req_out and _main_py_needs_openai_venv(main_py):
        req_out = DEFAULT_OPENAI_MCP_REQUIREMENTS.strip()
    # Every agent folder always has requirements.txt (possibly empty = stdlib-only on worker).
    with open(os.path.join(base, "requirements.txt"), "w") as f:
        f.write(req_out + ("\n" if req_out else ""))


def write_openai_mcp_agent_clone(
    agent_id: str,
    description: str,
    sample_input: str,
    requirements_override: str = "",
) -> None:
    main_py = _load_from_agent5("main.py")
    if not main_py:
        raise RuntimeError("agent5 template not found (expected agent5/main.py under ARTIFACTS_DIR or ./artifacts).")
    raw_req = _load_from_agent5("requirements.txt")
    req = (requirements_override or "").strip() or (raw_req or "").strip() or DEFAULT_OPENAI_MCP_REQUIREMENTS
    write_agent_bundle(agent_id.strip().lower(), description, sample_input, main_py, req)


def _exec_timeout_for_agent_files(agent_files: dict[str, str]) -> int:
    req = agent_files.get("requirements.txt", "").lower()
    if "openai" in req or "httpx" in req:
        return 120
    return 30


def execute_agent_test(
    agent_id: str,
    agent_files: dict[str, str],
    user_input: str,
) -> dict:
    result: dict = {
        "user_input_empty": not (user_input or "").strip(),
        "venv_failed": False,
        "venv_result": None,
        "venv_status": None,
        "stdout": "",
        "stderr": "",
        "success": False,
        "venv_used": None,
    }
    if result["user_input_empty"]:
        return result
    if not agent_files:
        result["stderr"] = (
            f"No files loaded for agent `{agent_id}` (empty folder under `{ARTIFACTS_DIR}`). "
            "Refresh the app or fix the agent directory."
        )
        return result
    if "main.py" not in agent_files:
        result["stderr"] = (
            f"Agent `{agent_id}` has no `main.py` in loaded files (keys: {list(agent_files.keys())}). "
            "The worker requires entry_point main.py in code_files."
        )
        return result

    if _main_py_needs_openai_venv(agent_files.get("main.py", "")):
        if "requirements.txt" not in agent_files or not (agent_files.get("requirements.txt") or "").strip():
            result["stderr"] = (
                f"Agent `{agent_id}` uses OpenAI/httpx in `main.py` but `requirements.txt` is missing or empty. "
                "Recreate it from **Generate** (LLM agent); every new agent includes `requirements.txt`."
            )
            return result

    has_requirements = "requirements.txt" in agent_files and bool(agent_files["requirements.txt"].strip())
    requirements_content = agent_files.get("requirements.txt", "").strip() if has_requirements else ""
    venv_failed = False
    if has_requirements and requirements_content:
        venv_result = ensure_venv(agent_id, agent_files["requirements.txt"])
        result["venv_result"] = venv_result
        if "error" in venv_result:
            venv_failed = True
            result["venv_failed"] = True
        elif venv_result.get("cached"):
            result["venv_status"] = "cached"
        else:
            result["venv_status"] = f"built in {venv_result.get('built_in_seconds', 0):.1f}s"
    if venv_failed:
        return result
    timeout = _exec_timeout_for_agent_files(agent_files)
    if "LLM" in agent_description(agent_id):
        timeout = max(timeout, 120)

    def _run_worker() -> tuple[str, str, bool, Optional[str]]:
        return execute_via_worker(
            code_files=agent_files,
            entry_point="main.py",
            user_input=user_input,
            agent_id=agent_id,
            timeout=timeout,
        )

    stdout, stderr, success, venv_used = _run_worker()
    result["stdout"] = stdout
    result["stderr"] = stderr
    result["success"] = success
    result["venv_used"] = venv_used

    combined = f"{stderr or ''}\n{stdout or ''}"
    import_broke = (
        has_requirements
        and (
            "ModuleNotFoundError" in combined
            or "ImportError" in combined
            or "No module named" in combined
        )
    )
    if import_broke:
        rb = rebuild_venv(agent_id, agent_files["requirements.txt"])
        result["venv_rebuild"] = rb
        if "error" in rb:
            result["stderr"] = (
                f"{result['stderr']}\n\nVenv rebuild failed: {rb['error']}"
            ).strip()
        else:
            result["venv_status"] = f"rebuilt ({rb.get('built_in_seconds', 0):.1f}s) after import error"
            stdout2, stderr2, success2, venv_used2 = _run_worker()
            result["stdout"] = stdout2
            result["stderr"] = stderr2
            result["success"] = success2
            result["venv_used"] = venv_used2

    return result


def render_agent_test_result(
    agent_id: str,
    result: dict,
    *,
    requirement_input: Optional[str] = None,
) -> None:
    if result.get("user_input_empty"):
        st.warning("Please enter some input to test.")
        return
    vr = result.get("venv_result")
    if result.get("venv_failed") and isinstance(vr, dict) and "error" in vr:
        st.error(f"Venv build failed: {vr['error']}")
        return
    vs = result.get("venv_status") or ""
    if vs.startswith("rebuilt") and "import error" in vs:
        st.info("Retried the run once after a **full venv rebuild** (fixes stale `/build` cache vs new `requirements.txt`).")

    stdout = (result.get("stdout") or "").strip()
    parsed = parse_mcp_trace_from_stdout(stdout) if stdout else {
        "has_trace": False,
        "answer": "",
        "tools_called": None,
        "note": None,
    }

    st.subheader("Tool call log")
    if requirement_input is not None and requirement_input.strip():
        ri = requirement_input.strip()
        st.caption(
            "Requirement / input for this run: "
            + (ri[:500] + "…" if len(ri) > 500 else ri)
        )
    if not stdout:
        st.caption("No agent stdout — cannot report tool usage from `[MCP trace]` for this run.")
    elif parsed["has_trace"]:
        tools = parsed["tools_called"] or []
        if tools:
            st.success(f"**MCP tools were invoked:** {', '.join(tools)}")
        else:
            st.warning("**No MCP tools were invoked** for this input (the model finished without calling tools).")
        if parsed.get("note"):
            st.caption(parsed["note"])
    else:
        st.info(
            "No `[MCP trace]` in stdout — structured logging applies to **LLM agents** using the **agent5** "
            "(OpenAI + MCP) template. Other agents may still call tools without this footer."
        )

    st.subheader("Output")
    display_stdout = parsed["answer"] if parsed["has_trace"] else (result.get("stdout") or "")
    if display_stdout:
        st.code(display_stdout, language="text")
    if result.get("stderr"):
        st.error(result["stderr"])
    if not display_stdout and not result.get("stderr"):
        st.info("No output.")
    cols = st.columns(2)
    with cols[0]:
        vu = result.get("venv_used")
        vs2 = result.get("venv_status")
        if vu:
            st.caption(f"Venv: `{vu}` ({vs2 or 'ready'})")
        else:
            st.caption("Venv: none (using system Python)")
    with cols[1]:
        if vs2:
            st.caption(f"Deps: {vs2}")


# --- Streamlit UI ---
st.set_page_config(page_title="Agent Tester", page_icon="🤖", layout="wide")
st.title("🤖 Agent Code Tester")

agents = list_agents()
has_agents = len(agents) > 0

# Sidebar: select agent and view code
with st.sidebar:
    st.header("Select Agent")
    if has_agents:
        selected = st.selectbox(
            "Agent",
            agents,
            format_func=lambda a: f"{a} – {agent_description(a)}",
            key="sidebar_agent",
        )
        agent_folder = os.path.join(ARTIFACTS_DIR, selected)
        agent_files = read_folder(agent_folder)
        has_requirements = "requirements.txt" in agent_files
        requirements_content = agent_files.get("requirements.txt", "").strip()

        st.divider()
        st.header("Code")
        for path, content in agent_files.items():
            if content.strip():
                with st.expander(path, expanded=(path == "main.py")):
                    st.code(content, language="python")

        st.divider()
        st.caption(f"Worker: `{WORKER_URL}`")
        st.caption(f"Folder: `{ARTIFACTS_DIR}/{selected}/`")
        st.caption(f"Files: {len(agent_files)}")
        if len(agent_files) == 0:
            st.error(
                f"**No files read** from `{agent_folder}` — the worker will return HTTP 400 (empty `code_files`). "
                "This often happens when **`agents-cache` PVC has empty agent dirs** and init used `cp -n` "
                "so bundled files never copied. Apply the updated `k8s/app.yaml` seed and run "
                "`kubectl rollout restart deployment/agent-app`, or delete PVC `agents-cache` to re-seed."
            )
        if has_requirements and requirements_content:
            st.caption("Dependencies: `requirements.txt` found")
        else:
            st.caption("Dependencies: none (stdlib only)")
    else:
        selected = None
        agent_files = {}
        has_requirements = False
        requirements_content = ""
        st.info("No agents yet. Open the **Generate** tab to create one.")
        st.caption(f"Agents dir: `{ARTIFACTS_DIR}`")
        st.caption(f"Tools dir: `{TOOLS_DIR}`")

# Main area
tab_gen, tab_test = st.tabs(["Generate", "Test"])

# --- Generate Tab ---
with tab_gen:
    st.header("LLM generation")
    st.caption(
        f"Agents and MCP tools are created only via the LLM, then saved under **`{ARTIFACTS_DIR}`** and **`{TOOLS_DIR}`**. "
        "After **Write to disk**, refresh the sidebar (**R**)."
    )
    st.info(
        "**Test** runs the agent on the worker; the model decides when to call MCP tools. "
        "The **Tool call log** shows whether tools ran for that input (agent5 `[MCP trace]`; set `AGENT_TOOL_TRACE=0` on the worker to hide it)."
    )
    st.caption(
        "LLM uses the same credentials as this app: `LLM_PROVIDER`, Azure or `OPENAI_API_KEY`. "
        "On Kubernetes, mount Secret **llm-api-keys** on **agent-app** (`k8s/app.yaml`)."
    )

    def _tool_ids_on_disk() -> list[str]:
        if not os.path.isdir(TOOLS_DIR):
            return []
        return sorted(
            d
            for d in os.listdir(TOOLS_DIR)
            if os.path.isdir(os.path.join(TOOLS_DIR, d)) and not d.startswith(".")
        )

    if not _llm_configured():
        st.warning(
            "LLM is not configured. Set Azure or OpenAI variables locally, or ensure Secret **llm-api-keys** "
            "exists and **agent-app** mounts it (see `k8s/app.yaml`)."
        )
    else:
        lc1, lc2 = st.columns(2)

        with lc1:
            st.markdown("**New tool (LLM)**")
            llm_tool_prompt = st.text_area(
                "Describe what the tool should do",
                height=100,
                key="llm_tool_prompt",
                placeholder="e.g. Given text, return word count and the longest word",
            )
            if st.button("Draft tool with LLM", key="btn_llm_tool_draft"):
                if not llm_tool_prompt.strip():
                    st.error("Enter a description first.")
                else:
                    try:
                        with st.spinner("Calling LLM..."):
                            st.session_state["llm_tool_payload"] = llm_draft_tool(
                                llm_tool_prompt.strip(),
                                _tool_ids_on_disk(),
                            )
                    except Exception as e:
                        st.error(f"LLM failed: {e}")
                        st.session_state.pop("llm_tool_payload", None)

            pl = st.session_state.get("llm_tool_payload")
            if pl:
                st.json({k: v for k, v in pl.items() if k != "main_py"})
                edited_main = st.text_area(
                    "main.py (edit before save)",
                    value=pl.get("main_py", ""),
                    height=260,
                    key="llm_tool_main_py",
                )
                cta, ctb = st.columns(2)
                with cta:
                    if st.button("Write tool to disk", type="primary", key="btn_llm_tool_write"):
                        try:
                            tid = write_tool_from_llm_payload(pl, main_py_override=edited_main)
                            st.success(
                                f"Created `{TOOLS_DIR}/{tid}/` — MCP will list it; agents can call it from **Test**."
                            )
                            st.session_state.pop("llm_tool_payload", None)
                            st.rerun()
                        except (ValueError, OSError, SyntaxError) as e:
                            st.error(str(e))
                with ctb:
                    if st.button("Clear draft", key="btn_llm_tool_clear"):
                        st.session_state.pop("llm_tool_payload", None)
                        st.rerun()

        with lc2:
            st.markdown("**New agent (LLM)**")
            st.caption(
                "Uses the **agent5** template: OpenAI tool-calling + dynamic MCP. Worker needs LLM keys and `MCP_SERVER_URL`."
            )
            llm_agent_prompt = st.text_area(
                "Describe what the agent should do",
                height=100,
                key="llm_agent_prompt",
                placeholder="e.g. When the user asks about weather, call weather_lookup; summarize text with text_analyzer",
            )
            if st.button("Draft agent with LLM", key="btn_llm_agent_draft"):
                if not llm_agent_prompt.strip():
                    st.error("Enter a description first.")
                else:
                    try:
                        with st.spinner("Calling LLM..."):
                            st.session_state["llm_agent_payload"] = llm_draft_agent5_meta(
                                llm_agent_prompt.strip(),
                                list_agents(),
                            )
                    except Exception as e:
                        st.error(f"LLM failed: {e}")
                        st.session_state.pop("llm_agent_payload", None)

            ap = st.session_state.get("llm_agent_payload")
            if ap:
                st.json(ap)
                st.caption("Runtime **main.py** is copied from **agent5** after you save.")
                aid_preview = (ap.get("agent_id") or "").strip().lower()
                e2e_inp = st.text_input(
                    "E2E test input (defaults to sample_input)",
                    value=ap.get("sample_input") or "",
                    key="llm_agent_e2e_input",
                )
                ac1, ac2, ac3 = st.columns(3)
                with ac1:
                    if st.button("Write agent to disk", type="primary", key="btn_llm_agent_write"):
                        try:
                            write_openai_mcp_agent_clone(
                                ap.get("agent_id", ""),
                                ap.get("description", ""),
                                ap.get("sample_input", ""),
                            )
                            st.success(f"Created `{ARTIFACTS_DIR}/{aid_preview}/`. Open **Test** or run **E2E** below.")
                            st.session_state["llm_agent_last_id"] = aid_preview
                            st.rerun()
                        except (ValueError, OSError, RuntimeError, SyntaxError) as e:
                            st.error(str(e))
                with ac2:
                    if st.button("Clear draft", key="btn_llm_agent_clear"):
                        st.session_state.pop("llm_agent_payload", None)
                        st.rerun()
                with ac3:
                    run_e2e = st.button("Run E2E on worker", key="btn_llm_agent_e2e")
                if run_e2e:
                    if not aid_preview or not os.path.isdir(os.path.join(ARTIFACTS_DIR, aid_preview)):
                        st.error("Write the agent to disk first (or refresh after save).")
                    elif not (e2e_inp or "").strip():
                        st.error("Set E2E test input.")
                    else:
                        folder = os.path.join(ARTIFACTS_DIR, aid_preview)
                        files = read_folder(folder)
                        with st.spinner(f"Running {aid_preview} on worker (MCP from pod)..."):
                            er = execute_agent_test(aid_preview, files, e2e_inp.strip())
                        st.markdown("**E2E result**")
                        render_agent_test_result(
                            aid_preview, er, requirement_input=e2e_inp.strip()
                        )

# --- Test Tab ---
with tab_test:
    if not has_agents or selected is None:
        st.info("Create an agent in the **Generate** tab first, then press **R** to refresh.")
    else:
        st.header(f"Test: {selected}")
        st.caption(agent_description(selected))

        if has_requirements and requirements_content:
            st.info(f"This agent requires: `{requirements_content}` — venv will be built automatically.")

        user_input = st.text_area(
            "User Input",
            value=agent_sample_input(selected),
            height=120,
            key="test_user_input",
        )

        run_clicked = st.button("Run Test", type="primary", key="run_test_btn")

        if run_clicked:
            with st.spinner("Ensuring venv (if needed) and running on worker..."):
                test_result = execute_agent_test(selected, agent_files, user_input)
            render_agent_test_result(selected, test_result, requirement_input=user_input)

        with st.expander("Advanced: venv & dependencies"):
            if has_requirements:
                st.subheader("requirements.txt (from agent code)")
                st.code(agent_files["requirements.txt"], language="text")
                st.caption("Venv is built automatically on **Run Test**.")
            else:
                st.info("This agent has no `requirements.txt` — it uses only stdlib.")

            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Manual venv")
                build_btn = st.button("Build / Rebuild Venv", key="build_venv")
                if build_btn:
                    req_content = agent_files.get("requirements.txt", "")
                    with st.spinner("Building venv..."):
                        try:
                            resp = requests.post(
                                f"{VENV_BUILDER_URL}/rebuild",
                                json={"agent_id": selected, "requirements": req_content},
                                timeout=180,
                            )
                            resp.raise_for_status()
                            result = resp.json()
                            st.success(f"Venv rebuilt in {result['built_in_seconds']:.1f}s for `{selected}`")
                            st.json(result)
                        except Exception as e:
                            st.error(str(e))

            with col2:
                st.subheader("Add packages")
                new_packages = st.text_area(
                    "Packages (one per line)",
                    placeholder="pandas==2.1.0\nnumpy>=1.26",
                    height=100,
                    key="new_packages",
                )
                update_btn = st.button("Install Packages", key="update_packages")
                if update_btn and new_packages.strip():
                    pkgs = [p.strip() for p in new_packages.strip().splitlines() if p.strip()]
                    with st.spinner(f"Installing {len(pkgs)} package(s)..."):
                        try:
                            resp = requests.post(
                                f"{VENV_BUILDER_URL}/update",
                                json={"agent_id": selected, "packages": pkgs, "upgrade": False},
                                timeout=120,
                            )
                            resp.raise_for_status()
                            result = resp.json()
                            st.success(f"Installed: {', '.join(pkgs)}")
                            st.json(result)
                        except Exception as e:
                            st.error(str(e))

            st.divider()
            st.subheader("All agent venvs")
            if st.button("Refresh", key="list_venvs"):
                venv_data = list_cached_venvs()
                venvs = venv_data.get("venvs", [])
                if venvs:
                    for v in venvs:
                        with st.expander(v["agent_id"]):
                            st.json(v)
                else:
                    st.info("No venvs built yet. Run a test to auto-build.")
