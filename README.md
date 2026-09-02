# Local Coding Agent

Local AI coding workspace for people who want practical development help on their own machine.

[![CI](https://github.com/pvnc228/local-coding-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/pvnc228/local-coding-agent/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Choose how you want to use it

| Package | Best for | What you get | Download |
| --- | --- | --- | --- |
| CLI | Terminal workflows, scripts, CI, and AI-agent integrations | The `local-agent` command, MCP server, Agent Skill, diagnostics, sessions, model tools, and task delegation | [CLI releases](https://github.com/pvnc228/local-coding-agent/releases) |
| Harness for Windows | A ready-to-use desktop coding workspace | The full Harness together with every CLI capability, model tools, sessions, and task delegation | [Windows releases](https://github.com/pvnc228/local-coding-agent/releases) |

The full Windows download already includes the CLI and delegation capabilities used by the application. You do not need to install Python or the CLI separately to use the complete desktop experience. Choose the CLI when you prefer the terminal or want to connect another AI coding environment; choose the Harness when you want everything in one application.

Local inference remains local. Install [Ollama](https://ollama.com/) or run a compatible local model server before requesting live model responses.

## Desktop Harness

Open the Harness, choose a workspace, select a local model, and start working. The desktop application brings the complete local coding workflow into one focused interface:

- Interactive Chat for questions, explanations, and everyday coding work.
- Chat, Build, Plan, and Auto modes for different kinds of requests.
- Delegated Tasks for handing a focused coding change to a local model.
- Workspace sessions so you can return to previous conversations and tasks.
- Model discovery, model profiles, context settings, hardware telemetry, and diagnostics.
- Clear status messages when the local backend is ready, unavailable, or needs attention.
- Reviewable code changes and checks before changes are applied.

The screenshots below show the application loading `qwen2.5-coder:7b` and returning a real response about the selected workspace.

<p align="center">
  <img src="docs/screenshots/harness-interactive-chat.png" alt="Interactive Chat with a local model response" width="49%">
  <img src="docs/screenshots/harness-working-inference.png" alt="Loaded local model and Harness response" width="49%">
</p>

## CLI installation

Requirements:

- Python 3.10 or newer.
- Git on `PATH` for workspace operations.
- Ollama or an OpenAI-compatible local model server for live inference.

Install the CLI package:

```bash
pipx install "local-coding-agent[mcp]"
```

Or install from a checkout:

```bash
git clone https://github.com/pvnc228/local-coding-agent.git
cd local-coding-agent
python -m pip install -e ".[mcp]"
```

Check the installation:

```bash
local-agent doctor
local-agent test-run --mock
```

Add `--json` where supported when the output will be consumed by another tool.

## Connect another AI coding agent

Local Coding Agent can provide its capabilities to other AI coding environments through MCP or an Agent Skill:

```bash
local-agent init-mcp --auto --write
local-agent init-skill --write
```

Start the MCP server manually when a client needs an explicit command:

```bash
local-agent serve-mcp --workspace . --enable-tasks
```

See [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md) for client configuration examples and [skills/local-coding-agent/SKILL.md](skills/local-coding-agent/SKILL.md) for the reusable Agent Skill.

## A typical coding request

1. Choose a workspace and a local model.
2. Ask a question or describe the change you want.
3. Inspect the suggested code change in the application.
4. Run the relevant checks and apply the change when it looks right.

The application keeps the workflow understandable: you can see what is being changed, which checks are being run, and whether the local backend is available. Commits, tags, pushes, and publishing remain under your control.

## CLI commands

| Command | Purpose |
| --- | --- |
| `local-agent delegate` | Give a focused coding task to a local model. |
| `local-agent decompose` | Break a larger request into smaller coding tasks. |
| `local-agent apply` | Apply a reviewed change and run its checks. |
| `local-agent chat` | Run a prompt or a persistent chat session. |
| `local-agent sessions` | List or inspect saved sessions. |
| `local-agent profiles` | Inspect configured model profiles. |
| `local-agent memory` | Inspect model memory and VRAM settings. |
| `local-agent calibrate` | Calculate safe worker bounds for a VRAM budget. |
| `local-agent benchmark` | Run the local coding benchmark. |
| `local-agent doctor` | Diagnose Python, Git, Ollama, RAM, VRAM, and models. |
| `local-agent monitor` | Serve the local metrics dashboard. |
| `local-agent test-run --mock` | Run deterministic local workflow checks. |

Examples:

```bash
local-agent chat "Explain what context_manager.py does"
local-agent chat --repl --session-id refactor-session
local-agent delegate --task task.json --model qwen2.5-coder:7b --json
local-agent apply --patch-file patch.diff --workspace . --check "pytest tests/"
local-agent monitor --port 8765
```

Open `http://127.0.0.1:8765/dashboard` to view queue load, latency, tokens per second, and change records.

## Local models

Model profiles describe the provider, model, context window, generation limits, and keep-alive behavior. The Harness can discover installed Ollama models and local GGUF files, while the CLI exposes the same information for scripts and integrations.

```bash
local-agent profiles list --json
local-agent memory status --json
```

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [MCP integration](docs/MCP_INTEGRATION.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Architecture reference](docs/ARCHITECTURE.md)
- [Protocol reference](docs/PROTOCOL.md)
- [Benchmark methodology](docs/BENCHMARK.md)

## License

Local Coding Agent is distributed under the [MIT License](LICENSE).
