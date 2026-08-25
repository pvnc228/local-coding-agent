"""SQLite FTS5 Full-Text Search Index for Session Events.

Provides persistent session event indexing, cross-session search, event search with
snippets, and full chronological trace retrieval.

Adapted from DeepSeek Harness @deepseek-ai/dsh-session-query and
@deepseek-ai/dsh-session-persistence-sqlite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping, Optional, Sequence

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
    event_from_dict,
    event_to_dict,
)


def sanitize_fts5_query(query: str) -> str:
    """Sanitize raw user search queries into safe FTS5 match expressions.

    Prevents syntax errors caused by punctuation, brackets, colons, or quotes in code search.
    """
    raw = str(query or "").strip()
    if not raw:
        return '""'

    # Extract words or quoted fragments
    tokens = re.findall(r'[a-zA-Z0-9_\u0400-\u04FF]+|"[^"]+"', raw)
    if not tokens:
        # Fallback: strip punctuation
        cleaned = re.sub(r'[^\w\s]', " ", raw).strip()
        tokens = cleaned.split()

    if not tokens:
        # If still empty (e.g. only symbols), escape whole string as a quoted token
        safe_str = raw.replace('"', '""')
        return f'"{safe_str}"'

    escaped = []
    for token in tokens:
        clean = token.strip('"').replace('"', '""')
        if clean:
            escaped.append(f'"{clean}"*')

    return " AND ".join(escaped) if escaped else '""'


class SessionQueryEngine:
    """SQLite FTS5 Search Engine for Session Events and Traces."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path) if db_path == ":memory:" else str(Path(db_path).resolve())
        self._lock = threading.RLock()

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema with WAL mode, session records, and FTS5 virtual table."""
        with self._lock:
            cur = self._conn.cursor()
            if self.db_path != ":memory:":
                cur.execute("PRAGMA journal_mode = WAL")
            cur.execute("PRAGMA synchronous = NORMAL")

            # 1. Session Records Table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS session_records (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    parent_session_id TEXT,
                    fork_seq INTEGER,
                    status TEXT DEFAULT 'active',
                    summary TEXT DEFAULT '',
                    event_count INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
                """
            )

            # 2. Raw Events Table (for precise sequential trace reconstruction)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events_raw (
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (session_id, seq)
                )
                """
            )

            # 3. FTS5 Virtual Table for semantic search
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    session_id UNINDEXED,
                    seq UNINDEXED,
                    event_type UNINDEXED,
                    timestamp UNINDEXED,
                    content,
                    tool_name,
                    tool_args,
                    tool_result,
                    tokenize = 'unicode61'
                )
                """
            )
            self._conn.commit()

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()

    def index_event(self, event: SessionEvent, commit: bool = True) -> None:
        """Index a single session event into FTS5 and raw tables, updating session metadata."""
        with self._lock:
            cur = self._conn.cursor()

            content = ""
            tool_name = ""
            tool_args = ""
            tool_result = ""

            # Extract searchable text fields
            if isinstance(event, UserPromptEvent):
                content = event.content
            elif isinstance(event, ModelTurnEvent):
                content = event.content
                if event.tool_calls:
                    tool_name = " ".join(tc.get("name", tc.get("function", {}).get("name", "")) for tc in event.tool_calls)
                    tool_args = " ".join(json.dumps(tc.get("arguments", tc.get("function", {}).get("arguments", {}))) for tc in event.tool_calls)
            elif isinstance(event, ToolCallEvent):
                tool_name = event.tool_name
                tool_args = json.dumps(event.arguments, ensure_ascii=False)
            elif isinstance(event, ToolResultEvent):
                tool_name = event.tool_name
                tool_result = json.dumps(event.result, ensure_ascii=False) if not isinstance(event.result, str) else event.result
                if event.error:
                    tool_result = f"{tool_result} error: {event.error}"
            elif isinstance(event, PrescriptionEvent):
                content = f"[{event.kind}] {event.instruction}"
                if event.details:
                    tool_args = json.dumps(event.details, ensure_ascii=False)
            elif isinstance(event, SessionCompletedEvent):
                content = event.summary
                if event.result:
                    tool_result = json.dumps(event.result, ensure_ascii=False)

            # Insert raw event
            raw_payload = json.dumps(event_to_dict(event), ensure_ascii=False)
            cur.execute(
                """
                INSERT OR REPLACE INTO events_raw (session_id, seq, event_type, timestamp, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event.session_id, event.seq, event.event_type, event.timestamp, raw_payload),
            )

            # Insert FTS doc
            cur.execute(
                """
                INSERT INTO events_fts (session_id, seq, event_type, timestamp, content, tool_name, tool_args, tool_result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.seq,
                    event.event_type,
                    event.timestamp,
                    content,
                    tool_name,
                    tool_args,
                    tool_result,
                ),
            )

            # Update or insert session record
            parent_session_id = getattr(event, "parent_session_id", None)
            fork_seq = getattr(event, "fork_seq", None)
            status = getattr(event, "status", None)
            summary = getattr(event, "summary", None)

            cur.execute("SELECT * FROM session_records WHERE session_id = ?", (event.session_id,))
            row = cur.fetchone()

            if row is None:
                cur.execute(
                    """
                    INSERT INTO session_records (
                        session_id, created_at, updated_at, parent_session_id, fork_seq,
                        status, summary, event_count, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        event.session_id,
                        event.timestamp,
                        event.timestamp,
                        parent_session_id,
                        fork_seq,
                        status or "active",
                        summary or "",
                        json.dumps(event.metadata, ensure_ascii=False),
                    ),
                )
            else:
                new_status = status if status is not None else row["status"]
                new_summary = summary if summary else row["summary"]
                cur.execute(
                    """
                    UPDATE session_records
                    SET updated_at = ?,
                        event_count = event_count + 1,
                        status = ?,
                        summary = ?
                    WHERE session_id = ?
                    """,
                    (event.timestamp, new_status, new_summary, event.session_id),
                )

            if commit:
                self._conn.commit()

    def index_session_log(self, session_log: SessionLog) -> None:
        """Index an entire SessionLog into the search engine."""
        with self._lock:
            for event in session_log.events:
                self.index_event(event, commit=False)
            self._conn.commit()

    def search_events(
        self,
        query: str,
        session_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search indexed events using SQLite FTS5 full-text matching."""
        fts_query = sanitize_fts5_query(query)
        with self._lock:
            cur = self._conn.cursor()
            conditions = ["events_fts MATCH ?"]
            params: list[Any] = [fts_query]

            if session_id is not None:
                conditions.append("session_id = ?")
                params.append(session_id)
            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)

            where_clause = " AND ".join(conditions)
            sql = f"""
                SELECT
                    session_id,
                    seq,
                    event_type,
                    timestamp,
                    content,
                    tool_name,
                    tool_args,
                    tool_result,
                    snippet(events_fts, 4, '<b>', '</b>', '...', 15) AS content_snippet,
                    snippet(events_fts, 6, '<b>', '</b>', '...', 15) AS args_snippet,
                    snippet(events_fts, 7, '<b>', '</b>', '...', 15) AS result_snippet,
                    rank
                FROM events_fts
                WHERE {where_clause}
                ORDER BY rank
                LIMIT ?
            """
            params.append(limit)

            try:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                # Fallback to plain phrase search if complex FTS syntax failed
                safe_fallback = f'"{query.replace(chr(34), chr(34)+chr(34))}"'
                params[0] = safe_fallback
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]

            results: list[dict[str, Any]] = []
            for row in rows:
                snippet = (
                    row["content_snippet"]
                    or row["result_snippet"]
                    or row["args_snippet"]
                    or row["content"]
                    or ""
                )
                results.append(
                    {
                        "session_id": row["session_id"],
                        "seq": int(row["seq"]),
                        "event_type": row["event_type"],
                        "timestamp": row["timestamp"],
                        "content": row["content"],
                        "tool_name": row["tool_name"],
                        "tool_args": row["tool_args"],
                        "tool_result": row["tool_result"],
                        "snippet": snippet,
                        "rank": float(row["rank"]) if row["rank"] is not None else 0.0,
                    }
                )
            return results

    def search_sessions(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search cross-session corpus and return ranked sessions with matching snippets."""
        fts_query = sanitize_fts5_query(query)
        with self._lock:
            cur = self._conn.cursor()
            sql = """
                SELECT
                    session_id,
                    seq,
                    event_type,
                    timestamp,
                    content,
                    snippet(events_fts, 4, '<b>', '</b>', '...', 15) AS content_snippet,
                    snippet(events_fts, 6, '<b>', '</b>', '...', 15) AS args_snippet,
                    snippet(events_fts, 7, '<b>', '</b>', '...', 15) AS result_snippet,
                    rank
                FROM events_fts
                WHERE events_fts MATCH ?
                ORDER BY rank ASC
            """
            try:
                cur.execute(sql, (fts_query,))
                event_rows = [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                safe_fallback = f'"{query.replace(chr(34), chr(34)+chr(34))}"'
                cur.execute(sql, (safe_fallback,))
                event_rows = [dict(r) for r in cur.fetchall()]

            if not event_rows:
                return []

            # Group matches by session_id
            session_matches: dict[str, list[dict[str, Any]]] = {}
            for row in event_rows:
                sess_id = str(row["session_id"])
                if sess_id not in session_matches:
                    session_matches[sess_id] = []
                session_matches[sess_id].append(row)

            # Sort sessions by their best (lowest) match rank
            sorted_sessions = sorted(
                session_matches.items(),
                key=lambda item: float(item[1][0]["rank"]) if item[1][0]["rank"] is not None else 0.0,
            )[:limit]

            results: list[dict[str, Any]] = []
            for sess_id, matches in sorted_sessions:
                best = matches[0]
                rec = self.get_session_record(sess_id)
                if rec is None:
                    continue

                snippet = (
                    best["content_snippet"]
                    or best["result_snippet"]
                    or best["args_snippet"]
                    or best["content"]
                    or ""
                )

                results.append(
                    {
                        "session_id": sess_id,
                        "created_at": rec["created_at"],
                        "updated_at": rec["updated_at"],
                        "parent_session_id": rec["parent_session_id"],
                        "fork_seq": rec["fork_seq"],
                        "status": rec["status"],
                        "summary": rec["summary"],
                        "event_count": rec["event_count"],
                        "match_count": len(matches),
                        "best_match": {
                            "seq": int(best["seq"]),
                            "event_type": best["event_type"],
                            "timestamp": best["timestamp"],
                            "snippet": snippet,
                            "rank": float(best["rank"]) if best["rank"] is not None else 0.0,
                        },
                    }
                )
            return results

    def get_session_trace(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieve full ordered event trace for a session."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT payload
                FROM events_raw
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
            trace: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                    trace.append(payload)
                except (json.JSONDecodeError, KeyError):
                    continue
            return trace

    def get_session_record(self, session_id: str) -> dict[str, Any] | None:
        """Get top-level metadata record for a session."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM session_records WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "parent_session_id": row["parent_session_id"],
                "fork_seq": row["fork_seq"],
                "status": row["status"],
                "summary": row["summary"],
                "event_count": int(row["event_count"]),
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            }

    def list_sessions(
        self,
        parent_session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List session records with optional filtering."""
        with self._lock:
            cur = self._conn.cursor()
            conditions: list[str] = []
            params: list[Any] = []

            if parent_session_id is not None:
                conditions.append("parent_session_id = ?")
                params.append(parent_session_id)
            if status is not None:
                conditions.append("status = ?")
                params.append(status)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = f"""
                SELECT * FROM session_records
                {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall()

            return [
                {
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "parent_session_id": row["parent_session_id"],
                    "fork_seq": row["fork_seq"],
                    "status": row["status"],
                    "summary": row["summary"],
                    "event_count": int(row["event_count"]),
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                }
                for row in rows
            ]

    def get_session_lineage(self, session_id: str) -> dict[str, Any]:
        """Retrieve ancestry chain and direct descendant sessions."""
        with self._lock:
            cur = self._conn.cursor()
            target = self.get_session_record(session_id)
            if target is None:
                raise ValueError(f"Session {session_id!r} not found in index")

            ancestors: list[dict[str, Any]] = []
            curr_parent = target["parent_session_id"]
            while curr_parent:
                parent_rec = self.get_session_record(curr_parent)
                if parent_rec:
                    ancestors.append(parent_rec)
                    curr_parent = parent_rec["parent_session_id"]
                else:
                    break

            descendants = self.list_sessions(parent_session_id=session_id)

            return {
                "session": target,
                "ancestors": ancestors,
                "descendants": descendants,
            }


# -----------------------------------------------------------------------------
# Module-level Convenience Functions
# -----------------------------------------------------------------------------

_DEFAULT_ENGINE: SessionQueryEngine | None = None
_DEFAULT_ENGINE_LOCK = threading.RLock()


def get_default_engine(db_path: str | Path | None = None) -> SessionQueryEngine:
    """Get or create singleton default SessionQueryEngine."""
    global _DEFAULT_ENGINE
    with _DEFAULT_ENGINE_LOCK:
        if _DEFAULT_ENGINE is None or db_path is not None:
            path = db_path or os.environ.get("LOCAL_AGENT_SESSION_DB", ":memory:")
            _DEFAULT_ENGINE = SessionQueryEngine(path)
        return _DEFAULT_ENGINE


def search_sessions(
    query: str,
    db_path: str | Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search cross-session corpus using SQLite FTS5."""
    engine = get_default_engine(db_path) if db_path is None else SessionQueryEngine(db_path)
    return engine.search_sessions(query, limit=limit)


def search_events(
    query: str,
    session_id: str | None = None,
    event_type: str | None = None,
    db_path: str | Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search event text using SQLite FTS5."""
    engine = get_default_engine(db_path) if db_path is None else SessionQueryEngine(db_path)
    return engine.search_events(query, session_id=session_id, event_type=event_type, limit=limit)


def get_session_trace(
    session_id: str,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Retrieve complete event trace for session."""
    engine = get_default_engine(db_path) if db_path is None else SessionQueryEngine(db_path)
    return engine.get_session_trace(session_id)
