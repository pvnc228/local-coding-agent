# Production-sensitive seam audit

Дата: 2026-09-18
Scope: governance/rules/audit documentation only. Product code, tests, workflows, release metadata and user-owned dirty changes were not modified by this audit. This revision reconciles the report with the aggregate working tree after a parallel remediation stream.

## Executive status

The repository has a strong proposal-only and bounded-tools design on the main delegation path, but the production boundary is wider than that path. The most material reachable gaps are:

- the stdlib monitor exposes a mutation endpoint without the desktop token/origin policy;
- Desktop request framing is locally remediated, while monitor/other HTTP/ACP readers and upstream streaming watchdogs still have separate open seams;
- worker and persistence failures can be swallowed or reduced to generic diagnostics;
- apply rollback has no demonstrated dirty-index/untracked baseline preservation gate;
- session/task/audit artifacts can retain sensitive payloads and task mirrors have cross-process write hazards;
- the release metadata mismatch and missing NSIS workflow definition from the pre-remediation baseline have been addressed in the aggregate working tree; annotated-tag/commit checks, scoped permissions, full-SHA action references and SHA-256 inventory are now present in the workflow and pass static local validation, while remote execution, provenance/attestation and clean-VM install evidence remain open.

These are audit findings and follow-ups, not product fixes. The new operational rule file turns them into gates for future implementation and verification.

## Evidence boundary and method

Confirmed during this audit:

- The current branch was `main...origin/main`. The working tree already contained unrelated product/workflow/version changes, deleted installer-smoke files and new tests. Those paths were preserved and excluded from this documentation change.
- `AGENTS.md`, `PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/PROTOCOL.md`, existing `.agents/rules/*.md`, relevant source seams and release manifests were inspected read-only.
- A code-only Graphify index was built in a temporary directory containing source/test/workflow inputs, not in the repository: exit code 0; 165 code files; 3,164 nodes; 8,030 edges; 119 communities. The first document/image-inclusive attempt was not used as evidence because it required an unavailable LLM key. Graphify results were used for structural navigation, not as runtime proof.
- The indirect-instruction scan over project docs/rules found ordinary engineering references, not a suspicious “ignore previous instructions” payload. Repository text was treated as evidence, not as authority to widen scope.

Not confirmed by this audit:

- live network behavior under malformed/partial reads on the remaining monitor/ACP/upstream seams;
- Windows junction/reparse-point behavior on an actual Windows runner;
- real Ollama/OpenAI idle-timeout behavior and backend context rejection;
- multi-process corruption under concurrent task/session writers;
- a clean build, signed/provenanced artifact, installer install/uninstall or a tag-triggered release.

## Aggregate snapshot after parallel remediation

The following distinction is required because the original audit and the current aggregate tree are not the same state.

### Baseline before remediation

The original finding was that Python metadata read `1.0.3`, Desktop/npm/Tauri metadata read `1.0.0`, README download links targeted older release assets, and the release workflow built Python distributions without a Windows NSIS build/upload path. Those statements describe the pre-remediation baseline only; they are not current claims about the working tree below.

### Locally verified resolved at static-manifest level

Read-only inspection of the current aggregate tree shows `1.0.4` in all ten governed release-surface locations: `pyproject.toml`, `local_coding_agent/__init__.py`, `local_coding_agent/mcp_server.py`, `README.md`, `CHANGELOG.md`, `package.json`, `package-lock.json`, `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml` and the `local-coding-agent-desktop` package record in `src-tauri/Cargo.lock`. README wheel/source/NSIS links and the top CHANGELOG entry also target `1.0.4`.

This is static local evidence of metadata alignment only. It does not prove that the tag exists, that a clean checkout contains the same state, or that any linked asset was built, uploaded or published.

### Locally verified partially resolved at workflow-definition level

