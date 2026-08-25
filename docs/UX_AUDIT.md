# End-User UX Audit — v0.8.0

> Audit date: 2026-08-22 · Method: hands-on CLI/server testing, wheel inspection,
> AST-level import analysis of the delegation path. Every claim carries a
> `file:line` reference. Purpose: track all findings to fixed.
>
> **Remediation pass 2026-08-23 (v0.8.1):** all concrete defects below are fixed;
> wire-or-relabel decisions recorded inline. See CHANGELOG 0.8.1 for the full list.

## Verdict

The safety core (allowlist → proposal-only → external checks → auto-rollback)
is real and genuinely enforced — better discipline than many popular tools.
But from an average user's seat this is currently an **envelope-first batch
tool with a demo-grade shell**, not a usable coding harness. One critical bug
breaks every non-editable install, and a large share of the advertised feature
surface is machinery with no road leading to it.

---

## 1. Broken today (reproduced, not inferred)

### 1.1 CRITICAL — published install path produces a dead CLI
- `pyproject.toml:35` hardcodes `packages = ["local_coding_agent", "local_coding_agent.desktop"]`.
- Built wheel ships **only** top-level modules + `desktop`. Missing subpackages:
  `cli/`, `controller/`, `acp_server/`, `benchmark/`, `hooks/`, `lsp/`, `terminal/`.
- Console entry point is `local_coding_agent.cli:main` (`pyproject.toml:27`) →
  any non-editable install (`pip install local-coding-agent[mcp]`, README step 1)
  fails with `ModuleNotFoundError` on first run.
- Fix: use `[tool.setuptools.packages.find]` discovery or list all packages.
- Status: [x] FIXED (v0.8.1) — packages.find auto-discovery; wheel contents verified.

### 1.2 HIGH — Windows task files with UTF-8 BOM are rejected
- PowerShell 5.1 `Set-Content -Encoding UTF8` writes a BOM (the default Windows
  workflow). `--task-file` rejects it:
  `{"kind":"input","message":"Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)"}`
- Contradicts the "Cross-Platform Resilience" invariant in AGENTS.md.
- Fix: decode task files with `utf-8-sig`.
- Status: [x] FIXED (v0.8.1) — task files (`cli/_input.py`) and patch files
  (`cli/_handlers.py --patch-file`) decode with `utf-8-sig`.

### 1.3 HIGH — backend-down errors are hostile and misclassified
- With Ollama stopped, `delegate` returns the raw localized WinError text
  three times in one JSON blob (summary + risks + error), no guidance
  ("Is Ollama running? Start it with `ollama serve`").
- Via some paths a backend 404 is reported as `"kind": "input"`
  (`local_coding_agent/cli/_handlers.py:66-68`).
- The desktop app already has a friendly translator for this
  (`local_coding_agent/desktop/server/_handlers.py:986-991`) — the CLI doesn't use it.
- Fix: reuse the desktop error translator; classify as `backend_error`; add hint text.
- Status: [x] FIXED (v0.8.1) — classifier moved into `ollama_adapter.classify_backend_error`
  (shared by CLI + desktop); controller's model boundary emits `backend_offline` /
  `backend_error` kinds; CLI annotates results with actionable hints.

### 1.4 HIGH — monitor dashboard can never show real data
- `monitor` creates a fresh empty `DelegationStats` per launch
  (`local_coding_agent/cli/_handlers2.py:336`); `DelegationStats.record()`
  has zero production callers. `/stats` verified live: `"total": 0` forever.
- R20 "Streaming Progress & Token Telemetry" advertised as shipped; nothing feeds it.
- Fix: wire service/controller events into a stats sink shared with the server,
  or mark the dashboard claim experimental.
- Status: [x] FIXED (v0.8.1) — every delegation appends to `.local-run/stats.jsonl`;
  MonitorServer aggregates the cross-process journal with in-process stats.

### 1.5 MEDIUM — `test-run` TPS is hardcoded
- The smoke test always prints `85.0 tokens/sec`: it reads audit keys
  (`eval_duration_ns`/`eval_tokens`) that the controller never writes
  (`local_coding_agent/smoke.py:196-203` vs bare `{"event":"model_response","turn":n}`).
