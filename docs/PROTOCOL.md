# Local Model Protocol

## Request Envelope

Every request sent to the local model contains:

- A concise system contract.
- The atomic task envelope.
- Only the necessary bounded tools.
- Explicit context and output budgets.
- `stream: false` for atomic responses.
- `think: false` (unless the task explicitly requests a reasoning trace).

Example Ollama request parameters:

```json
{
  "model": "qwen3-8b-q6k:latest",
  "stream": false,
  "think": false,
  "keep_alive": "10m",
  "options": {
    "temperature": 0.7,
    "top_p": 0.8,
    "num_ctx": 8192,
    "num_predict": 512
  }
}
```

Active models and allocated VRAM are queried via `/api/ps`; models can be unloaded immediately using `/api/generate` with `keep_alive: 0`.

`options.num_ctx` specifies the context window size for each request. The controller enforces bounds between 1 and `max_context_length`.

---

## System Contract

```text
You are a local coding sub-agent assigned to a single atomic task.
Operate strictly within the provided task envelope.
Do not invent missing context or assumptions.
Do not claim to have run tests or modified files without tool evidence.
Use only the provided tools.
If data is missing, ask one precise question.
Patches must be minimal and modify only allowlisted files.
Return only the structured JSON result upon completion.
```

---

## Tool Loop Execution

1. Send task envelope and bounded tool schemas to the model.
2. If `tool_calls` are returned, preserve the assistant message in conversation history.
3. Execute each tool call through the controller policy layer.
4. Append tool execution results as `role: tool` messages with matching `tool_name`.
5. Re-query the model.
6. Terminate upon receiving a final JSON response, policy error, duplicate tool call, or exceeding `max_turns`.

Default task limits:
- `max_turns: 4`
- `max_repair_rounds: 2` (CLI `--repair-rounds`, request/MCP `repair_rounds`; hard cap 5)
- `max_same_call: 1`
- `max_tool_result_bytes: 32000`
- `max_files: 5`

## Candidate verification and quality strategies

`delegate` and `delegate_code` share `run_quality`: `samples` defaults to 1
(cap 5), `strategy` defaults to `single`, and `max_model_calls` defaults to 30
(cap 100). The call budget includes planners, children, samples, and repairs.
The model and quantization stay fixed. Samples vary request temperature and seed.

Request/MCP `think` defaults to null (inherit the selected profile); true/false
override it. CLI exposes the same override as `--think`/`--no-think`, plus
`--num-predict` for the base generation budget. Profile `think=None` omits the
provider extension. Thinking uses a larger generation budget capped by context;
existing non-thinking profiles keep explicit false defaults.

With checks and repair enabled, a valid candidate is applied in a bounded private
copy, then host-approved checks run through the normal subprocess policy.
`verification: {"mode":"scratch","rounds":2}` means two candidate verification
rounds were observed. It does not claim verification in the caller's environment.
`--apply` retains a separate real-workspace check and rollback gate.
`repair_rounds=0` preserves legacy format-only behavior; no-check tasks return
`verification.mode="none"`. Neither case establishes candidate correctness.

Each failed check produces a deterministic redacted prescription of at most
1,500 UTF-8 bytes. Only the latest candidate and failure remain in repair history.
`repair_budget_exhausted` retains the rejected candidate with the fewest failed
checks. `model_call_budget_exhausted`, cancellation, and unavailable scratch
verification are explicit terminal outcomes. Unknown token counts remain null.

`per_file`/`by_files`, deterministic pytest `tests` splitting, and validated
same-model `plan` execution accumulate edits in a private copy and return one
combined patch with the parent scope/size gates and full checks. The legacy
`decompose` command emits envelopes only. It does not execute dependent steps.
Legacy Python `DelegatingAgent.run` requires `independent_files=True` before
splitting into separate proposals; otherwise it returns `independent_files_required`.
Atomic unsplit tasks are unaffected. Dependent tasks use the scratch strategies.
Keep test fixtures outside `files`; explicit file, directory, and glob check
targets are protected against edits. Test splitting supports narrow simple
pytest commands only; shell wrappers and unsafe node IDs are not splittable.

