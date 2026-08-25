"""Adversarial test suite for R30 Continuable Subagents & Hooks.

Probes lifecycle edge cases, mailbox memory and object safety, hook fail-open behaviors,
shell substitutions, exception handling, and protocol edge cases.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
import pytest

from local_coding_agent.hooks import (
    ClaudeCodeHookAdapter,
    CodexHookAdapter,
    HookBridge,
    HookDecision,
)
from local_coding_agent.subagent import (
    MailboxMessage,
    SubagentContext,
    SubagentCoordinator,
    SubagentReport,
)


# ============================================================================
# 1. Subagent Lifecycle & Concurrency Edge Cases
# ============================================================================

def test_adversarial_subagent_uncooperative_worker_shutdown():
    """Verify shutdown behavior when a worker ignores cancellation."""
    coord = SubagentCoordinator()
    hang_event = threading.Event()

    def stubborn_worker(ctx: SubagentContext):
        hang_event.set()
        # Stubbornly sleep without checking cancel_event
        time.sleep(0.3)
        return {"status": "completed"}

    agent_id = coord.spawn_subagent(
        role="stubborn",
        goal="ignore cancel",
        files=["src/stubborn.py"],
        worker_loop=stubborn_worker,
    )

    hang_event.wait(timeout=1.0)
    # Shutdown with short timeout should not block forever
    start_t = time.monotonic()
    coord.shutdown(timeout=0.05)
    elapsed = time.monotonic() - start_t
    assert elapsed < 0.5
    assert coord._stopping is True


def test_adversarial_subagent_cancellation_while_paused():
    """Verify cancelling a subagent while paused unpauses and sets status cleanly."""
    coord = SubagentCoordinator()
    ready = threading.Event()
    unblock = threading.Event()

    def pausable_worker(ctx: SubagentContext):
        ready.set()
        unblock.wait()
        record = coord._agents[ctx.agent_id]
        record.pause_event.wait()
        if ctx.is_cancelled():
            return {"status": "cancelled"}
        return {"status": "completed"}

    agent_id = coord.spawn_subagent(
        role="pausable",
        goal="pause test",
        files=["src/a.py"],
        worker_loop=pausable_worker,
    )

    ready.wait(timeout=1.0)
    coord.pause_subagent(agent_id)
    assert coord.get_subagent_status(agent_id)["status"] == "paused"

    # Unblock worker to enter pause_event.wait()
    unblock.set()
    time.sleep(0.05)

    # Cancel while paused
    coord.cancel_subagent(agent_id)
    res = coord.wait_subagent(agent_id, timeout=1.0)
    assert res["status"] == "cancelled"


def test_adversarial_subagent_duplicate_id_rejection():
    """Verify attempting to spawn with an existing ID is rejected."""
    coord = SubagentCoordinator()
    coord.spawn_subagent("r1", "g1", ["a.py"], agent_id="fixed-id")
    with pytest.raises(ValueError, match="already exists"):
        coord.spawn_subagent("r2", "g2", ["b.py"], agent_id="fixed-id")
    coord.shutdown(timeout=1.0)


# ============================================================================
# 2. Mailbox Concurrency & Object Safety
# ============================================================================

def test_adversarial_mailbox_uncopyable_content():
    """Verify behavior when mailbox message contains uncopyable or complex object."""
    lock = threading.Lock()
    msg = MailboxMessage(
        id="m1",
        sender_id="s1",
        target_id="t1",
        content={"lock": lock, "text": "hello"},
    )
    # Safe serialization converts uncopyable lock gracefully without raising TypeError
    res = msg.as_dict()
    assert res["id"] == "m1"
    assert res["content"]["text"] == "hello"


def test_adversarial_mailbox_receive_draining_unknown_agent():
    """Verify receive_messages on unknown agent returns empty list safely."""
    coord = SubagentCoordinator()
    msgs = coord.receive_messages("unknown-agent-xyz", clear=True)
    assert msgs == []


def test_adversarial_mailbox_concurrent_flood():
    """Verify high concurrency spamming of mailbox from multiple threads."""
    coord = SubagentCoordinator()
    agent_id = coord.spawn_subagent(
        "worker", "drain mailbox", ["a.py"],
        worker_loop=lambda ctx: time.sleep(0.1),
    )

    errors = []

    def sender(idx):
        try:
            for i in range(50):
                coord.send_message(agent_id, {"seq": i, "from": idx}, sender_id=f"sender-{idx}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=sender, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    msgs = coord.receive_messages(agent_id, clear=True)
    assert len(msgs) == 250
    coord.shutdown(timeout=1.0)


# ============================================================================
# 3. HookBridge Decision Folding & Safety
# ============================================================================

def test_adversarial_hook_handler_exception_fail_open():
    """Verify that a crashing security hook fails closed (allowed=False)."""
    bridge = HookBridge()

    @bridge.on_pre_tool_call(matcher="delete_database")
    def security_guard(target, payload):
        # Crash intentionally
        raise RuntimeError("Database security scanner crashed!")

    dec = bridge.trigger_pre_tool_call("delete_database", {"query": "DROP TABLE users"})
    # Confirms fail-closed behavior: error decision maps to allowed=False
    assert dec.allowed is False
    assert dec.decision == "error"
    assert "Database security scanner crashed!" in (dec.reason or "")


def test_adversarial_hook_handler_type_error_in_handler_body_crash():
    """Verify that a TypeError inside a 1-arg handler is captured safely without crashing trigger()."""
    bridge = HookBridge()

    def single_arg_handler(payload):
        # Internal TypeError inside handler body
        return 1 + "invalid"

    bridge.register(HookBridge.POINT_PRE_TOOL_CALL, single_arg_handler)
    dec = bridge.trigger_pre_tool_call("read_file", {})
    assert dec.allowed is False
    assert dec.decision == "error"


def test_adversarial_claude_code_variable_injection():
    """Verify shell substitution in ClaudeCodeHookAdapter."""
    adapter = ClaudeCodeHookAdapter(
        plugin_root='dir"; echo "INJECTED"; #',
        project_dir="safe_dir",
    )
    cmd = '${CLAUDE_PLUGIN_ROOT}/run.sh'
    substituted = adapter._substitute_vars(cmd, adapter.plugin_root, adapter.project_dir)
    assert 'dir"; echo "INJECTED"; #' in substituted


def test_adversarial_codex_exit_code_1_fail_open():
    """Verify that Codex hook returning non-zero exit code is denied."""
    adapter = CodexHookAdapter()
    dec = adapter.parse_hook_output(1, "", "syntax error in hook script")
    assert dec.allowed is False
    assert dec.decision == "deny"


def test_adversarial_claude_code_nested_updated_input():
    """Verify hookSpecificOutput.updatedInput is parsed correctly."""
    bridge = HookBridge()
    output = {
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "updatedInput": {"path": "sanitized/path.py"},
        }
    }
    dec = HookDecision()
    bridge._fold_outcome(dec, output)
    assert dec.updated_input == {"path": "sanitized/path.py"}
