"""Subcommand dispatch and remaining handlers for the local-coding-agent CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._handlers import (
    _handle_apply,
    _handle_calibrate,
    _handle_decompose,
    _handle_delegate,
    _handle_memory,
    _handle_profiles,
)
from ._handlers2 import (
    _handle_benchmark,
    _handle_chat,
    _handle_desktop,
    _handle_doctor,
    _handle_init_mcp,
    _handle_monitor,
    _handle_scan_models,
    _handle_serve_acp,
    _handle_serve_mcp,
    _handle_sessions,
    _handle_skill,
    _handle_smoke,
)


def _handle_skeletonize(args: argparse.Namespace) -> int:
    from ..ast_compactor import skeletonize_file

    try:
        skeleton = skeletonize_file(str(args.file), target_symbols=args.symbols)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"file": str(args.file), "symbols": args.symbols, "skeleton": skeleton},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            try:
                print(skeleton)
            except UnicodeEncodeError:
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write(skeleton.encode("utf-8", errors="replace"))
                    sys.stdout.buffer.write(b"\n")
                    sys.stdout.buffer.flush()
                else:
                    print(skeleton.encode("ascii", errors="replace").decode("ascii"))
        return 0
    except Exception as error:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"status": "failed", "error": str(error)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"Error skeletonizing file: {error}", file=sys.stderr)
        return 1


def _handle_lint_patch(args: argparse.Namespace) -> int:
    from ..semantic_linter import lint_patch_in_memory

    patch_str = args.patch
    if args.patch_file:
        patch_str = Path(args.patch_file).read_text(encoding="utf-8-sig", errors="replace")
    if not patch_str:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"valid": False, "error": "No patch specified"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("Error: No patch specified via --patch or --patch-file", file=sys.stderr)
        return 2
    report = lint_patch_in_memory(str(args.workspace), patch_str)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "valid": report.valid,
                    "diagnostics": [
                        {"file": d.file, "line": d.line, "message": d.message, "rule": d.rule}
                        for d in report.diagnostics
                    ],
                    "prescriptions": list(report.prescriptions),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if report.valid:
            print("[OK] Patch passed fast static linter gates without issues.")
        else:
            print("[FAIL] Patch failed static linter gates:")
            for p in report.prescriptions:
                print(f"  - {p}")
    return 0 if report.valid else 1


def _handle_spill_read(args: argparse.Namespace) -> int:
    from ..spill import read_spill

    loc = args.opt_locator or args.locator
    if not loc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": "Spill locator is required"}, ensure_ascii=False, indent=2))
        else:
            print("Error: spill locator is required", file=sys.stderr)
        return 2
    try:
        content = read_spill(loc, offset_line=args.offset, limit_lines=args.limit)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"locator": loc, "offset": args.offset, "limit": args.limit, "content": content},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            try:
                print(content)
            except UnicodeEncodeError:
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write(content.encode("utf-8", errors="replace"))
                    sys.stdout.buffer.write(b"\n")
                    sys.stdout.buffer.flush()
                else:
                    print(content.encode("ascii", errors="replace").decode("ascii"))
        return 0
    except Exception as error:
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        else:
            print(f"Error reading spill: {error}", file=sys.stderr)
        return 1


def _handle_grep(args: argparse.Namespace) -> int:
    from ..ripgrep import ripgrep_search

    try:
        globs = args.paths if args.paths else None
        matches = ripgrep_search(
            args.query,
            root=args.workspace,
            globs=globs,
            is_regex=args.regex,
            case_sensitive=args.case_sensitive,
            max_results=args.max_results,
        )
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "count": len(matches),
                        "matches": [
                            {"file": m.file, "line": m.line_number, "text": m.line_content}
                            for m in matches
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            for m in matches:
                print(f"{m.file}:{m.line_number}: {m.line_content}")
        return 0
    except Exception as error:
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        else:
            print(f"Error during grep: {error}", file=sys.stderr)
        return 1


def _handle_lsp(args: argparse.Namespace) -> int:
    from ..lsp import LspManager, LspPosition

    try:
        manager = LspManager(workspace_root=args.workspace)
        if args.operation == "definition":
            res = manager.go_to_definition(args.file, line=args.line, character=args.char, workspace_root=args.workspace)
            data = [r.to_dict() for r in res]
        elif args.operation == "references":
            res = manager.find_references(args.file, line=args.line, character=args.char, workspace_root=args.workspace)
            data = [r.to_dict() for r in res]
        elif args.operation == "hover":
            h_res = manager.hover(args.file, line=args.line, character=args.char, workspace_root=args.workspace)
            data = h_res.to_dict() if h_res else None
        elif args.operation == "symbols":
            syms = manager.document_symbols(args.file, workspace_root=args.workspace)
            data = [s.to_dict() for s in syms]
        else:
            data = None

        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"operation": args.operation, "file": str(args.file), "result": data},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        else:
            print(f"Error during LSP query: {error}", file=sys.stderr)
        return 1


def _handle_init_agent(args: argparse.Namespace) -> int:
    import os

    from ..profiles import get_profile

    base_url = args.desktop_url.rstrip("/")
    model = getattr(args, "model", None)
    if not model:
        try:
            profile = get_profile(os.environ.get("LCA_PROFILE") or "qwen2.5-coder")
            raw = str(getattr(profile, "model", "") or "qwen2.5-coder")
            model = raw if ":" in raw else f"{raw}:latest"
        except Exception:
            model = "qwen2.5-coder:latest"

    def emit(payload: dict, human: list[str], code: int = 0) -> int:
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("\n".join(human))
        return code

    if args.agent == "codex":
        toml_block = (
            "[model_providers.local_agent]\n"
            f'name = "Local Coding Agent (Desktop Harness)"\n'
            f'base_url = "{base_url}/v1"\n'
            'env_key = "LOCAL_AGENT_API_KEY"\n'
            'wire_api = "chat"\n'
        )
        launch_cmd = (
            f'codex -c model_provider=local_agent -m "{model}"  '
            "(set LOCAL_AGENT_API_KEY first; the Desktop Harness ignores its value)"
        )
        env_hint = (
            f'PowerShell: $env:LOCAL_AGENT_API_KEY="local"   |   sh/bash: export LOCAL_AGENT_API_KEY=local'
        )
        config_path = None
        written = False
        error_msg = None
        if args.write:
            codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
            config_path = codex_home / "config.toml"
            try:
                existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
                if "[model_providers.local_agent]" in existing:
                    return emit(
                        {"status": "failed", "error": f"{config_path} already defines [model_providers.local_agent]; edit it manually."},
                        [f"[REFUSED] {config_path} already defines [model_providers.local_agent]. Edit it manually instead of duplicating."],
                        1,
                    )
                config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(config_path, "a", encoding="utf-8") as fh:
                    if existing and not existing.endswith("\n"):
                        fh.write("\n")
                    fh.write(toml_block)
                written = True
            except OSError as os_error:
                return emit(
                    {"status": "failed", "error": str(os_error)},
                    [f"[FAILED] Could not write {config_path}: {os_error}"],
                    1,
                )
        payload = {
            "status": "ok",
            "agent": "codex",
            "model": model,
            "base_url": f"{base_url}/v1",
            "config_path": str(config_path) if config_path else None,
            "written": written,
            "config_snippet": toml_block,
            "launch_command": launch_cmd,
        }
        human = [
            f"--- Codex -> Local Agent ({'written' if written else 'preview'}) ---",
            toml_block,
            f"Launch: {launch_cmd}",
            f"Auth:   {env_hint}",
            f"Prereq: start the harness first (`local-agent desktop`), then load a model in Local Inference Servers.",
        ]
        if config_path:
            human.insert(1, f"Config: {config_path}")
        return emit(payload, human)

    # generic OpenAI-compatible target
    payload = {
        "status": "ok",
        "agent": "generic",
        "model": model,
        "base_url": f"{base_url}/v1",
        "env": {
            "OPENAI_BASE_URL": f"{base_url}/v1",
            "OPENAI_API_KEY": "local",
        },
    }
    human = [
        "--- Generic OpenAI-compatible client -> Local Agent ---",
        f"  OPENAI_BASE_URL={base_url}/v1",
        "  OPENAI_API_KEY=local                # value ignored by the Desktop Harness",
        f"  Model id to request: {model}",
        "Prereq: start the harness first (`local-agent desktop`), then load a model in Local Inference Servers.",
    ]
    return emit(payload, human)


_HANDLERS = {
    "delegate": _handle_delegate,
    "run": _handle_delegate,
    "decompose": _handle_decompose,
    "atomize": _handle_decompose,
    "profiles": _handle_profiles,
    "memory": _handle_memory,
    "calibrate": _handle_calibrate,
    "apply": _handle_apply,
    "init-skill": _handle_skill,
    "skill": _handle_skill,
    "doctor": _handle_doctor,
    "init-mcp": _handle_init_mcp,
    "test-run": _handle_smoke,
    "smoke": _handle_smoke,
    "serve-mcp": _handle_serve_mcp,
    "serve-acp": _handle_serve_acp,
    "monitor": _handle_monitor,
    "ui": _handle_monitor,
    "app": _handle_monitor,
    "desktop": _handle_desktop,
    "chat": _handle_chat,
    "scan-models": _handle_scan_models,
    "benchmark": _handle_benchmark,
    "skeletonize": _handle_skeletonize,
    "lint-patch": _handle_lint_patch,
    "spill-read": _handle_spill_read,
    "grep": _handle_grep,
    "lsp": _handle_lsp,
    "sessions": _handle_sessions,
    "init-agent": _handle_init_agent,
}


def handle_subcommand(args: argparse.Namespace) -> int:
    handler = _HANDLERS.get(args.subcommand)
    if handler is None:
        return -1
    return handler(args)
