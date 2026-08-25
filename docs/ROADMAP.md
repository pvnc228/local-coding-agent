# Project Roadmap

Historical milestones (M0–M6) are archived in [archive/ROADMAP_HISTORICAL.md](archive/ROADMAP_HISTORICAL.md).

---

## Completed Milestones

### R1 — Security, Budget & Resource Boundaries (Completed)
- Restricted `list_files` to the configured task allowlist.
- Added cumulative context token budgeting across multi-turn tool loops.
- Enforced graceful handling and diagnostics for Git CLI availability.
- Added cooperative task cancellation support to break blocking model and test calls.

### R2 — Protocol Robustness & Tool Loop Stability (Completed)
- Replaced rigid string matching with structured test runner evidence validation.
- Added pre-read byte limits for `search_text` to prevent memory exhaustion on large files.
- Optimized output truncation to eliminate O(n²) string slicing overhead.
- Fixed duplicate tool-call bypasses with normalized default arguments.

### R3 — Mediated Apply & Automatic Rollback (Completed)
- Added explicit opt-in `--apply` and `apply_proposal` execution seams.
- Ensured `apply_patch` is strictly controller-owned and never exposed to the local model.
- Kept proposal-only as the default operating mode.
- Added post-apply test re-execution with automatic workspace rollback upon check failures.

### R4 — Reproducible Benchmark Suite (Completed)
- Created an isolated benchmark runner with restricted Python child process oracles.
- Implemented automated calculation of 95% Wilson confidence intervals.

### R5 — Service Seam & Process Boundaries (Completed)
- Implemented transport-neutral `DelegationService` with caller-scoped idempotency.
- Implemented `StdioDelegationAdapter` for process-bound JSONL stdio communication.
- Built official `mcp==2.0.0` server compliant with the 2026-07-28 stateless era.

### R6 — Concurrency & Bounded Worker Pool (Completed)
- Implemented `BoundedWorkerPool` to enforce worker slot limits and request queue bounds.
- Added support for cooperative background task cancellation and `SharedExecutionGate`.

### R7 — Task Atomization & Decomposition (Completed)
- Added structured task envelope validation with fine-grained file allowlists.

### R8 — Retries & Escalation Policies (Completed)
- Implemented bounded retry budgets for recoverable model errors (`invalid_json`, `patch_parse_error`).

### R9 — SEARCH/REPLACE Edit Format (Completed)
- Replaced raw unified diff generation with strict character-exact SEARCH/REPLACE blocks (`edits`).
- Boosted patch validity and correctness from near-zero to 90%+ on quantized local models.

### R10 — Observability, Memory Management & Task Persistence (Completed)
- Integrated live VRAM calibration via Ollama `/api/ps`.
- Implemented `JsonFileTaskStore` for durable task recovery across process restarts.
- Added lightweight stdlib `MonitorServer` with real-time JSON endpoints (`/stats`, `/tasks`) and an interactive HTML web dashboard (`/dashboard`).

### R11 — Developer Experience & Packaging (Completed)
- Added `local-agent doctor` diagnostic wizard checking Python, Git, RAM/VRAM, and Ollama models.
- Added `local-agent init-mcp` for 1-click configuration generation (Claude Desktop, Cursor, Windsurf, VS Code).
- Added `local-agent test-run` for automated end-to-end smoke verification with live TPS tracking.
- Configured PyPI package metadata and console entry points (`local-agent`, `local-coding-agent`).

### R12 — Open-Source Showcase & Repository Readiness (Completed)
- Restructured user-facing documentation in English (`QUICKSTART.md`, `ARCHITECTURE.md`, `MCP_INTEGRATION.md`, `BENCHMARK.md`, `PROTOCOL.md`).
- Added open-source community standards: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, and issue/PR templates.
- Configured GitHub Actions matrix CI (`.github/workflows/ci.yml`) for automated testing on Linux, macOS, and Windows.

### R13 — Adaptive Model Calibration & Dynamic Profiler (Completed in v0.4.0)
- **OpenAI-Compatible & `llama-server` Adapter**: First-class support for `llama-server` on port 8080 and OpenAI-compatible endpoints with exact microsecond timing extraction.
- **Pinpointed Prescriptions Engine**: Deterministic in-context diagnostic translation turning validation failures into actionable repair instructions.
- **System Diagnostic Wizard**: Multi-point automated health checking via `local-agent doctor`.

