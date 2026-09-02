# Spec: Desktop Harness P1 Hardening (8 issues)

## Problem
Release candidate 0.8.2 carries 8 confirmed P1 defects across GGUF model identity,
history persistence, context-window resource safety, network exposure, offline UX,
window-close lifecycle, sidecar readiness parsing, and Doctor partial-failure UX.

## Goal
Close all 8 P1s with targeted red/green tests, no behavior regressions,
keeping CLI parity and release-gate invariants intact.

## Scope
In: `local_coding_agent/desktop/**`, `local_coding_agent/model_scanner.py`,
`src-tauri/src/lib.rs`, desktop tests, release-gate tests, CHANGELOG/version bump.
Out: installer signing, publishing, tagging, `.agents/` directory.

## Acceptance Criteria
1. Two GGUF files with identical basenames resolve to distinct stable IDs:
   `/api/models` exposes a stable normalized-path-derived `id` per GGUF entry;
   UI option value uses that id; `/api/model/load` resolves by id (basename
   fallback preserved for backward compat). Regression test proves both
   same-basename files load their own path.
2. Chat/session IDs no longer collide within the same second: server-side IDs
   use uuid; regression test posts two chats in quick succession and asserts
   both sessions persist.
3. `num_ctx` override has a hard upper bound (resource safety preflight):
   requested ctx is clamped/validated against GGUF native context length and
   free VRAM via existing `vram_fit` helpers before relaunch; a failed relaunch
   restores the previous working configuration (previous GGUF + previous ctx).
4. `DesktopServer` refuses to bind non-loopback hosts (raises ValueError).
5. `/api/models` response carries explicit `ollama.online` state consumed by
   UI: offline Ollama optgroup is labeled accordingly (no false "Ready to Use").
6. Tauri window close during an open folder picker cannot orphan the sidecar:
   sidecar spawn is guarded by app-aliveness check in the pick callback.
7. Rust readiness parser buffers partial JSON across `CommandEvent::Stdout`
   chunks until a complete line arrives.
8. `/api/doctor/fix` returns 200 with `status: "partial"` plus per-target
   errors when remediation partially succeeded (no misleading 500-only UX),
   keeping full-failure contract distinct; UI shows honest partial message.

## Constraints
- Keep all existing release-gate string contracts passing
  (`tests/test_desktop_release_gate.py`).
- No new dependencies (Rust or Python).
- Cross-platform: Windows-first, POSIX paths must keep working.
- Proposal-only invariants untouched.

## Non-Goals
- Signing, tagging, publishing, graphify.
- Rewriting UI in desktop-shell (real UI is Python-embedded).
- Changing TaskEnvelope contract.
