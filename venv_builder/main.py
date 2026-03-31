"""
Venv Build Service (Option C: isolated per agent + shared pip cache)
- Each agent gets its own venv at /venvs/<agent_id>/
- pip downloads cached at /venvs/.pip-cache/ (shared across all builds)
- Build, update, rebuild, list, delete operations
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("venv-builder")

app = FastAPI(title="Venv Builder", version="2.0.0")

VENV_BASE = os.getenv("VENV_BASE", "/venvs")
PIP_CACHE = os.path.join(VENV_BASE, ".pip-cache")
METADATA_FILE = "venv_meta.json"


class BuildRequest(BaseModel):
    agent_id: str
    requirements: str = ""


class UpdateRequest(BaseModel):
    agent_id: str
    packages: list[str]
    upgrade: bool = False


class BuildResponse(BaseModel):
    agent_id: str
    venv_path: str
    packages: list[str]
    built_in_seconds: float
    cached: bool


def _venv_dir(agent_id: str) -> str:
    return os.path.join(VENV_BASE, agent_id)


def _python(agent_id: str) -> str:
    return os.path.join(_venv_dir(agent_id), "bin", "python")


def _pip(agent_id: str) -> str:
    return os.path.join(_venv_dir(agent_id), "bin", "pip")


def _read_metadata(agent_id: str) -> dict:
    meta_path = os.path.join(_venv_dir(agent_id), METADATA_FILE)
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return {}


def _write_metadata(agent_id: str, meta: dict) -> None:
    meta_path = os.path.join(_venv_dir(agent_id), METADATA_FILE)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def _get_installed_packages(agent_id: str) -> list[str]:
    pip = _pip(agent_id)
    if not os.path.exists(pip):
        return []
    result = subprocess.run(
        [pip, "freeze"], capture_output=True, text=True, timeout=30,
    )
    return [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]


def _ensure_pip_cache():
    os.makedirs(PIP_CACHE, exist_ok=True)


def _create_venv(agent_id: str) -> str:
    """Create a bare venv for an agent. Returns venv_path."""
    venv_path = _venv_dir(agent_id)
    logger.info(f"Creating venv: agent={agent_id}, path={venv_path}")
    subprocess.run(
        [sys.executable, "-m", "venv", venv_path],
        check=True, capture_output=True, timeout=60,
    )
    return venv_path


def _pip_install(agent_id: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run pip install with shared cache."""
    _ensure_pip_cache()
    cmd = [_pip(agent_id), "install", "--cache-dir", PIP_CACHE] + args
    logger.info(f"pip install: agent={agent_id}, args={args}")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _build_venv(agent_id: str, requirements: str) -> tuple[str, float]:
    """Create venv + install requirements. Returns (venv_path, elapsed)."""
    start = time.time()
    venv_path = _create_venv(agent_id)

    if requirements.strip():
        req_file = os.path.join(venv_path, "requirements.txt")
        with open(req_file, "w") as f:
            f.write(requirements)
        result = _pip_install(agent_id, ["-r", req_file])
        if result.returncode != 0:
            shutil.rmtree(venv_path, ignore_errors=True)
            raise RuntimeError(f"pip install failed:\n{result.stderr}")

    elapsed = time.time() - start
    packages = _get_installed_packages(agent_id)

    _write_metadata(agent_id, {
        "agent_id": agent_id,
        "requirements": requirements,
        "packages": packages,
        "built_at": time.time(),
        "build_seconds": elapsed,
    })

    return venv_path, elapsed


def _venv_exists(agent_id: str) -> bool:
    return os.path.exists(_python(agent_id))