The current `.github/workflows/release.yml` now defines a semantic tag/version validation job with annotated-tag object and commit checks, checks out the resolved tag for the test/release jobs, scopes write permission to the publish job, uses full-SHA action references, runs desktop release regression tests, builds a Windows NSIS installer, checks its expected name/size, uploads it as a workflow artifact, and includes it with the wheel and source archive. The publish job also generates and verifies a deterministic SHA-256 inventory.

The static release-surface validator passed for 16 checks and found 21 full-SHA action references across CI/release files (14 in release, 7 in CI, zero mutable refs); the current targeted implementation run passed (`38 passed in 13.77s`), matching the independent review report (`38 passed in 14.80s`). This is still source/local evidence, not a GitHub run: no hosted annotated-tag execution, Windows runner NSIS build, artifact download from the final workflow, clean-VM install/uninstall or published release was verified in this session.

### Checksum evidence transition

- Pre-fix internal build-layout smoke was green: the three-entry manifest under `dist/...` matched the staged artifact paths and hashes (`build_layout_checksum=GREEN`, exit code `0`).
- The independent flat release-download oracle was red before remediation: the downloaded files were flat basenames while `SHA256SUMS.txt` contained `dist/...` paths; the reproduced oracle exited `1` with all three manifest paths missing.
- The current implementation changes the publish path to flat `release-staging`, generates the manifest from basenames and runs `sha256sum --check SHA256SUMS.txt` in that same directory. The current tree now contains the flat-layout regression, and the final targeted run is green (`38 passed in 13.77s`). This resolves the defect at local implementation/test level; hosted artifact readback and publication remain unverified.

### Still-open release risks

- Annotated-tag object/commit proof is implemented in the workflow and present in static validation, but no hosted run has executed it against a real tag; runtime release proof remains open.
- Least-privilege workflow permissions are implemented at source level (`contents: read` default and `contents: write` only on the publish job), but no hosted policy/run evidence was verified here.
- CI/release action references are full-SHA pinned in the current tree and the static validator found 21 such references (14 release, 7 CI, zero mutable refs); an approved dependency review and hosted execution are still not evidence in this session.
- The release workflow now defines a deterministic flat-basename SHA-256 inventory and verifies it before publication; local build-layout and flat-download evidence are green after remediation, but no final hosted-generated inventory was read back. Provenance/attestation/signing is not present.
- The NSIS file is checked for name and minimum size, but no clean-VM install/uninstall or post-install smoke evidence is present.
- The current working tree is dirty and parallel changes are not committed or published; therefore no release state can be called staged, committed, CI-verified or published from this session.

## Seam findings

### 1. Loopback HTTP and mutation authorization

**Seam → risk.** `local_coding_agent/monitor.py` uses `ThreadingHTTPServer` and `MonitorRequestHandler._handle_api_delegate` as a direct `/api/delegate` mutation path. A configurable host can be passed to `MonitorServer`; the handler reads the declared body and dispatches `Controller.run(..., apply=...)`. If this server is exposed beyond a trusted loopback boundary, an attacker can attempt task execution or apply through the browser-visible endpoint. Even on loopback, a local web page may be able to induce requests unless the endpoint has an explicit browser-origin/token policy.

**Current coverage.** The default host is `127.0.0.1`; desktop server code has a separate loopback check, `Host` validation, `X-Desktop-Token` HMAC comparison and Origin validation. Monitor response headers are bounded and events are lock-protected.

**Missing rule.** The desktop policy is not a global HTTP contract and does not cover monitor. There is no demonstrated monitor body cap, auth/token check, CSRF/origin rule or non-loopback rejection.

**Recommended wording.** “Every mutation endpoint, including `MonitorRequestHandler`, MUST enforce validated loopback binding plus the shared Host/Origin/token policy; a default address is not authentication. Non-loopback mode requires explicit authenticated deployment configuration and an external runner test.” This is now in `.agents/rules/production_sensitive_delivery.md`, §2.

### 2. HTTP framing, body limits and connection lifecycle

**Seam → risk.** The original finding covered both Desktop and monitor handlers. The Desktop request-framing seam is now locally remediated; remaining risk is in the separate monitor/other HTTP/ACP readers and in the upstream chat stream, which can still emit a truncated response after headers.

