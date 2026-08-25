"""Adversarial security and invariant test suite for R28 Session Engine & FTS5 Query."""

import json
from pathlib import Path
import sqlite3
import threading
import time
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
from local_coding_agent.session_query import (
    SessionQueryEngine,
    sanitize_fts5_query,
    search_events,
    search_sessions,
)


def test_fts5_sanitization_adversarial_inputs() -> None:
    """Test FTS5 query sanitization and execution against aggressive adversarial inputs."""
    engine = SessionQueryEngine(":memory:")

    adversarial_inputs = [
        # SQLite SQL Injection payloads
        "'; DROP TABLE session_records; --",
        "' OR '1'='1",
        "\" OR \"1\"=\"1",
        "admin'--",
        "UNION SELECT 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 --",
        "1; ATTACH DATABASE 'pwn.db' AS pwn; --",
        # FTS5 syntax injection & parser exploit tokens
        "*",
        "**",
        "\"*\"",
        "\"\"\"",
        "\"\"\"\"\"\"",
        "\"",
        "'",
        "\\",
        "\\\\",
        "//",
        "^",
        "NEAR(a, b)",
        "NEAR(a, b, 10)",
        "NOT a",
        "AND",
        "OR",
        "AND AND AND",
        "OR OR OR",
        "NOT NOT NOT",
        "content:foo",
        "events_fts:bar",
        "nonexistent_column:test",
        "\"hello*\"",
        "\"unclosed quote",
        "unclosed quote\"",
        "\"\"nested\"\"quotes\"\"",
        "()",
        "[]",
        "{}",
        "()[]{}<>$%^&!@#~`",
        "a* b* c*",
        "+a +b -c",
        # Unicode and emojis
        "🔥🚀💥🎉",
        "русский текст с ошибкой",
        "中文测试",
        "العربية",
        "👋🏽👨‍👩‍👧‍👦",
        # Control characters & long strings
        "foo\nbar\rbaz\tqux",
        "null\x00byte",
        "a" * 5000,
        "\"" + "a" * 1000 + "\"",
        # Whitespace variations
        "",
        " ",
        "   \t\n\r  ",
    ]

    for raw_query in adversarial_inputs:
        san = sanitize_fts5_query(raw_query)
        assert isinstance(san, str), f"Sanitizer must return str for {raw_query!r}"

        # Executing search_events must NEVER raise sqlite3.OperationalError or corrupt DB
        ev_results = engine.search_events(raw_query)
        assert isinstance(ev_results, list), f"search_events must return list for {raw_query!r}"

        sess_results = engine.search_sessions(raw_query)
        assert isinstance(sess_results, list), f"search_sessions must return list for {raw_query!r}"

    # Verify tables are completely intact after all injection attempts
    cur = engine._conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "session_records" in tables
    assert "events_raw" in tables
    assert "events_fts" in tables


def test_fts5_column_injection_defense() -> None:
    """Verify that user queries attempting column filter injection ('content:x', 'tool_name:y') do not bypass safety."""
    engine = SessionQueryEngine(":memory:")

    log = SessionLog("s_col_test")
    log.record_created()
    log.record_user_prompt("Searching in files")
    log.record_tool_call("c1", "grep_search", {"query": "secret_key"})
    log.record_tool_result("c1", "grep_search", result="FOUND secret_key in .env")
    engine.index_session_log(log)

    # Searching for non-existent column syntax should be sanitized to term matching without error
    res = engine.search_events("nonexistent_column:secret_key")
    assert isinstance(res, list)

    # Searching with valid column name in query should find terms safely
    res_content = engine.search_events("tool_name:grep_search")
    assert isinstance(res_content, list)


def test_concurrent_writes_and_thread_safety(tmp_path: Path) -> None:
    """Verify SessionLog thread safety under high concurrency."""
    log_file = tmp_path / "concurrent.jsonl"
    log = SessionLog("sess_conc", log_path=log_file)
    log.record_created()

    num_threads = 8
    events_per_thread = 50
    barrier = threading.Barrier(num_threads)
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            barrier.wait()
            for i in range(events_per_thread):
                log.record_user_prompt(f"Worker {worker_id} msg {i}")
        except Exception as ex:
            errors.append(ex)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"
    expected_total = 1 + (num_threads * events_per_thread)
    assert len(log) == expected_total

    # Verify log on disk matches in-memory log exactly
    reloaded = SessionLog.load_from_jsonl(log_file)
    assert len(reloaded) == expected_total

    # Verify strictly monotonic sequences [0, 1, 2, ..., expected_total - 1]
    for idx, ev in enumerate(reloaded.events):
        assert ev.seq == idx, f"Sequence broken at index {idx}: got {ev.seq}"


