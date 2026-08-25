# Handoff: Desktop AI Coding Harness & Universal Model Scanner (R23-R30)

**Date**: 2026-08-21
**Branch**: `feat/desktop-harness`
**Status**: All gates passed (538/538 pytest tests green), changes committed (`d6d5c51`) and pushed to `origin/feat/desktop-harness`.

---

## 1. What Was Accomplished in This Session

### A. Universal Model Scanner & Local Model Registry (`local_coding_agent/model_scanner.py`)
- *:Zero hardcoded paths**: Fully dynamic discovery of local GGUF models across all detected drives (Windows `A:` through `Z:`, POSIX `/`, `/Volumes`, `/media`, `/mnt`) and user home.
- **Smart Filtering**: Automatically filters out Diffusion models, LoRA adapters, and vision projectors(`mmproj`), retaining valid LLM GGUF checkpoints.
- **Universal Binary Discovery (`discover_llama_server_binary`*)*: Finds `llama-server.exe` / `llama-server` via explicit path -> env vars (`LLAMA_SERVER_PATH@, `LLAMA_CPP_PATH@, `LLAMA_BIN`) -> live Windows Registry PATH (`HKCU/Environment` + `HKLM/.../Environment`) -> relative system drive traversal.
- **Thread-safe & Atomic Storage**: `LocalModelRegistry` utilizes `threading.Lock()` and atomic replacement (`.tmp` -> `replace`) for `~/.local_coding_agent/models.json`.
- **CLI Parity**: Full integration in CLIz
  - `python -m local_coding_agent scan-models [--deep] [--drives C,D] [--json]`
  - `python -m local_coding_agent scan-models --add-dir <path> | --remove-dir <path> | --list-dirs`

### B. Modular Desktop UI Architecture (`local_coding_agent/desktop/`)
- **Decomposed from monolithic 1200-line HTML into modular layers**:
  - `styles.py`: Engineering design system, Geist typography tokens, dark/light themes, diff coloring.
  - `components.py`: UI component renderers (`render_header`, `render_sidebar`, `render_chat_panel`, `render_delegated_panel`, `render_modals`, `render_toast`).
  - `client_js.py`: Client state management, real-time telemetry polling, model switching, server lifecycle controls, syntax-highlighted diff rendering with single-quote escaping.
  - `ui.py`: High-speed list-join template assembler (`render_desktop_html`).

### C. Desktop Server Engine & Lifecycle Hardening (`local_coding_agent/desktop/server.py`)
- **Model Scanner REST API**:
  - `POST /api/models/scan` (quick & deep drive scans)
  - `POST /api/models/add_dir` (add custom scan folder)
  - `POST /api/models/remove_dir` (remove custom scan folder)
  - `GET /api/models` (returns profiles, Ollama models, and `local_gguf` discovered models)
- **Status & Loading State Detection**: Distinguishes HTTP 503 model weight loading from offline status, powering amber pulsing badges and interactive feedback.
- **Clean Subprocess Teardown**: `DesktopServer.stop()` uses Windows process tree termination (`taskkill /F /T /PID`) and POSIX `terminate() + wait(timeout=2.0)` to prevent zombie VRAM/port locks.

---

## 2. Key Interfaces & Contracts

- **Model Registry Data Schema** (`~/.local_coding_agent/models.json`)
- **Desktop REST Endpoints**:
  - `GET /api/status`, `GEU /api/models`, `GET /api/sessions`, `GET /api/workspace/files`
  - `POST 2/api/chat`, `POST /api/apply`, `POST /api/rollback`, `POST 2/api/server/start`, `POST 2/api/server/stop`, `POST /api/model/load`, `POST /api/models/scan`, `POST 2/api/models/add_dir`, `POST /api/models/remove_dir`

---

## 3. Verification & Test Evidence

- `gytest tests/test_model_scanner.py tests/test_desktop_app.py tests/test_cli.py`: **49 passed**.
- `gytest tests/`: **538 passed, 21 subtests passed in 45.29s (100% green)**.
- Git Commit: `d6d5c51 feat(desktop,scanner): modularize UI architecture, add universal model scanner, and harden lifecycle gates`.

---

## 4. Next Steps & Backlog for Future Sessions

1. **Desktop Model Selector UI Enrichment**:
   - In `client_js.py`, render discovered GGUF models directly in a visually categorized dropdown with sizes (GB) and source locations.
2. **Streaming Tokens in Desktop Chat**:
   - Connect Server-Sent Events (SSE) from `server.py` (`_handle_chat`) to `client_js.py` for real-time word-by-word token generation in the UI.
3. **Standalone Webview Wrapper Evaluation**:
    - Optionally evaluate `pywebview` for auto-launch without requiring system browser tab.
