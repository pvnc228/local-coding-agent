# Production-sensitive delivery rules

Этот документ — минимальный operational gate для production-sensitive seams проекта. Он дополняет `factual_grounded_reporting.md` и не означает, что перечисленные гарантии уже полностью реализованы в product-коде. При конфликте с пользовательской задачей scope и прямое разрешение пользователя имеют приоритет, но security/evidence ограничения не ослабляются молча.

## 1. Scope, trust boundaries and evidence

- Перед изменением определить reachable seam, trust boundary, владельца состояния, лимит ресурса и внешний runner. Для этого проекта минимум нужно рассматривать `MonitorRequestHandler`, `DesktopRequestHandler`, `StdioDelegationAdapter`, ACP codec/server, `BoundedWorkerPool`, `JsonFileTaskStore`, repository tools, terminal sessions, `DelegationService.apply`, session/spill/stats stores, model adapters, tool parser и release workflows.
- MUST distinguish `inspected`, `locally verified`, `CI-verified`, `staged`, `committed` and `published`. Source reading or a model's report is not runtime evidence.
- Every claimed gate MUST name the command/runner, exit code, relevant output and remaining blind spots. A skipped live backend, unavailable GPU, unbuilt installer or untested Windows path remains unknown.
- MUST preserve bounded diagnostics: request/task id, workspace/profile, state transition, command/check identity, exit code, timeout/cancel/termination result, rollback result and telemetry status. MUST NOT return raw credentials, bearer tokens, full unredacted prompts, arbitrary environment dumps or unrestricted tracebacks to a model/UI/log.
- Audit/documentation work MUST NOT silently fix a product defect. Product fixes, new tests, workflow changes and release metadata changes require their own explicit scope.

## 2. Loopback HTTP, framing and mutation endpoints

Reachable seams: `local_coding_agent/monitor.py` (`MonitorServer`, `MonitorRequestHandler`) and `local_coding_agent/desktop/server/_handlers.py`/`_server.py`.

- MUST bind control and mutation servers to validated loopback addresses by default and reject a non-loopback bind unless an explicit authenticated deployment mode exists. Do not infer safety from a default port or a browser UI.
- MUST protect every mutation endpoint with the same explicit origin/Host and per-server token policy. Desktop's `X-Desktop-Token`/Origin checks do not cover the separate monitor `/api/delegate` path; this is a current audit gap, not a claimed guarantee.
- MUST enforce a finite maximum `Content-Length` before allocation, reject missing/negative/malformed/overflowing lengths, and reject unsupported content types. A body reader MUST detect short reads, drain or close the request safely, and return a deterministic bounded error.
- MUST define connection policy explicitly. If keep-alive is not part of the contract, close responses; if it is supported, bound per-request reads and idle timeouts. Never rely on a process-wide socket timeout as a streaming watchdog.
- MUST bound upstream HTTP response size and use a per-chunk read timeout plus an idle watchdog. If headers have already been sent, record the stream termination cause and close the upstream connection; do not report a complete response for a truncated stream.
- MUST have tests for oversized bodies, malformed lengths, partial reads, invalid UTF-8/JSON, cross-origin mutation, missing/invalid token, connection reuse and upstream idle timeout. Static code inspection alone is insufficient.

## 3. Stdio and ACP framing

Reachable seams: `local_coding_agent/stdio.py`, `local_coding_agent/acp_server/_codec.py` and `local_coding_agent/acp_server/_server.py`.

- MUST keep a finite frame/header/body limit, count headers, reject invalid encoding where the protocol requires UTF-8, detect short bodies and avoid unbounded line/header buffering. A limit on JSONL does not automatically cover ACP or HTTP.
- MUST define how blocking stdin and header reads terminate. A process shutdown or cancellation path cannot wait forever on a reader that has no timeout/close strategy.
- Every subprocess-capable protocol path MUST use the same command allowlist, cwd/path boundary, timeout, pipe-drain, process-tree termination and evidence contract. The ACP `run_tests` seam must not become a privileged bypass merely because it accepts a command string.

## 4. Shared state, workers and persistence

Reachable seams: `ThreadingHTTPServer` handlers, `DesktopServer`, `SharedExecutionGate`, `BoundedWorkerPool`, `TaskRecord`, `JsonFileTaskStore` and desktop task/session persistence.

