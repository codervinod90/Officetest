"""
MCP Server for Dynamic Runtime Tools
- Discovers tool folders from TOOLS_DIR (live, no restart needed)
- Serves MCP protocol via SSE transport (for LLM agents)
- Serves REST API (for Streamlit UI)
- Executes tools via the worker service
- Auto-builds venvs via the venv builder service
"""

import json
import logging
import os
from pathlib import Path

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-server")

TOOLS_DIR = os.getenv("TOOLS_DIR", "/tools")
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
VENV_BUILDER_URL = os.getenv("VENV_BUILDER_URL", "http://localhost:8001")

# --- Tool Discovery (live from disk, no caching) ---


def discover_tools() -> dict[str, dict]:
    """Scan TOOLS_DIR for tool folders. Always reads fresh from disk."""
    tools = {}
    if not os.path.isdir(TOOLS_DIR):
        logger.warning(f"Tools directory not found: {TOOLS_DIR}")
        return tools

    for name in sorted(os.listdir(TOOLS_DIR)):
        tool_dir = os.path.join(TOOLS_DIR, name)
        if not os.path.isdir(tool_dir):
            continue
        schema_path = os.path.join(tool_dir, "tool.json")
        main_path = os.path.join(tool_dir, "main.py")
        if not os.path.exists(schema_path) or not os.path.exists(main_path):
            continue
        try:
            with open(schema_path) as f:
                schema = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Skipping {name}: bad tool.json: {e}")
            continue
        tools[name] = {
            "name": schema.get("name", name),
            "description": schema.get("description", ""),
            "input_schema": schema.get("inputSchema", {}),
            "dir": tool_dir,
        }

    return tools


def read_tool_files(tool_dir: str) -> dict[str, str]:
    """Read all files from a tool folder into {relative_path: content}."""
    files = {}
    base = Path(tool_dir)
    for filepath in sorted(base.rglob("*")):
        if filepath.is_file():
            rel = str(filepath.relative_to(base))
            with open(filepath, "r") as f:
                files[rel] = f.read()
    return files


async def ensure_tool_venv(tool_name: str, requirements: str) -> dict:
    """Build venv for a tool if requirements.txt exists."""
    if not requirements.strip():
        return {"cached": True}
    tool_id = f"tool_{tool_name}"
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{VENV_BUILDER_URL}/build",
            json={"agent_id": tool_id, "requirements": requirements},
        )
        resp.raise_for_status()
        return resp.json()


async def execute_tool(tool_name: str, params: dict) -> dict:
    """Send tool code to worker for execution."""
    tools = discover_tools()
    if tool_name not in tools:
        return {"error": f"Tool '{tool_name}' not found"}

    tool = tools[tool_name]
    code_files = read_tool_files(tool["dir"])

    requirements = code_files.get("requirements.txt", "")
    if requirements.strip():
        try:
            await ensure_tool_venv(tool_name, requirements)
        except Exception as e:
            logger.error(f"Venv build failed for tool {tool_name}: {e}")

    tool_id = f"tool_{tool_name}"
    payload = {
        "code_files": code_files,
        "entry_point": "main.py",
        "user_input": json.dumps(params),
        "agent_id": tool_id,
        "mode": "tool",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{WORKER_URL}/execute", json=payload)
        resp.raise_for_status()
        result = resp.json()

    if result.get("success") and result.get("stdout"):
        try:
            return json.loads(result["stdout"].strip())
        except json.JSONDecodeError:
            return {"result": result["stdout"].strip()}

    if result.get("stderr"):
        return {"error": result["stderr"]}

    return {"result": result.get("stdout", "")}


# --- MCP Protocol Server ---

mcp_server = Server("tool-server")


@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    tools = discover_tools()
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["input_schema"],
        )
        for t in tools.values()
    ]


@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    tools = discover_tools()
    folder_name = None
    for fname, t in tools.items():
        if t["name"] == name:
            folder_name = fname
            break

    if not folder_name:
        return [types.TextContent(type="text", text=json.dumps({"error": f"Tool '{name}' not found"}))]

    result = await execute_tool(folder_name, arguments or {})
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# --- SSE Transport ---

sse_transport = SseServerTransport("/messages/")


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await mcp_server.run(
            read_stream, write_stream, mcp_server.create_initialization_options()
        )


async def handle_messages(request: Request):
    await sse_transport.handle_post_message(
        request.scope, request.receive, request._send
    )


# --- REST API (for Streamlit UI) ---


async def rest_list_tools(request: Request):
    tools = discover_tools()
    return JSONResponse({
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools.values()
        ]
    })


async def rest_call_tool(request: Request):
    name = request.path_params["name"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    params = body.get("params", body)
    result = await execute_tool(name, params)
    return JSONResponse(result)


async def rest_health(request: Request):
    return JSONResponse({"status": "ok", "tools": len(discover_tools())})


# --- App ---

@asynccontextmanager
async def lifespan(app):
    tools = discover_tools()
    logger.info(f"Startup: {len(tools)} tools in {TOOLS_DIR} (live-reload enabled)")
    yield


app = Starlette(
    routes=[
        Route("/sse", handle_sse),
        Route("/messages/", handle_messages, methods=["POST"]),
        Route("/tools", rest_list_tools),
        Route("/tools/{name}/call", rest_call_tool, methods=["POST"]),
        Route("/health", rest_health),
    ],
    lifespan=lifespan,
)
