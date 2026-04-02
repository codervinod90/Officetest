# MicroVM — Agent & MCP tool tester

Streamlit UI to **generate** agents and MCP tools (LLM-assisted), **run** agents on a worker with **per-agent virtualenvs**, and call **MCP** tools from the model at test time. Designed for local Kubernetes (e.g. Docker Desktop) with optional paths for full local processes or AWS EKS.

**Where you test matters:** Development may happen on **macOS** while **QA runs on Windows** (Docker Desktop + WSL). Those are **different** clusters and node filesystems — issues like **venv `pip` / `.so` mmap** or **Secret drift** often appear only on Windows. After changing manifests, **apply and restart on the same machine you use for testing**, and treat **Mac “works here”** and **Windows “fails there”** as **two separate `kubectl` contexts**, not one shared environment.

---

## What runs where

| Component | Role |
|-----------|------|
| **agent-app** | Streamlit UI (`app.py`), reads/writes agents under `ARTIFACTS_DIR`, tools under `TOOLS_DIR` |
| **agent-worker** | Executes `main.py` with `run(user_input)`; uses `/venvs/<agent_id>/bin/python` when `requirements.txt` is non-empty |
| **venv-builder** | Creates and updates per-agent venvs on shared storage (`/build`, `/rebuild`, `/update`) |
| **mcp-server** | Lists and runs MCP tools from `TOOLS_DIR` |

**Persistence (Kubernetes):** PVCs **`agents-cache`**, **`tools-cache`**, **`venv-cache`** keep data across pod restarts. The worker does not store agent source code; it receives files per request.

---

## Prerequisites

- **Docker** and **Kubernetes** (e.g. Docker Desktop with Kubernetes enabled), or another cluster with `kubectl` configured  
- **`kubectl`** pointing at your cluster  
- **LLM credentials** for the UI and for OpenAI+MCP agents on the worker (Azure OpenAI and/or OpenAI), supplied via Secret **`llm-api-keys`**  
- **Python 3.11+** (optional) if you run the app or tests on your machine outside the cluster  

---

## Step-by-step: deploy on local Kubernetes

### 1. Build container images

From the **repository root** (`MicroVM/`), build images that match the names in the manifests (`imagePullPolicy: Never` assumes images exist on the node):

```bash
docker build -t agent-app:latest -f Dockerfile .

docker build -t agent-worker:latest -f worker/Dockerfile worker/

docker build -t venv-builder:latest -f venv_builder/Dockerfile venv_builder/

docker build -t mcp-server:latest -f mcp_server/Dockerfile .
```

Use the same machine (or load images into the cluster) so the Kubernetes node can pull these tags.

### 2. Create storage (PVCs)

The repo includes **hostPath-based** PVs for local dev (`/tmp/agents`, `/tmp/tools`, `/tmp/venvs`). Apply:

```bash
kubectl apply -f k8s/agents-storage.yaml
kubectl apply -f k8s/tools-storage.yaml
kubectl apply -f k8s/local-storage.yaml
```

For **AWS EKS** (or any cloud cluster), replace these with a suitable **StorageClass** and PVCs (e.g. EBS or EFS). See `k8s/efs-storage.yaml` as a starting point for shared filesystems when multiple nodes need the same volume.

### 3. Create the LLM Secret

Create a Secret named **`llm-api-keys`** in the same namespace as the app. You can:

- Edit a local manifest (do **not** commit real keys to git), then:

  ```bash
  kubectl apply -f k8s/openai-secret.yaml
  ```

- Or create it imperatively:

  ```bash
  kubectl create secret generic llm-api-keys \
    --from-literal=LLM_PROVIDER=azure \
    --from-literal=AZURE_OPENAI_ENDPOINT='https://YOUR_RESOURCE.openai.azure.com/openai/v1/' \
    --from-literal=AZURE_OPENAI_API_KEY='YOUR_KEY' \
    --from-literal=AZURE_OPENAI_DEPLOYMENT='YOUR_DEPLOYMENT' \
    --from-literal=OPENAI_API_KEY='YOUR_OPTIONAL_OPENAI_KEY'
  ```

The **agent-app** and **agent-worker** use `envFrom` this Secret so Streamlit and agent5-style agents can call the LLM.

### 4. Deploy services

Apply workloads (order below is safe; you can also `kubectl apply -f k8s/` once storage and the Secret exist):

```bash
kubectl apply -f k8s/mcp-server.yaml
kubectl apply -f k8s/venv-builder.yaml
kubectl apply -f k8s/worker.yaml
kubectl apply -f k8s/app.yaml
```

Wait until pods are ready:

```bash
kubectl get pods -w
```

### 5. Open the Streamlit UI

**Option A — port-forward**

```bash
kubectl port-forward svc/agent-app 8501:8501
```

