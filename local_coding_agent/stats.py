"""Minimal, stdlib-only statistics collector for delegation runs.

This is a lightweight observability seam: it counts outcomes and accumulates
latencies without any third-party dependency, and can optionally append one
JSONL record per terminal result. It is harness-agnostic — anything that
produces a controller/service result can feed it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping


class DelegationStats:
    """Thread-safe counters and latency aggregates over delegation results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_ns = time.monotonic_ns()
        self.total = 0
        self.by_status: dict[str, int] = {}
        self.by_model: dict[str, int] = {}
        self.by_error_kind: dict[str, int] = {}
        self.latency_count = 0
        self.latency_total_ns = 0
        self.latency_min_ns: int | None = None
        self.latency_max_ns: int | None = None
        self.tool_calls = 0
        self.model_calls = 0

    def record(
        self,
        result: Mapping[str, Any],
        *,
        model: str | None = None,
        latency_ns: int | None = None,
    ) -> None:
        with self._lock:
            self.total += 1
            status = str(result.get("status", "unknown"))
            self.by_status[status] = self.by_status.get(status, 0) + 1
            if model:
                self.by_model[model] = self.by_model.get(model, 0) + 1
            error = result.get("error")
            if isinstance(error, Mapping) and error.get("kind"):
                kind = str(error["kind"])
                self.by_error_kind[kind] = self.by_error_kind.get(kind, 0) + 1
            audit = result.get("audit")
            if isinstance(audit, (list, tuple)):
                for event in audit:
                    if isinstance(event, Mapping):
                        name = event.get("event")
                        if name == "tool_call":
                            self.tool_calls += 1
                        elif name == "model_request":
                            self.model_calls += 1
            if latency_ns is not None and latency_ns >= 0:
                self.latency_count += 1
                self.latency_total_ns += latency_ns
                self.latency_min_ns = (
                    latency_ns
                    if self.latency_min_ns is None
                    else min(self.latency_min_ns, latency_ns)
                )
                self.latency_max_ns = (
                    latency_ns
                    if self.latency_max_ns is None
                    else max(self.latency_max_ns, latency_ns)
                )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed_s = (time.monotonic_ns() - self._started_ns) / 1_000_000_000
            latency_avg_ns = (
                self.latency_total_ns / self.latency_count if self.latency_count else None
            )
            return {
                "elapsed_seconds": round(elapsed_s, 3),
                "total": self.total,
                "by_status": dict(self.by_status),
                "by_model": dict(self.by_model),
                "by_error_kind": dict(self.by_error_kind),
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "latency": {
                    "count": self.latency_count,
                    "avg_ms": (
                        round(latency_avg_ns / 1_000_000, 3)
                        if latency_avg_ns is not None
                        else None
                    ),
                    "min_ms": (
                        round(self.latency_min_ns / 1_000_000, 3)
                        if self.latency_min_ns is not None
                        else None
                    ),
                    "max_ms": (
                        round(self.latency_max_ns / 1_000_000, 3)
                        if self.latency_max_ns is not None
                        else None
                    ),
                },
            }


class JsonlStatsSink:
    """Append one JSON line per record to a UTF-8 file for later inspection."""

    _write_lock = threading.Lock()  # class-level: one lock across all sink instances/process threads.

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def write(self, record: Mapping[str, Any]) -> None:
        with self._write_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")


class TimedDelegationStats:
    """Convenience wrapper that times a delegate call and records the result."""

    def __init__(
        self,
        stats: DelegationStats,
        *,
        sink: JsonlStatsSink | None = None,
    ) -> None:
        self.stats = stats
        self.sink = sink

    def __call__(
        self,
        delegate,
        caller_id: str,
        request,
        *,
        model: str | None = None,
    ) -> Mapping[str, Any]:
        started = time.monotonic_ns()
        result = delegate(caller_id, request)
        latency_ns = time.monotonic_ns() - started
        self.stats.record(result, model=model, latency_ns=latency_ns)
        if self.sink is not None:
            record = {
                "ts": time.time(),
                "caller_id": caller_id,
                "request_id": getattr(request, "request_id", None),
                "model": model,
                "status": result.get("status"),
                "error_kind": (
                    result.get("error", {}).get("kind")
                    if isinstance(result.get("error"), Mapping)
                    else None
                ),
                "latency_ms": round(latency_ns / 1_000_000, 3),
            }
            self.sink.write(record)
        return result


def default_stats_path() -> Path:
    """Shared cross-process stats journal location (relative to the cwd)."""
    return Path(".local-run") / "stats.jsonl"


def append_stats(
    path: str | Path,
    result: Mapping[str, Any],
    *,
    model: str | None = None,
    latency_ns: int | None = None,
) -> None:
    """Append one slim, replayable delegation record. Never raises."""
    error = result.get("error")
    record = {
        "ts": time.time(),
        "model": model,
        "status": result.get("status"),
        "error": {"kind": error.get("kind")} if isinstance(error, Mapping) and error.get("kind") else None,
        "latency_ms": round(latency_ns / 1_000_000, 3) if latency_ns is not None else None,
    }
    try:
        JsonlStatsSink(path).write(record)
    except OSError:
        pass  # ponytail: telemetry must never break a delegation.


def load_stats(path: str | Path, *, max_records: int = 2000) -> DelegationStats:
    """Rebuild aggregate stats by replaying the JSONL journal written by append_stats."""
    stats = DelegationStats()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return stats
    for line in lines[-max_records:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        latency_ms = rec.get("latency_ms")
        stats.record(
            rec,
            model=rec.get("model"),
            latency_ns=int(latency_ms * 1_000_000) if isinstance(latency_ms, (int, float)) else None,
        )
    return stats


def merge_stats_snapshots(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge two DelegationStats.snapshot() dicts (counters summed, latency combined)."""
    merged = dict(base)

    def _sum_latency(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
        count = (a.get("count") or 0) + (b.get("count") or 0)
        total_ns = 0.0
        minimums, maximums = [], []
        for part in (a, b):
            if part.get("count"):
                total_ns += part["avg_ms"] * part["count"] * 1_000_000
            if part.get("min_ms") is not None:
                minimums.append(part["min_ms"])
            if part.get("max_ms") is not None:
                maximums.append(part["max_ms"])
        if not count:
            return {"count": 0, "avg_ms": None, "min_ms": None, "max_ms": None}
        avg_ns = total_ns / count
        return {
            "count": count,
            "avg_ms": round(avg_ns / 1_000_000, 3),
            "min_ms": min(minimums) if minimums else None,
            "max_ms": max(maximums) if maximums else None,
        }

    for key in ("total", "model_calls", "tool_calls"):
        if key in base or key in overlay:
            merged[key] = base.get(key, 0) + overlay.get(key, 0)
    # elapsed_seconds is intentionally dropped: journal-replay uptime and
    # server uptime measure different clocks; summing or max-ing misleads.
    merged.pop("elapsed_seconds", None)
    for key in ("by_status", "by_model", "by_error_kind"):
        counts = dict(base.get(key) or {})
        for name, value in (overlay.get(key) or {}).items():
            counts[name] = counts.get(name, 0) + value
        if key in base or key in overlay:
            merged[key] = counts
    merged["latency"] = _sum_latency(base.get("latency") or {}, overlay.get("latency") or {})
    return merged
