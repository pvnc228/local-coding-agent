# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.8.2] - 2026-08-26

### 🛠 Fixed
- **Cross-platform CI green again**: repaired three platform-dependent/flaky test assumptions (POSIX process-group assertion vs `/bin/sh` fork-vs-exec behavior, spill-store path comparison against unresolved macOS/Windows temp dirs, MCP tasks lifecycle teardown racing the background worker) in PR #9; no production code changed.

### 🚀 Headline Features
- **Standalone Desktop AI Coding Harness (`local-agent desktop`) (R23)**:
  - High-precision engineering cockpit with native WebView2 / system browser fallback.
  - Interactive chat arena, delegated task queue, real-time hardware telemetry (`nvidia-smi`), and model management.
- **Tool Output Spill Store, Ripgrep & FS Observation Policy (R24)**:
  - `local_coding_agent.spill`: Session-scoped private storage (`.local_agent/spill/<session_id>/`) with automatic spilling for outputs exceeding 30KB / 1,000 lines.
  - `local_coding_agent.ripgrep`: Direct `rg --json` subprocess execution with full quoting safety and transparent pure-Python stdlib fallback.
  - `local_coding_agent.observation_policy`: Enforces strict **read-before-edit** invariant (`FS_NOT_OBSERVED`), preventing blind model hallucinations.
  - CLI subcommands: `local-agent spill-read <locator> [--offset N] [--limit N] [--json]` and `local-agent grep <query> [paths...] [--regex] [--json]`.
- **Generic LSP Stdio Code Intelligence Seam (`local_coding_agent.lsp`) (R25)**:
  - Standard JSON-RPC stdio language server protocol client with Content-Length framing.
  - Multi-language server router (`pyright`, `typescript-language-server`, `rust-analyzer`, `gopls`) and built-in AST/regex fallback engine.
  - Operations: `definition`, `references`, `hover`, `symbols`.
  - CLI subcommand: `local-agent lsp --operation {definition|references|hover|symbols} --file <path> [--json]`.
- **Persistent PTY Terminal Seam & Interactive Process Control (`local_coding_agent.terminal`) (R26)**:
  - Persistent background interactive shell / process wrapper with circular scrollback buffers and non-blocking I/O.
  - Cross-platform process tree termination and signal delivery (`SIGINT`/Ctrl+C, `SIGTERM`).
  - Model tool suite: `terminal_open`, `terminal_send`, `terminal_read`, `terminal_signal`, `terminal_list`, `terminal_close`.
- **Plan Mode Controller, Structured Questions & Dynamic Checklist (`local_coding_agent.plan_mode`) (R27)**:
  - Formal Plan Mode state machine enforcing read-only tool policy during exploration until human approval.
  - Interactive multiple-choice `ask_user_question` tool with default write-in and programmatic simulation.
  - Dynamic `todo_write` checklist tool with single-active task discipline and ASCII/Markdown rendering.
- **Event-Sourced Session Engine & SQLite FTS5 Search Index (`local_coding_agent.session_events`, `local_coding_agent.session_query`) (R28)**:
  - Append-only immutable typed session events enforcing the **Model-Visible ⟺ Logged** invariant.
  - Session forking (`fork_session`) for time-travel replay and causal branch pruning.
  - SQLite FTS5 search index with injection-proof sanitization across historical session records.
- **Universal Agent Client Protocol (ACP) Server (`local_coding_agent.acp_server`) (R29)**:
  - Standard JSON-RPC 2.0 ACP stdio server with auto-detecting `Content-Length` and `JSONL` codec framing.
  - Subcommand: `local-agent serve-acp [--workspace ...] [--profile ...] [--framing ...]`.
- **Continuable Background Subagents & External Agent Hook Bridges (`local_coding_agent.subagent`, `local_coding_agent.hooks`) (R30)**:
  - In-process continuable subagent worker loops with isolated `TaskEnvelope` sandboxes and structured mailboxes.
  - External lifecycle hook bridges with full wire-protocol adapters for Claude Code and OpenAI Codex.

