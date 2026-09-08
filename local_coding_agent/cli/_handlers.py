"""Subcommand dispatch for the local-coding-agent CLI (part 1)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..atomizer import TaskBudget, decompose, preflight
from ..calibration import calibrate_for_model
from ..controller import Controller
from ..delegator import BY_FILES, PER_FILE
from ..memory import ModelMemoryManager
from ..ollama_adapter import BACKEND_OFFLINE_HINT, OllamaError, build_client, classify_backend_error
from ..profiles import get_profile, list_profiles
from ..stats import append_stats, default_stats_path
from ..validators import apply_patch, check_patch_applies

from ._input import load_task_input

_BACKEND_HINTS = {
    "backend_offline": BACKEND_OFFLINE_HINT,
    "backend_error": "The model backend returned an error. Check backend logs or verify the model is installed (`ollama list`).",
}


def _annotate_backend_error(result: dict) -> None:
    error = result.get("error")
    if isinstance(error, dict):
        hint = _BACKEND_HINTS.get(error.get("kind"))
        if hint:
            error["hint"] = hint


def _persist_proposal(result: dict, task_id: str) -> None:
    """Write an accepted/candidate patch to disk so it survives the process."""
    patch = result.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        return
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in (task_id or "proposal"))[:80]
    try:
        proposals_dir = Path(".local-run") / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = proposals_dir / f"{safe_id}.patch"
        proposal_path.write_text(patch, encoding="utf-8")
        result["patch_file"] = str(proposal_path)
    except OSError:
        pass  # ponytail: persistence is best-effort; never fail a finished run.


def _handle_delegate(args: argparse.Namespace) -> int:
    started_ns = time.monotonic_ns()
    try:
        task = load_task_input(args.task, getattr(args, "task_file", None))
        if getattr(args, "speculative_drafts", 1) > 1:
            if args.apply:
                # Each racer runs against the same checkout. Applying here
                # would let losing drafts mutate files before a winner is
                # selected; speculative mode is proposal-only until a single
                # result is chosen and explicitly mediated elsewhere.
                print(
                    json.dumps(
                        {
                            "status": "rejected",
                            "error": {
                                "kind": "speculative_apply_unsupported",
                                "message": "--apply cannot be combined with --speculative-drafts; speculative drafts are proposal-only",
                            },
                            "applied": False,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 1
            from threading import Event
            from ..speculative_racing import SpeculativeRacer

            def _make_runner(draft_idx: int):
                def _run(cancel_ev: Event) -> dict[str, Any]:
                    overrides: dict[str, Any] = {}
                    if args.endpoint:
                        overrides["endpoint"] = args.endpoint
                    if args.num_ctx is not None:
                        overrides["num_ctx"] = args.num_ctx
                    if getattr(args, "model", None):
                        overrides["model"] = args.model
                    temp = 0.0 if draft_idx == 0 else min(0.15 * draft_idx, 0.7)
                    overrides["temperature"] = temp
                    prof = get_profile(args.profile, **overrides)
                    cl = build_client(prof)
                    return Controller(
                        cl,
                        args.workspace,
                        max_turns=args.max_turns,
                        cancel_event=cancel_ev,
                    ).run(task, apply=False)

                return _run

            runners = [_make_runner(i) for i in range(args.speculative_drafts)]
            racer = SpeculativeRacer()
            result = racer.run(runners)
        else:
            overrides = {}
            if args.endpoint:
                overrides["endpoint"] = args.endpoint
            if args.num_ctx is not None:
                overrides["num_ctx"] = args.num_ctx
            if getattr(args, "model", None):
                overrides["model"] = args.model
            profile = get_profile(args.profile, **overrides)
            client = build_client(profile)
            result = Controller(
                client,
                args.workspace,
                max_turns=args.max_turns,
            ).run(task, apply=args.apply)
    except OllamaError as error:
        # Normalize to the same vocabulary the controller model boundary uses.
        kind = {
            "offline": "backend_offline",
            "server_error": "backend_error",
        }.get(classify_backend_error(error) or "", "backend_error")
        payload: dict[str, Any] = {
            "status": "failed",
            "error": {
                "kind": kind,
                "message": str(error),
            },
        }
        if kind == "offline":
            payload["error"]["hint"] = BACKEND_OFFLINE_HINT
        append_stats(
            default_stats_path(),
            {"status": "failed", "error": {"kind": kind}},
            model=getattr(args, "model", None) or args.profile,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": {"kind": "input", "message": str(error)}}, ensure_ascii=False, indent=2))
        return 2
    append_stats(
        default_stats_path(),
        result,
        model=getattr(args, "model", None) or args.profile,
        latency_ns=time.monotonic_ns() - started_ns,
    )
    _annotate_backend_error(result)
    _persist_proposal(result, task.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "accepted" else 1


def _handle_decompose(args: argparse.Namespace) -> int:
    try:
        task = load_task_input(args.task, getattr(args, "task_file", None))
        budget = TaskBudget(
            max_files=args.budget_files,
            max_context_bytes=args.budget_bytes,
            max_checks=args.budget_checks,
        )
        pre = preflight(task, budget)
        if args.strategy == "per_file":
            children = PER_FILE.split(task, budget)
        else:
            children = BY_FILES.split(task, budget)
        payload = {
            "task_id": task.id,
            "preflight": {
                "accepted": pre.accepted,
                "reason": pre.reason,
                "issues": list(pre.issues),
            },
            "strategy": args.strategy,
            "count": len(children),
            "children": [c.as_dict() for c in children],
        }
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": {"kind": "input", "message": str(error)}}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["children"] else 1


def _handle_profiles(args: argparse.Namespace) -> int:
    action = getattr(args, "profile_action", "list") or "list"
    if action == "get":
        if not args.name:
            print(json.dumps({"error": "profile name required for 'get'"}, ensure_ascii=False))
            return 2
        try:
            prof = get_profile(args.name)
            print(json.dumps(prof.__dict__, ensure_ascii=False, indent=2))
            return 0
        except ValueError as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
            return 1

    # list
    profile_names = list_profiles()
    installed_map: dict[str, bool] = {}
    if args.check_ollama:
        try:
            c = build_client(get_profile("qwen2.5-1.5b", endpoint=args.endpoint))
            tags = c.available_models().get("models", [])
            tag_names = {t.get("name") for t in tags if isinstance(t, dict) and "name" in t}
            for p_name in profile_names:
                p_obj = get_profile(p_name)
                installed_map[p_name] = p_obj.model in tag_names
        except Exception:
            pass

    items = []
    for p_name in profile_names:
        p_obj = get_profile(p_name)
        item = {
            "name": p_name,
            "model": p_obj.model,
            "provider": getattr(p_obj, "provider", "ollama"),
            "num_ctx": p_obj.num_ctx,
            "num_predict": p_obj.num_predict,
            "max_context_length": p_obj.max_context_length,
            "policy_context_cap": p_obj.max_context_length,
            "model_context_limit": p_obj.model_context_limit,
            "think": p_obj.think,
        }
        if args.check_ollama:
            item["installed_locally"] = installed_map.get(p_name, False)
        items.append(item)

    if args.json:
        print(json.dumps({"profiles": items}, ensure_ascii=False, indent=2))
    else:
        print(f"{'PROFILE':<25} {'MODEL':<35} {'CTX':<8} {'THINK':<6}")
        print("-" * 76)
        for it in items:
            print(f"{it['name']:<25} {it['model']:<35} {it['num_ctx']:<8} {str(it['think']):<6}")
    return 0


def _handle_memory(args: argparse.Namespace) -> int:
    action = getattr(args, "memory_action", "status")
    try:
        profile = get_profile(args.profile, endpoint=args.endpoint)
        client = build_client(profile)
        manager = ModelMemoryManager(client)
        if action == "unload":
            if not args.model:
                print(json.dumps({"error": "model name required for 'unload'"}, ensure_ascii=False))
                return 2
            snapshot = manager.unload_model(args.model)
        elif action == "unload-all":
            snapshot = manager.unload_all()
        elif action == "enforce":
            if args.limit is None:
                print(json.dumps({"error": "--limit required for 'enforce'"}, ensure_ascii=False))
                return 2
            snapshot = manager.enforce_limit(args.limit, keep=tuple(args.keep or []))
        else:
            snapshot = manager.snapshot()
        res = {"status": "memory_reconciled", "action": action, "memory": snapshot.as_dict()}
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1


def _handle_calibrate(args: argparse.Namespace) -> int:
    try:
        overrides = {}
        if args.endpoint:
            overrides["endpoint"] = args.endpoint
        profile = get_profile(args.profile, **overrides)
        client = build_client(profile)
        report = calibrate_for_model(
            client,
            profile.model,
            vram_budget_bytes=args.vram_bytes,
            per_worker_context_bytes=args.parallel_context_bytes,
        )
        print(json.dumps({"status": "calibrated", "report": report}, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1


def _rollback_after_apply(
    workspace: Path,
    patch: str,
    check_results: list[dict[str, Any]],
    failure_message: str,
) -> dict[str, Any]:
    """Rollback a patch after any post-apply check failure.

    Once ``apply_patch`` has mutated the checkout, every failure path must go
    through this function, including exceptions raised by the check runner.
    A rollback failure is deliberately surfaced as a distinct result because
    the workspace can no longer be reported as restored with confidence.
    """

    try:
        rollback_ok, rollback_detail = apply_patch(workspace, patch, reverse=True)
    except Exception as error:  # noqa: BLE001 - report rollback state to caller
        rollback_ok = False
        rollback_detail = str(error)

    rollback_error = None if rollback_ok else (rollback_detail or "rollback failed")
    result: dict[str, Any] = {
        "status": "rejected" if rollback_ok else "failed",
        "error": {
            "kind": "post_apply_check_failed" if rollback_ok else "rollback_failed",
            "message": failure_message,
        },
        "checks": check_results,
        "applied": False,
        "rollback_ok": rollback_ok,
        "workspace_state": "original" if rollback_ok else "unknown",
    }
    if rollback_error:
        result["error"]["rollback_error"] = rollback_error
    return result


def _handle_apply(args: argparse.Namespace) -> int:
    patch_applied = False
    check_results: list[dict[str, Any]] = []
    patch = ""
    ws_root: Path | None = None
    try:
        if args.patch_file:
            patch = Path(args.patch_file).read_text(encoding="utf-8-sig")
        elif args.patch:
            patch = args.patch
        else:
            print(json.dumps({"error": "--patch or --patch-file is required"}, ensure_ascii=False))
            return 2

        ws_root = Path(args.workspace).resolve()
        applies, apply_err = check_patch_applies(ws_root, patch)
        if not applies:
            res = {"status": "rejected", "error": {"kind": "patch_check_failed", "message": apply_err}, "applied": False}
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 1

        applied, detail = apply_patch(ws_root, patch)
        if not applied:
            res = {"status": "rejected", "error": {"kind": "apply_failed", "message": detail}, "applied": False}
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 1
        patch_applied = True

        checks_passed = True
        for cmd in args.checks:
            try:
                cp = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=ws_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=60,
                )
            except Exception as error:  # noqa: BLE001 - all runner failures rollback
                check_results.append(
                    {
                        "command": cmd,
                        "passed": False,
                        "error": {
                            "kind": type(error).__name__,
                            "message": str(error),
                        },
                    }
                )
                result = _rollback_after_apply(
                    ws_root,
                    patch,
                    check_results,
                    f"check runner failed: {error}",
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
            passed = cp.returncode == 0
            check_results.append({
                "command": cmd,
                "passed": passed,
                "evidence": (cp.stdout + cp.stderr).strip()[:500],
            })
            if not passed:
                checks_passed = False
                break

        if not checks_passed:
            res = _rollback_after_apply(
                ws_root,
                patch,
                check_results,
                "one or more checks failed; patch rolled back",
            )
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 1

        res = {"status": "accepted", "applied": True, "checks": check_results}
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        if patch_applied and ws_root is not None:
            result = _rollback_after_apply(
                ws_root,
                patch,
                check_results,
                f"apply operation failed after patch mutation: {error}",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
