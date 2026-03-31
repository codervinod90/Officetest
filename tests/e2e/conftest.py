"""
E2E fixtures: URLs from env (same names as app.py / k8s).

Run (services must be listening), e.g. local:

  WORKER_URL=http://127.0.0.1:8000 \\
  MCP_SERVER_URL=http://127.0.0.1:8002 \\
  VENV_BUILDER_URL=http://127.0.0.1:8001 \\
  pytest tests/e2e -v

Or port-forward Kubernetes services to those ports first.
"""

from __future__ import annotations

import os

import pytest
import requests

pytestmark = pytest.mark.e2e

TIMEOUT = float(os.getenv("E2E_HTTP_TIMEOUT", "5"))


@pytest.fixture(scope="session")
def e2e_urls() -> dict[str, str]:
    return {
        "worker": os.getenv("WORKER_URL", "http://127.0.0.1:8000").rstrip("/"),
        "mcp": os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8002").rstrip("/"),
        "venv": os.getenv("VENV_BUILDER_URL", "http://127.0.0.1:8001").rstrip("/"),
    }


@pytest.fixture(scope="session")
def worker_up(e2e_urls: dict[str, str]) -> str:
    base = e2e_urls["worker"]
    try:
        r = requests.get(f"{base}/health", timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok"
    except Exception as e:
        pytest.skip(f"Worker not reachable at {base}: {e}")
    return base


@pytest.fixture(scope="session")
def mcp_up(e2e_urls: dict[str, str]) -> str:
    base = e2e_urls["mcp"]
    try:
        r = requests.get(f"{base}/health", timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok"
    except Exception as e:
        pytest.skip(f"MCP server not reachable at {base}: {e}")
    return base


@pytest.fixture(scope="session")
def venv_builder_up(e2e_urls: dict[str, str]) -> str:
    base = e2e_urls["venv"]
    try:
        r = requests.get(f"{base}/health", timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok"
    except Exception as e:
        pytest.skip(f"Venv builder not reachable at {base}: {e}")
    return base