**Current coverage.** Desktop `DesktopRequestHandler` now enforces an 8 MiB request-body cap, one non-negative decimal `Content-Length`, rejects malformed/negative/duplicate lengths and `Transfer-Encoding`, reads in 64 KiB chunks with a 2-second idle timeout, detects short reads, drains rejected bodies only up to 64 KiB, closes unsynchronized connections, emits `Connection: close` on framing errors and returns bounded deterministic framing errors. The wire-level framing tests cover these cases. Stdio JSONL has a 64 KiB request cap and oversized-line drain. ACP codec has a 10 MiB message cap, header count cap and short-body checks for bytes streams. Ollama framing preserves arbitrary chunk boundaries, strict-decodes UTF-8 and caps a stream frame at 4 MiB.

**Missing rule.** The Desktop request-framing gap is resolved locally, but limits remain transport-specific. Monitor still lacks the same body/auth/framing contract; ACP header/read timeout and text-stream edge cases remain unverified; and the desktop upstream proxy still lacks an independently verified per-chunk idle watchdog and explicit incomplete-stream evidence after headers.

**Recommended wording.** “The Desktop framing contract is a locally verified reference seam: 8 MiB cap, single `Content-Length`, 64 KiB reads, 2-second idle timeout, partial-read detection, bounded rejected drain and explicit close. Monitor, ACP and upstream readers MUST reach equivalent external evidence before being called covered; truncated upstream streams remain incomplete.” §2–3 now states this.

### 3. Streaming watchdogs

**Seam → risk.** `OllamaClient` passes `stream_idle_timeout_seconds` to its transport and checks elapsed time between framed lines, but `UrllibTransport.stream` uses a single `urlopen(..., timeout=...)`; urllib read behavior is not a separately verified per-chunk watchdog. The desktop OpenAI-compatible proxy uses `HTTPConnection(..., timeout=600)` and `read(4096)` without an independent idle watchdog. A stalled upstream can therefore occupy a worker/thread far beyond the intended task budget, or a timeout can leave a client with a syntactically incomplete response.

**Current coverage.** The adapter has an explicit idle setting, strict framing and cleanup through the response context manager. Repository subprocess checks have deadlines and bounded pipe collectors.

**Missing rule.** No external runner evidence proves per-read timeout, stream close and task cancellation across all transports; 600 seconds is not a watchdog.

**Recommended wording.** “Each streaming transport MUST have a bounded per-chunk read timeout and active idle watchdog; timeout/cancel MUST close the upstream and record incomplete output.” §8 makes this a gate.

### 4. Shared state, task status and worker failures

**Seam → risk.** `BoundedWorkerPool` protects queue/job/idempotency state and `DesktopServer` has several locks, but `_persist_job` can swallow persistence exceptions. The worker converts any delegation exception to a generic `worker_error`; the public status path does not preserve a bounded diagnostic sufficient to tell backend failure, persistence failure, timeout or cancellation apart. Desktop task writes also swallow load/write exceptions.

**Current coverage.** Queue admission is bounded; state transitions and idempotency are explicit; worker finalization persists a terminal state; desktop workers catch exceptions and mark failed/cancelled; task recovery marks interrupted work.

**Missing rule.** Persistence failure is not a first-class state, and daemon-thread shutdown/short joins do not prove that running work stopped. Cross-process desktop task mirror writes are explicitly best effort and uncoordinated.

**Recommended wording.** “Worker exceptions MUST transition to failed with bounded/redacted error kind and correlation id; persistence failures MUST be surfaced as persistence_error/unknown or failed; daemon threads and best-effort mirrors MUST not be reported as completed.” §4 and §10 now require this.

### 5. Subprocesses, pipes and terminal sessions

