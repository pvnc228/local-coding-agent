"""Unit tests for External Agent Hook Bridges and Wire-Protocol Adapters (R30)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from local_coding_agent.hooks import (
    ClaudeCodeHookAdapter,
    CodexHookAdapter,
    HookBridge,
    HookDecision,
)


def test_hook_bridge_registration_and_unregistration():
    bridge = HookBridge()
    calls = []

    def sample_handler(match_target, payload):
        calls.append((match_target, payload))
        return {"decision": "allow"}

    hook_id = bridge.register(HookBridge.POINT_PRE_TOOL_CALL, sample_handler, matcher="read_file")
    assert hook_id.startswith("hook-")

    # Trigger with matching target
    dec = bridge.trigger_pre_tool_call("read_file", {"path": "src/a.py"})
    assert dec.allowed is True
    assert len(calls) == 1
    assert calls[0][0] == "read_file"
    assert calls[0][1]["tool_name"] == "read_file"

    # Trigger with non-matching target
    dec2 = bridge.trigger_pre_tool_call("write_file", {"path": "src/b.py"})
    assert dec2.allowed is True
    assert len(calls) == 1  # Not incremented

    # Unregister
    removed = bridge.unregister(hook_id)
    assert removed is True
    assert bridge.unregister("nonexistent-id") is False

    # Trigger after unregistration
    bridge.trigger_pre_tool_call("read_file", {"path": "src/a.py"})
    assert len(calls) == 1


def test_hook_bridge_decorators():
    bridge = HookBridge()
    pre_called = []
    post_called = []
    turn_start_called = []
    turn_end_called = []
    session_finish_called = []

    @bridge.on_pre_tool_call(matcher="search_.*")
    def on_pre(target, payload):
        pre_called.append(target)
        return {"decision": "allow"}

    @bridge.on_post_tool_call()
    def on_post(target, payload):
        post_called.append(target)
        return {"feedback": ["tool succeeded"]}

    @bridge.on_turn_start()
    def on_start(target, payload):
        turn_start_called.append(payload.get("prompt"))
        return {"additional_context": "injected context"}

    @bridge.on_turn_end()
    def on_end(target, payload):
        turn_end_called.append(payload.get("turn"))
        return None

    @bridge.on_session_finish()
    def on_finish(target, payload):
        session_finish_called.append(payload.get("session_id"))
        return None

    # Test pre-tool
    bridge.trigger_pre_tool_call("search_text", {"query": "foo"})
    assert pre_called == ["search_text"]

    # Test post-tool
    post_dec = bridge.trigger_post_tool_call("read_file", {}, {"content": "bar"})
    assert post_called == ["read_file"]
    assert post_dec.feedback == ["tool succeeded"]

    # Test turn start
    ts_dec = bridge.trigger_turn_start(turn=1, prompt="fix the bug")
    assert turn_start_called == ["fix the bug"]
    assert ts_dec.additional_context == ["injected context"]

    # Test turn end
    bridge.trigger_turn_end(turn=1, response={"status": "candidate"})
    assert turn_end_called == [1]

    # Test session finish
    bridge.trigger_session_finish(session_id="sess-123", summary="all done")
    assert session_finish_called == ["sess-123"]


def test_hook_bridge_blocking_decision():
    bridge = HookBridge()

    @bridge.on_pre_tool_call()
    def blocking_hook(target, payload):
        if target == "forbidden_tool":
            return {"decision": "deny", "reason": "access denied"}
        return {"decision": "allow"}

    dec_allowed = bridge.trigger_pre_tool_call("safe_tool", {})
    assert dec_allowed.allowed is True
    assert not dec_allowed.is_blocking()

    dec_denied = bridge.trigger_pre_tool_call("forbidden_tool", {})
    assert dec_denied.allowed is False
    assert dec_denied.is_blocking()
    assert dec_denied.decision == "deny"
    assert dec_denied.reason == "access denied"


def test_hook_bridge_decision_folding():
    bridge = HookBridge()

    # Register two hooks with different priorities
    bridge.register(
        HookBridge.POINT_PRE_TOOL_CALL,
        lambda t, p: {"additionalContext": "Context A", "systemMessage": "Warning 1"},
        priority=10,
    )
    bridge.register(
        HookBridge.POINT_PRE_TOOL_CALL,
        lambda t, p: {"additionalContext": "Context B", "continue": False, "stopReason": "reached limit"},
        priority=5,
    )

    dec = bridge.trigger_pre_tool_call("any_tool", {})
    assert dec.additional_context == ["Context A", "Context B"]
    assert dec.system_messages == ["Warning 1"]
    assert dec.stop is True
    assert dec.stop_reason == "reached limit"


def test_codex_hook_adapter_formatting():
    adapter = CodexHookAdapter(model="deepseek-chat")

    s_start = adapter.format_session_start("sess-1", "/path/to/cwd")
    assert s_start == {
        "event": "SessionStart",
        "session_id": "sess-1",
        "cwd": "/path/to/cwd",
        "model": "deepseek-chat",
        "source": "user",
    }

    u_prompt = adapter.format_user_prompt_submit(turn_id=2, prompt="Do X", cwd="/cwd")
    assert u_prompt == {
        "event": "UserPromptSubmit",
        "turn_id": "2",
        "prompt": "Do X",
        "cwd": "/cwd",
        "model": "deepseek-chat",
    }

    pre_tool = adapter.format_pre_tool_use("read_file", {"path": "a.py"}, turn_id=1, cwd="/cwd")
    assert pre_tool == {
        "event": "PreToolUse",
        "tool_name": "read_file",
        "tool_input": {"path": "a.py"},
        "turn_id": "1",
        "cwd": "/cwd",
        "model": "deepseek-chat",
    }

    post_tool = adapter.format_post_tool_use("read_file", {"path": "a.py"}, {"data": "xyz"}, turn_id=1, cwd="/cwd")
    assert post_tool == {
        "event": "PostToolUse",
        "tool_name": "read_file",
        "tool_input": {"path": "a.py"},
        "tool_output": {"data": "xyz"},
        "turn_id": "1",
        "cwd": "/cwd",
        "model": "deepseek-chat",
    }

    stop_evt = adapter.format_stop("sess-1", reason="done", cwd="/cwd")
    assert stop_evt == {
        "event": "Stop",
        "session_id": "sess-1",
        "reason": "done",
        "cwd": "/cwd",
        "model": "deepseek-chat",
    }


def test_codex_hook_adapter_output_parsing():
    adapter = CodexHookAdapter()

    # Exit code 2: blocking
    dec_exit2 = adapter.parse_hook_output(2, "", "Policy blocked tool")
    assert dec_exit2.allowed is False
    assert dec_exit2.decision == "deny"
    assert dec_exit2.reason == "Policy blocked tool"

    # Exit code 0 with plain text
    dec_plain = adapter.parse_hook_output(0, "Plain context info", "")
    assert dec_plain.allowed is True
    assert dec_plain.additional_context == ["Plain context info"]

    # Exit code 0 with JSON structured decision
    json_out = json.dumps({"decision": "block", "reason": "Disallowed file path"})
    dec_json = adapter.parse_hook_output(0, json_out, "")
    assert dec_json.allowed is False
    assert dec_json.decision == "block"
    assert dec_json.reason == "Disallowed file path"


def test_claude_code_hook_adapter_formatting():
    adapter = ClaudeCodeHookAdapter(project_dir="/proj")

    s_start = adapter.format_session_start("sess-99")
    assert s_start == {
        "hookEventName": "SessionStart",
        "sessionId": "sess-99",
        "projectDir": "/proj",
        "source": "startup",
    }

    u_prompt = adapter.format_user_prompt_submit(turn=3, prompt="Explain code", session_id="s1")
    assert u_prompt == {
        "hookEventName": "UserPromptSubmit",
        "turn": 3,
        "prompt": "Explain code",
        "sessionId": "s1",
    }

    pre_tool = adapter.format_pre_tool_use("list_files", {"path": "."}, turn=1, session_id="s1")
    assert pre_tool == {
        "hookEventName": "PreToolUse",
        "tool": "list_files",
        "toolInput": {"path": "."},
        "turn": 1,
        "sessionId": "s1",
    }

    post_tool = adapter.format_post_tool_use("list_files", {"path": "."}, {"files": ["a.txt"]}, turn=1, session_id="s1")
    assert post_tool == {
        "hookEventName": "PostToolUse",
        "tool": "list_files",
        "toolInput": {"path": "."},
        "toolOutput": {"files": ["a.txt"]},
        "turn": 1,
        "sessionId": "s1",
    }

    stop = adapter.format_stop("s1", stop_reason="complete")
    assert stop == {
        "hookEventName": "Stop",
        "sessionId": "s1",
        "stopReason": "complete",
    }

    sub_start = adapter.format_subagent_start("sub-1", "researcher", session_id="s1")
    assert sub_start == {
        "hookEventName": "SubagentStart",
        "subagentId": "sub-1",
        "role": "researcher",
        "sessionId": "s1",
    }

    sub_stop = adapter.format_subagent_stop("sub-1", session_id="s1", status="completed")
    assert sub_stop == {
        "hookEventName": "SubagentStop",
        "subagentId": "sub-1",
        "sessionId": "s1",
        "status": "completed",
    }


def test_claude_code_hook_adapter_output_parsing():
    adapter = ClaudeCodeHookAdapter()

    # Structured hookSpecificOutput permission decision
    json_out = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "reason": "Forbidden in test environment",
        },
        "additionalContext": "Important hint",
        "systemMessage": "Warning to user",
    })
    dec = adapter.parse_hook_output(0, json_out, "")
    assert dec.allowed is False
    assert dec.decision == "deny"
    assert dec.reason == "Forbidden in test environment"
    assert dec.additional_context == ["Important hint"]
    assert dec.system_messages == ["Warning to user"]

    # Non-zero exit code without stdout
    dec_err = adapter.parse_hook_output(1, "", "Execution crashed")
    assert dec_err.allowed is False
    assert dec_err.reason == "Execution crashed"


def test_codex_and_claude_code_config_loader(tmp_path: Path):
    codex_cfg = {
        "PreToolUse": [
            {
                "matcher": "read_file",
                "hooks": [{"command": "echo {\"decision\": \"allow\"}", "timeoutSec": 5}],
            }
        ]
    }
    codex_file = tmp_path / "codex_hooks.json"
    codex_file.write_text(json.dumps(codex_cfg), encoding="utf-8")

    codex_adapter = CodexHookAdapter()
    bridge1 = codex_adapter.load_config(codex_file)
    assert len(bridge1._hooks.get(HookBridge.POINT_PRE_TOOL_CALL, [])) == 1

    claude_cfg = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "propose_patch",
                    "hooks": [{"type": "command", "command": "python -c 'print(1)'", "timeout": 10}],
                }
            ]
        }
    }
    claude_file = tmp_path / "claude_hooks.json"
    claude_file.write_text(json.dumps(claude_cfg), encoding="utf-8")

    claude_adapter = ClaudeCodeHookAdapter(plugin_root=str(tmp_path), project_dir=str(tmp_path))
    bridge2 = claude_adapter.load_config(claude_file)
    assert len(bridge2._hooks.get(HookBridge.POINT_PRE_TOOL_CALL, [])) == 1