def test_corrupt_jsonl_recovery_behavior(tmp_path: Path) -> None:
    """Check how SessionLog.load_from_jsonl behaves with corrupt, partial, or malformed lines."""
    # 1. Partial/truncated JSON line (e.g. process killed mid-write)
    corrupt_file = tmp_path / "corrupt.jsonl"
    corrupt_file.write_text(
        '{"session_id": "s_corrupt", "seq": 0, "event_type": "session_created", "timestamp": "2026-08-21T00:00:00Z"}\n'
        '{"session_id": "s_corrupt", "seq": 1, "event_type": "user_prompt", "timestamp": "2026-08-21T00:00:01Z", "content": "hello"}\n'
        '{"session_id": "s_corrupt", "seq": 2, "event_type": "model_turn", "timestamp": "2026-08-21T00:00:02Z", "content": "incomplete jso',
        encoding="utf-8",
    )

    with pytest.raises(json.JSONDecodeError):
        SessionLog.load_from_jsonl(corrupt_file)

    # 2. Non-monotonic sequence in file
    broken_seq_file = tmp_path / "broken_seq.jsonl"
    broken_seq_file.write_text(
        '{"session_id": "s_broken", "seq": 0, "event_type": "session_created", "timestamp": "2026-08-21T00:00:00Z"}\n'
        '{"session_id": "s_broken", "seq": 5, "event_type": "user_prompt", "timestamp": "2026-08-21T00:00:01Z", "content": "skip seq"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="violates sequence monotonicity"):
        SessionLog.load_from_jsonl(broken_seq_file)

    # 3. Mismatched session_id in file
    mismatch_id_file = tmp_path / "mismatch.jsonl"
    mismatch_id_file.write_text(
        '{"session_id": "s_first", "seq": 0, "event_type": "session_created", "timestamp": "2026-08-21T00:00:00Z"}\n'
        '{"session_id": "s_second", "seq": 1, "event_type": "user_prompt", "timestamp": "2026-08-21T00:00:01Z", "content": "alien"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected session_id"):
        SessionLog.load_from_jsonl(mismatch_id_file)


def test_derive_messages_adversarial_edge_cases() -> None:
    """Verify derive_messages reconstructs model-visible conversation history under unusual event sequences."""
    # Case A: ModelTurn with multiple tool calls followed by matching ToolResults
    events_a: list[SessionEvent] = [
        UserPromptEvent("s1", 0, "2026-08-21T00:00:00Z", "Analyze two files"),
        ModelTurnEvent(
            "s1",
            1,
            "2026-08-21T00:00:01Z",
            content="Reading both files",
            model="ling-3.0",
            tool_calls=(
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}},
                {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"b.py\"}"}},
            ),
        ),
        ToolResultEvent("s1", 2, "2026-08-21T00:00:02Z", tool_call_id="c1", tool_name="read_file", result="content of a"),
        ToolResultEvent("s1", 3, "2026-08-21T00:00:03Z", tool_call_id="c2", tool_name="read_file", result="content of b"),
        ModelTurnEvent("s1", 4, "2026-08-21T00:00:04Z", content="Analyzed both files successfully."),
    ]
    msgs_a = derive_messages(events_a)
    assert len(msgs_a) == 5
    assert msgs_a[0]["role"] == "user"
    assert msgs_a[1]["role"] == "assistant"
    assert len(msgs_a[1]["tool_calls"]) == 2
    assert msgs_a[2]["role"] == "tool"
    assert msgs_a[2]["tool_call_id"] == "c1"
    assert msgs_a[3]["role"] == "tool"
    assert msgs_a[3]["tool_call_id"] == "c2"
    assert msgs_a[4]["role"] == "assistant"

    # Case B: ModelTurn with empty content and empty tool_calls
    events_b: list[SessionEvent] = [
        UserPromptEvent("s2", 0, "2026-08-21T00:00:00Z", "Hi"),
        ModelTurnEvent("s2", 1, "2026-08-21T00:00:01Z", content=""),
    ]
    msgs_b = derive_messages(events_b)
    assert len(msgs_b) == 2
    assert msgs_b[1] == {"role": "assistant", "content": ""}

    # Case C: ToolResult with complex dict/list result vs error
    events_c: list[SessionEvent] = [
        UserPromptEvent("s3", 0, "2026-08-21T00:00:00Z", "Test tool"),
        ToolResultEvent("s3", 1, "2026-08-21T00:00:01Z", "c1", "test_tool", result={"status": "ok", "items": [1, 2, 3]}),
        ToolResultEvent("s3", 2, "2026-08-21T00:00:02Z", "c2", "test_tool", result=None, error="Failed with exit code 1"),
        ToolResultEvent("s3", 3, "2026-08-21T00:00:03Z", "c3", "test_tool", result={"partial": True}, error="Warning: high memory"),
    ]
    msgs_c = derive_messages(events_c)
    assert len(msgs_c) == 4
    # Check JSON dumped dict
    assert json.loads(msgs_c[1]["content"]) == {"status": "ok", "items": [1, 2, 3]}
    # Check error formatting
    assert msgs_c[2]["content"] == "Error: Failed with exit code 1"
    assert "Error: Warning: high memory" in msgs_c[3]["content"]
    assert "{\"partial\": true}" in msgs_c[3]["content"]