- Fix: emit the timing keys from the controller or drop the number.
- Status: [x] FIXED (v0.8.1) — controller emits `eval_tokens`/`eval_duration_ns`;
  TPS is computed from real data or omitted entirely.

### 1.6 LOW — repo hygiene
- Bare `pytest` from repo root fails during collection (picks up `scratch/`
  and `tests_experiment/`; `tests_experiment/subagent_solution/test_rate_limiter.py`
  errors). No `[tool.pytest.ini_options]` / `testpaths` in pyproject.
- `service.py:166-168` prints raw tracebacks to **stdout**, corrupting the JSON
  stream for scripted consumers. Move to stderr or embed in result.
- Status: [x] FIXED (v0.8.1) — `testpaths = ["tests"]`; traceback explicitly to stderr.

---

## 2. Missing vs mainstream harnesses (Aider / Claude Code / OpenHands / LM Studio)

| Capability | Mainstream | This project |
|---|---|---|
| Freeform prompt to start work | Core UX | `delegate` requires hand-authored JSON envelope; `checks` de-facto mandatory (`service.py:260-265` refuses to apply without one) |
| Multi-turn interactive chat | Core UX | None. CLI `chat` is single-shot; desktop chat stateless one-turn (`desktop/server/_handlers.py:789-819`). Only ACP stdio has true multi-turn, no human terminal client |
| Any installed model | Any model name | **No `--model` flag exists anywhere.** Only 20 hardcoded profiles, 17 pointing at private tags (`codex-*`, models on `Q:\AI\Models\...`) |
| Sessions resume/list/history | `--continue`/`--resume` | None in CLI. Full engine exists (`session_events.py`, `session_query.py`) — nothing calls it |
| Streaming tokens / progress | Standard | Silent blocking until final JSON; adapter streams internally and discards (`ollama_adapter.py:101-137`) |
| Patch review before apply | Colored diff + y/n | Raw JSON dump; desktop has diff view, CLI doesn't |
| Git checkpoints | Aider auto-commits | Rollback only on failed apply; accepted patches land uncommitted |
| Config persistence | rc/config files | No config file/env var for profile/workspace defaults |

Default-model trap: out of the box the tool defaults to `qwen2.5-1.5b`
(`local_coding_agent/cli/_parser.py:21,74`) — one of the weakest coding models;
first-run success will be near-zero without reading source.

Status: [x] RESOLVED where cheap (v0.8.1): `--model` override on root/delegate/chat,
`LCA_PROFILE`/`LCA_WORKSPACE` env defaults, default profile raised to `qwen2.5-coder`,
multi-turn `chat --repl` with persistent sessions via SessionLog, `sessions list|show`
front door over the FTS5 engine, proposals persisted to `.local-run/proposals/`.
[ ] OPEN (deferred, product-scale): freeform zero-envelope delegation, streaming
tokens to the terminal, diff preview + confirm before apply, git checkpoint commits.

---

## 3. Advertised-but-unwired features (trust gap)

Verified via AST import analysis: `controller/_controller.py` imports only
`atomizer, context_manager, prescriptions, repository_tools, validators`. Therefore:

- **R17 AST-guided context compaction**: controller never calls `ast_compactor`;
  standalone `skeletonize` command only. [x] RELABELED — ROADMAP marks R17 as a
  standalone experimental utility; wiring would break byte-exact SEARCH/REPLACE anchors.
- **R18 semantic linter pre-gates**: controller never calls `semantic_linter`;
  standalone `lint-patch` only. [x] WIRED (v0.8.1) — enforced in candidate validation
  (with `SEMANTIC_LINT_FAILED` prescription feedback loop) and mediated apply.
- **R24 read-before-write gate**: `observation_policy.FsObservationGate` has no
  production callers. [x] RELABELED — ROADMAP marks it a reference implementation;
  enforcement tracked as future work.
- **R25/R26 model tool suites (LSP, terminal_\*)**: the delegated model gets
  exactly 5 tools — `list_files, read_file, search_text, propose_patch, run_tests`
  (`controller/_constants.py:22-95`). LSP/terminal/grep/spill are human CLI
  utilities, not agent capabilities, despite ROADMAP wording. [x] RELABELED in ROADMAP.
