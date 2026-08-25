# Handoff: Model Reliability Gaps & Proposed Fixes (Desktop Harness)

**Date**: 2026-08-21
**Branch**: `feat/desktop-harness`
**Status**: Diagnostic only — fixes documented, NOT yet implemented.

---

## Observed failures (external evidence)

Running the desktop chat against `gemma-4-E4B-it-Q4_K_M` and `Qwen3-8B-Q6_K` (via llama-server `:8080`):

| Prompt | Result |
|---|---|
| `add a line to this file ... then delete the line` | `Expecting ':' delimiter: line 1 column 66 (char 65)` |
| `add a line to the top of the file, it can contain anything` | `Expecting ',' delimiter: line 1 column 160 (char 159)` |
| `then just add a line at the top` | `retry budget exhausted: invalid_json` |

All three collapse to the same category: the small model emits a **tool call whose `arguments` string is malformed/truncated JSON**, and the controller surfaces a raw `json.JSONDecodeError` instead of self-correcting.

---

## Root cause

`local_coding_agent/controller/_controller.py:309-310`:

```python
except (ValueError, TypeError, json.JSONDecodeError) as error:
    return self._failure("failed", "policy", str(error), audit)
```

When `_decode_tool_call()` (line 645+) does `json.loads(arguments)` and the model produced invalid JSON (truncated `{"file": "x", "search": "..."`, or prose concatenated), the controller **aborts the whole task immediately** with the raw parser message. It never sends a prescription back to the model, so the model never gets a chance to fix its own tool call — even though the system already has a deterministic prescription engine (`prescriptions.py`) and retry budget (`max_retries`, `max_turns`) designed exactly for this.

Compare with the **adjacent** `except ToolPolicyError` branch (line 297-308): that one correctly feeds `tool_policy_prescription(...)` back as a `tool` message and `continue`s. The malformed-JSON path should do the same but does not.

A secondary amplifier: `num_predict=512` in the controller path can truncate a long tool-call JSON mid-stream (the `finish_reason == "length"` case), which then lands here as `invalid_json`.

---

## Proposed fixes (by priority)

### P1 — Feed malformed tool-call arguments back as a prescription (fixes the reported errors)

In `_controller.py`, replace the hard-fail `except (ValueError, TypeError, json.JSONDecodeError)` around the tool-call decode with a feedback path mirroring the `ToolPolicyError` branch:

- Append a `tool` message whose content is `tool_policy_prescription(name, str(error))` (or a dedicated `json_syntax_prescription(str(error))` payload) so the model sees the exact parse error + a deterministic "return arguments as valid JSON" hint.
- `continue` to the next turn so the model can re-issue the tool call within `max_turns`/`max_retries`.
- Only escalate to `_failure("failed", "policy", ...)` once the retry budget is actually exhausted (reuse the existing `attempts`/`retries` machinery).

Acceptance: re-running `add a line to the top ...` should produce a corrected tool call or a clean escalation, never a raw `Expecting ':' delimiter` string.

### P2 — Handle truncation on `finish_reason == "length"` for tool calls

In `ollama_adapter.py`, the streaming path already accumulates content/tool-calls. Add a marker in the returned dict when the final chunk has `finish_reason == "length"` (Ollama) or the SSE `finish_reason` field (OpenAI). In the controller, when a truncated tool-call is detected, feed back "your tool call was cut off, continue the arguments" instead of treating the partial JSON as a final answer.

### P3 — Gate under-specified goals before entering the tool-loop

Prompts like `add a line ... it can contain anything` have no actionable target. Add a cheap preflight in `_handle_chat` (desktop) that detects goals with no file-bearing intent and asks a single clarifying question (the controller contract already says "Если данных не хватает, задай один точный вопрос"). Prevents burning turns on unfixable vagueness.

### P4 — Consider raising `num_predict` for the Controller path

The info/explain path already bumps `num_predict` to 2048 (`_handlers.py`). The Controller path still uses the profile default (512). Raise it for `_handle_chat`'s controller invocation (or make it a per-profile/overridable knob) so tool-call JSON has headroom.

---

## Explicit non-fixes (YAGNI)

- No new abstraction/interface: reuse `prescriptions.py` and the existing retry/turn budget.
- No changes to the validated-patch apply/rollback flow — that path is already sound.
- No model-specific prompt hacks in the controller; keep the deterministic prescription approach.

---

## Verification notes for the next session

- `pytest tests/` is green (562 passed) at this commit; these are behavioral fixes requiring a live small model to reproduce.
- Repro harness: launch desktop, select a GGUF model, `Load into VRAM`, send `add a line to the top of the file` and confirm no raw `json.JSONDecodeError` reaches the UI.
