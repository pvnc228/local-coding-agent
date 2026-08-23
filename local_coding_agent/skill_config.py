"""Skill configuration and installer for AI coding agents.

Integrates SKILL.md into Codex, Antigravity, Claude, and workspace skill directories.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_EMBEDDED_SKILL_MD = """---
name: local-coding-agent
description: Offload and delegate atomic coding tasks, bug fixes, refactoring, and unit test implementations to local Ollama LLMs via MCP (delegate_code, apply_proposal) or direct CLI.
---

# Local Coding Agent — AI Agent Delegation Skill

## Overview

The `local-coding-agent` is a bounded, proposal-only controller designed to offload small, deterministic coding tasks to local LLMs (such as Qwen 2.5 Coder, Qwen3, Devstral, and Gemma) running via Ollama or OpenAI-compatible local endpoints.

### Core Security & Architecture Invariants
- **Proposal-Only by Default**: The local model never directly writes to disk or executes arbitrary shell commands. It only generates structured diffs (`patch`) or search/replace blocks (`edits`).
- **External Test Verification**: Test results are never self-reported by the model; they are executed and verified strictly by the external runner.
- **Bounded Scope**: Context and files are strictly allowlisted. Tasks exceeding budget limits must be decomposed into atomic child subtasks.
- **Mediated Apply with Auto-Rollback**: Applying patches (`apply_proposal` or `--apply`) verifies the diff, applies it, re-runs all targeted test checks, and automatically rolls back if any test fails.
- **100% CLI Parity**: Every feature available via MCP is equally accessible via CLI subcommands for non-MCP agents and terminal workflows.

---

## Delegation Decision Matrix

Use this matrix to determine whether to delegate a task to the local model:

| Task Type | Delegate? | Reason / Action |
|---|---|---|
| Single-function bug fix | **YES** | Perfect fit. Provide targeted test check and allowlist 1 file. |
| Writing unit tests | **YES** | Provide the target implementation file + test file in `files`. |
| Localized refactor | **YES** | Clear acceptance criteria and targeted test runner command. |
| Edge-case handling | **YES** | Explicit constraints and expected inputs/outputs in `context`. |
| Cross-repo architecture | **NO** | Solve yourself. Too broad for bounded local model context. |
| Unknown / exploratory debugging | **NO** | First inspect and isolate the issue, then delegate the isolated fix. |
| Task with no test / verification check | **NO** | Provide a check command or solve directly. |

---

## MCP Tools Reference

When the `local-coding-agent` MCP server is active in your environment, two primary tools are available:

### 1. `delegate_code`
Delegates one atomic, proposal-only coding task to a local Ollama model.

#### Parameters:
- `request_id` *(string, required)*: A unique caller-generated idempotency key (e.g. `task-fix-auth-01`).
- `workspace_ref` *(string, required)*: The registered workspace identifier (usually `"workspace"`).
- `model_profile` *(string, required)*: Named profile (e.g. `"qwen2.5-coder"`, `"qwen3-8b-q6k"`, `"devstral-small-2-24b"`).
- `task` *(object, required)*: The structured task envelope:
  - `id` *(string)*: Short descriptive identifier (e.g. `"fix-null-dereference"`).
  - `goal` *(string)*: 1 concise sentence describing the goal.
  - `files` *(array of strings)*: List of allowlisted relative file paths (1 to 5 files, ideally 1-2).
  - `context` *(string)*: Compact relevant context (types, function signatures, error traces). Do **not** dump entire irrelevant files.
  - `constraints` *(array of strings)*: Guardrails (e.g. `["do not add external dependencies", "preserve public signature"]`).
  - `checks` *(array of strings)*: Targeted test commands (e.g. `["pytest tests/test_auth.py -k test_null"]`). Non-empty patches require at least 1 check.
  - `acceptance` *(array of strings)*: Bullet points describing acceptance criteria.