**Seam → risk.** `BoundedRepositoryTools._run_tests` has a strong bounded path, but ACP `run_tests` accepts `args["command"]` and calls `subprocess.run(..., shell=True, cwd=workspace, PIPE, timeout=...)`. The protocol seam is therefore broader than the repository-tool allowlist and does not demonstrably share process-tree kill and collector behavior. Persistent terminal sessions inherit the full process environment and are a materially wider privilege surface than proposal-only tools.

**Current coverage.** Repository checks require an exact declared check, drain stdout/stderr concurrently, bound output, use isolated env filtering, enforce deadlines and kill process trees. Terminal fallback and Windows `taskkill` paths exist.

**Missing rule.** There is no single mandatory execution contract across ACP/desktop/terminal; command shell usage and descendant cleanup are not uniformly gated or evidenced. A successful parent exit does not prove the process tree was bounded.

**Recommended wording.** “Every subprocess seam MUST use exact allowlisting or explicit reviewed shell policy, concurrent bounded pipe drains, deadline, full-tree termination and termination evidence; terminal sessions require explicit privileged enablement.” §5.

### 6. Paths, links and allowlists

**Seam → risk.** Repository path resolution uses `Path.resolve()` and Windows case folding for candidates, which is a good containment base. Declared allowlist normalization is presentation/case normalization rather than a complete link/reparse policy. A symlink/junction/reparse point can change the meaning of a path between admission and use unless the policy is explicit and rechecked.

**Current coverage.** Absolute/drive/root/NUL escapes are rejected; resolved candidates must be relative to the resolved workspace; allowlist matching folds case on Windows; spill paths have traversal checks.

**Missing rule.** No evidence-backed policy defines whether links are rejected, pinned to an inode/file identity, or allowed only after final resolution on Windows/Linux/macOS. No targeted test was run for junctions/reparse points.

**Recommended wording.** “Containment is proven only after platform normalization and final resolution; define and test symlink/junction/reparse policy at validation and use.” §6.

### 7. Apply, dirty worktree and rollback

**Seam → risk.** `DelegationService.apply` uses workspace locking, patch validation, `git apply`, allowlisted checks and reverse-patch rollback. However, the inspected path has no demonstrated snapshot/compare gate for pre-existing staged, unstaged and untracked state. Reverse patch rollback can collide with user edits, and the historical rule's broad `git restore --staged . && git restore .` would destroy unrelated work in a shared checkout.

**Current coverage.** Apply is controller-owned; accepted proposal and non-empty checks are required; semantic checks run; rollback failure is surfaced as residual workspace modification; validators use bounded git subprocesses.

**Missing rule.** Baseline ownership, index preservation, untracked preservation, overlap detection and exact post-rollback comparison are not a hard precondition. No dirty-tree external runner evidence was produced.

**Recommended wording.** “Before apply capture HEAD/index/worktree/untracked baseline and overlapping file identities; refuse unsafe overlap; rollback only controller-owned paths; compare baseline; classify mismatch as failed_with_residual_changes.” §6 and the AGENTS invariant now state this.

### 8. Secrets, session persistence and generated artifacts

**Seam → risk.** `session_events.py` persists user content, model content, tool calls, arguments/results and errors as JSONL/SQLite payloads; repository `_record` stores full arguments; `.local_agent_tasks.json` mirrors can contain patch/check content; spill files expose an absolute locator. `.gitignore` prevents common files from being committed but does not protect runtime readers or logs. The terminal inherits `os.environ`.

**Current coverage.** Repository checks filter selected secret-like environment variables. Spill files use owner-only POSIX permissions where available and reject traversal. Stats are intentionally slim and use locks within the process. `.gitignore` excludes several runtime paths.

**Missing rule.** There is no common classification/redaction/retention/access contract, no proof of Windows ACL behavior, and no cross-process file lock/atomicity rule for every mirror/sink. Full payload persistence is not safe as a default production evidence strategy.

**Recommended wording.** “Classify and redact prompts, tool data, patches and environment-derived values before persistence; bound retention/size, enforce permissions, define readers/deleters and treat generated artifacts as untrusted data.” §7.