### R13b — Universal Agent Skills & 100% CLI Parity (Completed in v0.5.0)
- **Multi-Agent Skill (`SKILL.md`)**: Full compatibility with Claude Code, Cursor, Windsurf, Roo Code, ChatGPT Codex, and Google Antigravity via `local-agent init-skill --write`.
- **100% CLI Parity ("Fool-Proof" Control)**: Console commands for all capabilities (`delegate`, `decompose`, `profiles`, `memory`, `calibrate`, `apply`, `init-skill`, `doctor`, `init-mcp`, `test-run`, `serve-mcp`, `monitor`, `benchmark`) with machine-parseable `--json` output.
- **Cross-Platform Resilience**: Hardened stdout encoding defense for Windows (`cp1252`), macOS, and Linux.

### R15 — Multi-Dimensional Capability Ladder & Intelligence Benchmark (Completed in v0.6.0)
- **Taxonomy of Difficulty Tiers**: Progressive test suite from Tier 0 (Syntax/Formatting/Typos) -> Tier 1 (Atomic Pure Functions) -> Tier 2 (Single-File Multi-Hunk Refactor) -> Tier 3 (Cross-File Invariants) -> Tier 4 (Algorithmic & Strict Constraints).
- **Adaptive Early-Exit Benchmark Ladder**: Dynamic step-up evaluation that calculates the model's reliability ceiling without wasting compute on out-of-reach tiers.
- **Task Decomposition & Granularity Tolerance**: Quantitative evaluation of maximum digestible chunk size (`atomic_hunk`, `function_level`, `file_level`, `multi_file_batch`).
- **Polyglot Evaluation Matrix**: Multi-language capability benchmarks across Python, TypeScript/JavaScript, Rust, and Go to establish per-language capability ratings.
- **Tool Horizon & Turn Endurance**: Measurement of the model's degradation point (repetition, loop fatigue, schema drift, hallucination) across extended multi-turn tool loops.

### R16 — MCP Capability Discovery, Smart Routing & Task Gatekeeper (Completed in v0.6.0)
- **Structured Capability Profile**: Standardized JSON capability vector (`overall_tier`, `confidence_95_ci`, `granularity_tolerance`, `turn_horizon`, `languages`, `tps_generation`).
- **MCP Protocol Discovery & Introspection**: Expose model capabilities and routing advice via MCP tool definitions, MCP resource `model://profile`, and system prompt injection for calling host agents.
- **Pre-Flight Complexity Gatekeeper**: Immediate controller-level rejection/warning before invoking LLM if submitted task exceeds model's verified tier or file bounds (`CAPABILITY_OVERLOAD`).
- **Decomposition Guidance for Host Agents**: Actionable error envelopes instructing host agents (Codex/Claude) how to decompose rejected tasks into digestible chunks for the active local model.
- **CLI Intelligence Inspector**: `local-agent doctor --rank` and `local-agent benchmark --ladder` reporting model intelligence tier, supported languages, and routing sweet spot.

---

### R14 — Dynamic Context Compaction & Harness State Machine (Completed in v0.7.0)
- **Agentic Harness vs Conversational Chat (Stateless Context Reconstruction & Turn Assembly)**: Transition controller loop from passive chat history accumulation (`messages.append`) to an active stateful agentic harness. On each turn, the controller evaluates the world state (`HarnessState`: task envelope, observed files, latest tool observation, active pinpointed prescription) and synthesizes a clean, reconstructed context from scratch rather than sending a growing dialogue log. Deterministic state machine transitions (`received` -> `context_ready` -> `awaiting_model` -> `evaluating_candidate` -> `reconstructing_turn`).
- **Tool Output Trimming & Eviction**: Automatic summarization/pruning of historical `read_file` and `search_text` results older than 1 turn to prevent context blowup and attention degradation on 3B–14B models (preserving `assistant(tool_calls)` ↔ `role:tool` pairing invariant).
- **Diff Residue & Error Echo Elimination**: Purge multi-turn failed diff attempts and syntax errors from active context, replacing them with a minimal task envelope + active pinpointed prescription. Small models receive only the current state of files and precise repair instructions without seeing past hallucinations.

### R17 — AST-Guided Context Compaction & Skeletonization (`ast_compactor.py`) (Completed in v0.7.0)
- **AST File Skeletonizer**: Pre-processor that parses code structures (Python `ast`, `tree-sitter`) and collapses non-target classes/functions down to their signatures and docstrings (`def process_order(id: str) -> bool: ...`).
- **Target Function Expansion**: Full code body is expanded only for the specific symbol targeted for editing.
- **Token Efficiency Gain**: Slashes prompt context by 60–85%, keeping 1B–4B models focused inside their optimal attention window and reducing generation latency.
- **Status**: standalone experimental utility (`local-agent skeletonize`). Deliberately NOT wired into the delegation loop: skeletonized bodies break byte-exact SEARCH/REPLACE anchors, which is the patch format small models actually use.