@app.post("/build", response_model=BuildResponse)
def build_venv(req: BuildRequest):
    """Build a venv for an agent. Returns cached only if requirements match last build."""
    incoming = (req.requirements or "").strip()
    if _venv_exists(req.agent_id):
        meta = _read_metadata(req.agent_id)
        stored = (meta.get("requirements") or "").strip()
        if stored == incoming:
            return BuildResponse(
                agent_id=req.agent_id,
                venv_path=_venv_dir(req.agent_id),
                packages=meta.get("packages", []),
                built_in_seconds=meta.get("build_seconds", 0),
                cached=True,
            )
        logger.info(
            "requirements changed for agent=%s — rebuilding venv (was cached for different deps)",
            req.agent_id,
        )
        shutil.rmtree(_venv_dir(req.agent_id), ignore_errors=True)

    try:
        venv_path, elapsed = _build_venv(req.agent_id, req.requirements)
    except Exception as e:
        logger.error(f"Build failed: agent={req.agent_id}, error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return BuildResponse(
        agent_id=req.agent_id,
        venv_path=venv_path,
        packages=_get_installed_packages(req.agent_id),
        built_in_seconds=elapsed,
        cached=False,
    )


@app.post("/update")
def update_venv(req: UpdateRequest):
    """Add or upgrade packages. Auto-creates venv if none exists."""
    if not _venv_exists(req.agent_id):
        logger.info(f"No venv for agent={req.agent_id}, auto-creating")
        try:
            _build_venv(req.agent_id, "")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create venv: {e}")

    args = []
    if req.upgrade:
        args.append("--upgrade")
    args.extend(req.packages)

    result = _pip_install(req.agent_id, args)
    if result.returncode != 0:
        logger.error(f"Update failed: agent={req.agent_id}, stderr={result.stderr}")
        raise HTTPException(status_code=500, detail=f"pip install failed:\n{result.stderr}")

    packages = _get_installed_packages(req.agent_id)
    meta = _read_metadata(req.agent_id)
    meta["packages"] = packages
    meta["updated_at"] = time.time()
    _write_metadata(req.agent_id, meta)

    return {
        "agent_id": req.agent_id,
        "venv_path": _venv_dir(req.agent_id),
        "packages": packages,
        "installed": req.packages,
        "upgraded": req.upgrade,
    }


@app.post("/rebuild", response_model=BuildResponse)
def rebuild_venv(req: BuildRequest):
    """Delete and recreate a venv from scratch."""
    venv_path = _venv_dir(req.agent_id)
    if os.path.exists(venv_path):
        shutil.rmtree(venv_path)
        logger.info(f"Deleted old venv: agent={req.agent_id}")

    try:
        venv_path, elapsed = _build_venv(req.agent_id, req.requirements)
    except Exception as e:
        logger.error(f"Rebuild failed: agent={req.agent_id}, error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return BuildResponse(
        agent_id=req.agent_id,
        venv_path=venv_path,
        packages=_get_installed_packages(req.agent_id),
        built_in_seconds=elapsed,
        cached=False,
    )


@app.get("/list")
def list_venvs():
    """List all agent venvs."""
    if not os.path.isdir(VENV_BASE):
        return {"venvs": []}
    venvs = []
    for name in sorted(os.listdir(VENV_BASE)):
        if name.startswith("."):
            continue
        venv_path = os.path.join(VENV_BASE, name)
        if not os.path.isdir(venv_path):
            continue
        meta = _read_metadata(name)
        venvs.append({
            "agent_id": name,
            "packages": meta.get("packages", []),
            "built_at": meta.get("built_at"),
            "updated_at": meta.get("updated_at"),
        })
    return {"venvs": venvs}


@app.delete("/delete/{agent_id}")
def delete_venv(agent_id: str):
    """Delete an agent's venv."""
    venv_path = _venv_dir(agent_id)
    if not os.path.exists(venv_path):
        raise HTTPException(status_code=404, detail=f"No venv for agent '{agent_id}'")
    shutil.rmtree(venv_path)
    logger.info(f"Deleted venv: agent={agent_id}")
    return {"deleted": agent_id}


@app.get("/health")
def health():
    return {"status": "ok"}
