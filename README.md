# Agent Code Generator & Tester

Base app + Worker setup for testing runtime-generated agent code.

## Architecture

```
┌─────────────────┐         ┌─────────────────────┐
│  Base App       │  HTTP   │  Worker              │
│  (Streamlit)    │────────▶│  (FastAPI)           │
│  - Generate     │         │  - Execute agent code │
│  - UI           │◀────────│  - Subprocess +      │
│                 │  result │    cleanup           │
└─────────────────┘         └─────────────────────┘
```

## Setup

### 1. Create virtual environment (project root)

```bash
cd /path/to/MicroVM
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
# App dependencies
pip install -r requirements.txt

# Worker dependencies
pip install -r worker/requirements.txt
```

## Run

**Terminal 1 – Start Worker:**
```bash
cd /path/to/MicroVM
source .venv/bin/activate
uvicorn worker.main:app --host 0.0.0.0 --port 8000
```
> **Important:** After code changes, restart the worker (Ctrl+C, then run again).
> Verify: `curl http://localhost:8000/version` should show `{"version":"2.0","multifile":true}`.

**Terminal 2 – Start App:**
```bash
cd /path/to/MicroVM
source .venv/bin/activate
streamlit run app.py
```

Open **http://localhost:8501** for the UI.

## Configuration

| Env Var      | Default              | Description        |
|--------------|----------------------|--------------------|
| `WORKER_URL` | `http://localhost:8000` | Worker service URL |

For Kubernetes: set `WORKER_URL=http://agent-worker:8000` (or your worker service name).

## Flow

1. **Generate** – Click "Single File" or "Multi-File" in sidebar
2. **Test** – Enter user input, click "Run Test"
3. App sends to Worker:
   - Single file: `{ agent_code, user_input }`
   - Multi-file: `{ code_files: {path: content}, entry_point, user_input }`
4. Worker writes files (creating subdirs), executes, cleans up, returns output
5. App displays result

## Multi-file / Subfolder Support

When generated code has multiple files or folders:

```json
{
  "code_files": {
    "main.py": "from utils.helper import process\ndef run(user_input): ...",
    "utils/__init__.py": "",
    "utils/helper.py": "def process(text): ..."
  },
  "entry_point": "main.py",
  "user_input": "hello"
}
```

- `files`: dict of `path -> content` (paths can include subdirs, e.g. `src/agent.py`)
- `entry_point`: file that defines `run(user_input: str) -> str`