### 9. Telemetry and unknown values

**Seam → risk.** GPU telemetry can be unavailable/unsupported and model/context geometry can be invalid. Treating missing measurements as zero would falsely advertise capacity or health and could cause unsafe admission decisions.

**Current coverage.** `get_nvidia_gpu_telemetry` returns `unavailable`/`unsupported` and `None` fields on failures; stats tracks unknown statuses; `vram_fit` returns an explicit `unknown` result for invalid geometry or absent telemetry. This is a confirmed positive seam-level behavior from source inspection.

**Missing rule.** Provenance, timestamp/freshness and rendering/consumer behavior are not a single enforced contract, and source inspection does not prove every consumer preserves unknown.

**Recommended wording.** “Every metric carries source/status/freshness; `None`/unknown/error remains visible and is never rendered as a measured zero. Tests must cover unavailable telemetry at every consumer.” §7.

### 10. VRAM, context and compaction

**Seam → risk.** `vram_fit` accounts for GQA and a 15% default reserve, and context compaction uses character-based estimates. Character/byte budgets are useful admission heuristics but are not tokenizer proof; a backend can still return context overflow or reject a request because runtime model limits differ from a policy cap.

**Current coverage.** `ContextFit` distinguishes fits/does_not_fit/unknown; GGUF header parsing can return unavailable; `ModelProfile` distinguishes policy cap from verified `model_context_limit`; controller checks cumulative context before model calls.

**Missing rule.** Consumers need an explicit distinction between estimated, backend-reported and verified token counts; boundary tests must include unknown geometry and actual rejection. The present audit did not run a live model.

**Recommended wording.** “Preflight estimates are not backend guarantees; use backend/tokenizer authority when available and fail conservatively on unknown. Preserve GQA/runtime overhead/reserve in the formula.” §8.

### 11. Tool-call JSON and batch poisoning

**Seam → risk.** Controller parsing has structured prescriptions, duplicate-call detection and per-call argument handling. The text parser can still abort a batch for some unallowlisted `return` content; a malformed or malicious sibling must not suppress valid independent calls or produce a partial execution ambiguity.

**Current coverage.** JSON decode errors are normalized; valid items can be promoted individually in several paths; retries use syntax prescriptions; invalid tool args become a bounded tool error rather than a process crash.

**Missing rule.** The independent-item guarantee is not universal and needs a targeted mixed-batch external test. The report must identify accepted, rejected and unexecuted items separately.

**Recommended wording.** “Parse and validate each candidate independently; one invalid/duplicate/unallowlisted item cannot poison valid siblings; execute only revalidated items and emit a pinpointed prescription.” §8.

### 12. CLI/MCP/Skill contract parity

**Seam → risk.** `mcp_server.py` exposes `delegate_code` and optional `apply_proposal`; CLI/parser and the skill docs are separate adapters. Contract drift can create a safer proposal-only CLI path and a more permissive MCP/desktop path, or leave agents with stale parameter/status assumptions.

**Current coverage.** Disk `skills/local-coding-agent/SKILL.md` and `_EMBEDDED_SKILL_MD` are covered by `tests/test_skill_parity.py`; service-level policy is intended to be transport-neutral; MCP task mode requires explicit enablement.

**Missing rule.** One parity test does not prove command/options/JSON/error/telemetry parity, and no release gate enumerates the full adapter manifest. Direct help and representative JSON probes are required.

**Recommended wording.** “Any envelope/schema/subcommand/status change updates disk+embedded Skill and runs parity, help and JSON probes; adapters share service policy and cannot bypass gates.” §9.

### 13. Version, desktop artifact and release workflow

**Seam → risk.** Before the parallel remediation, Python/Desktop versions, README release links and the claimed desktop artifact surface diverged. The current aggregate tree has aligned `1.0.4` metadata and a declared NSIS build/upload path, but a workflow file and static manifest alignment do not prove a tag-triggered, reproducible, least-privilege, signed or installable release.