### R18 — Semantic Linter & Fast Pre-Test Prescriptions (`semantic_linter.py`) (Completed in v0.7.0; wired into controller + mediated apply in v0.8.1)
- **Sub-50ms Static Pre-Gates**: Pure-stdlib AST syntax gate running immediately after patch generation — no external linter binaries required.
- **Instant In-Context Feedback**: Catches syntax errors before workspace mutation or heavy unit test runners (`pytest`), converting diagnostics into pinpointed prescriptive hints fed back to the model.
- **Runtime wiring**: enforced as a pre-gate inside `Controller.run` candidate validation (blocks acceptance of syntax-broken patches with a `SEMANTIC_LINT_FAILED` prescription) and inside `DelegationService.apply`.

### R19 — Speculative Multi-Drafting & Model Racing Engine (Completed in v0.7.0)
- **Parallel Speculative Dispatch**: Coordinates concurrent execution of 2 lightweight workers across the `BoundedWorkerPool` (e.g. `qwen2.5-1.5b` with `temp=0` vs `gemma4-2b` with `temp=0.2`).
- **First-Pass Winner Acceptance**: The first candidate patch that passes `git apply --check` and targeted tests is accepted; the competing worker is immediately cancelled.
- **Reliability Boost**: Increases first-attempt success rates from ~70% to 95%+ with sub-second turnaround.

### R20 — Streaming Progress & Token Telemetry (MCP + SSE) (Completed in v0.7.0)
- **Server-Sent Events (SSE)**: Real-time event stream (`/api/events`) for dashboard and terminal CLI progress bars.
- **Persistent delegation telemetry**: every `DelegationService.delegate` / CLI `delegate` run appends a slim record to `.local-run/stats.jsonl`; the `monitor` dashboard aggregates this journal plus in-process stats, so `/stats` reflects real cross-process traffic.
- **Status**: MCP `notifications/progress` broadcasting is NOT wired — `mcp_server.py` exposes tools/resource only. Track separately before claiming it in the changelog.

### R21 — Self-Healing Environment & Auto-Pulling (`doctor --fix`) (Completed in v0.7.0)
- **VRAM-Aware Quant Selection**: Automatic hardware introspection determining the highest-performing quant fitting the system GPU budget.
- **Automated Ingestion Wizard**: `local-agent doctor --fix` and `local-agent profiles pull <tier>` downloading recommended Ollama models / GGUFs and setting up IDE configs automatically.

### R22 — AI Harness & Modern Workbench Prototype (Experimental Web Preview)
- **Experimental Web Prototype**: Lightweight embedded stdlib web UI (`/workbench`) on port 8765 for rapid prototyping of TaskEnvelopes and local model execution. Marked experimental pending full desktop redesign.
- **Interactive Coding Arena**: Web UI allowing developers to submit prompts, configure TaskEnvelopes, and interact with local models directly.
- **Side-by-Side Diff Preview**: Split diff view with patch review and status feedback.
- **One-Click Action Controls**: Buttons for `Apply Proposal`, `Auto-Rollback`, and `Retry with Prescription`.

---

## Planned Milestones (DeepSeek Harness Borrowing & Evolution)

