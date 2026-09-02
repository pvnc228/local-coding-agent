# Tauri desktop implementation plan

## Phase 1 - trustworthy public seams

1. Add failing HTTP seam tests for loopback authentication, hostile Origin rejection, Doctor response semantics, and internal-file filtering.
2. Implement the minimum server/client changes to pass them.
3. Add DOM-template assertions for neutral delegated state, validation, shortcuts, and truthful failure messages.

## Phase 2 - offline and resilient desktop surface

1. Add failing tests proving `/app` has no remote assets and scanner access errors are contained.
2. Precompile/vend required CSS and icon runtime into package data, or replace them with local equivalents.
3. Re-run targeted HTTP/UI/model-scanner tests and a browser smoke pass.

## Phase 3 - Tauri sidecar and Windows bundle

1. Add failing CLI readiness tests for headless port-0 startup.
2. Add PyInstaller build configuration and create the target-triple sidecar binary.
3. Add a minimal Tauri v2 Rust shell, capability policy, lifecycle handling, and NSIS configuration.
4. Build the installer, install or launch the produced artifact, and repeat the novice black-box matrix.

## Gates

- `python -W error::SyntaxWarning -m py_compile` for embedded assets.
- Targeted pytest after every red-green slice.
- Full `pytest tests/` outside the restricted sandbox when filesystem evidence requires it.
- `cargo check`, `cargo test`, Tauri build, installer artifact hash, and `git diff --check`.
- No commit, push, tag, signing, publishing, or graphify without a later explicit request.
