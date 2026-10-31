"""Unit tests for Continuable Background Subagents & Coordinator (R30)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
import pytest

from local_coding_agent.subagent import (
    MailboxMessage,
    SubagentContext,
    SubagentCoordinator,
    SubagentReport,
)
from local_coding_agent.task import TaskEnvelope


def test_subagent_coordinator_initialization():
    coord = SubagentCoordinator(max_workers=4, default_profile="test-profile")
    assert coord._max_workers == 4
    assert coord._default_profile == "test-profile"
    assert coord.list_subagents() == []


def test_subagent_spawn_validation():
    coord = SubagentCoordinator()
    with pytest.raises(ValueError, match="role must be a non-empty string"):
        coord.spawn_subagent(role="", goal="fix bug", files=["src/a.py"])

    with pytest.raises(ValueError, match="goal must be a non-empty string"):
        coord.spawn_subagent(role="coder", goal="", files=["src/a.py"])

    with pytest.raises(ValueError, match="files must not be empty"):
        coord.spawn_subagent(role="coder", goal="fix bug", files=[])


def test_subagent_spawn_and_lifecycle():
    coord = SubagentCoordinator()
    agent_id = coord.spawn_subagent(
        role="researcher",
        goal="Find all callers of execute_tool",
        files=["src/a.py"],
        allowed_tools=["read_file", "search_text"],
        context="Searching codebase",
    )
    assert agent_id.startswith("subagent-")
    status = coord.get_subagent_status(agent_id)
    assert status["role"] == "researcher"
    assert status["goal"] == "Find all callers of execute_tool"
    assert status["allowed_tools"] == ["read_file", "search_text"]
    assert status["status"] in ("pending", "running", "completed")

    # Wait for completion
    res = coord.wait_subagent(agent_id, timeout=2.0)
    assert res["status"] == "completed"
    assert "result" in res
    assert res["result"]["status"] == "accepted"


def test_subagent_custom_worker_loop():
    coord = SubagentCoordinator()

    def custom_worker(ctx: SubagentContext) -> dict:
        ctx.report("init", {"step": 1})
        ctx.send_message("coordinator", {"greeting": "hello from child"})
        time.sleep(0.05)
        ctx.report("done", {"step": 2})
        return {
            "status": "accepted",
            "summary": f"Analyzed {len(ctx.task.files)} files",
            "patch": "",
        }

    agent_id = coord.spawn_subagent(
        role="custom_agent",
        goal="Perform specialized analysis",
        files=["src/main.py"],
        worker_loop=custom_worker,
    )

    res = coord.wait_subagent(agent_id, timeout=2.0)
    assert res["status"] == "completed"
    assert res["result"]["summary"] == "Analyzed 1 files"

    # Verify reports
    reports = coord.get_reports(agent_id)
    assert len(reports) == 2
    assert reports[0]["status"] == "init"
    assert reports[1]["status"] == "done"

    # Verify message sent to coordinator
    msgs = coord.receive_messages("coordinator")
    assert len(msgs) == 1
    assert msgs[0]["sender_id"] == agent_id
    assert msgs[0]["content"] == {"greeting": "hello from child"}


def test_subagent_mailbox_communication():
    coord = SubagentCoordinator()
    
    # Spawn two agents
    agent1 = coord.spawn_subagent(
        role="worker1",
        goal="Generate data",
        files=["src/a.py"],
        worker_loop=lambda ctx: {"status": "accepted"},
    )
    agent2 = coord.spawn_subagent(
        role="worker2",
        goal="Process data",
        files=["src/b.py"],
        worker_loop=lambda ctx: {"status": "accepted"},
    )

    coord.wait_all(timeout=2.0)

    # Test send_message between agents
    ok = coord.send_message(agent2, {"task": "process_part_1"}, sender_id=agent1)
    assert ok is True

    # Test receive_messages with clear=False
    inbox = coord.receive_messages(agent2, clear=False)
    assert len(inbox) == 1
    assert inbox[0]["sender_id"] == agent1
    assert inbox[0]["content"] == {"task": "process_part_1"}

    # Still present
    inbox2 = coord.receive_messages(agent2, clear=True)
    assert len(inbox2) == 1

    # Drained
    inbox3 = coord.receive_messages(agent2, clear=True)
    assert len(inbox3) == 0

    # Sending to nonexistent agent returns False
    assert coord.send_message("nonexistent-agent", {"foo": "bar"}) is False


def test_subagent_cancellation():
    coord = SubagentCoordinator()
    started = threading.Event()

    def long_running_worker(ctx: SubagentContext):
        started.set()
        while not ctx.is_cancelled():
            time.sleep(0.01)
        return {"status": "cancelled"}

    agent_id = coord.spawn_subagent(
        role="long_worker",
        goal="Loop indefinitely",
        files=["src/loop.py"],
        worker_loop=long_running_worker,
    )

    started.wait(timeout=1.0)
    status_before = coord.get_subagent_status(agent_id)
    assert status_before["status"] == "running"

    # Cancel
    cancelled = coord.cancel_subagent(agent_id)
    assert cancelled is True

    res = coord.wait_subagent(agent_id, timeout=2.0)
    assert res["status"] == "cancelled"


def test_subagent_pause_resume():
    coord = SubagentCoordinator()
    step_count = [0]
    paused_flag = threading.Event()

    def pausable_worker(ctx: SubagentContext):
        for i in range(10):
            if ctx.is_cancelled():
                break
            record = coord._agents[ctx.agent_id]
            record.pause_event.wait()
            step_count[0] += 1
            if step_count[0] == 2:
                paused_flag.set()
            time.sleep(0.02)
        return {"status": "accepted"}

    agent_id = coord.spawn_subagent(
        role="stepper",
        goal="Step through work",
        files=["src/step.py"],
        worker_loop=pausable_worker,
    )

    paused_flag.wait(timeout=1.0)
    coord.pause_subagent(agent_id)
    assert coord.get_subagent_status(agent_id)["status"] == "paused"

    val_during_pause = step_count[0]
    time.sleep(0.05)
    # Shouldn't increase much while paused
    assert step_count[0] <= val_during_pause + 1

    coord.resume_subagent(agent_id)
    assert coord.get_subagent_status(agent_id)["status"] == "running"

    coord.wait_subagent(agent_id, timeout=2.0)
    assert coord.get_subagent_status(agent_id)["status"] == "completed"


def test_subagent_error_handling():
    coord = SubagentCoordinator()

    def faulty_worker(ctx: SubagentContext):
        raise RuntimeError("simulated child failure")

    agent_id = coord.spawn_subagent(
        role="faulty",
        goal="Cause exception",
        files=["src/err.py"],
        worker_loop=faulty_worker,
    )

    res = coord.wait_subagent(agent_id, timeout=2.0)
    assert res["status"] == "failed"
    assert "error" in res
    assert res["error"]["kind"] == "subagent_exception"
    assert "simulated child failure" in res["error"]["message"]


def test_subagent_wait_all_and_list():
    coord = SubagentCoordinator()
    id1 = coord.spawn_subagent("r1", "goal 1", ["src/1.py"])
    id2 = coord.spawn_subagent("r2", "goal 2", ["src/2.py"])

    all_statuses = coord.wait_all(timeout=3.0)
    assert id1 in all_statuses
    assert id2 in all_statuses
    assert all_statuses[id1]["status"] == "completed"
    assert all_statuses[id2]["status"] == "completed"

    subagents_list = coord.list_subagents()
    assert len(subagents_list) == 2


def test_subagent_shutdown():
    coord = SubagentCoordinator()
    coord.spawn_subagent("r1", "goal", ["src/1.py"], worker_loop=lambda ctx: time.sleep(0.5))
    coord.shutdown(timeout=1.0)
    assert coord._stopping is True
    with pytest.raises(RuntimeError, match="SubagentCoordinator is shutting down"):
        coord.spawn_subagent("r2", "goal2", ["src/2.py"])