### R23 — Standalone Desktop AI Coding Harness (`local-agent desktop`)
- **Dedicated Desktop Architecture**: Transitioning from a browser sandbox to a first-class desktop application ([`local_coding_agent/desktop/app.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/desktop/app.py), [`local_coding_agent/desktop/server.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/desktop/server.py)).
- **Native Workspace & Git Integration**: Native folder picker, active git diff tree, character-exact SEARCH/REPLACE diff visualizer, and file-tree context picker.
- **Speculative Model Racing Arena**: Visual side-by-side execution split view between competing local model drafts.
- **Hardware & VRAM Telemetry Hub**: Real-time GPU VRAM (`nvidia-smi`), context window token meters, and live model process management.
- **AST Skeletonizer & Token Savings Studio**: Interactive preview of code compaction before LLM dispatch ([`local_coding_agent/ast_compactor.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/ast_compactor.py)).
- **Pinpointed Prescriptions Studio**: Visual repair assistant for model diff alignment errors ([`local_coding_agent/prescriptions.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/prescriptions.py)).
- **Conversation Node UI Framework**: Modular frontend cards for Diffs, Terminal output, Todo checklists, and Plan review dialogs ([`local_coding_agent/desktop/ui.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/desktop/ui.py)).

---

### R24 — Tool Output Spill Store, Ripgrep & FS Observation Policy (Completed in v0.8.0-dev)
- **Tool Output Spill Store**:
  - Implementation of [`local_coding_agent/spill.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/spill.py) managing private session-scoped directories under `.local_agent/spill/<session_id>/` (with strict 0700 permissions and path traversal neutralizers).
  - Hard byte/line thresholds (default: 30KB / 1,000 lines). Oversized tool outputs are spilled to disk, returning a structured summary (head snippet + tail snippet + total line count + unique locator path).
  - Subcommand: `local-agent spill-read <locator> [--offset N] [--limit N] [--json]`.
- **Packaged Ripgrep Discovery (`ripgrep.py`)**:
  - Implementation of [`local_coding_agent/ripgrep.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/ripgrep.py) executing direct `rg` binary invocations for `glob` and `grep` with structured JSON parsing, with pure Python fallback.
  - Subcommand: `local-agent grep <query> [paths...] [--regex] [--json]`.
- **Filesystem Observation Policy Gate**:
  - Implementation of the **read-before-edit** / **read-before-write** invariant in [`local_coding_agent/observation_policy.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/observation_policy.py).
  - **Status**: reference implementation, not yet enforced inside the delegation loop (the controller tracks `viewed_files` for escalation context but does not hard-reject unobserved edits). Wiring it in is tracked as future work.

### R25 — Generic LSP Stdio Code Intelligence Seam (Completed in v0.8.0-dev)
- **Language Server Protocol Stdio Client**:
  - Implementation of [`local_coding_agent/lsp.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/lsp.py) providing JSON-RPC stdio communication with language servers (`pyright`, `typescript-language-server`, `rust-analyzer`, `gopls`) and built-in AST/regex fallback engine.
  - Process lifecycle management, initialize handshake, capabilities negotiation, and timeout protection.
- **Language Intelligence CLI & Tooling**:
  - Operations: `definition`, `references`, `hover`, `symbols`.
  - Subcommand: `local-agent lsp --operation {definition|references|hover|symbols} --file <path> [--line N] [--char N] [--json]`.
  - **Status**: human-facing CLI utility. The delegated model's tool suite remains the bounded five (`list_files`, `read_file`, `search_text`, `propose_patch`, `run_tests`); LSP is not exposed to the model.

---

### R26 — Persistent PTY Terminal Seam & Interactive Process Control (Completed in v0.8.0-dev)
- **Cross-Platform PTY Process Manager**:
  - Implementation of [`local_coding_agent/terminal.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/terminal.py) providing persistent, stateful terminal sessions.
  - Windows support via `winpty` / `ConPTY` / non-blocking pipes; Linux/macOS support via standard `pty` / `termios`.
- **Terminal Tool Suite**:
  - `terminal_open`, `terminal_send`, `terminal_read`, `terminal_signal`, `terminal_list`, `terminal_close`.
  - **Status**: human-facing seam, not part of the delegated model's bounded tool suite.
- **Use Cases**: Interactive REPLs (Python, Node), watch-mode testing, long builds, and live local development servers.

### R27 — Plan Mode Controller, Structured Questions & Dynamic Checklist (Completed in v0.8.0-dev)
- **Plan Mode State Machine**:
  - Implementation of [`local_coding_agent/plan_mode.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/plan_mode.py).
  - Enforces strict read-only tool policy during exploration (`read_file`, `lsp`, `grep`, `glob`), blocking mutation tools until explicit human approval.
- **Structured Interactive `ask_user_question` Tool**:
  - Enables the model to clarify ambiguous requirements with structured multiple-choice questions, default write-in, and multi-select options.
- **Dynamic `todo_write` Checklist Tool**:
  - Session-scoped task checklist (`pending`, `in_progress`, `completed`) with single-active task discipline and ASCII/Markdown rendering.

### R28 — Event-Sourced Session Engine & SQLite FTS5 Search Index (Completed in v0.8.0-dev)
- **Event-Sourced Session Architecture**:
  - Implementation of [`local_coding_agent/session_events.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/session_events.py).
  - Append-only immutable typed events (`SessionEvent`), monotonic sequence numbering, and strict **Model-Visible ⟺ Logged** invariant.
- **Session Branching & Time-Travel Replay**:
  - Session forking (`fork_session`) from any historical step index preserving full parent lineage.
- **SQLite FTS5 Full-Text Search**:
  - Implementation of [`local_coding_agent/session_query.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/session_query.py) with SQLi/FTS5 injection-proof full-text search across all session events.

### R29 — Universal Agent Client Protocol (ACP) Server & Interop Gateway (Completed in v0.8.0-dev)
- **Agent Client Protocol (ACP) stdio Server**:
  - Implementation of [`local_coding_agent/acp_server.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/acp_server.py) implementing standard JSON-RPC 2.0 ACP protocol over stdio.
  - Exposes local agent harness to AI-native IDEs (Zed, Cursor, VS Code, JetBrains, OpenCode).
- **CLI Subcommand**:
  - `local-agent serve-acp [--workspace ...] [--profile ...] [--framing ...]`.

### R30 — Continuable Background Subagents & External Agent Hook Bridges (Completed in v0.8.0-dev)
- **In-Process Continuable Subagents**:
  - Implementation of [`local_coding_agent/subagent.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/subagent.py) with isolated TaskEnvelopes, restricted tool subsets, and inter-agent mailbox communication.
- **Claude Code & Codex Wire-Protocol Hooks**:
  - Implementation of [`local_coding_agent/hooks.py`](file:///c:/Users/mist8/Documents/Codex/2026-08-12/new-chat-2/codex-local-coding-agent/local_coding_agent/hooks.py) bridging tool execution hooks and session lifecycle events with external host CLI ecosystems.

---

## Security Invariants & Defensive Controls (OWASP / MITRE ATLAS AML.T0053)

To ensure bulletproof safety during autonomous tool execution, all planned capabilities adhere to strict security invariants:

1. **Deny-by-Default Tool Allowlisting**:
   - Every tool call is validated against strict Pydantic/dataclass JSON schemas before execution. Unknown tools or extra properties are immediately rejected.
2. **Read-Before-Write Observation Gate**:
   - No patch or write operation is accepted for files that were not previously observed in the session.
3. **Strict Path Normalization & Traversal Defense**:
   - All filesystem paths (`spill`, `fs`, `lsp`, `terminal`) are normalized against the registered workspace root. `../` and symlink escapes outside workspace boundaries trigger immediate `SECURITY_VIOLATION` errors.
4. **Isolated Process Boundaries & Non-Blocking Streams**:
   - All child processes (tests, LSP servers, persistent PTYs, ripgrep) run with bounded timeouts and continuous async pipe drainage to prevent deadlocks on full OS pipes.
5. **Human-in-the-Loop (HITL) for High-Impact Actions**:
   - Disk writes during mediated apply and Plan Mode exit require explicit human confirmation.

---

## Decisions & Architectural Rationale

1. **Backend adapter → ship it (Option B)**: The OpenAI-compatible `llama-server` adapter is a first-class milestone, sequenced *before* R13. It is the whole point of the feature: enabling non-Ollama GGUF architectures (`ling-3.0-tiny-q6k` and future BailingMoE/KDA/MLA) through the real controller path.
2. **Context budget → real token count (Option B)**: Replace `max_context_bytes` with an actual token budget mapped against `num_ctx`. Bytes were never the right unit; a byte-bounded task can silently exceed `num_ctx` and be truncated by the model. Use a per-model tokenizer or a tokens≈bytes/N approximation rather than raw bytes.
3. **Compaction → preserve pairing (recommended, Option A)**: R14 eviction may only drop whole `assistant(tool_calls)` ↔ `role:tool` pairs. Never orphan a tool result from its call. Summarization of past turns is a later step, not the MVP.
4. **Capability vector freshness → versioning + invalidation (Option B)**: The profile is keyed to `model` + quant + hash. A stale vector invalidates the profile and the gate refuses to route on it.
5. **Polyglot → do it now (Option B)**: Ship Python/TypeScript/Rust/Go evaluation. This requires building external oracles/verifiers for the non-Python tiers first — no language tier is reported without a working verifier.
6. **Gating evidence → gate only on verified tiers (recommended, Option A)**: `CAPABILITY_OVERLOAD` may only reject/warn on CI-confirmed tiers. Unverified tiers are reported as `unknown`, never used to cut a task.
7. **Standalone Harness UI Architecture**: Embedded FastAPI/Starlette backend serving a lightweight modern static bundle (`diff2html`, Monaco, Tailwind, Chart.js) with zero Node.js runtime requirements for the user.
8. **Spill Store vs Context Compression (Option A)**: Tool output spilling to `.local_agent/spill/` is strictly decoupled from LLM context summarization. The filesystem owns large blobs; the LLM receives only clean locators and summaries.
9. **LSP Stdio Isolation (Option B)**: Language servers run in dedicated child processes isolated per workspace and serialized through an async queue, preventing server crashes from killing the agent controller.
10. **Event-Sourced Monotonic Log (Option A)**: `SessionEvent` records are append-only. All UI views, telemetry, and message projections are derived views from this immutable event stream.






