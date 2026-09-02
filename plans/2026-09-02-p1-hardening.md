# Plan: Desktop Harness P1 Hardening

Spec: specs/2026-09-02-p1-hardening.md

## Challenge Log

1. Does this solve the problem? Every AC maps 1:1 to a P1 item.
2. Most efficient solution? Reuse existing seams: `DiscoveredModel.to_dict` gains
   a computed `id`; `vram_fit.max_fitting_ctx` reused for the ctx preflight;
   `pick_folder` callback gains an app-liveness check; Rust parser gets a line
   buffer; doctor endpoint reuses existing `report.to_dict()`. No new deps.
   Alternatives rejected: hashing file contents (slow for multi-GB GGUFs,
   mtime+size already in registry — id derived from normalized path is stable
   and free); separate Rust readiness channel (protocol change, more surface).
3. Code for code's sake? No new abstractions; all fixes are guards or field
   additions on existing structures.

## Phase 1 — Model identity: stable GGUF IDs (P1-1)

Files: `model_scanner.py`, `desktop/server/_models.py`, `desktop/server/_handlers.py`,
`desktop/client_js.py`, `tests/test_model_scanner.py`, `tests/test_desktop_app.py`.

1. Red: registry test — two GGUFs with same basename in different dirs produce
   distinct `id`s; id derived from normalized path (drive-letter case + `\` -> `/`).
2. Green: `DiscoveredModel` computes `id` in `to_dict()` from `path` (pure
   function `_gguf_model_id(path)`; no schema/registry-file change).
3. `_handle_models` already passes dicts through — id flows automatically.
4. Red: handler test — `/api/model/load` with `model` = full id launches the
   right path; `find_discovered_gguf` matches by id first, basename second.
5. Green: `find_discovered_gguf` + `resolve_model_profile` match `id` exactly
   before the existing lowercased basename match.
6. Red: client string-contract — option value uses `g.id || g.name`.
7. Green: `client_js.py` option value + `recordProviders` keyed by id.

## Phase 2 — Unique session/chat/task IDs (P1-2)

Files: `desktop/server/_handlers.py`, `desktop/client_js.py`, `tests/test_desktop_release_gate.py`.

1. Red: two rapid `/api/chat` posts (mode chat, fake client) persist 2 sessions.
2. Green: `task_id = f"task-{uuid.uuid4().hex[:12]}"`; both `sess-` fallbacks
   become `f"sess-{uuid.uuid4().hex[:12]}"` (only fallback paths — provided ids
   and `response.task_id` respected).
3. Client `sess-${Date.now()}` -> `sess-${crypto.randomUUID?.() || Date.now()+'-'+Math.random()}`.

## Phase 3 — num_ctx resource preflight + safe reload (P1-3)

Files: `desktop/server/_handlers.py`, `tests/test_desktop_app.py`.

1. Upper bound: `_parse_ctx_override` gains a module-level max
   (`_MAX_CTX_OVERRIDE = 262144`) — cheap hard stop regardless of model.
2. Preflight in `_launch_llama_model`: before killing the running server, read
   GGUF params (`read_gguf_ctx_params`) + VRAM telemetry; if requested ctx
   exceeds `max_fitting_ctx(...)`, clamp down to the fit and record
   `ctx_warning`; proceed with the clamped value.
3. Rollback on failure: capture previous `llama_num_ctx`, `llama_gguf_path`,
   `llama_gguf_label` before stop; if the relaunch fails (proc exits / timeout),
   attempt one relaunch with the previous configuration and report honestly.
4. Red/green: preflight clamps oversized ctx to native context length when
   metadata is readable; failed relaunch restores previous server state
   (fake Popen that always fails -> old config relaunched, error surfaced).

## Phase 4 — Loopback-only bind (P1-4)

Files: `desktop/server/_server.py`, `tests/test_desktop_release_gate.py`.

1. Red: `DesktopServer(host="0.0.0.0")` raises ValueError.
2. Green: in `__init__`, validate `host` resolves to loopback
   (`ipaddress.ip_address(host).is_loopback`, plus literal `localhost`); raise
   ValueError otherwise. `--host` CLI already defaults 127.0.0.1.

## Phase 5 — Offline Ollama honesty in models list (P1-5)

Files: `desktop/client_js.py`, `tests/test_desktop_release_gate.py`.

1. `/api/models` already returns `backends.ollama.online`; UI group label for
   Ollama models becomes conditional: offline -> "Installed in Ollama (backend
   offline — start Ollama)" instead of "✅ (Ready to Use)".
2. String-contract test pins the offline label and the online label.

## Phase 6 — Tauri close vs folder picker (P1-6)

Files: `src-tauri/src/lib.rs`, `tests/test_desktop_release_gate.py`.

1. In the `pick_folder` callback, guard: only spawn sidecar if the main window
   still exists (`get_webview_window("main").is_some()`); when absent, skip
   spawn silently (app is closing). Cancelled-selection path: keep existing
   message but also skip sidecar (already does).
2. Release-gate string assertions for the guard.

## Phase 7 — Robust readiness parser (P1-7)

Files: `src-tauri/src/lib.rs`, `tests/test_desktop_release_gate.py`.

1. Accumulate stdout bytes into a `String` buffer; split on `\n`; parse each
   complete line; retain remainder. Handle UTF-8 incrementally (lossy ok).
2. String assertions: buffer + `lines()` parsing in release gate.

## Phase 8 — Doctor partial-results UX (P1-8)

Files: `desktop/server/_handlers.py`, `desktop/client_js.py`,
`tests/test_desktop_release_gate.py`.

1. `_handle_doctor_fix`: when `report.success` is False but `report.actions`
   is non-empty -> HTTP 200 with `status: "partial"`, `report` dict, `error`
   string. Full failure (no actions) stays 500. Success stays 200 `ok`.
2. UI: `runDoctorCheck` renders partial state with per-error toast message.
3. Update the pinned release-gate test (it currently asserts 500 for the
   partial case) to the new contract.

## Verification matrix (after each phase + final)

- `pytest tests/test_desktop_app.py tests/test_desktop_release_gate.py tests/test_model_scanner.py tests/test_vram_fit.py -q` (targeted)
- `pytest tests/ -q` (full, after all phases)
- `cargo fmt --check`, `cargo test`, `cargo check --locked` (phases 6-7)
- `npm run build` (frontend embeds client_js via Python — build still exercises Tailwind)
- `npm run tauri -- build` + fresh SHA-256 (final, on request)
