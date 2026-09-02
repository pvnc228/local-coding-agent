# Tauri desktop distribution contract

## Goal

Ship the existing Local AI Coding Harness as a Windows Tauri v2 application that a nontechnical user can install and launch without installing Python.

## Acceptance criteria

- Every state-changing loopback request requires a per-process secret and rejects hostile cross-origin requests.
- Doctor, model, rollback, and delegated-task UI never display success unless the API response proves success.
- Empty/invalid user input is explained at the control where it occurs.
- Harness-owned metadata files never enter an inferred task allowlist.
- Model scanning skips inaccessible drives/directories and reports partial failures without crashing the request thread.
- The rendered application has no network-loaded fonts, CSS framework, or icon runtime.
- The Python desktop entry point can run headlessly on port 0 and prints one JSON readiness record.
- Tauri starts and stops the bundled sidecar, opens the readiness URL, and builds an NSIS installer on Windows.
- CLI, HTTP API, and full Python regression tests remain green; the installed artifact receives a repeat novice black-box pass.

## Non-goals

- Rewriting the Python controller in Rust.
- Bundling Ollama or model weights.
- Publishing/signing a release or running graphify.