### 🔍 Field Insights (best-practice adoption pass, pattern-level only — source repo is AGPL-3.0)
- **Background Task Queue in the Desktop Harness**: `POST /api/tasks` / `GET /api/tasks` / `POST /api/tasks/cancel`, sequential daemon worker (0.3s poll, oldest-first) mirroring `_handle_delegate` construction, cancel Events passed into the Controller, records persisted at `<workspace>/.local_agent_tasks.json` (cap 100, stale-`running` recovery on restart), injectable `controller_factory` test seam; UI panel with live status badges and Apply/Rollback reusing the existing `/api/apply` + `/api/rollback` endpoints.
- **Chat-history auto-compaction (`local_coding_agent/chat_compaction.py`)**: honest token estimation (CJK-aware, long-run pricing), whole-turn eviction that never drops system/last messages, `[context compacted: N ...]` notice insertion, sticky boundary (`next_sticky_boundary = sticky_boundary + dropped_count`) to preserve llama-server prefix cache. Module complete; CLI REPL/desktop wiring pending.
- **VRAM-fit context sizing (`local_coding_agent/vram_fit.py`)**: header-only GGUF parser (v2/v3, suffix-matched keys, early exit, never raises → all-None on corruption), `kv_bytes_per_token`, `max_fitting_ctx` (15% reserve, 256-alignment, native-context cap). Module complete; `_handle_model_load num_ctx:"auto"` wiring pending.
- **`run_tests` subprocess hardening (`repository_tools.py`)**: name-based secret-env scrubbing (`_SECRET_ENV_NAME_RE`: token/secret/password/api-key/aws_/azure_client), POSIX preexec (`setsid`, PR_SET_PDEATHSIG via ctypes, RLIMIT_CPU=timeout+30, RLIMIT_NOFILE=256; RLIMIT_AS deliberately skipped), Windows `CREATE_NO_WINDOW`.
- **OpenAI-compatible `/v1` router in the Desktop Harness**: `GET /v1/models` and `POST /v1/chat/completions` now proxy to whichever local backend serves the requested model id (resident llama-server first, then Ollama — both speak the OpenAI wire format natively, so routing is byte-level with streamed passthrough via `http.client`). Unknown models get a prescriptive 404 listing available ids.
- **`local-agent init-agent` subcommand**: emits agent-native wiring pointing at the harness `/v1` endpoint — Codex `[model_providers.local_agent]` TOML (`wire_api = "chat"`, `CODEX_HOME`-aware, duplicate-safe with exit code 1 on refusal) or generic `OPENAI_BASE_URL`/`OPENAI_API_KEY` env instructions. Preview by default, `--write` to persist, `--json` supported.

