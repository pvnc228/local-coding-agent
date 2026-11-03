# Contributing to Local Coding Agent

Thank you for your interest in contributing! This project follows a disciplined engineering workflow to guarantee security, sandbox boundaries, and 100% verified test evidence.

---

## Core Invariants

Before writing or modifying code, ensure you understand our core design invariants:
1. **Proposal-only by default**: Local models must never modify disk directly unless explicitly requested via mediated apply (`--apply` or `apply_proposal`).
2. **Context boundaries**: Local models never receive more context than requested in the task allowlist.
3. **No tests without external evidence**: Check results belong to the external runner, never model self-assertion.
4. **Duplicate tool call guard**: Repeated tool calls immediately terminate the loop with a failure status.
5. **Transport neutral core**: Core controller logic relies exclusively on the Python standard library.

---

## Development Setup

1. Clone the repository and install in editable mode with development dependencies:
   ```bash
   git clone https://github.com/pvnc228/local-coding-agent.git
   cd local-coding-agent
   pip install -e .[mcp,dev]
   ```


2. Run test discovery:
   ```bash
   python -m pytest -q
   ```
   The desktop client JS tests run through Node.js and are skipped when `node` is not installed.

3. Validate byte-compilation:
   ```bash
   python -m compileall -q local_coding_agent dev
   ```

4. Check git diff formatting:
   ```bash
   git diff --check
   ```

---

## Pull Request Workflow

1. Create a feature branch from `main`.
2. Add tests that demonstrate the bug or the required new behavior before modifying implementation.
3. Ensure all checks pass (`211+` tests, zero compilation errors, clean diff).
4. Provide a clear summary, testing evidence, and risk analysis in your PR description.