def test_fork_session_deep_pruning_and_invariants(tmp_path: Path) -> None:
    """Verify session forking strictly prunes newer events, maintains causality, and renumbers seq."""
    storage_dir = tmp_path / "fork_test"
    storage_dir.mkdir()

    parent = SessionLog("parent_full", storage_dir=storage_dir)
    parent.record_created(metadata={"initial": "data"})
    parent.record_user_prompt("Prompt 1 (seq 1)")
    parent.record_model_turn("Turn 1 (seq 2)", tool_calls=[{"name": "toolA"}])
    parent.record_tool_call("t1", "toolA", {})
    parent.record_tool_result("t1", "toolA", "resA")
    parent.record_user_prompt("Prompt 2 (seq 5)")
    parent.record_model_turn("Turn 2 (seq 6)")
    parent.record_completed("failed", summary="failed at seq 7")
    parent.save()

    assert len(parent) == 8

    # Fork at step_index = 0 (SessionCreated only)
    fork0 = fork_session("parent_full", 0, "child_0", storage_dir=storage_dir)
    assert len(fork0) == 1
    assert fork0[0].event_type == "session_created"
    assert fork0.parent_session_id == "parent_full"
    assert fork0.fork_seq == 0

    # Fork at step_index = 4 (ToolResult seq 4)
    fork4 = fork_session("parent_full", 4, "child_4", storage_dir=storage_dir)
    assert len(fork4) == 5  # SessionCreated (seq 0) + Prompt1 (seq 1) + Turn1 (seq 2) + ToolCall (seq 3) + ToolResult (seq 4)
    assert fork4.parent_session_id == "parent_full"
    assert fork4.fork_seq == 4
    assert [ev.seq for ev in fork4] == [0, 1, 2, 3, 4]
    assert [ev.session_id for ev in fork4] == ["child_4"] * 5
    assert fork4[1].content == "Prompt 1 (seq 1)"
    assert fork4[4].result == "resA"

    # Fork at step_index = 7 (all events)
    fork7 = fork_session("parent_full", 7, "child_7", storage_dir=storage_dir)
    assert len(fork7) == 8
    assert fork7[7].event_type == "session_completed"
    assert fork7[7].summary == "failed at seq 7"

    # Fork with step_index > max len
    fork_excess = fork_session("parent_full", 999, "child_excess", storage_dir=storage_dir)
    assert len(fork_excess) == 8  # all 8 events captured


