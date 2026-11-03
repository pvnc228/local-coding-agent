"""Tests for Event-Sourced Session Engine (session_events.py)."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import pytest

from local_coding_agent.session_events import (
    ModelTurnEvent,
    PrescriptionEvent,
    SessionCompletedEvent,
    SessionCreatedEvent,
    SessionEvent,
    SessionLog,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
    derive_messages,
    event_from_dict,
    event_to_dict,
    fork_session,
)


def test_immutable_events() -> None:
    """Verify events are immutable frozen dataclasses."""
    ev = UserPromptEvent(
        session_id="s1",
        seq=0,
        timestamp="2026-08-21T00:00:00Z",
        content="Fix login bug",
    )
    assert ev.session_id == "s1"
    assert ev.seq == 0
    assert ev.content == "Fix login bug"
    assert ev.event_type == "user_prompt"

    with pytest.raises(FrozenInstanceError):
        ev.content = "Modified prompt"  # type: ignore[misc]


def test_event_serialization_roundtrip() -> None:
    """Verify all event types serialize and deserialize cleanly."""
    events: list[SessionEvent] = [
        SessionCreatedEvent(
            session_id="s1",
            seq=0,
            timestamp="2026-08-21T00:00:00Z",
            parent_session_id="p0",
            fork_seq=5,
            metadata={"tag": "test"},
        ),
        UserPromptEvent(
            session_id="s1",
            seq=1,
            timestamp="2026-08-21T00:00:01Z",
            content="Implement auth handler",
        ),
        ModelTurnEvent(
            session_id="s1",
            seq=2,
            timestamp="2026-08-21T00:00:02Z",
            content="I will read auth.py",
            model="ling-3.0-tiny",
            tool_calls=({"name": "read_file", "arguments": {"path": "auth.py"}},),
        ),
        ToolCallEvent(
            session_id="s1",
            seq=3,
            timestamp="2026-08-21T00:00:03Z",
            tool_call_id="call_123",
            tool_name="read_file",
            arguments={"path": "auth.py"},
        ),
        ToolResultEvent(
            session_id="s1",
            seq=4,
            timestamp="2026-08-21T00:00:04Z",
            tool_call_id="call_123",
            tool_name="read_file",
            result={"content": "def login(): pass"},
            error=None,
        ),
        PrescriptionEvent(
            session_id="s1",
            seq=5,
            timestamp="2026-08-21T00:00:05Z",
            kind="SEARCH_BLOCK_NOT_FOUND",
            instruction="Copy exact line from file",
        ),
        SessionCompletedEvent(
            session_id="s1",
            seq=6,
            timestamp="2026-08-21T00:00:06Z",
            status="success",
            summary="Bug fixed successfully",
            result={"diff": "+ login_valid()"},
        ),
    ]

    for ev in events:
        data = event_to_dict(ev)
        assert data["event_type"] == ev.event_type
        assert data["session_id"] == ev.session_id
        assert data["seq"] == ev.seq

        reconstructed = event_from_dict(data)
        assert reconstructed == ev


def test_session_log_monotonic_sequencing() -> None:
    """Verify SessionLog enforces strictly monotonic sequence numbers and session_id matching."""
    log = SessionLog(session_id="s_test")
    assert len(log) == 0

    ev0 = log.record_created()
    assert ev0.seq == 0
    assert ev0.session_id == "s_test"
    assert len(log) == 1

    ev1 = log.record_user_prompt("Write tests")
    assert ev1.seq == 1
    assert len(log) == 2

    # Attempting to append an event with invalid sequence number
    bad_seq_ev = UserPromptEvent(
        session_id="s_test",
        seq=5,  # expected 2
        timestamp="2026-08-21T00:00:00Z",
        content="Bad seq",
    )
    with pytest.raises(ValueError, match="Non-monotonic event sequence"):
        log.append(bad_seq_ev)

    # Attempting to append an event with mismatching session_id
    bad_id_ev = UserPromptEvent(
        session_id="other_session",
        seq=2,
        timestamp="2026-08-21T00:00:00Z",
        content="Wrong id",
    )
    with pytest.raises(ValueError, match="Event session_id mismatch"):
        log.append(bad_id_ev)


def test_session_log_persistence(tmp_path: Path) -> None:
    """Verify SessionLog streaming persistence to JSONL and reloading."""
    log_file = tmp_path / "test_session.jsonl"
    log = SessionLog(session_id="session_persist", log_path=log_file)

    log.record_created(metadata={"env": "dev"})
    log.record_user_prompt("Hello local model")
    log.record_model_turn("Hello user", model="ling")
    log.record_completed(status="success", summary="done")

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4

    # Load from disk and verify
    reloaded = SessionLog.load_from_jsonl(log_file)
    assert len(reloaded) == 4
    assert reloaded.session_id == "session_persist"
    assert reloaded[0].event_type == "session_created"
    assert reloaded[1].event_type == "user_prompt"
    assert reloaded[2].event_type == "model_turn"
    assert reloaded[3].event_type == "session_completed"
    assert reloaded.get_event(1).content == "Hello local model"  # type: ignore[union-attr]


def test_derive_messages_invariant() -> None:
    """Verify derive_messages reconstructs model-visible context strictly from logged events."""
    log = SessionLog(session_id="s_messages")
    log.record_created()
    log.record_user_prompt("Read a file and fix it")
    log.record_model_turn(
        content="I will call read_file",
        model="qwen3-8b",
        tool_calls=[{"name": "read_file", "arguments": {"path": "main.py"}}],
    )
    log.record_tool_call("call_001", "read_file", {"path": "main.py"})
    log.record_tool_result("call_001", "read_file", result="print('hello')", error=None)
    log.record_prescription(
        kind="DIFF_CORRUPT_HUNK",
        instruction="Use edits format instead of patch",
    )
    log.record_completed("success", summary="Finished")

    messages = derive_messages(log.events)

    # Invariant: session_created and session_completed are log-only metadata (not model messages)
    assert len(messages) == 4

    # 1. User Prompt
    assert messages[0] == {"role": "user", "content": "Read a file and fix it"}

    # 2. Assistant Turn
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "I will call read_file"
    assert messages[1]["tool_calls"] == [{"name": "read_file", "arguments": {"path": "main.py"}}]

    # 3. Tool Result
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_001",
        "name": "read_file",
        "content": "print('hello')",
    }

    # 4. Prescription message
    assert messages[3] == {
        "role": "user",
        "content": "[PRESCRIPTION: DIFF_CORRUPT_HUNK] Use edits format instead of patch",
    }


def test_fork_session(tmp_path: Path) -> None:
    """Verify session branching/forking at specific step_index."""
    storage_dir = tmp_path / "sessions"
    storage_dir.mkdir()

    original = SessionLog(session_id="parent_sess", storage_dir=storage_dir)
    original.record_created()
    original.record_user_prompt("Step 1: check files")
    original.record_model_turn("Checked files. Everything looks good.")
    original.record_user_prompt("Step 2: make bad modification")
    original.record_model_turn("Made bad modification.")
    original.save()

    assert len(original) == 5

    # Fork session at step_index=2 (after first model turn)
    forked = fork_session(
        original_session_id="parent_sess",
        step_index=2,
        new_session_id="forked_sess",
        storage_dir=storage_dir,
    )

    assert forked.session_id == "forked_sess"
    assert len(forked) == 3  # SessionCreated + UserPrompt(seq 1) + ModelTurn(seq 2)
    assert forked.parent_session_id == "parent_sess"
    assert forked.fork_seq == 2

    # Verify forked events are properly re-indexed
    assert forked[0].seq == 0
    assert isinstance(forked[0], SessionCreatedEvent)
    assert forked[0].parent_session_id == "parent_sess"
    assert forked[0].fork_seq == 2

    assert forked[1].seq == 1
    assert isinstance(forked[1], UserPromptEvent)
    assert forked[1].content == "Step 1: check files"

    assert forked[2].seq == 2
    assert isinstance(forked[2], ModelTurnEvent)
    assert forked[2].content == "Checked files. Everything looks good."

    # Verify original session is completely untouched
    assert len(original) == 5
    assert original.session_id == "parent_sess"


def test_event_from_dict_invalid() -> None:
    """Verify invalid event dictionaries raise descriptive ValueErrors."""
    with pytest.raises(ValueError, match="Unknown or missing event_type"):
        event_from_dict({"session_id": "s1", "seq": 0})

    with pytest.raises(ValueError, match="Unknown or missing event_type"):
        event_from_dict({"session_id": "s1", "seq": 0, "event_type": "alien_event"})


def test_session_log_edge_cases(tmp_path: Path) -> None:
    """Verify SessionLog boundary behaviors, indexing, and empty access."""
    log = SessionLog(session_id="s_empty")
    assert len(log) == 0
    assert log.last_event is None
    assert log.parent_session_id is None
    assert log.fork_seq is None
    assert log.get_event(0) is None
    assert log.get_event(-1) is None

    with pytest.raises(ValueError, match="No path specified"):
        log.save()

    # Empty file load error
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Session log file is empty"):
        SessionLog.load_from_jsonl(empty_file)

    # Missing file load error
    with pytest.raises(FileNotFoundError):
        SessionLog.load_from_jsonl(tmp_path / "non_existent.jsonl")


def test_fork_session_validation_errors(tmp_path: Path) -> None:
    """Verify fork_session input validations."""
    with pytest.raises(ValueError, match="step_index must be non-negative"):
        fork_session("s1", -1, "s2")

    with pytest.raises(ValueError, match="could not be resolved"):
        fork_session("s1", 0, "s2", storage_dir=tmp_path / "empty_dir")


def test_derive_messages_tool_error() -> None:
    """Verify tool results with errors are formatted properly into tool messages."""
    log = SessionLog(session_id="s_err")
    log.record_user_prompt("Execute script")
    log.record_model_turn("Executing script...", tool_calls=[{"name": "exec", "arguments": {"cmd": "run"}}])
    log.record_tool_result("call_err", "exec", result=None, error="Command timed out after 30s")

    messages = derive_messages(log.events)
    assert len(messages) == 3
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call_err"
    assert "Error: Command timed out after 30s" in messages[2]["content"]
