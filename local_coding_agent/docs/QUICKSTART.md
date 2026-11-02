<!-- help: section="Getting started" order=1 -->
# Quickstart Guide

Get up and running with **Local Coding Agent** in under 60 seconds.

---

## 1. Prerequisites

- **Python 3.10+** (Python 3.10, 3.11, 3.12, or 3.13)
- **Git** (available in your system `PATH`)
- **Ollama** running locally: [ollama.ai](https://ollama.ai) (default port `11434`)

---

## ⚡ 1-Prompt Setup for AI Coding Agents

If you are using an AI coding agent (**Claude Code / Desktop**, **ChatGPT Desktop / Codex CLI**, **Antigravity Desktop / agy CLI**, **OpenCode Desktop / CLI**, **Cline Desktop / CLI**, **Cursor**, or **Windsurf**), simply copy-paste this instruction directly to your agent:

```text
Install and configure https://github.com/pvnc228/local-coding-agent:
1. Run `pip install -e .[mcp]` (or `pip install local-coding-agent[mcp]`)
2. Run `python -m local_coding_agent doctor` to check Ollama status
3. Run `python -m local_coding_agent init-mcp --auto --write` to register the MCP server in my IDE
4. Run `python -m local_coding_agent test-run --mock` to verify the execution sandbox
```

---

## 2. Manual Installation

You can install Local Coding Agent directly via `pipx` or `pip`:

```bash
# Recommended with pipx:
pipx install local-coding-agent[mcp]

# Or with standard pip:
pip install local-coding-agent[mcp]
```


Or clone and install in development mode:

```bash
git clone https://github.com/pvnc228/local-coding-agent.git
cd local-coding-agent
pip install -e .[mcp]
```

---

## 3. Verify Environment (`doctor`)

Run the automated diagnostic wizard to verify your Ollama connection, RAM/VRAM, and model availability:

```bash
local-agent doctor
```

Output example:
```text
============================================================
  Local Coding Agent — System Diagnostic Wizard
============================================================
[OK]   Python Runtime: Python 3.12.0 (Windows / Linux / macOS)
[OK]   Git Executable: git version 2.47.1
[OK]   Host Memory: RAM: 32 GB total (20 GB available)
[OK]   Ollama API: Connected to http://127.0.0.1:11434 (latency: 45ms, 4 models)

Installed vs Recommended Models:
  • qwen3-8b-q6k:latest (Installed)
  • qwen3.8-27b-q4:latest (Installed)
============================================================
  Overall Status: READY (All critical checks passed)
============================================================
```

---

## 4. Run an End-to-End Smoke Test (`test-run`)

Verify end-to-end task execution in an isolated sandbox:

```bash
# Using live Ollama model:
local-agent test-run --profile qwen2.5-coder

# Or using the built-in deterministic mock runner:
local-agent test-run --mock
```

---

## 5. Connect to Your AI Editor (Desktop & CLI)

Integrate Local Coding Agent as a standard MCP server in one click:

```bash
# Auto-detect IDE in current workspace and system:
local-agent init-mcp --auto --write

# Or configure a specific client:
local-agent init-mcp --claude --write       # Claude Desktop & Claude Code
local-agent init-mcp --chatgpt --write      # ChatGPT Desktop & Codex CLI
local-agent init-mcp --antigravity --write  # Google Antigravity Desktop & agy CLI
local-agent init-mcp --opencode --write     # OpenCode Desktop & OpenCode CLI
local-agent init-mcp --cline --write        # Cline Desktop & CLI / VS Code
local-agent init-mcp --cursor --write       # Cursor Composer
local-agent init-mcp --windsurf --write     # Windsurf Flow / Cascade
```


---

## 6. Live Monitoring Dashboard

Start the built-in real-time HTTP metrics and monitoring dashboard:

```bash
local-agent monitor --port 8765
```

Open your browser at `http://127.0.0.1:8765/dashboard` to inspect:
- Active delegations & queued tasks
- Real-time TPS (Tokens Per Second)
- Latency percentile distributions
- Validation report audit log