def test_derive_messages_strict_invariants() -> None:
    """Verify derive_messages guarantees:
    1. No internal metadata events leak into LLM context (session_created, session_completed).
    2. Tool call arguments, tool result pairing, and error status are preserved.
    3. Types (scalars, arrays, objects) serialize deterministically.
    """
    events: list[SessionEvent] = [
        SessionCreatedEvent("s_inv", 0, "2026-08-21T00:00:00Z", metadata={"secret_internal_key": "x123"}),
        UserPromptEvent("s_inv", 1, "2026-08-21T00:00:01Z", "Perform audit"),
        ModelTurnEvent(
            "s_inv",
            2,
            "2026-08-21T00:00:02Z",
            content="Auditing...",
            model="ling-3.0",
            tool_calls=(
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_scan", "arguments": "{\"depth\": 3, \"fast\": true}"},
                },
            ),
        ),
        ToolResultEvent(
            "s_inv",
            3,
            "2026-08-21T00:00:03Z",
            tool_call_id="call_1",
            tool_name="run_scan",
            result={"vulnerabilities": 0, "scanned_files": 42},
            error=None,
        ),
        PrescriptionEvent(
            "s_inv",
            4,
            "2026-08-21T00:00:04Z",
            kind="EVAL_PASS",
            instruction="Proceed to next stage",
            details={"score": 100},
        ),
        ModelTurnEvent(
            "s_inv",
            5,
            "2026-08-21T00:00:05Z",
            content="Audit completed cleanly.",
            model="ling-3.0",
        ),
        SessionCompletedEvent(
            "s_inv",
            6,
            "2026-08-21T00:00:06Z",
            status="success",
            summary="All clean",
            result={"done": True},
        ),
    ]

    msgs = derive_messages(events)

    # Invariant: exactly 5 messages derived (1 user, 1 assistant, 1 tool, 1 prescription as user, 1 assistant)
    assert len(msgs) == 5

    # Invariant: NO metadata leakage from session_created or session_completed
    msgs_json = json.dumps(msgs)
    assert "secret_internal_key" not in msgs_json
    assert "All clean" not in msgs_json
    assert "session_created" not in msgs_json
    assert "session_completed" not in msgs_json

    # Invariant: Tool result matches tool call ID
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_1"
    assert json.loads(msgs[2]["content"]) == {"vulnerabilities": 0, "scanned_files": 42}

    # Invariant: Prescription formatted correctly
    assert msgs[3]["role"] == "user"
    assert "[PRESCRIPTION: EVAL_PASS] Proceed to next stage" in msgs[3]["content"]


def test_fts5_indexing_all_event_types_and_snippets(tmp_path: Path) -> None:
    """Verify that every event type correctly indexes its specific searchable text into FTS5."""
    db_path = tmp_path / "fts_events.db"
    engine = SessionQueryEngine(db_path)

    log = SessionLog("s_all_types")
    log.record_created()
    log.record_user_prompt("Investigate memory leak in buffer pool")
    log.record_model_turn(
        content="I will inspect buffer_pool.py allocations",
        tool_calls=[{"name": "inspect_allocator", "arguments": {"size": 4096}}],
    )
    log.record_tool_call("c_alloc", "inspect_allocator", {"size": 4096})
    log.record_tool_result("c_alloc", "inspect_allocator", result={"leaked_bytes": 1048576}, error="Memory limit exceeded")
    log.record_prescription("MEMORY_PRESSURE", "Reduce chunk size to 1024", details={"max_chunk": 1024})
    log.record_completed("failed", summary="OOM failure during buffer allocation", result={"exit_code": 137})

    engine.index_session_log(log)

    # 1. Search in user prompt
    r1 = engine.search_events("memory leak buffer pool")
    assert len(r1) >= 1
    assert any(h["event_type"] == "user_prompt" for h in r1)

    # 2. Search in model turn content & tool_args
    r2 = engine.search_events("buffer_pool")
    assert len(r2) >= 1

    # 3. Search in tool_result error
    r3 = engine.search_events("Memory limit exceeded")
    assert len(r3) >= 1
    assert any(h["event_type"] == "tool_result" for h in r3)

    # 4. Search in prescription instruction
    r4 = engine.search_events("Reduce chunk size")
    assert len(r4) >= 1
    assert any(h["event_type"] == "prescription" for h in r4)

    # 5. Search in session_completed summary
    r5 = engine.search_events("OOM failure")
    assert len(r5) >= 1
    assert any(h["event_type"] == "session_completed" for h in r5)

    # 6. Search sessions by tool_result snippet
    sess_hits = engine.search_sessions("leaked_bytes")
    assert len(sess_hits) == 1
    assert sess_hits[0]["session_id"] == "s_all_types"

    engine.close()


def test_session_query_trace_resilience(tmp_path: Path) -> None:
    """Verify get_session_trace handles corrupted raw payloads without crashing."""
    db_path = tmp_path / "corrupt_trace.db"
    engine = SessionQueryEngine(db_path)

    log = SessionLog("s_valid")
    log.record_created()
    log.record_user_prompt("Good event")
    engine.index_session_log(log)

    # Manually inject a corrupt JSON row into events_raw
    cur = engine._conn.cursor()
    cur.execute(
        "INSERT INTO events_raw (session_id, seq, event_type, timestamp, payload) VALUES (?, ?, ?, ?, ?)",
        ("s_valid", 2, "corrupt_event", "2026-08-21T00:00:00Z", "{invalid json corrupt"),
    )
    engine._conn.commit()

    # get_session_trace must skip corrupt payload and return valid events without crashing
    trace = engine.get_session_trace("s_valid")
    assert len(trace) == 2
    assert trace[0]["event_type"] == "session_created"
    assert trace[1]["event_type"] == "user_prompt"

    engine.close()

