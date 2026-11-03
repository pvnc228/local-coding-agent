"""Unit tests for HarnessState, ContextAssembler and dynamic compaction (R14)."""

import json
from local_coding_agent.context_manager import (
    ContextAssembler,
    HarnessState,
    compact_tool_exchanges,
    purge_diff_residues,
)
from local_coding_agent.task import TaskEnvelope


def _make_task() -> TaskEnvelope:
    return TaskEnvelope(
        id="task-1",
        goal="Fix calculate_total to handle tax correctly",
        files=["calc.py"],
        checks=["pytest test_calc.py"],
        acceptance=["all tests pass"],
    )


def test_harness_state_initialization():
    task = _make_task()
    state = HarnessState(task=task)
    assert state.task.id == "task-1"
    assert state.turn == 1
    assert state.observed_files == {}
    assert state.latest_tool_result is None
    assert state.active_prescription is None


def test_context_assembler_turn_one():
    task = _make_task()
    state = HarnessState(task=task)
    assembler = ContextAssembler()
    messages = assembler.assemble(state, system_contract="SYSTEM_TEST")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "SYSTEM_TEST"
    assert messages[1]["role"] == "user"
    payload = json.loads(messages[1]["content"])
    assert payload["id"] == "task-1"
    assert payload["goal"] == task.goal


def test_context_assembler_with_latest_tool_result_preserves_pairing():
    task = _make_task()
    state = HarnessState(
        task=task,
        turn=2,
        latest_tool_name="read_file",
        latest_tool_arguments={"path": "calc.py"},
        latest_tool_result={"content": "def calc(): return 42"},
        latest_tool_call_id="call_123",
    )
    assembler = ContextAssembler()
    messages = assembler.assemble(state, system_contract="SYSTEM_TEST")

    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[3]["role"] == "tool"
    assert messages[3]["tool_name"] == "read_file"
    assert messages[3]["tool_call_id"] == "call_123"
    assert "def calc()" in messages[3]["content"]


def test_context_assembler_with_active_prescription():
    task = _make_task()
    state = HarnessState(
        task=task,
        turn=3,
        active_prescription="SEARCH block not found in calc.py. Use exact whitespace.",
    )
    assembler = ContextAssembler()
    messages = assembler.assemble(state, system_contract="SYSTEM_TEST")

    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "SYSTEM_TEST"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "user"
    assert "SEARCH block not found in calc.py" in messages[2]["content"]


def test_compact_tool_exchanges_drops_old_turns_preserving_pairing():
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK"},
        # Turn 1
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}]},
        {"role": "tool", "tool_name": "read_file", "content": "A" * 500},
        # Turn 2
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path":"b.py"}'}}]},
        {"role": "tool", "tool_name": "read_file", "content": "B" * 500},
    ]
    # Limit to 800 bytes so older turn must be dropped
    compacted, dropped = compact_tool_exchanges(messages, max_bytes=800)
    assert len(dropped) == 2
    assert dropped[0]["role"] == "assistant"
    assert dropped[1]["role"] == "tool"
    # Remaining messages keep system, task, and the newest turn
    assert len(compacted) == 4
    assert compacted[0]["role"] == "system"
    assert compacted[1]["role"] == "user"
    assert compacted[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert "B" * 500 in compacted[3]["content"]


def test_purge_diff_residues():
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK"},
        {
            "role": "assistant",
            "content": json.dumps({"status": "candidate", "patch": "diff --git a/a.py b/a.py\n+broken line"}),
        },
    ]
    purged = purge_diff_residues(messages)
    content = json.loads(purged[2]["content"])
    assert content["patch"] == "<invalid_patch_omitted>"