- MUST protect every shared mutable dict/list/counter and cross-request state with an explicit lock or a single-owner queue. The lock scope and lock ordering must be documented when more than one state store is touched.
- MUST bound admission queue, active workers, task age, cancellation wait and shutdown joins. Daemon threads are not a completion guarantee; shutdown must expose unfinished work as interrupted/unknown and retain a diagnostic reason.
- A worker MUST catch `Exception`, transition the task to `failed`, persist a bounded/redacted error kind and preserve the correlation id. Swallowing persistence errors (`except: pass`) is not acceptable for a production completion claim: surface `persistence_error` and make the task state unknown/failed as appropriate.
- Persistence MUST be atomic, validated on load, size-bounded and resistant to concurrent writers. Recovery of `queued`/`working` records must be explicit and idempotent. A best-effort task mirror must not silently lose updates or expose patch/check payloads without access/retention rules.
- State transitions MUST be monotonic and externally observable: queued → working → completed/failed/cancelled/interrupted. Never present an absent record, timed-out worker or failed persistence as completed.
- Tests MUST cover concurrent submit/status/cancel/shutdown, duplicate idempotency keys, persistence failure, corrupt records, recovery and worker exception. Race-free source-looking code is not enough.

## 5. Subprocess, terminal and process-tree control

Reachable seams: `BoundedRepositoryTools._run_tests`, repository `_BoundedPipeCollector`, `_kill_tree`, `terminal/_process.py`, `terminal/_session.py` and ACP `run_tests`.

- MUST avoid `shell=True` unless the command is an exact pre-declared check or an explicitly reviewed shell seam. User/model text must never become an arbitrary command.
- MUST drain stdout and stderr concurrently into bounded buffers, record truncation, and never deadlock on a full pipe. Output returned to a model/UI must be redacted and size-limited.
- MUST enforce a deadline and terminate the complete process tree. Record whether graceful termination, forced termination and descendant cleanup succeeded; a timeout with a live child is not a completed cancellation.
- MUST use an isolated, minimal environment for checks and exclude secret-like variables. Persistent terminal sessions inherit a wider environment and are a separate privileged surface; they require explicit enablement, allowlist, lifecycle, and secret policy rather than inheriting proposal-only trust.
- MUST bound process creation, concurrent sessions, output buffers and shutdown joins on Windows, Linux and macOS. Tests must exercise the platform-specific tree-kill and encoding paths where the runner permits.

## 6. Paths, allowlists and apply/rollback

Reachable seams: repository `_resolve_allowlisted`, `BoundedRepositoryTools`, validators, `DelegationService._execute`/`apply` and patch tooling.

- MUST normalize paths with platform case rules, resolve the candidate and workspace, reject absolute/drive/root/NUL escapes, and define policy for symlinks, junctions/reparse points and links created after validation. `Path.as_posix()` is presentation, not containment proof.
- MUST validate every touched path against the task allowlist after parsing the candidate diff, not only before asking the model. A rejected hunk/item must not allow an accepted sibling to escape scope.
- Before apply, record `HEAD`, staged/unstaged/untracked baseline, file hashes/metadata for overlapping paths and the exact accepted proposal/check set. If the checkout is dirty or another writer owns an overlapping path, refuse or use an isolated workspace.
- Rollback MUST restore only controller-owned changes and then compare the complete relevant baseline, including index/staging and untracked files. Never run `git restore .`, `git restore --staged .`, stash, reset or cleanup over a shared checkout as a generic rollback.
- On rollback failure, the operation is `failed_with_residual_changes`, not successful. Preserve a bounded recovery report and stop further writes until ownership is resolved.
- Tests MUST cover case-folding, `..`, absolute paths, symlink/junction/reparse traversal, dirty staged/unstaged/untracked worktrees, overlapping concurrent edits, apply failure and rollback failure.

## 7. Secrets, sessions, generated artefacts and telemetry

Reachable seams: `session_events.py`, `.local_agent_tasks.json` mirrors, `spill.py`, `stats.py`, desktop sessions and repository `_record` audit events.

