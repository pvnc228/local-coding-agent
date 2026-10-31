import json
import tempfile
import unittest
from pathlib import Path

from local_coding_agent.stats import (
    DelegationStats,
    JsonlStatsSink,
    TimedDelegationStats,
    append_stats,
    load_stats,
    merge_stats_snapshots,
)


def _accepted():
    return {"status": "accepted", "audit": [{"event": "model_request"}, {"event": "tool_call"}]}


def _failed(kind):
    return {"status": "failed", "error": {"kind": kind}, "audit": []}


class DelegationStatsTests(unittest.TestCase):
    def test_record_accumulates_statuses_and_audit_counts(self):
        stats = DelegationStats()
        stats.record(_accepted(), model="a", latency_ns=1_000_000)
        stats.record(_failed("context_limit"), model="a", latency_ns=2_000_000)

        snapshot = stats.snapshot()

        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(snapshot["by_status"], {"accepted": 1, "failed": 1})
        self.assertEqual(snapshot["by_model"], {"a": 2})
        self.assertEqual(snapshot["by_error_kind"], {"context_limit": 1})
        self.assertEqual(snapshot["model_calls"], 1)
        self.assertEqual(snapshot["tool_calls"], 1)
        self.assertEqual(snapshot["latency"]["count"], 2)
        self.assertEqual(snapshot["latency"]["avg_ms"], 1.5)
        self.assertEqual(snapshot["latency"]["min_ms"], 1.0)
        self.assertEqual(snapshot["latency"]["max_ms"], 2.0)

    def test_snapshot_without_latency_leaves_none(self):
        stats = DelegationStats()
        stats.record(_accepted())

        self.assertIsNone(stats.snapshot()["latency"]["avg_ms"])

    def test_timed_stats_records_and_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sink = JsonlStatsSink(Path(temp_dir) / "stats.jsonl")
            stats = DelegationStats()
            timed = TimedDelegationStats(stats, sink=sink)

            def delegate(caller_id, request):
                return _accepted()

            result = timed(delegate, "caller", type("R", (), {"request_id": "r1"})(), model="m")

            self.assertEqual(result["status"], "accepted")
            self.assertEqual(stats.snapshot()["total"], 1)
            lines = (Path(temp_dir) / "stats.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["request_id"], "r1")
            self.assertEqual(record["model"], "m")
            self.assertEqual(record["status"], "accepted")

    def test_append_and_load_stats_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stats.jsonl"
            append_stats(path, _accepted(), model="m1", latency_ns=100_000_000)
            append_stats(path, _failed("backend_offline"), model="m2", latency_ns=50_000_000)
            # A corrupt partial line must be skipped, not raise.
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"status": "trunc')

            stats = load_stats(path)

            self.assertEqual(stats.snapshot()["total"], 2)
            self.assertEqual(stats.snapshot()["by_status"], {"accepted": 1, "failed": 1})
            self.assertEqual(stats.snapshot()["by_error_kind"], {"backend_offline": 1})
            self.assertEqual(stats.snapshot()["by_model"], {"m1": 1, "m2": 1})
            self.assertEqual(stats.snapshot()["latency"]["count"], 2)
            self.assertEqual(stats.snapshot()["latency"]["avg_ms"], 75.0)

    def test_load_stats_missing_file_returns_empty(self):
        stats = load_stats(Path(tempfile.gettempdir()) / "no-such-stats-file.jsonl")
        self.assertEqual(stats.snapshot()["total"], 0)

    def test_merge_stats_snapshots_sums_and_weights_latency(self):
        base = DelegationStats()
        base.record(_accepted(), model="a", latency_ns=100_000_000)
        base.record(_accepted(), model="a", latency_ns=300_000_000)
        overlay = DelegationStats()
        overlay.record(_accepted(), model="b", latency_ns=200_000_000)
        overlay.record({"status": "rejected", "audit": []})

        merged = merge_stats_snapshots(base.snapshot(), overlay.snapshot())

        self.assertEqual(merged["total"], 4)
        self.assertEqual(merged["by_status"], {"accepted": 3, "rejected": 1})
        self.assertEqual(merged["by_model"], {"a": 2, "b": 1})
        # Weighted average: (100 + 300 + 200) / 3, not a mean of means.
        self.assertAlmostEqual(merged["latency"]["avg_ms"], (100.0 + 300.0 + 200.0) / 3.0, places=3)
        self.assertEqual(merged["latency"]["min_ms"], 100.0)
        self.assertEqual(merged["latency"]["max_ms"], 300.0)
        self.assertNotIn("elapsed_seconds", merged)


if __name__ == "__main__":
    unittest.main()