#### Example MCP Invocation:
```json
{
  "request_id": "req-20260818-unique-01",
  "workspace_ref": "workspace",
  "model_profile": "qwen2.5-coder",
  "task": {
    "id": "unique-preserve-order",
    "goal": "Remove sorting from unique function and preserve first-seen insertion order",
    "files": ["src/unique.py"],
    "context": "def unique(items: list[Any]) -> list[Any]:\\n    # currently does sorted(set(items))",
    "constraints": [
      "preserve public signature of unique()",
      "do not add external dependencies"
    ],
    "checks": [
      "pytest tests/test_unique.py"
    ],
    "acceptance": [
      "diff modifies only src/unique.py",
      "sorting logic is replaced with order-preserving deduplication",
      "targeted test passes"
    ]
  }
}
```

### 2. `apply_proposal`
Safely applies a previously accepted proposal to the workspace after verifying confirmation.

#### Parameters:
- `request_id` *(string, required)*: The request ID of the accepted proposal.
- `workspace_ref` *(string, required)*: The target workspace.
- `confirmation` *(object)*: Standard MCP elicitation confirmation containing `confirm: true`, `proposal_id`, `workspace_ref`, and `proposal_digest`.

---

## Interpreting Result Envelopes

The return value is a controller-owned JSON object:

```json
{
  "status": "accepted",
  "summary": "Replaced sorted set with dict.fromkeys to preserve insertion order",
  "patch": "--- a/src/unique.py\\n+++ b/src/unique.py\\n@@ -5,2 +5,2 @@\\n-    return sorted(list(set(items)))\\n+    return list(dict.fromkeys(items))\\n",
  "checks": [
    {
      "command": "pytest tests/test_unique.py",
      "passed": true,
      "evidence": "1 passed in 0.04s"
    }
  ],
  "risks": [],
  "validation": {
    "syntax_valid": true,
    "allowlist_clean": true
  },
  "applied": false
}
```

### Result Statuses:
- **`accepted`**: Valid diff generated, all targeted checks executed and passed.
- **`rejected`**: The proposal failed syntax validation, touched non-allowlisted files, or failed targeted test checks. Check `error` and `risks`.
- **`needs_context`**: The local model requested clarification. Inspect the question, refine `context`, and retry.
- **`failed`**: Controller policy violation, timeout, or queue overload.

---

## Model Profile Selection & Multi-Runtime Support

The controller is runtime-agnostic and natively supports two local inference backends:
1. **Ollama Runtime** (`provider="ollama"`, default: `http://127.0.0.1:11434`): Standard native Ollama API with VRAM introspection and dynamic model loading/unloading.
2. **llama.cpp / llama-server & OpenAI-Compatible** (`provider="openai"`, default: `http://127.0.0.1:8080`): Standalone `llama-server`, vLLM, or LM Studio endpoints providing `/v1/chat/completions` with exact timing measurements (`prompt_ms`, `predicted_ms`) and tool-calling support.

### Model Tiers:

| Model Tier / Class | Memory / VRAM | Example Profiles / Runtimes | When to Choose |
|---|---|---|---|
| **Ultra-Lightweight (1B-3B)** | 2-4 GB VRAM / CPU | `qwen2.5-1.5b`, `gemma4-e2b-q4`, `ling-3.0-tiny-q6k` (llama-server) | Trivial fixes, single-line edits, docstrings, type annotations, ultra-fast response (<2s). |
| **Standard Workhorse (7B-14B)** | 6-12 GB VRAM | `qwen2.5-coder`, `qwen3-8b-q6k`, `qwen2.5-coder-7b-q4` | **Default choice.** Standard functions, bug fixes, localized refactoring, unit test generation. |
| **Advanced Reasoning (24B-32B)** | 16-24 GB VRAM | `devstral-small-2-24b`, `qwen3.8-27b-q4`, `qwen3-coder-30b` | Complex algorithms, subtle edge cases, multi-step logic, difficult refactors. |
| **llama-server / Custom OpenAI** | Any GGUF / GPU | `ling-3.0-tiny-q6k` (llama-server:8080), custom vLLM | Standalone `llama-server` instances or custom OpenAI-compatible proxies. |

### Dynamic Discovery Rule
Never guess or hardcode installed models. Determine host capabilities dynamically:
- **Discover active & installed profiles**: Run `local-agent profiles list --check-ollama --json` or `local-agent doctor --json`.
- **Targeting llama-server**: Specify `--endpoint http://127.0.0.1:8080` or choose an OpenAI-provider profile.
- **Default Profile**: If unspecified, use the default configured profile (`qwen2.5-coder` or the MCP server default).




