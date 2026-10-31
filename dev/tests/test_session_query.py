"""Tests for SQLite FTS5 Session Query Engine (session_query.py)."""

from pathlib import Path
import pytest

from local_coding_agent.session_events import (
    SessionLog,
    fork_session,
)
from local_coding_agent.session_query import (
    SessionQueryEngine,
    get_session_trace,
    sanitize_fts5_query,
    search_events,
    search_sessions,
)


def test_sanitize_fts5_query() -> None:
    """Verify raw query string sanitization for safe FTS5 MATCH execution."""
    assert sanitize_fts5_query("login authentication") == '"login"* AND "authentication"*'
    assert sanitize_fts5_query('def test_foo():') == '"def"* AND "test_foo"*'
    assert sanitize_fts5_query('error: "file not found"') == '"error"* AND "file not found"*'
    assert sanitize_fts5_query('   ') == '""'
    assert sanitize_fts5_query('исправить ошибку') == '"исправить"* AND "ошибку"*'


def test_index_and_search_sessions(tmp_path: Path) -> None:
    """Verify session indexing and cross-session full-text search."""
    db_path = tmp_path / "sessions.db"
    engine = SessionQueryEngine(db_path)

    # Session 1: Auth task
    s1 = SessionLog("sess_auth")
    s1.record_created()
    s1.record_user_prompt("Please refactor the JWT authentication token generator")
    s1.record_model_turn("Refactored auth token logic successfully", model="qwen3-8b")
    s1.record_completed(status="success", summary="JWT auth refactored")
    engine.index_session_log(s1)

    # Session 2: Database migration task
    s2 = SessionLog("sess_db")
    s2.record_created()
    s2.record_user_prompt("Add index to postgres migration schema for user table")
    s2.record_tool_call("c1", "write_file", {"path": "schema.sql", "content": "CREATE INDEX idx_user"})
    s2.record_tool_result("c1", "write_file", result={"bytes_written": 42})
    s2.record_completed(status="success", summary="Postgres index migration added")
    engine.index_session_log(s2)

    # Search for JWT auth
    auth_hits = engine.search_sessions("JWT authentication")
    assert len(auth_hits) == 1
    assert auth_hits[0]["session_id"] == "sess_auth"
    assert auth_hits[0]["summary"] == "JWT auth refactored"
    assert auth_hits[0]["match_count"] >= 1
    assert "authentication" in auth_hits[0]["best_match"]["snippet"].lower()

    # Search for postgres migration
    db_hits = engine.search_sessions("postgres migration")
    assert len(db_hits) == 1
    assert db_hits[0]["session_id"] == "sess_db"
    assert db_hits[0]["summary"] == "Postgres index migration added"

    # Search for non-existent term
    empty_hits = engine.search_sessions("non_existent_symbol_12345")
    assert len(empty_hits) == 0

    engine.close()


def test_search_events_with_filters(tmp_path: Path) -> None:
    """Verify granular event search with session_id and event_type filters."""
    db_path = tmp_path / "sessions.db"
    engine = SessionQueryEngine(db_path)

    s1 = SessionLog("sess_multi")
    s1.record_created()
    s1.record_user_prompt("Fix rate limiter deadlock")
    s1.record_model_turn("Inspecting rate_limiter.py lock acquisition")
    s1.record_prescription("TOOL_FORBIDDEN_FILE", "Stay within allowlisted files: rate_limiter.py")
    s1.record_completed(status="success", summary="Deadlock fixed")
    engine.index_session_log(s1)

    # Search all events for deadlock
    hits = engine.search_events("deadlock")
    assert len(hits) == 2  # user prompt + session completed
    assert all(h["session_id"] == "sess_multi" for h in hits)

    # Search filtered by event_type
    prompt_hits = engine.search_events("deadlock", event_type="user_prompt")
    assert len(prompt_hits) == 1
    assert prompt_hits[0]["event_type"] == "user_prompt"
    assert "deadlock" in prompt_hits[0]["content"].lower()

    # Search prescription text
    presc_hits = engine.search_events("allowlisted", event_type="prescription")
    assert len(presc_hits) == 1
    assert presc_hits[0]["event_type"] == "prescription"

    engine.close()


def test_get_session_trace(tmp_path: Path) -> None:
    """Verify retrieval of complete sequential session traces."""
    db_path = tmp_path / "sessions.db"
    engine = SessionQueryEngine(db_path)

    log = SessionLog("sess_trace")
    log.record_created(metadata={"user": "alice"})
    log.record_user_prompt("Run test suite")
    log.record_model_turn("Running pytest", model="ling")
    log.record_tool_call("t1", "run_command", {"command": "pytest"})
    log.record_tool_result("t1", "run_command", result={"exit_code": 0, "passed": 10})
    log.record_completed("success", summary="Tests passed")
    engine.index_session_log(log)

    trace = engine.get_session_trace("sess_trace")
    assert len(trace) == 6
    assert [ev["seq"] for ev in trace] == [0, 1, 2, 3, 4, 5]
    assert trace[0]["event_type"] == "session_created"
    assert trace[1]["event_type"] == "user_prompt"
    assert trace[2]["event_type"] == "model_turn"
    assert trace[3]["event_type"] == "tool_call"
    assert trace[4]["event_type"] == "tool_result"
    assert trace[5]["event_type"] == "session_completed"
    assert trace[4]["result"]["passed"] == 10

    # Non-existent session returns empty trace
    assert engine.get_session_trace("non_existent") == []

    engine.close()