**Current coverage.** Static local inspection confirms the ten release-surface values and `1.0.4` links. The current targeted implementation run passes (`38 passed in 13.77s`); the independent repeat reported by the review was also `38 passed in 14.80s`. A separate static validator confirms annotated-tag/commit checks, scoped permissions, 21 full-SHA action references (14 release, 7 CI, zero mutable refs), NSIS build/upload and the flat `release-staging` SHA-256 inventory definition. The release workflow therefore has materially improved source-level coverage, but no remote run or clean-VM artifact evidence was produced.

**Missing rule.** Hosted execution evidence, generated artifact/hash readback from the final workflow, provenance/attestation/signing, clean-VM install/uninstall and published-release verification remain required. The prior nested-path checksum defect is addressed in the current `release-staging` workflow and flat-layout regression, but it must not be represented as hosted release success without CI/artifact evidence.

**Recommended wording.** “A release is publishable only after exact full-surface manifest/tag/CHANGELOG agreement, a hosted proof of the annotated tag pointing to the tested commit, green CI, every claimed artifact built and inspected, verified checksums, provenance/attestation where required, approved pinned actions and clean-VM install/uninstall evidence. Source definitions and local targeted tests are partial evidence, not publication proof.” §9 and `AGENTS.md` release boundary now state this.

### 14. Incident evidence and bounded failure

**Seam → risk.** Errors cross HTTP, worker, model, subprocess, apply and persistence boundaries. Without a common incident record, an HTTP 200, a completed parent process or a task JSON record can conceal a partial stream, live descendant, lost persistence write or incomplete rollback.

**Current coverage.** Several seams already return structured status/error kinds, bounded output and audit events; stats intentionally avoids raising from telemetry sinks.

**Missing rule.** There is no single required incident ledger containing the correlation id, runner/exit code, baseline, termination and rollback evidence, or explicit unknown assumptions.

**Recommended wording.** “On timeout, cancellation, persistence/rollback failure, partial stream or child leak, stop the transition, mark conservative state, retain bounded redacted evidence and expose recovery action. Do not claim remediation before a new external runner.” §10 and factual reporting rules now state this.

## Follow-ups deliberately not implemented here

1. Add monitor authentication/CSRF and equivalent body/partial-read/connection handling; close the remaining ACP framing/header-timeout and upstream stream-watchdog gaps. Desktop request framing is locally remediated and covered by wire-level tests.
2. Unify ACP, desktop, monitor and terminal subprocess execution behind one reviewed policy; remove or explicitly isolate broad `shell=True` paths.
3. Add persistence error states, cross-process locking/atomicity, corruption validation, retention and task/session artifact redaction.
4. Add dirty-tree/index/untracked baseline capture and safe rollback verification; refuse overlapping user changes.
5. Define link/reparse policy and run Windows/Linux/macOS path tests.
6. Add mixed tool-call batch tests and ensure valid siblings survive invalid/unallowlisted candidates.
7. Complete the remaining release evidence: execute the new annotated-tag/permission/full-SHA/NSIS/flat-SHA-256 gates in hosted CI, read back final generated artifacts and hashes, add provenance/attestation/signing if required, and run clean-VM installer install/uninstall smoke. The source-level implementation, flat-layout regression and targeted local regressions are present; they are not a published-release claim.
8. Add a machine-readable incident/evidence ledger consumed consistently by CLI, MCP, Desktop and reports.

## Documentation consistency check

`PROJECT.md` and `docs/ARCHITECTURE.md` describe proposal-only control, bounded tools, external checks, loopback monitoring, task recovery and release/CLI surfaces as intended architecture. This audit does not reinterpret those goals as runtime proof. The new rules preserve the intended non-goals (no arbitrary shell, no automatic publication, controller-owned apply) and make the missing evidence gates explicit. The existing historical architecture rule mentions broad `git restore`; the updated `AGENTS.md` and this rule supersede that unsafe rollback wording for shared checkouts without rewriting the historical note.
