"""Argument parser construction for the local-coding-agent CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..profiles import list_profiles

DEFAULT_PROFILE = "qwen2.5-coder"


def _default_profile() -> str:
    return os.environ.get("LCA_PROFILE") or DEFAULT_PROFILE


def _default_workspace() -> Path:
    return Path(os.environ.get("LCA_WORKSPACE") or Path.cwd())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-agent",
        description="Local Coding Agent: Bounded controller for atomic coding tasks.",
    )

    # Root options (backward compatibility)
    parser.add_argument("--task", help="UTF-8 JSON task envelope (inline JSON string or file path)")
    parser.add_argument("--task-file", type=Path, dest="task_file", help="Path to UTF-8 JSON task envelope file")
    parser.add_argument("--workspace", type=Path, default=_default_workspace(), help="Workspace directory (env: LCA_WORKSPACE)")
    parser.add_argument("--profile", choices=list_profiles(), default=_default_profile(), help="Model profile to use (env: LCA_PROFILE)")
    parser.add_argument("--model", help="Override the profile model tag for any installed Ollama/llama.cpp model (e.g. qwen2.5-coder:7b)")
    parser.add_argument("--endpoint", help="Override the profile Ollama endpoint")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the accepted patch to the workspace instead of proposal-only mode",
    )
    parser.add_argument("--num-ctx", type=int, help="Override model context window in tokens")
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run the fixed proposal-only benchmark instead of one task",
    )
    parser.add_argument(
        "--benchmark-model",
        action="append",
        choices=list_profiles(),
        dest="benchmark_models",
        help="Benchmark one named profile; repeat the option for multiple models",
    )
    parser.add_argument("--benchmark-repeats", type=int, default=1)
    parser.add_argument("--benchmark-timeout-seconds", type=float, default=300)
    parser.add_argument(
        "--benchmark-output",
        type=Path,
        default=Path(".local-run") / "benchmarks" / "latest.json",
    )
    memory_group = parser.add_mutually_exclusive_group()
    memory_group.add_argument("--unload-model", metavar="MODEL", help="Unload one model from Ollama VRAM")
    memory_group.add_argument("--unload-all", action="store_true", help="Unload every model currently held by Ollama")
    parser.add_argument("--vram-limit-bytes", type=int, help="Evict unprotected models until this VRAM budget fits")
    parser.add_argument("--keep-model", action="append", default=[], help="Model name to protect during VRAM eviction")
    parser.add_argument(
        "--calibrate-workers",
        type=int,
        metavar="VRAM_BYTES",
        help="Derive a bounded worker count for the selected profile model within this VRAM budget",
    )
    parser.add_argument(
        "--parallel-context-bytes",
        type=int,
        help="Measured incremental VRAM estimate per concurrent request context/KV cache",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # 1. delegate (run)
    del_p = subparsers.add_parser("delegate", aliases=["run"], help="Delegate an atomic task to local model")
    del_p.add_argument("--task", help="UTF-8 JSON task envelope (inline string or file path)")
    del_p.add_argument("--task-file", type=Path, help="Path to task JSON envelope file")
    del_p.add_argument("--workspace", type=Path, default=_default_workspace(), help="Workspace directory (env: LCA_WORKSPACE)")
    del_p.add_argument("--profile", choices=list_profiles(), default=_default_profile(), help="Model profile to use (env: LCA_PROFILE)")
    del_p.add_argument("--model", help="Override the profile model tag for any installed Ollama/llama.cpp model")
    del_p.add_argument("--endpoint", help="Override Ollama/OpenAI endpoint")
    del_p.add_argument("--max-turns", type=int, default=4, help="Maximum conversation turns")
    del_p.add_argument("--num-ctx", type=int, help="Override context window in tokens")
    del_p.add_argument("--speculative-drafts", type=int, default=1, help="Number of concurrent speculative drafts to race")
    del_p.add_argument("--apply", action="store_true", help="Apply accepted patch directly with auto-rollback")
    del_p.add_argument("--json", action="store_true", help="Ensure JSON output")

    # 2. decompose (atomize)
    dec_p = subparsers.add_parser("decompose", aliases=["atomize"], help="Preflight and decompose wide tasks into atomic envelopes")
    dec_p.add_argument("--task", help="UTF-8 JSON task envelope (inline string or file path)")
    dec_p.add_argument("--task-file", type=Path, help="Path to task JSON envelope file")
    dec_p.add_argument("--strategy", choices=["by_files", "per_file"], default="by_files", help="Decomposition strategy")
    dec_p.add_argument("--budget-files", type=int, default=5, help="Maximum allowed files per subtask envelope")
    dec_p.add_argument("--budget-bytes", type=int, default=32000, help="Maximum context bytes")
    dec_p.add_argument("--budget-checks", type=int, default=3, help="Maximum test checks per envelope")
    dec_p.add_argument("--json", action="store_true", help="Output decomposition result in JSON format")

    # 3. profiles
    prof_p = subparsers.add_parser("profiles", help="List and inspect model profiles")
    prof_p.add_argument("profile_action", nargs="?", choices=["list", "get"], default="list", help="Action: list or get")
    prof_p.add_argument("name", nargs="?", help="Profile name (for 'get')")
    prof_p.add_argument("--check-ollama", action="store_true", help="Query Ollama /api/tags to show local install status")
    prof_p.add_argument("--endpoint", default="http://127.0.0.1:11434", help="Ollama endpoint")
    prof_p.add_argument("--json", action="store_true", help="Output profiles in JSON format")

    # 4. memory
    mem_p = subparsers.add_parser("memory", help="Inspect and manage Ollama VRAM allocation")
    mem_p.add_argument("memory_action", choices=["status", "unload", "unload-all", "enforce"], help="Memory action")
    mem_p.add_argument("model", nargs="?", help="Model name to unload (for 'unload')")
    mem_p.add_argument("--limit", type=int, dest="limit", help="VRAM limit in bytes (for 'enforce')")
    mem_p.add_argument("--keep", action="append", default=[], help="Model name to protect from eviction")
    mem_p.add_argument("--profile", default=DEFAULT_PROFILE, help="Profile to derive client settings")
    mem_p.add_argument("--endpoint", default="http://127.0.0.1:11434", help="Ollama endpoint")
    mem_p.add_argument("--json", action="store_true", help="Output memory status in JSON")

    # 5. calibrate
    cal_p = subparsers.add_parser("calibrate", help="Calculate worker capacity from VRAM budget")
    cal_p.add_argument("--vram-bytes", type=int, required=True, help="Target VRAM budget in bytes")
    cal_p.add_argument("--profile", choices=list_profiles(), default="qwen3-8b-q6k", help="Model profile")
    cal_p.add_argument("--parallel-context-bytes", type=int, help="Context VRAM delta per worker")
    cal_p.add_argument("--endpoint", help="Ollama endpoint")
    cal_p.add_argument("--json", action="store_true", help="Output report in JSON format")

    # 6. benchmark
    bench_p = subparsers.add_parser("benchmark", help="Run benchmark across model profiles")
    bench_p.add_argument("--model", action="append", choices=list_profiles(), dest="benchmark_models", help="Model profile to benchmark")
    bench_p.add_argument("--repeats", type=int, default=1, dest="benchmark_repeats", help="Benchmark repeats")
    bench_p.add_argument("--timeout-seconds", type=float, default=300, dest="benchmark_timeout_seconds", help="Timeout per model run")
    bench_p.add_argument("--output", type=Path, default=Path(".local-run") / "benchmarks" / "latest.json", dest="benchmark_output", help="Output artifact path")
    bench_p.add_argument("--max-turns", type=int, default=4, help="Max turns per task")
    bench_p.add_argument("--json", action="store_true", help="Output benchmark results in JSON format")
    bench_p.add_argument("--ladder", action="store_true", help="Run adaptive capability ladder benchmark")


    # 7. apply
    app_p = subparsers.add_parser("apply", help="Safely apply patch to workspace with verification and auto-rollback")
    app_p.add_argument("--patch-file", type=Path, help="Path to unified diff patch file")
    app_p.add_argument("--patch", help="Unified diff patch string")
    app_p.add_argument("--workspace", type=Path, default=Path.cwd(), help="Target workspace path")
    app_p.add_argument("--check", action="append", dest="checks", default=[], help="Targeted check command(s) to verify")
    app_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    # 8. doctor
    doc_p = subparsers.add_parser("doctor", help="Run system diagnostics and model recommendations")
    doc_p.add_argument("--endpoint", default="http://127.0.0.1:11434", help="Ollama endpoint to check")
    doc_p.add_argument("--fix", action="store_true", help="Auto-remediate missing MCP configs, Agent Skills, and setup")
    doc_p.add_argument("--dry-run", action="store_true", help="Preview remediation actions without writing")
    doc_p.add_argument("--json", action="store_true", help="Output diagnostic report in JSON format")
    doc_p.add_argument("--strict", action="store_true", help="Exit with code 1 if any check fails")

    # 9. init-mcp
    mcp_p = subparsers.add_parser("init-mcp", help="Generate or configure MCP client integration")
    mcp_p.add_argument(
        "--client",
        choices=["claude", "cursor", "windsurf", "cline", "antigravity", "opencode", "codex", "chatgpt", "vscode", "auto", "all"],
        default="claude",
        help="Target MCP client (default: claude, or 'auto'/'all')",
    )
    mcp_p.add_argument("--claude", action="store_const", const="claude", dest="client", help="Configure for Claude Desktop & Claude Code")
    mcp_p.add_argument("--cursor", action="store_const", const="cursor", dest="client", help="Configure for Cursor")
    mcp_p.add_argument("--windsurf", action="store_const", const="windsurf", dest="client", help="Configure for Windsurf")
    mcp_p.add_argument("--cline", action="store_const", const="cline", dest="client", help="Configure for Cline Desktop / Extension")
    mcp_p.add_argument("--antigravity", action="store_const", const="antigravity", dest="client", help="Configure for Antigravity Desktop & agy CLI")
    mcp_p.add_argument("--opencode", action="store_const", const="opencode", dest="client", help="Configure for OpenCode Desktop & CLI")
    mcp_p.add_argument("--codex", action="store_const", const="codex", dest="client", help="Configure Codex Desktop & Codex CLI (~/.codex/config.toml)")
    mcp_p.add_argument("--chatgpt", action="store_const", const="codex", dest="client", help="Legacy alias for --codex")
    mcp_p.add_argument("--vscode", action="store_const", const="cline", dest="client", help="Configure for VS Code / Cline")
    mcp_p.add_argument("--auto", action="store_const", const="auto", dest="client", help="Auto-detect IDEs in workspace and host environment")
    mcp_p.add_argument("--all", action="store_const", const="all", dest="client", help="Configure all detected IDE environments")
    mcp_p.add_argument("--workspace", default=".", help="Workspace path for the MCP server")
    mcp_p.add_argument("--profile", default="qwen3-8b-q6k", help="Default profile for MCP delegation")
    mcp_p.add_argument("--dry-run", action="store_true", help="Print config without writing to disk")
    mcp_p.add_argument("--write", action="store_true", help="Write/merge directly to client configuration file")
    mcp_p.add_argument("--path", type=Path, help="Explicit configuration file path")

    # 10. init-skill (skill)
    skill_p = subparsers.add_parser("init-skill", aliases=["skill"], help="Export or install Agent Skill to agent directories")
    skill_p.add_argument(
        "--client",
        choices=["codex", "antigravity", "claude", "workspace", "auto", "all"],
        default="auto",
        help="Target agent ecosystem (default: auto)",
    )
    skill_p.add_argument("--workspace", default=".", help="Workspace path")
    skill_p.add_argument("--target-dir", type=Path, dest="path", help="Explicit target directory or file path")
    skill_p.add_argument("--dry-run", action="store_true", help="Preview target paths without writing")
    skill_p.add_argument("--write", action="store_true", help="Write SKILL.md to target directories")
    skill_p.add_argument("--print", action="store_true", dest="print_content", help="Print SKILL.md to stdout")
    skill_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    # 11. test-run / smoke
    smoke_p = subparsers.add_parser("test-run", aliases=["smoke"], help="Run interactive end-to-end smoke test")
    smoke_p.add_argument("--profile", default="qwen2.5-coder", help="Model profile to use")
    smoke_p.add_argument("--mock", action="store_true", help="Use scripted mock model instead of live Ollama")
    smoke_p.add_argument("--no-fallback", action="store_true", help="Do not fallback to mock if Ollama is offline")

    # 12. serve-mcp
    serve_mcp_p = subparsers.add_parser("serve-mcp", help="Run the MCP stdio server")
    serve_mcp_p.add_argument("--workspace-ref", default="workspace")
    serve_mcp_p.add_argument("--workspace", default=".")
    serve_mcp_p.add_argument("--enable-tasks", action="store_true", help="Mount Tasks extension and apply_proposal")
    serve_mcp_p.add_argument("--profile", help="Default model profile")
    serve_mcp_p.add_argument("--endpoint", help="Ollama API endpoint")

    # 13. monitor
    mon_p = subparsers.add_parser("monitor", help="Start the live HTTP monitoring dashboard")
    mon_p.add_argument("--host", default="127.0.0.1")
    mon_p.add_argument("--port", type=int, default=8765)
    # 14. skeletonize
    skel_p = subparsers.add_parser("skeletonize", help="Skeletonize source file by collapsing non-target structures")
    skel_p.add_argument("file", type=Path, help="Path to source file")
    skel_p.add_argument("--symbol", action="append", dest="symbols", default=[], help="Symbol name to keep expanded")
    skel_p.add_argument("--json", action="store_true", help="Output skeleton in JSON format")

    # 15. lint-patch
    lint_p = subparsers.add_parser("lint-patch", help="Run sub-50ms fast static linter pre-gates on a patch")
    lint_p.add_argument("--patch-file", type=Path, help="Path to unified diff patch file")
    lint_p.add_argument("--patch", help="Unified diff patch string")
    lint_p.add_argument("--workspace", type=Path, default=Path.cwd(), help="Target workspace path")
    lint_p.add_argument("--json", action="store_true", help="Output linter report in JSON format")

    # 16. ui (app)
    ui_p = subparsers.add_parser("ui", aliases=["app"], help="[Experimental Preview] Start the standalone Web Workbench & Coding Arena")
    ui_p.add_argument("--host", default="127.0.0.1")
    ui_p.add_argument("--port", type=int, default=8766, help="Port to bind (default: 8766, distinct from monitor)")
    ui_p.add_argument("--experimental", action="store_true", help="Acknowledge running the experimental web workbench preview")

    # 17. desktop
    desk_p = subparsers.add_parser("desktop", help="Start the Standalone Desktop AI Coding Harness (R23)")
    desk_p.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    desk_p.add_argument("--port", type=int, default=8767, help="Port to bind (default: 8767, distinct from monitor/ui)")
    desk_p.add_argument("--browser", action="store_true", help="Force open in system browser instead of native window")
    desk_p.add_argument("--workspace", default=".", help="Target workspace path")
    desk_p.add_argument("--profile", default="qwen2.5-coder", help="Default model profile")

    # 18. spill-read (R24)
    spill_p = subparsers.add_parser("spill-read", help="Read or paginate a spilled tool output artifact (R24)")
    spill_p.add_argument("locator", nargs="?", default=None, help="Spill locator token (e.g. locator:spill:... or path)")
    spill_p.add_argument("--locator", dest="opt_locator", help="Explicit spill locator")
    spill_p.add_argument("--offset", type=int, default=0, help="0-based line offset")
    spill_p.add_argument("--limit", type=int, default=1000, help="Maximum number of lines to read")
    spill_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    # 19. grep (R24)
    grep_p = subparsers.add_parser("grep", help="Fast ripgrep / regex code search across workspace (R24)")
    grep_p.add_argument("query", help="Search query string or regex pattern")
    grep_p.add_argument("paths", nargs="*", default=[], help="Glob filters or file paths (e.g. *.py)")
    grep_p.add_argument("--regex", action="store_true", help="Treat query as regular expression")
    grep_p.add_argument("--case-sensitive", action="store_true", help="Perform case-sensitive matching")
    grep_p.add_argument("--max-results", type=int, default=100, help="Maximum number of matching lines")
    grep_p.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root directory")
    grep_p.add_argument("--json", action="store_true", help="Output matches in JSON format")

    # 20. lsp (R25)
    lsp_p = subparsers.add_parser("lsp", help="Run LSP code intelligence query (R25)")
    lsp_p.add_argument("--operation", choices=["definition", "references", "hover", "symbols"], required=True, help="LSP query operation")
    lsp_p.add_argument("--file", type=Path, required=True, help="Target source file path")
    lsp_p.add_argument("--line", type=int, default=0, help="0-based cursor line number")
    lsp_p.add_argument("--char", type=int, default=0, help="0-based cursor column offset")
    lsp_p.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root directory")
    lsp_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    # 21. serve-acp (R29)
    acp_p = subparsers.add_parser("serve-acp", help="Start Agent Client Protocol (ACP) JSON-RPC stdio server (R29)")
    acp_p.add_argument("--workspace", type=Path, default=Path.cwd(), help="Default workspace directory")
    acp_p.add_argument("--profile", default=DEFAULT_PROFILE, help="Default model profile")
    acp_p.add_argument("--framing", choices=["auto", "jsonl", "content-length"], default="auto", help="Framing mode")

    # 22. chat (R23 mode feature CLI parity)
    chat_p = subparsers.add_parser("chat", help="Interactive chat: single-shot prompt or --repl multi-turn session")
    chat_p.add_argument("prompt", nargs="?", default="", help="The user message (optional in --repl mode)")
    chat_p.add_argument("--mode", choices=["chat", "build", "plan", "hybrid"], default="hybrid", help="Operational mode (default: hybrid auto-classifies)")
    chat_p.add_argument("--profile", choices=list_profiles(), default=_default_profile(), help="Model profile to use (env: LCA_PROFILE)")
    chat_p.add_argument("--model", help="Override the profile model tag for any installed model")
    chat_p.add_argument("--num-ctx", type=int, help="Override model context window in tokens")
    chat_p.add_argument("--workspace", type=Path, default=_default_workspace(), help="Workspace directory (env: LCA_WORKSPACE)")
    chat_p.add_argument(
        "--repl",
        action="store_true",
        help="Run an interactive multi-turn REPL with persistent session history",
    )
    chat_p.add_argument("--session-id", dest="session_id", help="Session id to create/resume (default: auto-generated)")
    chat_p.add_argument("--list-sessions", dest="list_sessions", action="store_true", help="List persisted sessions and exit")
    chat_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    # 23. scan-models
    scan_p = subparsers.add_parser("scan-models", help="Discover and index local GGUF models across drives")
    scan_p.add_argument("--deep", action="store_true", help="Perform deep filesystem scan across all system drives")
    scan_p.add_argument("--drives", help="Comma-separated drive letters to target (e.g. C,D,Q)")
    scan_p.add_argument("--add-dir", dest="add_dir", help="Add custom directory to persistent model registry")
    scan_p.add_argument("--remove-dir", dest="remove_dir", help="Remove custom directory from registry")
    scan_p.add_argument("--list-dirs", action="store_true", help="List all registered custom model directories")
    scan_p.add_argument("--json", action="store_true", help="Output discovered models in JSON format")

    # 24. sessions
    sess_p = subparsers.add_parser("sessions", help="List and inspect persisted chat sessions")
    sess_p.add_argument("session_action", nargs="?", choices=["list", "show"], default="list", help="Action: list or show")
    sess_p.add_argument("session_id", nargs="?", help="Session id (for 'show')")
    sess_p.add_argument("--limit", type=int, default=20, help="Maximum number of sessions to list")
    sess_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    # 25. init-agent (cloud-agent wiring to local /v1)
    ia_p = subparsers.add_parser(
        "init-agent",
        help="Wire a cloud coding agent (Codex, OpenAI-compatible tools) to the Desktop Harness /v1 endpoint",
    )
    ia_p.add_argument("--agent", choices=["codex", "generic"], default="codex", help="Agent config format to emit")
    ia_p.add_argument("--desktop-url", dest="desktop_url", default="http://127.0.0.1:8767", help="Desktop Harness base URL serving the OpenAI-compatible /v1 API")
    ia_p.add_argument("--model", help="Model id agents should request (default: Ollama tag of LCA_PROFILE/qwen2.5-coder)")
    ia_p.add_argument("--write", action="store_true", help="Write the config into the agent's home directory (default: preview only)")
    ia_p.add_argument("--json", action="store_true", help="Output result in JSON format")

    return parser