### ✅ Adoption pass completed (handoff — prior "🚧 In progress")
1. **Tool-call text parser module** (`local_coding_agent/tool_call_parser.py`): `extract_tool_calls(text, allowed_names=None) -> ToolCallExtraction(calls, remaining_text, formats_detected)`; emitters: qwen `<tool_call>` JSON, qwen XML `<function=NAME><parameter=K>` (plus `<parameter name="K">`), llama `<|python_tag|>{json}`, mistral `[TOOL_CALLS] [...]`; promotes only allowlisted names, truncated/invalid tails left in text, **never raises** (empty `[TOOL_CALLS]` array guarded).
2. **Parser → controller integration**: `controller/_controller.py` run-loop falls back to `extract_tool_calls(content, allowed_names=...)` when `_decode_content_tool_call` returns None; promotes to `message.tool_calls` + `text_tool_call_promoted` audit event (now carrying `formats`).
3. **Compaction → CLI REPL wiring**: `cli/_handlers2.py` multi-turn loop guards each model call with `fit_history(messages, max(1024, profile.num_ctx-1024), sticky_boundary)`; sticky boundary persisted per session; `[context compacted]` notice on trigger.
4. **VRAM auto-fit → desktop model load wiring**: `desktop/server/_handlers.py` `_handle_model_load` accepts `num_ctx:"auto"` on the GGUF branch via `_auto_fit_llama_ctx` (`read_gguf_ctx_params` + free VRAM from `get_nvidia_gpu_telemetry()` → `max_fitting_ctx`, guarded on all-`None` metadata); explicit int behavior stays byte-identical.
5. Full suite green: `741 passed, 2 skipped` (<code>pytest tests/</code>).
- **Effective-context read-back (llama-server `/props`)**: after every llama-server launch the controller now queries `/props` and stores `llama_effective_ctx` on the server; when the served `n_ctx` differs from the requested `-c` value (llama.cpp silently clamps to the model's native context length), a `ctx_warning` is surfaced in `/api/model/load` responses and requested/effective ctx are exposed in `/api/status`. Reality over intent.
- **Tool-call argument healing**: small models frequently emit wrong-but-close argument names (`filepath` → `path`, `pattern` → `query`, `cmd` → `command`). The controller now repairs these against the tool schema (case/underscore-insensitive + explicit alias map) before execution instead of burning a retry turn; ambiguous or shadowed keys pass through untouched. Audited as `tool_arguments_healed`.
- **CLI parity fix**: `chat` subcommand now declares its own `--num-ctx` flag (previously only reachable as a root-level option before the subcommand).

---

## [0.8.1] - 2026-08-23

### 🛠 Fixed (end-user UX audit remediation — docs/UX_AUDIT.md)
- **Packaging**: replaced hardcoded `packages` list with setuptools auto-discovery (`[tool.setuptools.packages.find]`). Non-editable installs previously shipped without `cli/`, `controller/`, `acp_server/`, `benchmark/`, `hooks/`, `lsp/`, `terminal/`, breaking the console entry point with `ModuleNotFoundError` on first run.
- **Windows BOM resilience**: task files and patch files are decoded as `utf-8-sig`, so PowerShell 5.1 `Set-Content -Encoding UTF8` output is accepted.
- **Backend errors**: transport failures are now classified at the controller model boundary (`backend_offline` / `backend_error` instead of generic `model_error`), the CLI attaches an actionable hint ("Start it with `ollama serve`…"), and the classifier is shared between CLI and desktop instead of duplicated.
- **Monitor shows real data**: every delegation run (CLI `delegate`, MCP/service `delegate`, monitor `/api/delegate`) appends a slim record to `.local-run/stats.jsonl`; the dashboard aggregates this cross-process journal together with in-process stats. `/stats` no longer reports `"total": 0` forever.
- **Honest TPS**: the controller emits `eval_tokens` / `eval_duration_ns` on `model_response` audit events; `test-run` computes real tokens/sec from them instead of printing a hardcoded `85.0`.
- **Repo hygiene**: `testpaths = ["tests"]` configured so bare `pytest` from the repo root works; controller-failure tracebacks in `service.py` now go explicitly to stderr, never corrupting the JSON stream.

### ➕ Added
- **`--model` override** on root, `delegate`, and `chat`: delegate to any installed Ollama/llama.cpp model tag without registering a profile.
- **Environment defaults**: `LCA_PROFILE` (default profile is now the `qwen2.5-coder` coder model, not the 1.5B toy) and `LCA_WORKSPACE`.
- **Multi-turn chat REPL**: `local-agent chat --repl [--session-id ID]` persists conversations via the event-sourced `SessionLog`; resume a session by id.
- **Sessions front door**: `local-agent sessions list|show [id]` indexes JSONL session logs into the SQLite FTS5 engine and prints transcripts.
- **Proposal persistence**: accepted/candidate patches from `local-agent delegate` are written to `.local-run/proposals/<task_id>.patch` and referenced via `patch_file` in the JSON output.
- **Semantic linter pre-gate wired (R18)**: syntax-broken patches are now rejected before acceptance/apply inside `Controller.run` (with a `SEMANTIC_LINT_FAILED` prescription fed back to the model for self-repair) and inside `DelegationService.apply`.

### 🔧 Changed
- Distinct default ports: `monitor` keeps 8765, web workbench `ui/app` defaults to 8766, desktop app to 8767 — no more collisions when running side by side.
- English-only CLI help strings (the Russian `--apply` help was localized).
- Docs truthfulness pass: ROADMAP R17/R24 marked as standalone experimental utilities (not wired into the delegated loop), R25/R26 labeled human-facing CLI seams (not agent tool suites), R20 MCP progress notifications claim corrected, README gateway diagram version removed, uninstall/cleanup path documented.

### 🔬 Field Insights
- **Classify at the boundary you own**: the controller already normalized all model failures into `model_error`; pushing backend classification one layer down (into the controller's model-boundary handler) fixed three consumers (CLI, service, desktop) at once instead of string-matching WinError text upstream.
- **Cross-process telemetry needs a file**: in-memory stat singletons can't be seen by a separately launched monitor process; a 12-line append-only JSONL journal plus replay-on-read closed the gap without any daemon or socket protocol.

---

## [0.8.0] - 2026-08-22

### 🚀 Headline Features
- **Four Interaction Modes (chat / build / plan / hybrid) (R31)**:
  - `local_coding_agent.mode_router`: deterministic classifier (`classify_fast`) plus hybrid router (`classify_mode`) that consults a small local model in an isolated context every N requests, falling back to deterministic heuristics on failure or uncertainty.
  - Desktop harness (`local-agent desktop`) gains a Chat / Build / Plan / Auto selector: `chat` → plain completion, `build` → Controller tool-loop (patch + external test evidence), `plan` → read-only exploration producing a `PlanArtifact` (goal/steps/risks/files_to_modify), `hybrid` → auto-routing across the three.
  - New CLI subcommand `local-agent chat "<prompt>" [--mode chat|build|plan|hybrid] [--json]` for full CLI parity.
  - `build_mode_router` factory wires a real small-model classifier (`qwen2.5-1.5b` / `ling-3.0-tiny-q6k`) into the hybrid path across desktop and CLI.

### 🔬 Real-World Dogfooding & Field Insights (Agent Field Report)
- **Small-model mode routing is cheap and reliable**: a 1.5B model returns a single `chat|build|plan` token with near-zero latency, so periodic re-classification (every 3 requests) adds negligible overhead while keeping deterministic fallback for hard or ambiguous prompts.
- **Isolated context is the key**: the router sees only the recent user prompts (no workspace or task context), which prevents the small model from "helping" and keeps classification fast and scoped.

---

## [0.7.0] - 2026-08-19

### 🚀 Headline Features
- **Dynamic Context Compaction & Harness State Machine (R14)**:
  - Transitioned controller loop from passive conversational chat log accumulation to an active state machine (`HarnessState` & `ContextAssembler`).
  - Reconstructs clean, stateless context envelopes per turn, eliminating dialogue confusion on small models.
  - Automatic eviction of older tool-exchange blocks preserving `assistant(tool_calls)` ↔ `role:tool` pairing.
  - Diff residue elimination purging failed diff attempts and syntax errors.
- **AST-Guided Context Compaction & Skeletonization (`local_coding_agent.ast_compactor`) (R17)**:
  - Python AST pre-processor collapsing non-target function and class bodies down to signatures and docstrings (`...`).
  - Slashes context footprint by 60–85%, reducing latency and memory pressure on 1B–4B models.
  - CLI subcommand: `local-agent skeletonize <file> --symbol <name> [--json]`.
- **Semantic Linter & Fast Pre-Test Prescriptions (`local_coding_agent.semantic_linter`) (R18)**:
  - Sub-50ms static pre-gates executing before slow test runners (`ast.parse`, `compile`, in-memory patch testing).
  - Translates syntax errors directly into deterministic pinpointed prescriptions.
  - CLI subcommand: `local-agent lint-patch --patch-file <file> [--json]`.
- **Speculative Multi-Drafting & Model Racing Engine (`local_coding_agent.speculative_racing`) (R19)**:
  - Concurrent speculative dispatch of candidate drafts across worker pool.
  - First-pass winner acceptance with instant cancellation of competing racers.
  - CLI option: `local-agent delegate --speculative-drafts 2`.
- **Streaming Progress & Token Telemetry (R20)**:
  - Real-time event stream and telemetry over SSE `/api/events`.
- **Self-Healing Environment & Auto-Remediation (R21)**:
  - 1-click self-healing wizard via `local-agent doctor --fix`.
  - Automatically configures missing MCP servers and exports Agent Skills.
- **Standalone Web Workbench & Coding Arena (`local-agent ui` / `local-agent app`) (R22)**:
  - Zero-dependency embedded web server on port 8765.
  - Interactive browser workbench (`/workbench`) for prompt testing, task delegation, diff visualization, and mediated apply without host IDE dependencies.

---

## [0.6.0] - 2026-08-19

### 🚀 Headline Features
- **Multi-Dimensional Capability Ladder (`local_coding_agent.capability`) (R15)**:
  - 5-tier difficulty taxonomy: Tier 0 (Syntax & Typo Repair), Tier 1 (Atomic Pure Functions), Tier 2 (Single-File Multi-Hunk), Tier 3 (Cross-File Invariants), Tier 4 (Algorithmic & Strict Constraints).
  - Adaptive Early-Exit ladder benchmark (`local-agent benchmark --ladder`).
  - Formal `CapabilityVector` tracking 95% Wilson confidence intervals, tokens/second generation speed, and maximum digestible chunk size (`granularity_tolerance`).
- **MCP Capability Discovery & Pre-Flight Gatekeeper (R16)**:
  - Exposes official MCP resource `model://profile` providing host agents (Codex, Claude, Antigravity) with dynamic insight into local model intelligence tiers and latest benchmark scores.
  - Pre-flight complexity gatekeeper rejecting out-of-tier task envelopes with `CAPABILITY_OVERLOAD` and pinpointed decomposition advice.
- **Multi-Backend Resilience (`llama-server` / OpenAI API)**:
  - Graceful fallback in `ModelMemoryManager.snapshot()` when VRAM introspection is not exposed by OpenAI-compatible backends.
  - Controller and tool context headroom optimizations (128 KB limit support).
  - Human-friendly ASCII capability ladder rendering in console CLI (`local-agent benchmark --ladder`).

### 🔬 Real-World Dogfooding & Field Insights (Agent Field Report)
- **Model Hardware & Hugging Face Specifications**:
  - **Model**: [`inclusionAI/Ling-3.0-tiny`](https://huggingface.co/inclusionAI/Ling-3.0-tiny) (Ant Group / inclusionAI).
  - **Architecture**: BailingMoeV3 hybrid MoE with hybrid linear-attention (3:1 alternating stack of KDA: Kimi Delta Attention and MLA: Multi-Head Latent Attention).
  - **Parameters**: 7.9B total parameters, with only **~1.3B–1.4B active parameters per token** (128 routed experts, 8 active + 1 shared).
  - **Format & Runtime**: `Ling-3.0-tiny-Q6_K.gguf` (6.5 GB) served locally via `llama-server` daemon.
- **Key Empirical Insights**:
  1. **Ultra-Fast Local Generation**: Because only ~1.3B parameters are activated per token, the model sustains **118.8 tokens/sec** on local hardware, completing atomic subtasks in 2–3 seconds without cloud latency or API billing.
  2. **The Context Window Invariant**: Small models fail when dumped with wide context (>300 lines of source code triggers context limits). Restricting tasks to strict 1-file / 1-function envelopes with character-exact `SEARCH/REPLACE` blocks yielded **100% first-turn success**.
  3. **SEARCH/REPLACE vs. Unified Diff**: MoE models struggle with line-offset arithmetic in raw `@@ -x,y +x,y @@` unified diffs, but generate structured JSON `edits` (`file`, `search`, `replace`) with surgical accuracy. The controller's internal translation to `git apply --check` eliminates all friction.
  4. **Verified Intelligence Ceiling**: The capability ladder proved `Ling-3.0-tiny` is a solid **Tier 2 (Single-File Multi-Hunk)** engine (100% Tier 0, 100% Tier 2 single-file edits, 95% on 20-case baseline benchmark), while multi-file coordination (Tier 3) requires decomposition by the host agent.
  5. **Zero-Distillation Mediated Apply**: Automatic test verification and git auto-rollback gave 100% confidence during development — zero regressions across the 290-test test suite.

---

## [0.5.1] - 2026-08-18



### 🛡️ Resilience & Validation
- **Zero-Context Unified Diff Support**:
  - Added `--unidiff-zero` flag to `_run_git_apply` in `local_coding_agent.validators`.
  - Enables seamless `git apply` execution for compact atomic diffs generated without standard 3-line context windows.
- **Workflow & Invariants Synchronization**:
  - Updated `AGENTS.md` automated agent setup instructions and core invariant guidelines.
- **Repository Hygiene**:
  - Ignored experimental and scratch benchmarking run directories (`tests_experiment/`, `.local-run/`).

---

## [0.5.0] - 2026-08-18

### 🚀 Headline Features
- **Universal Agent Skill (`skills/local-coding-agent/SKILL.md`)**:
  - Full support for any AI coding harness (Claude Code, Cursor, Windsurf, Roo Code, ChatGPT Codex, Google Antigravity, OpenCode).
  - Built-in multi-agent installer: `local-agent init-skill --write`.
  - Comprehensive delegation decision matrix, envelope construction blueprint, and model tier recommendations.
- **100% CLI-First Console Parity ("Fool-Proof" Control)**:
  - First-class CLI subcommands with `--json` output and standard return codes for every feature:
    - `delegate` / `run`: Run atomic delegation directly via console.
    - `decompose` / `atomize`: Preflight and decompose wide task envelopes.
    - `profiles`: Query and inspect registered model profiles with Ollama availability checks.
    - `memory`: Manage Ollama VRAM, unload models, and enforce memory limits.
    - `calibrate`: Derive worker pool capacity for a given VRAM budget.
    - `apply`: Safely apply patches to workspace with test validation and auto-rollback.
    - `init-skill`: Export/install Agent Skills to agent directories.
- **Multi-Runtime Support: Native Ollama & llama.cpp (`llama-server`)**:
  - Direct support for `llama-server` and any OpenAI-compatible runtime (`/v1/chat/completions`) with precise timing extraction (`prompt_ms`, `predicted_ms`).
  - Runtime-agnostic profile dispatch via `OpenAICompatibleClient`.

### 🛡️ Safety & Architecture
- **CLI-First Invariant**: Formalized architectural rule that all future features must maintain 100% CLI parity with automated tests.
- **Verified Test Suite**: Full cross-platform test suite verifying CLI subcommands, skill installers, worker pools, and memory management.


---

## [0.4.0] - 2026-08-15

### Added
- **Pinpointed Prescriptions Engine (`local_coding_agent.prescriptions`)**:
  - Deterministic in-context diagnostic translation for small models (2B–4B).
  - Zero Distillation Guarantee: Rule-based translation without leaking host LLM reasoning.
- **Multi-Client MCP Configuration Generator (`init-mcp`)**:
  - Auto-detection and multi-client configuration merger for Claude, Cursor, Windsurf, Cline, Antigravity, OpenCode, Codex.
- **System Diagnostic Wizard (`doctor`)**:
  - Automated environment checks for Ollama API, Git CLI, RAM/VRAM, and model catalog.
- **Real-Time HTTP Monitoring & Dashboard (`monitor`)**:
  - Web dashboard showing worker load, queue latency, and live tokens per second.