def test_session_lineage_and_list(tmp_path: Path) -> None:
    """Verify session lineage tracking across parent and forked sessions."""
    db_path = tmp_path / "sessions.db"
    engine = SessionQueryEngine(db_path)

    # 1. Create root session
    root = SessionLog("root_session")
    root.record_created()
    root.record_user_prompt("Step 1")
    root.record_model_turn("Done step 1")
    engine.index_session_log(root)

    # 2. Fork session
    forked = fork_session(
        original_session_id="root_session",
        step_index=1,
        new_session_id="child_session",
        original_log=root,
    )
    forked.record_user_prompt("Step 2 from child")
    engine.index_session_log(forked)

    # Verify session list
    all_sessions = engine.list_sessions()
    assert len(all_sessions) == 2

    # Verify lineage for child session
    lineage = engine.get_session_lineage("child_session")
    assert lineage["session"]["session_id"] == "child_session"
    assert lineage["session"]["parent_session_id"] == "root_session"
    assert lineage["session"]["fork_seq"] == 1
    assert len(lineage["ancestors"]) == 1
    assert lineage["ancestors"][0]["session_id"] == "root_session"

    # Verify lineage for root session
    root_lineage = engine.get_session_lineage("root_session")
    assert root_lineage["ancestors"] == []
    assert len(root_lineage["descendants"]) == 1
    assert root_lineage["descendants"][0]["session_id"] == "child_session"

    engine.close()


def test_convenience_functions(tmp_path: Path) -> None:
    """Verify module-level search and trace helper functions."""
    db_path = tmp_path / "shared_sessions.db"
    engine = SessionQueryEngine(db_path)

    log = SessionLog("sess_conv")
    log.record_created()
    log.record_user_prompt("Optimize memory consumption")
    log.record_completed("success", summary="Memory optimized")
    engine.index_session_log(log)
    engine.close()

    # Call convenience functions passing db_path
    sess_hits = search_sessions("memory consumption", db_path=db_path)
    assert len(sess_hits) == 1
    assert sess_hits[0]["session_id"] == "sess_conv"

    event_hits = search_events("consumption", db_path=db_path)
    assert len(event_hits) == 1
    assert event_hits[0]["session_id"] == "sess_conv"
    assert event_hits[0]["event_type"] == "user_prompt"

    trace = get_session_trace("sess_conv", db_path=db_path)
    assert len(trace) == 3
    assert [ev["event_type"] for ev in trace] == ["session_created", "user_prompt", "session_completed"]


def test_empty_db_and_special_symbols(tmp_path: Path) -> None:
    """Verify searches on empty database and with bizarre special characters."""
    engine = SessionQueryEngine(tmp_path / "empty.db")
    assert engine.search_sessions("anything") == []
    assert engine.search_events("anything") == []
    assert engine.get_session_record("none") is None
    assert engine.list_sessions() == []

    with pytest.raises(ValueError, match="not found in index"):
        engine.get_session_lineage("non_existent")

    # Index something and test special characters
    log = SessionLog("sess_special")
    log.record_created()
    log.record_user_prompt("Special chars: [{(<*+-$#@!%^&>)}] / \\ : ; ' \" ` ~ = ?")
    engine.index_session_log(log)

    # Should not throw syntax errors
    res = engine.search_sessions("[{(<*+-$#@!%^&>)}]")
    assert isinstance(res, list)

    res_ev = engine.search_events("/// ::: ;;; ??? ***")
    assert isinstance(res_ev, list)

    engine.close()


def test_multi_level_lineage(tmp_path: Path) -> None:
    """Verify lineage traversal across grandparent -> parent -> child hierarchy."""
    engine = SessionQueryEngine(tmp_path / "lineage.db")

    # 1. Grandparent
    gp = SessionLog("gp_session")
    gp.record_created()
    gp.record_user_prompt("Root task")
    engine.index_session_log(gp)

    # 2. Parent
    parent = fork_session("gp_session", 0, "parent_session", original_log=gp)
    parent.record_user_prompt("Subtask")
    engine.index_session_log(parent)

    # 3. Child
    child = fork_session("parent_session", 1, "child_session", original_log=parent)
    child.record_user_prompt("Leaf task")
    engine.index_session_log(child)

    child_lineage = engine.get_session_lineage("child_session")
    assert child_lineage["session"]["session_id"] == "child_session"
    assert len(child_lineage["ancestors"]) == 2
    assert child_lineage["ancestors"][0]["session_id"] == "parent_session"
    assert child_lineage["ancestors"][1]["session_id"] == "gp_session"

    # Filter list_sessions by parent
    parent_children = engine.list_sessions(parent_session_id="parent_session")
    assert len(parent_children) == 1
    assert parent_children[0]["session_id"] == "child_session"

    engine.close()