Copies are limited to 20,000 files and 256 MB; links/reparse points are refused.
This is repository-state isolation, not an operating-system sandbox. Check
commands remain host-approved executable code and must be safe for that host.

`selection="tests"` requires passing candidate checks. With multiple samples
and no checks, `selection="majority"` selects the most common normalized patch,
first on ties. `selection="format_only"` describes an unverified choice.
- `max_patch_files: 2`

`TaskBudget` enforces bounded preflight checks on service and controller boundaries. Non-empty patches require at least one pre-allowlisted targeted check.

---

## Final Candidate Format

```json
{
  "status": "candidate",
  "summary": "Description of changes",
  "patch": "unified diff string",
  "edits": [
    {
      "file": "src/unique.py",
      "search": "old code block",
      "replace": "new code block"
    }
  ],
  "checks": [
    {
      "command": "pytest tests/test_unique.py",
      "passed": true,
      "evidence": "1 passed in 0.05s"
    }
  ],
  "risks": []
}
```

A candidate proposal contains either a `patch` (unified diff) or `edits` (SEARCH/REPLACE blocks), but never both. Test `checks` in the terminal result are populated strictly by the external test runner; model self-reports without runner evidence are rejected.

---

## SEARCH/REPLACE (`edits`)

Local models (7B–27B) frequently struggle with raw unified diffs due to line-counting and hunk header errors (`@@ -x,y +x,y @@`). The `edits` format removes this failure mode: the model copies the exact existing code block into `search` and provides the modified version in `replace`.

Rules:
- Each edit block is an object: `{"file": "path", "search": "...", "replace": "..."}`.
- `search` must match the file content character-for-character, occur exactly once, and align with line boundaries.
- If `search` is not found, appears multiple times, or is misaligned, the proposal is rejected with a machine-readable diagnostic error.
- The controller automatically converts valid `edits` into a unified diff and validates it with `git apply --check`.

---

## Mediated Apply & Auto-Rollback

Local models only propose changes via `propose_patch`; they cannot write directly to disk. Every apply surface (service `apply`, CLI `local-agent apply`, desktop `/api/apply`) routes through the same controller-owned gate:

1. The request must declare a file scope and at least one targeted check; a patch with no check is refused with `error.kind == "apply_requires_checks"` (no evidence, no apply).
2. The patch must parse as a unified diff and touch only the declared files, otherwise `error.kind == "patch_outside_scope"`.
3. The controller validates applicability via `git apply --check` and runs the semantic linter in memory before touching the workspace (`stale_workspace` / `semantic_lint_failed`).
4. The baseline digest (SHA-256) of every file the patch changes is recorded, then the patch is applied.
5. All allowlisted targeted checks are re-executed against the modified workspace.
6. If all checks pass, the result receives `"applied": true`.
7. If any check fails, the patch is automatically rolled back, and the status is set to `"rejected"` with risk kind `post_apply_check_failed`.
8. The rollback is verified against the recorded baseline. If the workspace does not match it, the result reports `workspace_modified: true`, `workspace_state: "unknown"` and risk kind `rollback_failed` instead of claiming a clean restore.

---

## Process-Bound stdio Adapter

`StdioDelegationAdapter` accepts one UTF-8 JSONL command per line:

```json
{
  "method": "delegate_code",
  "caller_id": "trusted-host",
  "params": {
    "request_id": "opaque-idempotency-key",
    "workspace_ref": "registered-workspace",
    "model_profile": "qwen3-8b-q6k",
    "task": {
      "id": "refactor-func",
      "goal": "Refactor helper function",
      "files": ["src/helper.py"]
    }
  }
}
```

The adapter returns one UTF-8 JSONL response string matching the `DelegationService` response format.

---

## Retries & Escalation

Retries are only permitted when a specific, remediable failure reason occurs:
- `invalid_json`
- `patch_parse_failed`
- `file_outside_allowlist`
- `check_failed`
- `needs_context`

Repeating an identical request without altering context or constraints is prohibited.