- **Orphaned entirely** (tested but reachable by nothing): plan_mode lifecycle,
  subagent coordinator, hooks bridge, session events/search, task_store
  (`serve-mcp` passes none), delegator, stats, stdio adapter. [x] PARTIALLY WIRED:
  session events/search now reachable via `chat --repl` + `sessions`; stats wired
  via the JSONL journal. Remaining orphans are documented scaffolding.
- CHANGELOG claims MCP progress notifications; `mcp_server.py` exposes 2 tools +
  1 resource, no progress wiring. [x] CORRECTED — claim removed from ROADMAP/R20.

Action: either wire each into the runtime or relabel as experimental/scaffold
in README, ROADMAP, CHANGELOG. → Done for all items above.

---

## 4. Friction inventory (cumulative papercuts)

- Mixed-language UX: Russian help strings (`--apply  Применить принятый патч…`)
  in an otherwise English CLI; localized OS error text leaking through. [x] FIXED (CLI help English-only).
- Port collisions: `monitor`, `ui/app`, `desktop` all default to 8765. [x] FIXED (monitor 8765 / ui 8766 / desktop 8767).
- Duplicated controls: global flags `--unload-model/--vram-limit-bytes/--calibrate-workers`
  duplicate the `memory`/`calibrate` subcommands. [ ] WON'T FIX — kept for backward
  compatibility; subcommands remain canonical.
- Proposals ephemeral: `apply_proposal` resolves from an in-memory LRU keyed by
  `(caller_id, workspace_ref, request_id)` (`service.py:105,125,244-248`);
  restart/eviction → `"unknown_proposal"`. CLI patches land nowhere on disk —
  copy out of stdout or rerun with `--apply`. [x] MITIGATED — CLI delegate writes
  `.local-run/proposals/<task_id>.patch` (+ `patch_file` key). The in-memory LRU
  itself remains by design (proposal-only ownership).
- Docs drift: README mermaid says "Gateway (v0.5.0)"; QUICKSTART promises a
  60-second setup whose step 1 is the broken wheel (see 1.1). [x] FIXED — version
  string removed from diagram; wheel repaired so the promise holds.
- No uninstall/cleanup path documented for `init-mcp --write` / `init-skill --write`. [x] FIXED — README "Uninstall / Cleanup" section.

---

## 5. What genuinely works (keep)

- Safety pipeline is real: allowlist enforcement, proposal-only default,
  external runner evidence required, auto-rollback on failed checks
  (`service.py`, `controller/_controller.py:475-537`) — the project's moat.
- `doctor`, `init-mcp` (dry-run preview), `test-run --mock`, `profiles list`
  behave as documented with decent ASCII output.
- Desktop server API richer/friendlier than CLI (structured status, GPU telemetry,
  session store, translated errors).
- SEARCH/REPLACE-over-unified-diff for small models is empirically justified.

---

## 6. Priority fixes (ordered by user impact)

1. **P0** Fix packaging (§1.1) — nothing else matters until `pip install` works. [x] DONE (v0.8.1)
2. **P0** `utf-8-sig` task loading + backend-error reclassification/translation (§1.2, §1.3). [x] DONE (v0.8.1)
3. **P1** `--model` override + config file for defaults; stop defaulting to 1.5B toy (§2). [x] DONE (v0.8.1 — `--model`, `LCA_PROFILE`/`LCA_WORKSPACE`, default `qwen2.5-coder`)
4. **P1** Real REPL chat loop with sessions (machinery exists; needs terminal front door). [x] DONE (v0.8.1 — `chat --repl`, `sessions list|show`)
5. **P1** Wire-or-amputate: feed `DelegationStats` from service, integrate
   ast_compactor/linter into controller, relabel unreachable modules (§3, §1.4). [x] DONE (v0.8.1 — stats journal wired, linter wired, R17/R24/R25/R26 relabeled)
6. **P2** stderr progress during delegation; diff preview + confirm before apply;
   git checkpoint commit on accepted patches; pytest testpaths config;
   English-only help strings; distinct default ports (§4, §1.6). [x] PARTIAL — testpaths,
   English help, distinct ports done; stderr live progress, diff preview/confirm and
   git checkpoints deferred (product-scale, see §2 OPEN list).

Honest one-line pitch today: *a very safe proposal-only micro-task executor for
agents*. That core deserves polish; everything above that line should be wired
or clearly labeled scaffolding.
