"""Wire-protocol adapters for Codex and Claude Code hooks (R30)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ._bridge import HookBridge
from ._decision import HookDecision


class CodexHookAdapter:
    """Wire-protocol adapter and command runner for Codex hooks."""

    DIALECT = "codex"

    def __init__(self, bridge: HookBridge | None = None, *, model: str = "") -> None:
        self.bridge = bridge or HookBridge()
        self.model = model

    def format_session_start(
        self, session_id: str, cwd: str, model: str | None = None, source: str = "user"
    ) -> dict[str, Any]:
        """Format SessionStart event into Codex wire protocol."""
        return {
            "event": "SessionStart",
            "session_id": session_id,
            "cwd": cwd,
            "model": model or self.model,
            "source": source,
        }

    def format_user_prompt_submit(
        self, turn_id: str | int, prompt: str, cwd: str = "", model: str | None = None
    ) -> dict[str, Any]:
        """Format UserPromptSubmit event into Codex wire protocol."""
        return {
            "event": "UserPromptSubmit",
            "turn_id": str(turn_id),
            "prompt": prompt,
            "cwd": cwd,
            "model": model or self.model,
        }

    def format_pre_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        turn_id: str | int = "",
        cwd: str = "",
        model: str | None = None,
    ) -> dict[str, Any]:
        """Format PreToolUse event into Codex wire protocol."""
        return {
            "event": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "turn_id": str(turn_id),
            "cwd": cwd,
            "model": model or self.model,
        }

    def format_post_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: dict[str, Any],
        turn_id: str | int = "",
        cwd: str = "",
        model: str | None = None,
    ) -> dict[str, Any]:
        """Format PostToolUse event into Codex wire protocol."""
        return {
            "event": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "turn_id": str(turn_id),
            "cwd": cwd,
            "model": model or self.model,
        }

    def format_stop(
        self, session_id: str, reason: str = "", cwd: str = "", model: str | None = None
    ) -> dict[str, Any]:
        """Format Stop event into Codex wire protocol."""
        return {
            "event": "Stop",
            "session_id": session_id,
            "reason": reason,
            "cwd": cwd,
            "model": model or self.model,
        }

    def parse_hook_output(self, exit_code: int, stdout: str, stderr: str) -> HookDecision:
        """Parse raw process output into a normalized HookDecision."""
        trimmed_stdout = stdout.strip()
        trimmed_stderr = stderr.strip()

        # In Codex spec, non-zero exit codes indicate failure / blocking
        if exit_code != 0:
            reason = trimmed_stderr or trimmed_stdout or f"blocked by hook (exit code {exit_code})"
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=reason,
                raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}],
            )

        if not trimmed_stdout:
            return HookDecision(raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}])

        try:
            parsed = json.loads(trimmed_stdout)
            if isinstance(parsed, dict):
                decision = HookDecision()
                self.bridge._fold_outcome(decision, parsed)
                decision.raw_outputs.append({"exit_code": exit_code, "stdout": stdout, "stderr": stderr})
                return decision
        except json.JSONDecodeError:
            pass

        # Plain text stdout on exit code 0 is treated as additional context
        if exit_code == 0 and trimmed_stdout:
            return HookDecision(
                additional_context=[trimmed_stdout],
                raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}],
            )

        if exit_code != 0:
            err_msg = stderr.strip() or f"hook exited with code {exit_code}"
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=err_msg,
                raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}],
            )

        return HookDecision(raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}])

    def run_command_hook(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> HookDecision:
        """Run an external command hook sending JSON to stdin and return HookDecision."""
        try:
            # Codex sends payload without trailing newline
            input_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            proc = subprocess.run(
                command,
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
                shell=True,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
            return self.parse_hook_output(proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=f"hook command timed out after {timeout}s",
                raw_outputs=[{"error": "timeout", "command": command}],
            )
        except Exception as exc:
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=f"hook command execution error: {exc}",
                raw_outputs=[{"error": str(exc), "command": command}],
            )

    def load_config(
        self, config_data: dict[str, Any] | str | Path, cwd: str | None = None
    ) -> HookBridge:
        """Load a Codex hooks.json config and register command handlers onto the bridge."""
        if isinstance(config_data, (str, Path)):
            with open(config_data, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = dict(config_data)

        for event_name, groups in data.items():
            if not isinstance(groups, list):
                continue
            for grp in groups:
                if not isinstance(grp, dict):
                    continue
                matcher = grp.get("matcher", "")
                hooks = grp.get("hooks", [])
                for h in hooks:
                    cmd = h.get("command")
                    timeout = float(h.get("timeoutSec", 30))
                    if not cmd:
                        continue

                    def make_handler(command: str, timeout_sec: float):
                        def handler(match_target: str, payload: dict[str, Any]) -> HookDecision:
                            return self.run_command_hook(command, payload, timeout=timeout_sec, cwd=cwd)
                        return handler

                    self.bridge.register(
                        event_name,
                        make_handler(cmd, timeout),
                        matcher=matcher,
                    )
        return self.bridge


class ClaudeCodeHookAdapter:
    """Wire-protocol adapter and command runner for Claude Code hooks."""

    DIALECT = "claude-code"

    def __init__(
        self,
        bridge: HookBridge | None = None,
        *,
        plugin_root: str = "",
        project_dir: str = "",
    ) -> None:
        self.bridge = bridge or HookBridge()
        self.plugin_root = plugin_root
        self.project_dir = project_dir

    def format_session_start(
        self, session_id: str, project_dir: str = "", source: str = "startup"
    ) -> dict[str, Any]:
        """Format SessionStart event into Claude Code wire protocol."""
        return {
            "hookEventName": "SessionStart",
            "sessionId": session_id,
            "projectDir": project_dir or self.project_dir,
            "source": source,
        }

    def format_user_prompt_submit(
        self, turn: int, prompt: str, session_id: str = ""
    ) -> dict[str, Any]:
        """Format UserPromptSubmit event into Claude Code wire protocol."""
        return {
            "hookEventName": "UserPromptSubmit",
            "turn": turn,
            "prompt": prompt,
            "sessionId": session_id,
        }

    def format_pre_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        turn: int = 1,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Format PreToolUse event into Claude Code wire protocol."""
        return {
            "hookEventName": "PreToolUse",
            "tool": tool_name,
            "toolInput": tool_input,
            "turn": turn,
            "sessionId": session_id,
        }

    def format_post_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: dict[str, Any],
        turn: int = 1,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Format PostToolUse event into Claude Code wire protocol."""
        return {
            "hookEventName": "PostToolUse",
            "tool": tool_name,
            "toolInput": tool_input,
            "toolOutput": tool_output,
            "turn": turn,
            "sessionId": session_id,
        }

    def format_stop(self, session_id: str, stop_reason: str = "") -> dict[str, Any]:
        """Format Stop event into Claude Code wire protocol."""
        return {
            "hookEventName": "Stop",
            "sessionId": session_id,
            "stopReason": stop_reason,
        }

    def format_subagent_start(
        self, subagent_id: str, role: str, session_id: str = ""
    ) -> dict[str, Any]:
        """Format SubagentStart event into Claude Code wire protocol."""
        return {
            "hookEventName": "SubagentStart",
            "subagentId": subagent_id,
            "role": role,
            "sessionId": session_id,
        }

    def format_subagent_stop(
        self, subagent_id: str, session_id: str = "", status: str = "completed"
    ) -> dict[str, Any]:
        """Format SubagentStop event into Claude Code wire protocol."""
        return {
            "hookEventName": "SubagentStop",
            "subagentId": subagent_id,
            "sessionId": session_id,
            "status": status,
        }

    def parse_hook_output(self, exit_code: int, stdout: str, stderr: str) -> HookDecision:
        """Parse Claude Code hook command output into a normalized HookDecision."""
        trimmed_stdout = stdout.strip()
        trimmed_stderr = stderr.strip()

        if exit_code != 0 and not trimmed_stdout:
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=trimmed_stderr or f"hook failed with exit code {exit_code}",
                raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}],
            )

        if not trimmed_stdout:
            return HookDecision(raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}])

        try:
            parsed = json.loads(trimmed_stdout)
            if isinstance(parsed, dict):
                decision = HookDecision()
                self.bridge._fold_outcome(decision, parsed)
                decision.raw_outputs.append({"exit_code": exit_code, "stdout": stdout, "stderr": stderr})
                return decision
        except json.JSONDecodeError:
            pass

        return HookDecision(
            additional_context=[trimmed_stdout] if exit_code == 0 and trimmed_stdout else [],
            raw_outputs=[{"exit_code": exit_code, "stdout": stdout, "stderr": stderr}],
        )

    def _substitute_vars(self, command: str, plugin_root: str, project_dir: str) -> str:
        res = command.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
        res = res.replace("$CLAUDE_PLUGIN_ROOT", plugin_root)
        res = res.replace("${CLAUDE_PROJECT_DIR}", project_dir)
        res = res.replace("$CLAUDE_PROJECT_DIR", project_dir)
        return res

    def run_command_hook(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> HookDecision:
        """Run a Claude Code command hook with JSON input and variable substitutions."""
        plugin_root = self.plugin_root or (cwd or os.getcwd())
        project_dir = self.project_dir or (cwd or os.getcwd())
        substituted_cmd = self._substitute_vars(command, plugin_root, project_dir)

        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = project_dir
        if self.plugin_root:
            env["CLAUDE_PLUGIN_ROOT"] = self.plugin_root

        try:
            input_bytes = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            proc = subprocess.run(
                substituted_cmd,
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
                cwd=cwd or project_dir,
                shell=True,
                env=env,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
            return self.parse_hook_output(proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=f"hook command timed out after {timeout}s",
                raw_outputs=[{"error": "timeout", "command": command}],
            )
        except Exception as exc:
            return HookDecision(
                decision="deny",
                allowed=False,
                reason=f"hook execution error: {exc}",
                raw_outputs=[{"error": str(exc), "command": command}],
            )

    def load_config(
        self,
        config_data: dict[str, Any] | str | Path,
        cwd: str | None = None,
        plugin_root: str | None = None,
        project_dir: str | None = None,
    ) -> HookBridge:
        """Load Claude Code hooks.json / settings.json config and register onto bridge."""
        if plugin_root:
            self.plugin_root = plugin_root
        if project_dir:
            self.project_dir = project_dir

        if isinstance(config_data, (str, Path)):
            with open(config_data, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = dict(config_data)

        # Handle top-level 'hooks' wrapper if present
        hooks_dict = data.get("hooks", data)
        if not isinstance(hooks_dict, dict):
            return self.bridge

        for event_name, groups in hooks_dict.items():
            if not isinstance(groups, list):
                continue
            for grp in groups:
                if not isinstance(grp, dict):
                    continue
                matcher = grp.get("matcher", "")
                hooks = grp.get("hooks", [])
                for h in hooks:
                    # Claude code supports {type: "command", command: ...}
                    if isinstance(h, dict) and h.get("type", "command") == "command":
                        cmd = h.get("command")
                        timeout = float(h.get("timeoutSec", h.get("timeout", 30)))
                        if not cmd:
                            continue

                        def make_handler(command: str, timeout_sec: float):
                            def handler(match_target: str, payload: dict[str, Any]) -> HookDecision:
                                return self.run_command_hook(
                                    command, payload, timeout=timeout_sec, cwd=cwd
                                )
                            return handler

                        self.bridge.register(
                            event_name,
                            make_handler(cmd, timeout),
                            matcher=matcher,
                        )
        return self.bridge
