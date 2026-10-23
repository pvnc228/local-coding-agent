# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Desktop shell: Tauri 2 (WebView2 on Windows) or pywebview, loading a page served by the local Python sidecar at `/app`. Window 1360×840 by default, minimum 960×640.

## Stack

Can change (confirmed 2026-10-23). The current front end is one HTML page assembled from Python strings (`local_coding_agent/desktop/components.py`, `client_js.py`, `styles.py`) with Tailwind 3 and lucide icons served from `/assets`. It may move into regular front-end files or a build step if that makes the redesign easier. Offline stays fixed: Tauri CSP is `default-src 'self'`, so no CDN, remote fonts or remote assets.

## Users

Public, open-source developers who run local models on their own GPU (Ollama, llama.cpp / llama-server) and already use cloud coding agents. The desktop app's main job (confirmed): **the user chats with a local model directly**. They write a task in Chat, Build or Plan mode, read the reply, review the proposed patch, and apply or roll it back. Two jobs come second: reviewing tasks that a cloud agent delegated over MCP, and managing models, VRAM and inference servers.

## Product Purpose

Local Coding Agent is a local execution and safety layer. A local model proposes a change and the controller checks it: the patch must stay inside the declared file scope, pass static analysis and pass real external test runs before it counts as applied. A failed post-apply check rolls the change back, and the rollback is verified. The desktop harness is the human-facing surface for the same gate the CLI and MCP use.

## Positioning

The local model proposes and the controller verifies. Nothing is called applied or tested without external evidence. The model gets no unrestricted shell or write access, and Git and delivery stay with the user.

## Operating Context

- A developer's workstation, often next to an IDE and a cloud agent session. Long sessions with a consumer GPU (for example 8 GB VRAM) running a quantized model.
- Workflow: pick a model profile, load it into VRAM, write a prompt, read the reasoning and the answer, inspect the unified diff, apply it with checks, and roll back if needed.
- Delegated tasks arrive from Claude Code, Codex, Cursor and similar agents through MCP (`delegate_code`, `apply_proposal`) and run in a background queue.

## Capabilities and Constraints

- Modes: Chat, Build, Plan, Auto (hybrid routing).
- Sessions sidebar with User / Agent filters, plus a background task queue (submit, cancel, apply, roll back).
- Model and profile selector with a context-window override; preload into VRAM and eject; discovery of Ollama and GGUF models with a deep drive scan and custom folders; doctor.
- GPU telemetry (VRAM, load, temperature) and server status for Ollama (:11434) and llama-server (:8080).
- The diff view shows proposals in unified-diff form. Apply requires checks and is mediated with verified rollback.
- Interface language: English only.
- Every feature must also stay reachable from the CLI (CLI-first parity). The desktop app adds no capability that the CLI lacks.
- Tests depend on DOM ids in the rendered HTML (`tests/test_desktop_app.py`, `tests/test_desktop_release_gate.py`). Renaming an id means updating those tests.

## Brand Commitments

- Product name: Local Coding Agent. Desktop window title: "Local AI Coding Harness".
- Current inspiration noted in AGENTS.md: "in the style of LM Studio", a dense dual-mode desktop tool. This is a reference, not a binding visual rule.

## Evidence on Hand

- Real model names, profiles and telemetry come from the running sidecar. There are no testimonials, benchmarks or customer claims, and none should be invented.

## Product Principles

1. Evidence over assertion: the UI never shows a check, test or apply as passed unless an external runner confirmed it. "Not run", "unknown" and "error" stay visibly distinct from success.
2. The human decides: apply and rollback are deliberate actions with clear consequences and are never implied.
3. Conversation first: the chat with the local model is the primary surface, and the diff and telemetry support it.
4. Honest machine state: model, backend, VRAM and server status are always truthful and show their state at a glance.
5. Parity: the desktop app is a window onto the same controller the CLI uses, with no hidden behavior.