- MUST classify user prompts, model output, tool arguments/results, patches, environment-derived values and generated files before persistence. Redact credentials by value/pattern and never assume a filename such as `.env` is the only secret source.
- MUST set and verify least-privilege file permissions where the platform supports them, use atomic writes, bound file size/retention, validate loaded JSON/JSONL/SQLite records, and define who may read/delete them. Generated artefacts are data, not trusted executable input.
- MUST keep audit events useful without storing full sensitive payloads by default. If a full payload is required for a local debug mode, it needs explicit opt-in, bounded retention and a visible warning.
- MUST report telemetry provenance, timestamp/freshness and status (`ok`, `unavailable`, `unsupported`, `error`, `unknown`). Missing GPU/latency/queue data remains `None`/unknown; zero is valid only when actually measured.
- Stats sinks and task/session mirrors MUST define cross-process concurrency behavior. A thread lock alone does not protect two processes writing the same JSONL/JSON file.
- Tests MUST verify redaction, permissions where supported, truncation, retention/error paths and that malformed or missing telemetry cannot be rendered as healthy zeroes.

## 8. Model context, VRAM, streaming and tool JSON

Reachable seams: `vram_fit.py`, `chat_compaction.py`, `atomizer.py`, `ollama_adapter.py`, controller parser and tool dispatch.

- MUST distinguish policy caps, character/byte estimates and backend-reported tokenizer/context limits. A preflight estimate is not proof that the backend will accept the request.
- VRAM fits MUST account for GQA (`n_kv_head`), layer/runtime overhead and a documented reserve (currently the policy target is 15–20%). Invalid geometry or unavailable telemetry must return `unknown`, not fit.
- Every streaming transport MUST have a bounded per-read timeout and active idle watchdog, close the response on error, preserve framing across arbitrary chunk boundaries and mark truncated output as incomplete. A single 30/300/600-second request timeout is not an idle watchdog.
- Tool JSON parsing MUST isolate each candidate call. One malformed, duplicate or unallowlisted item cannot poison valid siblings; return a pinpointed prescription and evidence of which items were accepted/rejected. Never execute a partially decoded command without revalidation.
- Tests MUST include context overflow boundary, unknown telemetry, GQA math, chunk split/invalid UTF-8, idle timeout and mixed valid/invalid/duplicate tool batches.

## 9. CLI, MCP, Skill and release parity

Reachable seams: CLI parser/commands, `mcp_server.py`, `skills/local-coding-agent/SKILL.md`, `_EMBEDDED_SKILL_MD`, `tests/test_skill_parity.py` and release workflows/manifests.

- Any `TaskEnvelope`, schema, subcommand, option, status or error contract change MUST update the disk Skill and embedded Skill together, then run the parity test and direct `--help`/`--json` probes. Do not claim 100% parity from one passing import.
- MCP and CLI adapters MUST share the same service-level policy for allowlist, checks, apply confirmation, timeouts, errors and telemetry. A transport-specific bypass is a security defect.
- A release gate MUST compare the complete release surface: `pyproject.toml`, `local_coding_agent/__init__.py`, `local_coding_agent/mcp_server.py`, `README.md`, `CHANGELOG.md`, `package.json`, `package-lock.json`, `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml` and the local `local-coding-agent-desktop` package record in `src-tauri/Cargo.lock`. It MUST build and inspect every claimed artifact, verify checksums/provenance, and confirm the tag points to the tested commit.
- Release workflows MUST validate exact tag/version/changelog agreement, least-privilege permissions and pinned/approved action revisions. A tag trigger or `workflow_dispatch` input is not proof that the tag is annotated or that the desktop installer exists.
- No commit, push, tag, release, PR, merge or publication may be performed without separate explicit user authorization. A clean local check is not publication evidence.

## 10. Incident and bounded-failure protocol

- On timeout, cancellation, malformed input, worker exception, persistence failure, child-process leak, partial stream, apply failure or rollback failure: stop the affected transition, mark the state conservatively, retain a bounded redacted diagnostic and expose the next recovery action.
- A successful HTTP status, task record, test command exit code or model response cannot override a lower-level failure that was not drained, verified, rolled back or persisted.
- Incident reports MUST include the seam, trigger, request/task id, affected workspace/profile, observed state, exact command/runner and exit code, containment/rollback result, evidence location, and unverified assumptions. Do not claim remediation until a new external runner confirms it.