---

## Error Handling & Task Decomposition

### 1. Preflight Budget Violations (`too_many_files`, `context_too_large`)
If a task touches more than 5 files or context exceeds 32KB, decompose the task into child subtasks using the decomposition tool:
```bash
local-agent decompose --task task.json --json
```
Or split the task into independent child envelopes per file.

### 2. Pinpointed Prescriptions
When a small local model produces a syntax or diff formatting error, the Pinpointed Prescriptions Engine returns a laser-focused deterministic hint (e.g. exact SEARCH/REPLACE alignment rules). Pass this hint directly in `constraints` on retry.

---

## CLI Console Control (Fool-Proof Fallback for Any Agent / Shell)

For AI agents operating in shell environments without MCP tools, or for direct script automation, every capability is directly available via `local-agent` / `python -m local_coding_agent`:

### Delegate a Task (Single or Speculative Multi-Draft Racing):
```bash
local-agent delegate --task '{"id":"fix-1","goal":"Fix bug","files":["src/foo.py"],"checks":["pytest tests/test_foo.py"]}' --json
# Or run speculative racing between 2 drafts:
local-agent delegate --task task.json --speculative-drafts 2 --json
# Or target ANY installed model tag without registering a profile (--model override):
local-agent delegate --task task.json --model qwen2.5-coder:7b --json
```
Accepted/candidate patches are also persisted to `.local-run/proposals/<task_id>.patch`
and referenced as `patch_file` in the JSON output. Environment defaults: `LCA_PROFILE`
(default `qwen2.5-coder`), `LCA_WORKSPACE`.

### Apply Patch Directly:
```bash
local-agent delegate --task task.json --apply --json
# Or apply a raw patch with verification:
local-agent apply --patch-file changes.diff --workspace . --check "pytest tests/" --json
```

### Fast Semantic Linter Pre-Gate:
```bash
local-agent lint-patch --patch-file changes.diff --workspace . --json
```

### AST-Guided Skeletonization:
```bash
local-agent skeletonize src/large_module.py --symbol target_function --json
```

### Decompose a Broad Task:
```bash
local-agent decompose --task-file wide_task.json --strategy by_files --json
```

### Manage Profiles:
```bash
local-agent profiles list --json
local-agent profiles get qwen2.5-coder --json
```

### Manage Ollama Memory & VRAM:
```bash
local-agent memory status --json
local-agent memory unload qwen2.5-coder:latest --json
local-agent memory enforce --limit 12884901888 --keep qwen3-8b-q6k:latest --json
```

### Check System & Remediate Environment:
```bash
local-agent doctor --json
# Or auto-remediate missing MCP configs, Agent Skills, and setup:
local-agent doctor --fix
```

### Launch Desktop AI Coding Harness (R23 Cockpit):
```bash
local-agent desktop --port 8767
# Or force open in system browser:
local-agent desktop --browser
```

### Tool Output Spill Store & Fast Ripgrep (R24):
```bash
# Read or paginate oversized spill artifact:
local-agent spill-read locator:spill:session_123/456_output.txt --offset 0 --limit 100 --json

# Fast ripgrep search across workspace:
local-agent grep "def my_func" src/*.py --json
```

### LSP Code Intelligence & Navigation (R25):
```bash
# Extract document symbols (classes, functions, methods):
local-agent lsp --operation symbols --file src/service.py --json

# Jump to symbol definition:
local-agent lsp --operation definition --file src/app.py --line 42 --char 15 --json
```

### Interactive Chat with Mode Routing (R31):
```bash
# Single-shot classified chat. Mode defaults to hybrid (auto-router to chat/build/plan):
local-agent chat "fix off-by-one in sliding window" --json
# Force a specific mode:
local-agent chat "refactor calculate_total to integer cents" --mode build --json
local-agent chat "explain what main.py does" --mode chat --json
local-agent chat "plan the migration to the new API" --mode plan --json
```
The hybrid mode routes each prompt to `chat` (plain completion), `build` (Controller
tool-loop → patch + external test evidence), or `plan` (read-only exploration →
`PlanArtifact` with goal/steps/risks/files_to_modify). A small local model
(`qwen2.5-1.5b` or `ling-3.0-tiny-q6k`) is consulted in an isolated context every
N requests to auto-select the mode; on failure it falls back to deterministic
heuristics. The desktop harness (`local-agent desktop`) exposes the same four
modes via the Chat/Build/Plan/Auto selector.

