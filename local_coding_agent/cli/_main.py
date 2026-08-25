"""CLI main entry point for the local-coding-agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence

from ..calibration import calibrate_for_model
from ..controller import Controller
from ..memory import ModelMemoryManager
from ..ollama_adapter import OllamaError, build_client
from ..profiles import get_profile
from ..benchmark import run_benchmark, write_artifact

from ._dispatch import handle_subcommand
from ._input import load_task_input
from ._parser import build_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.subcommand:
        sub_code = handle_subcommand(args)
        if sub_code != -1:
            return sub_code

    try:
        overrides = {}
        if args.endpoint:
            overrides["endpoint"] = args.endpoint
        if args.num_ctx is not None:
            overrides["num_ctx"] = args.num_ctx
        profile = get_profile(args.profile, **overrides)
        client = build_client(profile)
        if args.benchmark:
            if args.task is not None or getattr(args, "task_file", None) is not None:
                raise ValueError("--benchmark cannot be combined with --task or --task-file")
            if args.unload_all or args.unload_model or args.vram_limit_bytes is not None or args.calibrate_workers is not None:
                raise ValueError("--benchmark cannot be combined with memory controls")
            if args.benchmark_repeats <= 0:
                raise ValueError("--benchmark-repeats must be positive")
            if args.benchmark_timeout_seconds <= 0:
                raise ValueError("--benchmark-timeout-seconds must be positive")
            names = tuple(args.benchmark_models or (
                "qwen3-8b-q6k",
                "qwen3.8-27b-q4",
                "qwen2.5-coder",
                "ornith-9b",
                "qwen3-coder-30b",
                "devstral-small-2-24b",
            ))
            artifact = {
                "schema": "local-coding-agent/benchmark-v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "repeats": args.benchmark_repeats,
                "models": [],
            }
            for name in names:
                benchmark_profile = get_profile(
                    name,
                    **overrides,
                    timeout_seconds=args.benchmark_timeout_seconds,
                )
                benchmark_client = build_client(benchmark_profile)
                try:
                    available = benchmark_client.available_models()
                    available_names = {
                        item.get("name")
                        for item in available.get("models", [])
                        if isinstance(item, dict) and isinstance(item.get("name"), str)
                    }
                    if benchmark_profile.model not in available_names:
                        artifact["models"].append(
                            {
                                "profile": name,
                                "model": benchmark_profile.model,
                                "status": "unavailable",
                                "error": f"model is not present in Ollama /api/tags: {benchmark_profile.model}",
                            }
                        )
                        continue
                    model_info = next(
                        (
                            item
                            for item in available.get("models", [])
                            if isinstance(item, dict) and item.get("name") == benchmark_profile.model
                        ),
                        {},
                    )
                    memory = ModelMemoryManager(benchmark_client)
                    memory_before = memory.snapshot().as_dict()
                    if memory_before["models"]:
                        memory.unload_all()
                    run = run_benchmark(
                        name,
                        benchmark_client,
                        repeats=args.benchmark_repeats,
                        max_turns=args.max_turns,
                    )
                    run["profile"] = benchmark_profile.__dict__
                    run["status"] = "completed"
                    run["memory_before"] = memory_before
                    run["memory_after"] = memory.snapshot().as_dict()
                    run["ollama_model_info"] = {
                        key: model_info.get(key)
                        for key in ("name", "size", "digest", "details", "capabilities")
                        if key in model_info
                    }
                    artifact["models"].append(run)
                except OllamaError as error:
                    artifact["models"].append(
                        {
                            "profile": name,
                            "model": benchmark_profile.model,
                            "status": "unavailable",
                            "error": {"kind": error.kind, "message": str(error)},
                        }
                    )
            write_artifact(args.benchmark_output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 0 if artifact["models"] and all(item["status"] == "completed" for item in artifact["models"]) else 1
        if args.keep_model and args.vram_limit_bytes is None:
            raise ValueError("--keep-model requires --vram-limit-bytes")
        if args.calibrate_workers is not None:
            report = calibrate_for_model(
                client,
                profile.model,
                vram_budget_bytes=args.calibrate_workers,
                per_worker_context_bytes=args.parallel_context_bytes,
            )
            print(json.dumps({"status": "calibrated", "report": report}, ensure_ascii=False, indent=2))
            return 0
        if args.unload_all or args.unload_model or args.vram_limit_bytes is not None:
            manager = ModelMemoryManager(client)
            if args.unload_all:
                snapshot = manager.unload_all()
            elif args.unload_model:
                snapshot = manager.unload_model(args.unload_model)
            else:
                snapshot = manager.snapshot()
            if args.vram_limit_bytes is not None:
                snapshot = manager.enforce_limit(args.vram_limit_bytes, keep=tuple(args.keep_model))
            print(json.dumps({"status": "memory_reconciled", "memory": snapshot.as_dict()}, ensure_ascii=False, indent=2))
            return 0
        if args.task is None and getattr(args, "task_file", None) is None:
            raise ValueError("--task or --task-file is required unless memory controls are used")
        task = load_task_input(args.task, getattr(args, "task_file", None))
        result = Controller(
            client,
            args.workspace,
            max_turns=args.max_turns,
        ).run(task, apply=args.apply)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, OllamaError) as error:
        print(json.dumps({"status": "failed", "error": {"kind": "input", "message": str(error)}}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "accepted" else 1
