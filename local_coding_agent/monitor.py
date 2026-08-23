"""Lightweight stdlib-only HTTP monitoring server and HTML dashboard.

Provides real-time visibility into delegation performance, worker pool load,
queue depth, latency distributions, and task outcomes.
"""

from __future__ import annotations

import html
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .stats import DelegationStats, append_stats, load_stats, merge_stats_snapshots
from .worker_pool import BoundedWorkerPool


class MonitorRequestHandler(BaseHTTPRequestHandler):
    """Handler for HTTP metrics and live dashboard."""

    @property
    def monitor(self) -> Any:
        return getattr(self.server, "monitor_server", self.server)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in {"", "/dashboard"}:
            self._handle_dashboard()
        elif path == "/workbench":
            self._handle_workbench()
        elif path == "/health":
            self._handle_health()
        elif path == "/stats":
            self._handle_stats()
        elif path == "/tasks":
            self._handle_tasks()
        elif path in {"/api/events", "/events"}:
            self._handle_events()
        else:
            self._send_response(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"404 Not Found\n")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in {"/api/delegate", "/delegate"}:
            self._handle_api_delegate()
        else:
            self._send_response(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"404 Not Found\n")

    def _handle_api_delegate(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body)
            raw_task = data.get("task", {})
            profile_name = data.get("profile", "qwen2.5-1.5b")
            apply_flag = bool(data.get("apply", False))

            from pathlib import Path
            from .controller import Controller
            from .ollama_adapter import build_client
            from .profiles import get_profile
            from .task import TaskEnvelope

            task = TaskEnvelope.from_mapping(raw_task)
            profile = get_profile(profile_name)
            client = build_client(profile)
            controller = Controller(client, str(Path.cwd()))
            started_ns = time.monotonic_ns()
            result = controller.run(task, apply=apply_flag)
            if getattr(self.monitor, "stats_path", None):
                append_stats(
                    self.monitor.stats_path,
                    result,
                    model=profile_name,
                    latency_ns=time.monotonic_ns() - started_ns,
                )
            self._send_json(result)
        except Exception as error:
            self._send_json({"status": "failed", "error": str(error)})

    def _handle_events(self) -> None:
        events = self.monitor.get_recent_events()
        payload = {
            "uptime_seconds": round(time.monotonic() - self.monitor.started_at, 2),
            "events": events,
            "count": len(events),
        }
        self._send_json(payload)

    def _handle_health(self) -> None:
        payload = {
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - self.monitor.started_at, 2),
        }
        self._send_json(payload)

    def _stats_snapshot(self) -> dict[str, Any]:
        """Aggregate in-process stats with the cross-process JSONL journal."""
        if self.monitor.stats is None:
            return {}
        snapshot = self.monitor.stats.snapshot()
        stats_path = getattr(self.monitor, "stats_path", None)
        if stats_path:
            snapshot = merge_stats_snapshots(load_stats(stats_path).snapshot(), snapshot)
        return snapshot

    def _handle_stats(self) -> None:
        pool_data = self.monitor.worker_pool.status() if self.monitor.worker_pool is not None else {}
        payload = {
            "uptime_seconds": round(time.monotonic() - self.monitor.started_at, 2),
            "stats": self._stats_snapshot(),
            "worker_pool": pool_data,
        }
        self._send_json(payload)

    def _handle_tasks(self) -> None:
        pool_data = self.monitor.worker_pool.status() if self.monitor.worker_pool is not None else {}
        payload = {
            "jobs": pool_data.get("jobs", []),
            "total": pool_data.get("total_jobs", 0),
            "queued": pool_data.get("queued_jobs", 0),
            "active": pool_data.get("active_workers", 0),
        }
        self._send_json(payload)

    def _handle_dashboard(self) -> None:
        stats_data = self._stats_snapshot()
        pool_data = self.monitor.worker_pool.status() if self.monitor.worker_pool is not None else {}
        dashboard_html = self._render_dashboard_html(stats_data, pool_data)
        self._send_response(HTTPStatus.OK, "text/html; charset=utf-8", dashboard_html.encode("utf-8"))


    def _render_dashboard_html(self, stats: dict[str, Any], pool: dict[str, Any]) -> str:
        total = stats.get("total", 0)
        by_status = stats.get("by_status", {})
        accepted = by_status.get("accepted", 0)
        rejected = by_status.get("rejected", 0)
        failed = by_status.get("failed", 0)
        escaped = by_status.get("escalated", 0)
        success_rate = f"{(accepted / total * 100):.1f}%" if total > 0 else "N/A"

        active_workers = pool.get("active_workers", 0)
        max_workers = pool.get("max_workers", 1)
        queued_jobs = pool.get("queued_jobs", 0)
        max_queue = pool.get("max_queue", 16)
        stopping = pool.get("stopping", False)
        pool_status = "Stopping" if stopping else ("Busy" if active_workers >= max_workers else "Healthy")

        latency = stats.get("latency", {})
        avg_latency = f"{latency.get('avg_ms', 0):.1f} ms" if latency.get("avg_ms") is not None else "N/A"
        min_latency = f"{latency.get('min_ms', 0):.1f} ms" if latency.get("min_ms") is not None else "N/A"
        max_latency = f"{latency.get('max_ms', 0):.1f} ms" if latency.get("max_ms") is not None else "N/A"

        model_calls = stats.get("model_calls", 0)
        tool_calls = stats.get("tool_calls", 0)

        jobs = pool.get("jobs", [])
        jobs_rows = ""
        for job in jobs[-15:]:
            state = html.escape(str(job.get("state", "")))
            badge_class = "badge-success" if state == "completed" else ("badge-warning" if state == "running" else "badge-info")
            jobs_rows += f"""
            <tr>
              <td class="mono">{html.escape(str(job.get('job_id', '')))}</td>
              <td>{html.escape(str(job.get('caller_id', '')))}</td>
              <td><span class="badge {badge_class}">{state}</span></td>
              <td class="mono small">{html.escape(str(job.get('created_at', '')))}</td>
              <td class="mono small">{html.escape(str(job.get('updated_at', '')))}</td>
            </tr>
            """
        if not jobs:
            jobs_rows = "<tr><td colspan='5' class='empty'>No delegation jobs recorded yet.</td></tr>"

        errors = stats.get("by_error_kind", {})
        errors_rows = ""
        for kind, count in sorted(errors.items(), key=lambda x: x[1], reverse=True):
            errors_rows += f"<tr><td class='mono'>{html.escape(str(kind))}</td><td class='numeric'>{count}</td></tr>"
        if not errors:
            errors_rows = "<tr><td colspan='2' class='empty'>No errors recorded.</td></tr>"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="3">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Local Coding Agent Monitor</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --success: #4ade80;
      --warning: #facc15;
      --danger: #f87171;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }}
    h1 {{ font-size: 1.5rem; font-weight: 700; color: var(--accent); }}
    .status-badge {{
      display: inline-flex;
      align-items: center;
      padding: 4px 12px;
      border-radius: 9999px;
      font-size: 0.875rem;
      font-weight: 600;
      background: #064e3b;
      color: var(--success);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }}
    .card-title {{ font-size: 0.8125rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }}
    .card-value {{ font-size: 1.75rem; font-weight: 700; margin-top: 4px; }}
    .card-sub {{ font-size: 0.75rem; color: var(--text-muted); margin-top: 4px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.875rem;
    }}
    th, td {{
      padding: 10px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }}
    th {{ background: #182234; color: var(--text-muted); font-weight: 600; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 0.8125rem; }}
    .small {{ font-size: 0.75rem; color: var(--text-muted); }}
    .numeric {{ text-align: right; }}
    .empty {{ text-align: center; color: var(--text-muted); padding: 20px; }}
    .badge {{
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }}
    .badge-success {{ background: #065f46; color: #a7f3d0; }}
    .badge-warning {{ background: #713f12; color: #fef08a; }}
    .badge-info {{ background: #1e3a8a; color: #bfdbfe; }}
    .section-title {{ font-size: 1.125rem; font-weight: 600; margin-bottom: 12px; }}
    .two-col {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }}
    @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Local Coding Agent Monitor</h1>
      <p class="card-sub">Ollama Autonomous Delegation Service &amp; Bounded Worker Pool</p>
    </div>
    <span class="status-badge">{pool_status}</span>
  </header>

  <div class="grid">
    <div class="card">
      <div class="card-title">Worker Capacity</div>
      <div class="card-value">{active_workers} / {max_workers}</div>
      <div class="card-sub">Active workers running jobs</div>
    </div>
    <div class="card">
      <div class="card-title">Queue Depth</div>
      <div class="card-value">{queued_jobs} / {max_queue}</div>
      <div class="card-sub">Pending delegation requests</div>
    </div>
    <div class="card">
      <div class="card-title">Success Rate</div>
      <div class="card-value" style="color: var(--success);">{success_rate}</div>
      <div class="card-sub">{accepted} accepted / {total} total</div>
    </div>
    <div class="card">
      <div class="card-title">Avg Latency</div>
      <div class="card-value">{avg_latency}</div>
      <div class="card-sub">Min: {min_latency} | Max: {max_latency}</div>
    </div>
    <div class="card">
      <div class="card-title">Calls &amp; Tools</div>
      <div class="card-value">{model_calls} / {tool_calls}</div>
      <div class="card-sub">Model inferences / Tool executions</div>
    </div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Recent Delegation Jobs</div>
      <table>
        <thead>
          <tr>
            <th>Job ID</th>
            <th>Caller</th>
            <th>State</th>
            <th>Created</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {jobs_rows}
        </tbody>
      </table>
    </div>
    <div class="card">
      <div class="section-title">Failure Categories</div>
      <table>
        <thead>
          <tr>
            <th>Error Kind</th>
            <th class="numeric">Count</th>
          </tr>
        </thead>
        <tbody>
          {errors_rows}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""

    def _handle_workbench(self) -> None:
        workbench_html = self._render_workbench_html()
        self._send_response(HTTPStatus.OK, "text/html; charset=utf-8", workbench_html.encode("utf-8"))

    def _render_workbench_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Local Coding Agent — Interactive Coding Workbench</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --success: #4ade80;
      --warning: #facc15;
      --danger: #f87171;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    h1 { font-size: 1.5rem; font-weight: 700; color: var(--accent); }
    nav a {
      color: var(--text-muted);
      text-decoration: none;
      margin-left: 16px;
      font-size: 0.875rem;
      font-weight: 500;
    }
    nav a.active { color: var(--accent); border-bottom: 2px solid var(--accent); padding-bottom: 4px; }
    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }
    @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
    }
    .form-group { margin-bottom: 16px; }
    label { display: block; font-size: 0.8125rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; }
    input, textarea, select {
      width: 100%;
      background: #0b1120;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      padding: 8px 12px;
      font-family: inherit;
      font-size: 0.875rem;
    }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
    button {
      background: var(--accent);
      color: #0f172a;
      border: none;
      border-radius: 6px;
      padding: 10px 18px;
      font-weight: 600;
      cursor: pointer;
      font-size: 0.875rem;
    }
    button:hover { opacity: 0.9; }
    pre {
      background: #0b1120;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.8125rem;
      overflow-x: auto;
      max-height: 480px;
    }
  </style>
</head>
<body>
  <header>
    <div>
      <div style="display: flex; align-items: center; gap: 10px;">
        <h1>Local Coding Agent</h1>
        <span style="background: #854d0e; color: #fef08a; font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase;">Experimental Preview</span>
      </div>
      <p style="color: var(--text-muted); font-size: 0.8125rem;">Interactive Coding Workbench (Standalone Desktop Harness Redesign Underway)</p>
    </div>
    <nav>
      <a href="/dashboard">Dashboard</a>
      <a href="/workbench" class="active">Workbench</a>
    </nav>
  </header>

  <div class="two-col">
    <div class="card">
      <h2 style="font-size: 1.1rem; margin-bottom: 16px;">Task Envelope</h2>
      <form id="taskForm">
        <div class="form-group">
          <label>Task ID</label>
          <input type="text" id="taskId" value="atomic-fix-1" required>
        </div>
        <div class="form-group">
          <label>Goal</label>
          <input type="text" id="taskGoal" placeholder="Describe the single atomic change" required>
        </div>
        <div class="form-group">
          <label>Target Files (comma-separated)</label>
          <input type="text" id="taskFiles" placeholder="src/module.py" required>
        </div>
        <div class="form-group">
          <label>Target Checks (comma-separated)</label>
          <input type="text" id="taskChecks" placeholder="pytest tests/test_module.py">
        </div>
        <div class="form-group">
          <label>Model Profile</label>
          <select id="taskProfile">
            <option value="qwen2.5-1.5b">qwen2.5-1.5b</option>
            <option value="ling-3.0-tiny-q6k">ling-3.0-tiny-q6k (llama-server:8080)</option>
            <option value="qwen3-8b-q6k">qwen3-8b-q6k</option>
            <option value="qwen3.8-27b-q4">qwen3.8-27b-q4</option>
          </select>
        </div>
        <button type="submit" id="btnSubmit">Delegate Task</button>
      </form>
    </div>

    <div class="card">
      <h2 style="font-size: 1.1rem; margin-bottom: 16px;">Execution Result &amp; Diff</h2>
      <div id="statusBadge" style="margin-bottom: 12px; font-size: 0.875rem; color: var(--text-muted);">Ready. Submit a task envelope to run.</div>
      <pre id="outputPre">// Result will appear here...</pre>
    </div>
  </div>

  <script>
    document.getElementById('taskForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btnSubmit');
      const badge = document.getElementById('statusBadge');
      const output = document.getElementById('outputPre');

      btn.disabled = true;
      btn.textContent = 'Running...';
      badge.textContent = 'Delegating task to local model executor...';

      const task = {
        id: document.getElementById('taskId').value,
        goal: document.getElementById('taskGoal').value,
        files: document.getElementById('taskFiles').value.split(',').map(s => s.trim()).filter(Boolean),
        checks: document.getElementById('taskChecks').value.split(',').map(s => s.trim()).filter(Boolean)
      };

      try {
        const res = await fetch('/api/delegate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task, profile: document.getElementById('taskProfile').value })
        });
        const data = await res.json();
        badge.textContent = 'Status: ' + (data.status || 'done');
        output.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        badge.textContent = 'Error executing task';
        output.textContent = String(err);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Delegate Task';
      }
    });
  </script>
</body>
</html>"""

    def _send_json(self, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_response(HTTPStatus.OK, "application/json; charset=utf-8", body)

    def _send_response(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Ponytail / minimal: suppress default stderr request logging
        del format, args


class MonitorServer:
    """Thread-safe, daemonized monitoring HTTP server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        worker_pool: BoundedWorkerPool | None = None,
        stats: DelegationStats | None = None,
        stats_path: str | Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.worker_pool = worker_pool
        self.stats = stats
        self.stats_path = stats_path
        self.started_at = time.monotonic()
        self._events: list[dict[str, Any]] = []
        self._events_lock = threading.Lock()
        self._max_events = 200
        self._httpd = ThreadingHTTPServer((host, port), MonitorRequestHandler)
        self._httpd.monitor_server = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    def emit_event(self, event: dict[str, Any]) -> None:
        """Emit a telemetry or lifecycle event."""
        with self._events_lock:
            event_with_ts = {"timestamp": time.time(), **event}
            self._events.append(event_with_ts)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]

    def get_recent_events(self) -> list[dict[str, Any]]:
        """Return a copy of recent telemetry events."""
        with self._events_lock:
            return list(self._events)

    @property
    def server_address(self) -> tuple[str, int]:
        return self._httpd.server_address

    @property
    def actual_port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.actual_port}"

    def start(self) -> None:
        """Start serving in a background daemon thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="local-agent-monitor-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the HTTP server and release port."""
        if self._thread is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None

    def __enter__(self) -> MonitorServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