### Multi-Turn Chat REPL & Persistent Sessions:
```bash
# Interactive multi-turn REPL with persistent session history:
local-agent chat --repl
# Create/resume a named session:
local-agent chat --repl --session-id my-session
# List persisted sessions or show a full event transcript:
local-agent sessions list
local-agent sessions show my-session
```

### Install / Export Agent Skill:
```bash
local-agent init-skill --write
```
"""



def get_skill_content(workspace: str | Path = ".") -> str:
    """Retrieve skill markdown content from file or embedded fallback."""
    candidate_paths = [
        Path(workspace) / "skills" / "local-coding-agent" / "SKILL.md",
        Path(__file__).resolve().parent.parent / "skills" / "local-coding-agent" / "SKILL.md",
    ]
    for path in candidate_paths:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                pass
    return _EMBEDDED_SKILL_MD.strip() + "\n"


def _get_skill_target_path(client: str, workspace: str | Path = ".") -> Path:
    home = Path.home()
    c = client.lower().strip()
    if c in ("codex", "chatgpt"):
        return home / ".codex" / "skills" / "local-coding-agent" / "SKILL.md"
    if c in ("antigravity", "agy", "gemini"):
        return home / ".gemini" / "antigravity" / "skills" / "local-coding-agent" / "SKILL.md"
    if c == "claude":
        return home / ".claude" / "skills" / "local-coding-agent" / "SKILL.md"
    if c == "workspace":
        return Path(workspace).resolve() / "skills" / "local-coding-agent" / "SKILL.md"
    return home / ".codex" / "skills" / "local-coding-agent" / "SKILL.md"


def _detect_installed_agent_dirs(workspace: str | Path = ".") -> list[tuple[str, Path]]:
    home = Path.home()
    detected = []
    
    # Codex
    codex_dir = home / ".codex"
    if codex_dir.is_dir() or (home / ".codex" / "skills").is_dir():
        detected.append(("codex", codex_dir / "skills" / "local-coding-agent" / "SKILL.md"))
        
    # Antigravity
    agy_dir = home / ".gemini" / "antigravity"
    if agy_dir.is_dir() or (agy_dir / "skills").is_dir():
        detected.append(("antigravity", agy_dir / "skills" / "local-coding-agent" / "SKILL.md"))
        
    # Claude
    claude_dir = home / ".claude"
    if claude_dir.is_dir():
        detected.append(("claude", claude_dir / "skills" / "local-coding-agent" / "SKILL.md"))
        
    # Always include workspace target
    ws_target = Path(workspace).resolve() / "skills" / "local-coding-agent" / "SKILL.md"
    detected.append(("workspace", ws_target))
    
    return detected


def integrate_skill_config(
    client: str = "auto",
    workspace: str | Path = ".",
    target_path: str | Path | None = None,
    dry_run: bool = False,
    print_content: bool = False,
) -> dict[str, Any]:
    """Export or install the Agent Skill to agent skill directories."""
    content = get_skill_content(workspace)
    
    if print_content:
        return {"action": "print", "content": content}
        
    client_norm = client.lower().strip()
    
    if target_path:
        dest_path = Path(target_path).resolve()
        if dest_path.is_dir():
            dest_path = dest_path / "SKILL.md"
        targets = [(client_norm or "custom", dest_path)]
    elif client_norm in ("auto", "all"):
        targets = _detect_installed_agent_dirs(workspace)
    else:
        targets = [(client_norm, _get_skill_target_path(client_norm, workspace))]
        
    results = []
    for client_name, path in targets:
        written = False
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written = True
        results.append({
            "client": client_name,
            "path": str(path),
            "written": written,
            "status": "installed" if written else "dry_run_preview",
        })
        
    if target_path or (len(results) == 1 and client_norm not in ("auto", "all")):
        return results[0]
        
    return {
        "action": "install_skill",
        "results": results,
        "dry_run": dry_run,
    }

