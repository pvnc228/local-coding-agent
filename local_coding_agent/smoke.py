"""Interactive smoke test and end-to-end task runner."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .controller import Controller
from .ollama_adapter import build_client
from .profiles import ModelProfile, get_profile
from .task import TaskEnvelope


class MockSmokeOllamaClient:
    """Mock client providing a scripted tool-loop response for isolated smoke tests."""

    def __init__(self, profile: ModelProfile, test_command: str = "") -> None:
        self.profile = profile
        self.test_command = test_command
        self.turn = 0
        self.endpoint = profile.endpoint

    def available_models(self) -> dict[str, Any]:
        return {"models": [{"name": self.profile.model}]}

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
        self.turn += 1
        if self.turn == 1:
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-read-1",
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "calculator.py"},
                            },
                        }
                    ],
                },
                "eval_count": 45,
                "eval_duration": 450_000_000,
                "prompt_eval_count": 120,
                "prompt_eval_duration": 100_000_000,
            }
        elif self.turn == 2:
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-patch-1",
                            "function": {
                                "name": "propose_patch",
                                "arguments": {
                                    "edits": [
                                        {
                                            "file": "calculator.py",
                                            "search": "    return a - b",
                                            "replace": "    return a + b",
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                },
                "eval_count": 60,
                "eval_duration": 600_000_000,
                "prompt_eval_count": 200,
                "prompt_eval_duration": 150_000_000,
            }
        else:
            return {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "candidate",
                        "summary": "Fixed subtraction to addition in calculator.py",
                        "edits": [
                            {
                                "file": "calculator.py",
                                "search": "    return a - b",
                                "replace": "    return a + b",
                            }
                        ],
                        "checks": [],
                        "risks": [],
                    }),
                },
                "eval_count": 40,
                "eval_duration": 400_000_000,
                "prompt_eval_count": 260,
                "prompt_eval_duration": 180_000_000,
            }



def run_smoke_test(
    profile_name: str = "qwen2.5-coder",
    workspace_dir: str | Path | None = None,
    use_mock: bool = False,
    fallback_to_mock: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    steps = []
    start_total = time.perf_counter()

    if verbose:
        print("=" * 60)
        print("  Local Coding Agent — End-to-End Smoke Test")
        print("=" * 60)

    # 1. Setup isolated workspace
    is_temp = workspace_dir is None
    temp_dir_obj = tempfile.TemporaryDirectory() if is_temp else None
    ws_path = Path(temp_dir_obj.name if temp_dir_obj else workspace_dir).resolve()

    try:
        # Create calculator.py
        target_file = ws_path / "calculator.py"
        target_file.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")

        steps.append({"step": "workspace_prep", "status": "ok", "message": f"Workspace created at {ws_path}"})
        if verbose:
            print("[OK] Step 1: Isolated test workspace prepared.")

        # 2. Build task envelope
        task = TaskEnvelope(
            id="smoke-fix-add",
            goal="Fix addition bug in calculator.py so that add(a, b) returns a + b",
            files=["calculator.py"],
            context="The function add currently subtracts instead of adding.",
            constraints=["Do not change the function signature"],
            checks=[],
            acceptance=["calculator.py fixed"],
        )
        steps.append({"step": "task_envelope", "status": "ok", "message": "TaskEnvelope ready"})
        if verbose:
            print("[OK] Step 2: Atomic TaskEnvelope formulated.")

        # 3. Client selection
        profile = get_profile(profile_name)
        mock_fallback = False

        if use_mock:
            client: Any = MockSmokeOllamaClient(profile)
            if verbose:
                print("[INFO] Step 3: Using scripted mock model runner.")
        else:
            try:
                real_client = build_client(profile)
                real_client.available_models()
                client = real_client
                if verbose:
                    print(f"[OK] Step 3: Connected to live Ollama ({profile.model}).")
            except Exception as exc:
                if fallback_to_mock:
                    mock_fallback = True
                    client = MockSmokeOllamaClient(profile)
                    if verbose:
                        print(f"[WARN] Step 3: Live Ollama unavailable ({exc}). Using mock fallback.")
                else:
                    raise exc


        # 4. Controller execution
        start_exec = time.perf_counter()
        controller = Controller(client, ws_path, max_turns=5)
        run_res = controller.run(task, apply=False)
        exec_duration = round(time.perf_counter() - start_exec, 2)

        status = run_res.get("status")
        success = status == "accepted"
        steps.append({
            "step": "execution",
            "status": "ok" if success else "fail",
            "message": f"Controller status: {status} in {exec_duration}s",
        })

        if verbose:
            tag = "[OK]" if success else "[FAIL]"
            print(f"{tag} Step 4: Delegation completed. Status: {status} ({exec_duration}s)")

        # 5. TPS Calculation & validation summary
        total_eval_tokens = 0
        total_eval_duration_ns = 0
        for ev in run_res.get("audit", []):
            if ev.get("event") == "model_response":
                dur = ev.get("eval_duration_ns") or 0
                cnt = ev.get("eval_tokens") or 0
                total_eval_tokens += cnt
                total_eval_duration_ns += dur

        tps: float | None = (
            round(total_eval_tokens / (total_eval_duration_ns / 1e9), 1)
            if total_eval_duration_ns > 0
            else None
        )
        tps_label = f" ({tps} tokens/sec)" if tps is not None else ""

        if success:
            steps.append({
                "step": "validation",
                "status": "ok",
                "message": f"Patch candidate validated{tps_label}",
            })
            if verbose:
                print(f"[OK] Step 5: Patch candidate validated. Generation speed:{tps_label}")
        else:
            steps.append({
                "step": "validation",
                "status": "fail",
                "message": f"Patch validation skipped or failed (Status: {status})",
            })
            if verbose:
                print(f"[FAIL] Step 5: Patch validation skipped or failed (Status: {status}).")
                print("")
                print("-" * 60)
                print("  Smoke Test Summary")
                print("-" * 60)
                print(f"  Result Status : {status.upper() if status else 'UNKNOWN'}")
                print(f"  Total Time    : {round(time.perf_counter() - start_total, 2)}s")
                if tps is not None:
                    print(f"  Eval TPS      : {tps} tok/s")
                print(f"  Monitor UI    : http://127.0.0.1:8765/dashboard")
                print("=" * 60)


        return {
            "success": success,
            "status": status,
            "tps": tps,
            "duration_seconds": round(time.perf_counter() - start_total, 2),
            "mock_fallback": mock_fallback,
            "result": run_res,
            "steps": steps,
        }
    finally:
        if temp_dir_obj:
            temp_dir_obj.cleanup()
