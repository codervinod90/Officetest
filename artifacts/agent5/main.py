"""
LLM Agent with Dynamic MCP Tool Calling
- Supports both OpenAI and Azure AI Foundry (set LLM_PROVIDER env var)
- Discovers all available MCP tools at runtime
- Sends them as function definitions, lets the LLM decide which to call
- Handles multi-turn tool-call loops
- Parallel MCP calls when the model returns multiple tool_calls in one turn
- Reuses HTTP connections to the MCP server for lower latency
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from openai import OpenAI


MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8002").rstrip("/")
MAX_TOOL_ROUNDS = 5


def _tool_trace_enabled() -> bool:
    """Append [MCP trace] footer to stdout so you can see if MCP tools ran."""
    v = os.environ.get("AGENT_TOOL_TRACE", "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def _with_tool_trace(answer: str, invoked: list[str], note: str = "") -> str:
    if not _tool_trace_enabled():
        return answer
    names = ", ".join(invoked) if invoked else "(none)"
    extra = f" {note}" if note else ""
    return f"{answer}\n\n---\n[MCP trace] Tools invoked: {names}.{extra}"

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "azure")

if LLM_PROVIDER == "azure":
    client = OpenAI(
        base_url=os.environ.get("AZURE_OPENAI_ENDPOINT", "https://testing-af.cognitiveservices.azure.com/openai/v1/"),
        api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
    )
    MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
else:
    client = OpenAI()
    MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def discover_tools(http: httpx.Client) -> list[dict]:
    """Fetch all available tools from the MCP server."""
    r = http.get(f"{MCP_SERVER_URL}/tools", timeout=10.0)
    r.raise_for_status()
    return r.json().get("tools", [])


def call_mcp_tool(http: httpx.Client, name: str, params: dict) -> dict:
    """Call an MCP tool via the REST API (same connection pool as discover)."""
    r = http.post(
        f"{MCP_SERVER_URL}/tools/{name}/call",
        json={"params": params},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def _run_one_tool_shared(http: httpx.Client, tool_call) -> tuple[str, str]:
    """Execute a single tool using shared client (caller must not use this from multiple threads)."""
    fn_name = tool_call.function.name
    try:
        fn_args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        fn_args = {}
    try:
        result = call_mcp_tool(http, fn_name, fn_args)
        content = json.dumps(result, indent=2)
    except Exception as e:
        content = json.dumps({"error": str(e)})
    return tool_call.id, content


def _run_one_tool_isolated(tool_call) -> tuple[str, str]:
    """Execute a single tool with its own HTTP client (safe for ThreadPoolExecutor)."""
    fn_name = tool_call.function.name
    try:
        fn_args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        fn_args = {}
    try:
        with httpx.Client(timeout=60.0) as http:
            result = call_mcp_tool(http, fn_name, fn_args)
        content = json.dumps(result, indent=2)
    except Exception as e:
        content = json.dumps({"error": str(e)})
    return tool_call.id, content


def mcp_tools_to_openai_functions(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool schemas to OpenAI function-calling format."""
    functions = []
    for tool in mcp_tools:
        schema = tool.get("input_schema", {})
        if not schema.get("type"):
            schema["type"] = "object"
        if "properties" not in schema:
            schema["properties"] = {}

        functions.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": schema,
            },
        })
    return functions


def run(user_input: str) -> str:
    limits = httpx.Limits(max_keepalive_connections=8, max_connections=16)
    with httpx.Client(limits=limits) as http:
        mcp_err: str | None = None
        try:
            mcp_tools = discover_tools(http)
        except Exception as e:
            mcp_tools = []
            mcp_err = str(e)

        tool_names = [t["name"] for t in mcp_tools]
        openai_tools = mcp_tools_to_openai_functions(mcp_tools) if mcp_tools else []

        sys_lines = [
            "You are a helpful assistant.",
            "For general knowledge, chit-chat, or anything you can answer without external tools, "
            "reply in natural language and do not call tools.",
            "When the user needs data or actions that only the listed tools provide (e.g. live weather, "
            "structured analysis), call the right tool(s). Prefer one assistant turn with multiple "
            "parallel tool calls when lookups are independent.",
            "After tool results, summarize clearly for the user.",
        ]
        if tool_names:
            sys_lines.append(f"Available tools: {', '.join(tool_names)}.")
        if mcp_err:
            sys_lines.append(
                f"MCP server was unreachable ({mcp_err}). Answer from general knowledge only; no tools can be run."
            )

        messages = [
            {"role": "system", "content": "\n".join(sys_lines)},
            {"role": "user", "content": user_input},
        ]

        # No tools registered or MCP down — plain chat completion
        if not openai_tools:
            response = client.chat.completions.create(model=MODEL, messages=messages)
            text = response.choices[0].message.content or "(No response from LLM)"
            hint = (
                " MCP discovery failed (see system context)."
                if mcp_err
                else " MCP listed zero tools — nothing to invoke."
            )
            return _with_tool_trace(text, [], note=hint)

        tools_invoked: list[str] = []

        for _ in range(MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
            )

            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason == "stop" or not msg.tool_calls:
                text = msg.content or "(No response from LLM)"
                note = (
                    " Model replied without calling any tools."
                    if not tools_invoked
                    else " Final reply after the tool call(s) listed above."
                )
                return _with_tool_trace(text, tools_invoked, note=note)

            messages.append(msg)

            tcalls = msg.tool_calls
            for tc in tcalls:
                fn = getattr(getattr(tc, "function", None), "name", None) or "?"
                tools_invoked.append(fn)

            if len(tcalls) == 1:
                tid, content = _run_one_tool_shared(http, tcalls[0])
                messages.append({"role": "tool", "tool_call_id": tid, "content": content})
            else:
                results_by_id: dict[str, str] = {}
                workers = min(len(tcalls), 8)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_run_one_tool_isolated, tc): tc for tc in tcalls}
                    for fut in as_completed(futures):
                        tid, content = fut.result()
                        results_by_id[tid] = content
                for tc in tcalls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": results_by_id[tc.id],
                    })

    return _with_tool_trace(
        "Reached maximum tool-call rounds without a final answer.",
        tools_invoked,
        note=" Hit MAX_TOOL_ROUNDS; check model or raise MAX_TOOL_ROUNDS in main.py.",
    )