Open **http://127.0.0.1:8501**

**Option B — NodePort** (if your `app.yaml` Service uses `NodePort`)

Use the printed node IP and port (e.g. `30501` as in the sample manifest).

### 6. Use the UI

1. **Generate** — Draft MCP tools and OpenAI+MCP agents with the LLM; **Write to disk** saves under the configured directories (`/agents` and `/tools` in the cluster).  
2. **Test** — Select an agent, enter input, **Run Test**. The app calls **venv-builder** automatically when `requirements.txt` is present, then the **worker** runs the agent. **Tool call log** summarizes MCP usage when the agent emits the agent5 `[MCP trace]` line. (No separate Dependencies / venv management UI for now.)

After changing **application code** in the image, rebuild **`agent-app`** and restart:

```bash
docker build -t agent-app:latest -f Dockerfile .
kubectl rollout restart deployment/agent-app
```

Do the same for **worker**, **venv-builder**, or **mcp-server** when their code or dependencies change.

---

## Environment variables (reference)

| Variable | Typical local K8s value | Meaning |
|----------|-------------------------|---------|
| `WORKER_URL` | `http://agent-worker:8000` | Worker execute API |
| `VENV_BUILDER_URL` | `http://venv-builder:8001` | Venv build API |
| `MCP_SERVER_URL` | `http://mcp-server:8002` | MCP REST API |
| `ARTIFACTS_DIR` | `/agents` | Agent folders (`main.py`, `meta.json`, `requirements.txt`) |
| `TOOLS_DIR` | `/tools` | MCP tool folders (`tool.json`, `main.py`) |

---

## Tests

**Unit / default tests** (no live HTTP dependency unless you add them):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -v -m "not e2e"
```

**E2E HTTP tests** (worker, MCP, venv-builder must be reachable — port-forward or local processes):

```bash
export WORKER_URL=http://127.0.0.1:8000
export MCP_SERVER_URL=http://127.0.0.1:8002
export VENV_BUILDER_URL=http://127.0.0.1:8001
pytest tests/e2e -v
```

---

## Troubleshooting (short)

| Symptom | Things to check |
|---------|-------------------|
| Empty agent list or HTTP 400 on execute | PVC seeding: `agents-cache` populated; restart **agent-app** after fixing `k8s/app.yaml` init; see sidebar error in UI |
| `ModuleNotFoundError` (e.g. `httpx`) | `requirements.txt` present and non-empty for OpenAI agents; **venv-builder** `/build` vs cached venv; use **Rebuild venv**; worker image includes fallback deps for system Python in recent versions |
| MCP tools missing | **tools-cache** and **mcp-server** init; restart **mcp-server** |
| LLM errors in Generate | Secret **`llm-api-keys`** mounted on **agent-app**; correct `LLM_PROVIDER` and keys |
| **venv-builder** HTTP **500** on `/build`, logs show **Permission denied** on **`.../bin/pip`** | Often **Docker Desktop Windows** (or bind mounts) where **`bin/pip` isn’t executable**. The service uses **`python -m pip`** instead of calling **`pip`** directly, and puts the wheel cache under **`/tmp/pip-cache`** by default (override with env **`PIP_CACHE_DIR`**). Rebuild **venv-builder** after pulling this change. |
| **venv-builder** **500**, **Permission denied** writing under **`/venvs`** | **EFS/NFS / root-squash**, **UID/fsGroup**, or an old PVC owned by **root**. Use **`fsGroup` / non-root** in `k8s/venv-builder.yaml`, or fix ownership / recreate **`venv-cache`**. |
| **`failed to map segment from shared object`** (`pydantic_core` `.so`) on **Docker Desktop Windows** | The node’s **`/venvs` mount** often can’t **mmap** native libs. **`agent-worker`** supports **`WORKER_VENV_SCRATCH=/venv-scratch`** plus an **`emptyDir`** mount (see `k8s/worker.yaml`): it **copies** the venv to scratch before running. On Linux cloud where `/venvs` is fine, remove **`WORKER_VENV_SCRATCH`** and the **`venv-scratch`** volume/mount to avoid the extra copy. |

---

## AWS EKS (high level)

- Push images to **ECR**; change manifests to use those URIs and an appropriate `imagePullPolicy`.  
- Replace hostPath PVs with **EBS** and/or **EFS** CSI drivers; validate **access modes** vs **replica count** (multiple **agent-worker** replicas sharing one **RWO** volume can block scheduling — prefer **EFS (RWX)** or fewer replicas).  
- Expose the UI with **Ingress + TLS** (e.g. ALB) and manage secrets with **Secrets Manager / External Secrets** and **IRSA** where applicable.

---

## License / security

Treat API keys and cluster Secrets as confidential. Rotate any keys that were ever committed to version control.
