# Tauri desktop readiness research

## Current evidence

- The Python wheel installs cleanly and the externally run suite passed: 743 tests, 2 skipped, 21 subtests.
- Black-box desktop checks found false-success UI states, unauthenticated loopback POST mutations, hidden harness metadata entering inferred file scopes, restricted-drive scan failures, and CDN-only frontend dependencies.
- The repository has no Tauri scaffold. The host already has Rust MSVC, Visual Studio C++ tools, Windows SDK, Node, and WebView2; only the Tauri CLI/project dependencies are absent.

## Options considered

1. Rewrite the controller and HTTP API in Rust. This removes embedded Python, but duplicates a tested controller and is a product rewrite rather than packaging.
2. Tauri shell plus a PyInstaller one-file sidecar. This preserves the tested controller, removes the need for user-installed Python, and follows Tauri's documented Python-sidecar use case.
3. Keep pywebview and ship a Python installer. This is smaller, but does not satisfy the requested Tauri distribution or first-run experience.

## Recommendation

Use option 2. The Tauri process owns the sidecar lifecycle, waits for a machine-readable readiness line, and navigates its WebView to the authenticated random-port loopback URL. Vendor/precompile all UI assets. Treat a literal Rust rewrite with no embedded Python bytes as a separate migration.

Primary references:

- https://v2.tauri.app/develop/sidecar/
- https://v2.tauri.app/reference/config/#bundleconfig
- https://v2.tauri.app/distribute/windows-installer/
- https://v2.tauri.app/start/prerequisites/
