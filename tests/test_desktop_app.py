"""Unit tests for Desktop AI Coding Harness (R23)."""

import json
import subprocess
import urllib.error
import urllib.request
import pytest
from pathlib import Path
from unittest.mock import patch

from local_coding_agent.desktop.server import (
    DesktopRequestHandler,
    DesktopServer,
    _classify_backend_error,
    profile_model_is_available,
    resolve_model_profile,
    select_available_profile,
)
from local_coding_agent.cli import build_parser
from local_coding_agent.ollama_adapter import OllamaError


def _mutation_headers(server: DesktopServer) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Desktop-Token": server.mutation_token,
    }


def test_desktop_server_html_endpoint():
    with DesktopServer() as server:
        req = urllib.request.Request(f"{server.url}/app")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "Local AI Coding Harness" in content
            assert 'href="/assets/tailwind.css"' in content
            assert 'src="/assets/lucide.min.js"' in content
            assert "fonts.googleapis.com" not in content
            assert "cdn.tailwindcss.com" not in content
            assert "unpkg.com" not in content
            assert "Interactive Chat" in content
            assert "Delegated Tasks" in content


def test_desktop_server_status_api():
    with DesktopServer() as server:
        req = urllib.request.Request(f"{server.url}/api/status")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "healthy"
            assert "workspace" in data
            assert "git_branch" in data
            assert "vram" in data


def test_desktop_server_models_api():
    with DesktopServer() as server:
        req = urllib.request.Request(f"{server.url}/api/models")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "profiles" in data
            assert len(data["profiles"]) > 0
            assert "backends" in data
            assert "ollama" in data["backends"]
            assert "llama_server" in data["backends"]


def test_desktop_server_sessions_api():
    with DesktopServer() as server:
        # POST new user session
        new_sess_payload = json.dumps({
            "id": "test-sess-1",
            "type": "user",
            "title": "Test new session",
            "file": "calc.py",
            "patch": "diff --git a/calc.py b/calc.py\n+def add(): pass",
            "checks": ["pytest tests/"],
        }).encode("utf-8")
        req_post = urllib.request.Request(
            f"{server.url}/api/sessions",
            data=new_sess_payload,
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req_post, timeout=3.0) as resp_post:
            assert resp_post.status == 200
            post_data = json.loads(resp_post.read().decode("utf-8"))
            assert post_data["status"] == "created"
            assert post_data["session"]["title"] == "Test new session"

        # POST new agent session
        agent_sess_payload = json.dumps({
            "id": "test-sess-2",
            "type": "agent",
            "title": "Agent delegated task",
            "file": "calc.py",
            "patch": "",
            "checks": ["pytest tests/"],
        }).encode("utf-8")
        req_post_agent = urllib.request.Request(
            f"{server.url}/api/sessions",
            data=agent_sess_payload,
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req_post_agent, timeout=3.0) as resp_agent:
            assert resp_agent.status == 200

        # GET sessions
        req = urllib.request.Request(f"{server.url}/api/sessions")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "sessions" in data
            assert len(data["sessions"]) >= 2
            types = {s["type"] for s in data["sessions"]}
            assert "user" in types
            assert "agent" in types


def test_desktop_server_chat_api(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, *a, **k):
            raise RuntimeError("no model in test")

        def complete(self, *a, **k):
            raise RuntimeError("no model in test")

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    with DesktopServer() as server:
        payload = json.dumps({"prompt": "Fix bug in window.py", "profile": "qwen2.5-coder"}).encode("utf-8")
        req = urllib.request.Request(
            f"{server.url}/api/chat",
            data=payload,
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] in ("accepted", "failed", "completed")
            assert "thinking" in data


def _post_chat(server: DesktopServer, payload: dict):
    req = urllib.request.Request(
        f"{server.url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers=_mutation_headers(server),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def test_desktop_chat_mode_chat_does_not_run_controller(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    calls = {"chat": 0}
    controller_runs = []

    class _FakeClient:
        def chat(self, *a, **k):
            calls["chat"] += 1
            return {"message": {"content": "hello from fake"}}

    class _FakeController:
        def __init__(self, *a, **k):
            pass

        def run(self, task, **k):
            controller_runs.append(task)
            return {"status": "accepted", "summary": "should not run", "patch": "", "checks": []}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr("local_coding_agent.controller.Controller", _FakeController)
    with DesktopServer() as server:
        data = _post_chat(server, {"prompt": "hello there", "profile": "qwen2.5-coder", "mode": "chat"})
    assert data["mode"] == "chat"
    assert data["message"] == "hello from fake"
    assert calls["chat"] == 1
    assert controller_runs == []  # Controller must NOT run for chat mode


def test_desktop_chat_mode_build_runs_controller(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    controller_runs = []

    class _FakeClient:
        def chat(self, *a, **k):
            return {"message": {"content": "x"}}

    class _FakeController:
        def __init__(self, *a, **k):
            pass

        def run(self, task, **k):
            controller_runs.append(task)
            return {"status": "accepted", "summary": "built", "patch": "diff", "checks": []}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr("local_coding_agent.controller.Controller", _FakeController)
    with DesktopServer() as server:
        data = _post_chat(server, {"prompt": "fix the bug", "profile": "qwen2.5-coder", "mode": "build"})
    assert data["mode"] == "build"
    assert len(controller_runs) == 1


def test_desktop_chat_mode_plan_returns_plan_artifact(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    ctor_kwargs = []

    class _FakeClient:
        def chat(self, *a, **k):
            return {"message": {"content": "x"}}

    class _FakeController:
        def __init__(self, *a, **k):
            ctor_kwargs.append(k)

        def run(self, task, **k):
            return {
                "status": "candidate",
                "summary": "refactor steps",
                "patch": "",
                "checks": [],
                "risks": [{"kind": "k", "message": "risk1"}],
            }

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr("local_coding_agent.controller.Controller", _FakeController)
    with DesktopServer() as server:
        data = _post_chat(server, {
            "prompt": "plan the refactor", "profile": "qwen2.5-coder", "mode": "plan",
            "files": ["a.py"],
        })
    assert data["mode"] == "plan"
    # Read-only guarantee must be enforced at the tool-policy layer.
    assert ctor_kwargs and ctor_kwargs[0]["blocked_tools"] == {"propose_patch", "run_tests"}
    plan = data["plan"]
    assert set(plan) >= {"goal", "steps", "risks", "files_to_modify"}
    assert plan["goal"] == "plan the refactor"
    assert plan["risks"] == ["risk1"]
    assert plan["files_to_modify"] == ["a.py"]


def test_desktop_chat_mode_hybrid_resolves(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, *a, **k):
            return {"message": {"content": "x"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    with DesktopServer() as server:
        # no mode -> hybrid -> classifier resolves to a concrete mode
        data = _post_chat(server, {"prompt": "hello there", "profile": "qwen2.5-coder"})
    assert data["mode"] in {"chat", "build", "plan"}
    assert data["mode"] != "hybrid"


def test_desktop_chat_invalid_mode_falls_back_to_hybrid(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, *a, **k):
            return {"message": {"content": "x"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    with DesktopServer() as server:
        data = _post_chat(server, {"prompt": "hello there", "profile": "qwen2.5-coder", "mode": "bogus"})
    assert data["mode"] in {"chat", "build", "plan"}
    assert data["mode"] != "hybrid"


def test_desktop_chat_hybrid_uses_small_model_router(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    controller_runs = []
    router_calls = []

    class _FakeClient:
        def chat(self, *a, **k):
            return {"message": {"content": "x"}}

    class _FakeController:
        def __init__(self, *a, **k):
            pass

        def run(self, task, **k):
            controller_runs.append(task)
            return {"status": "accepted", "summary": "built", "patch": "diff", "checks": []}

    def fake_build_router(*args, **kwargs):
        # Must be called with NO positional profile — the small default profile.
        assert args == ()
        router_calls.append(None)

        def fake_router(recent_prompts=None):
            return "chat"

        return fake_router

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr("local_coding_agent.controller.Controller", _FakeController)
    monkeypatch.setattr("local_coding_agent.mode_router.build_mode_router", fake_build_router)
    with DesktopServer() as server:
        data = _post_chat(server, {"prompt": "hello there", "profile": "qwen2.5-coder"})
    assert data["mode"] == "chat"  # router wins over heuristic
    assert data["message"] == "x"
    assert len(router_calls) == 1
    assert controller_runs == []  # Controller must NOT run for chat


def test_desktop_chat_hybrid_router_build_failure_falls_back(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, *a, **k):
            return {"message": {"content": "x"}}

    def broken_build_router(profile_name, *, client=None):
        raise RuntimeError("no router model available")

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr("local_coding_agent.mode_router.build_mode_router", broken_build_router)
    with DesktopServer() as server:
        data = _post_chat(server, {"prompt": "hello there", "profile": "qwen2.5-coder"})
    assert data["status"] == "completed"
    assert data["mode"] in {"chat", "build", "plan"}
    assert data["mode"] != "hybrid"


def test_desktop_build_mode_question_bypasses_controller(monkeypatch, tmp_path):
    # A question never needs a patch: even with Build explicitly selected,
    # "can u tell me ..." must go to the file-aware info branch, not the
    # coding agent loop (which small models fail with max_turns exceeded).
    import local_coding_agent.desktop.server._handlers as h

    seen_messages = []
    controller_runs = []

    class _FakeClient:
        def chat(self, messages):
            seen_messages.append(messages)
            return {"message": {"content": "it defines the window!"}}

    class _FakeController:
        def __init__(self, *a, **k):
            pass

        def run(self, task, **k):
            controller_runs.append(task)
            return {"status": "accepted", "summary": "should not run", "patch": "", "checks": []}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr("local_coding_agent.controller.Controller", _FakeController)

    (tmp_path / "window.py").write_text("def render(): pass\n", encoding="utf-8")
    with DesktopServer(workspace=tmp_path) as server:
        data_build = _post_chat(server, {
            "prompt": "can u tell me what window.py does?",
            "profile": "qwen2.5-coder",
            "mode": "build",
        })
        data_chat = _post_chat(server, {
            "prompt": "can u tell me what window.py does?",
            "profile": "qwen2.5-coder",
            "mode": "chat",
        })

    for data in (data_build, data_chat):
        assert data["status"] == "completed"
        assert data["message"] == "it defines the window!"
        assert data["file"] == "window.py"
    assert controller_runs == []
    system = seen_messages[0][0]["content"]
    assert "window.py" in system and "def render()" in system


def test_desktop_detect_files_keyword_scoring(monkeypatch, tmp_path):
    # "desktop ui components" must surface desktop/ui.py + desktop/components.py
    # even though the prompt mentions no filename (old fallback: git-dirty files).
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, messages):
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())

    pkg = tmp_path / "local_coding_agent" / "desktop"
    pkg.mkdir(parents=True)
    (tmp_path / "local_coding_agent" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ui.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "components.py").write_text("x = 2\n", encoding="utf-8")
    (pkg / "server.py").write_text("x = 3\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("x = 4\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    (tmp_path / "unrelated.py").write_text("x = 5\n", encoding="utf-8")  # dirty

    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/chat",
            data=json.dumps({
                "prompt": "well can at least list the files relating to ui components?",
                "profile": "qwen2.5-coder",
                "mode": "build",
            }).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))

    assert data["file"] in (
        "local_coding_agent/desktop/components.py",
        "local_coding_agent/desktop/ui.py",
    )
    assert "unrelated.py" not in data["file"]


def test_desktop_chat_completion_backend_failure_not_masked(monkeypatch):
    # A dead backend must surface a failure, never the canned "Connected"
    # greeting (which previously lied while Ollama was offline).
    import local_coding_agent.desktop.server._handlers as h

    class _DeadClient:
        def chat(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(h, "build_client", lambda profile: _DeadClient())
    with DesktopServer() as server:
        data = _post_chat(server, {"prompt": "tell me a joke", "profile": "qwen2.5-coder", "mode": "chat"})
    assert data["status"] == "failed"
    assert "connection refused" in data["error"]


def test_desktop_chat_ctx_override_reaches_client_profile(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as h

    seen_profiles = []

    class _FakeClient:
        def chat(self, messages):
            return {"message": {"content": "answered"}}

    def fake_build(profile):
        seen_profiles.append(profile)
        return _FakeClient()

    monkeypatch.setattr(h, "build_client", fake_build)
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    with DesktopServer(workspace=tmp_path) as server:
        data = _post_chat(server, {
            "prompt": "what does calc.py do?",
            "profile": "qwen2.5-coder",
            "mode": "chat",
            "num_ctx": 16384,
        })
    assert data["status"] == "completed"
    assert seen_profiles[-1].num_ctx == 16384


def test_desktop_chat_ctx_override_relaunches_llama_server(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as h

    launches = []

    class _FakeClient:
        def chat(self, messages):
            return {"message": {"content": "answered"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())

    def fake_launch(self, gguf_path, label, num_ctx=None):
        launches.append({"path": gguf_path, "label": label, "num_ctx": num_ctx})
        if num_ctx is not None:
            self.server_inst.llama_num_ctx = max(512, int(num_ctx))
        return {"status": "started", "backend": "llama_server"}

    monkeypatch.setattr(h.DesktopRequestHandler, "_launch_llama_model", fake_launch)
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    with DesktopServer(workspace=tmp_path) as server:
        server.llama_gguf_path = "C:/models/Ling-3.0-tiny-Q6_K.gguf"
        server.llama_gguf_label = "Ling-3.0-tiny-Q6_K"
        data = _post_chat(server, {
            "prompt": "what does calc.py do?",
            "profile": "ling-3.0-tiny-q6k",
            "mode": "chat",
            "num_ctx": 16384,
        })
        assert data["status"] == "completed"
        assert launches and launches[0]["num_ctx"] == 16384
        assert server.llama_num_ctx == 16384


def test_desktop_chat_ctx_override_external_server_is_prescriptive(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, messages):
            return {"message": {"content": "should not be reached"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    with DesktopServer(workspace=tmp_path) as server:
        data = _post_chat(server, {
            "prompt": "what does calc.py do?",
            "profile": "ling-3.0-tiny-q6k",
            "mode": "chat",
            "num_ctx": 16384,
        })
    assert data["status"] == "failed"
    assert "-c 8192" in data["error"]
    assert "16384" in data["error"]


def test_read_effective_ctx_parses_props_and_fails_closed(monkeypatch):
    # llama-server may silently clamp -c to the model's native context length;
    # the controller must read back /props instead of trusting the launch flag.
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured = {}

    class _PropsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            body = json.dumps({"default_generation_settings": {"n_ctx": 4096}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), _PropsHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        effective = DesktopRequestHandler._read_effective_ctx(None, port=port)
        assert effective == 4096
        assert captured["path"] == "/props"
    finally:
        httpd.shutdown()
        httpd.server_close()

    # Unreachable / non-llama endpoint must return None, never raise.
    assert DesktopRequestHandler._read_effective_ctx(None, port=1) is None


def test_launch_llama_model_reports_clamped_ctx(monkeypatch, tmp_path):
    import types

    import local_coding_agent.desktop.server._handlers as h

    monkeypatch.setattr(h.DesktopRequestHandler, "_find_llama_server_bin", lambda self, custom=None: "llama-server")
    monkeypatch.setattr(h.DesktopRequestHandler, "_stop_backend", lambda self, name: None)
    monkeypatch.setattr(h.DesktopRequestHandler, "_kill_llama_on_port", lambda self, port: None)
    fake_proc = types.SimpleNamespace(poll=lambda: None, pid=4321)
    spawned_cmds = []
    monkeypatch.setattr(
        h.subprocess,
        "Popen",
        lambda cmd, **kwargs: (spawned_cmds.append(cmd), fake_proc)[1],
    )
    monkeypatch.setattr(
        h.DesktopRequestHandler,
        "_wait_for_model_loaded",
        lambda self, proc, backend, timeout=90.0: {"ok": True, "status": "started"},
    )
    # Server clamped the requested 16384 down to the model's native 8192.
    monkeypatch.setattr(h.DesktopRequestHandler, "_read_effective_ctx", lambda self, port=8080: 8192)

    stub = object.__new__(h.DesktopRequestHandler)
    server_inst = types.SimpleNamespace(
        workspace=str(tmp_path),
        llama_num_ctx=16384,
        llama_gguf_path=None,
        llama_gguf_label=None,
        spawned_processes={},
    )
    # handler.server_inst is a read-only property over self.server.desktop_server
    stub.server = types.SimpleNamespace(desktop_server=server_inst)
    result = h.DesktopRequestHandler._launch_llama_model(stub, str(tmp_path / "m.gguf"), "m")

    assert result["status"] == "started"
    assert server_inst.llama_effective_ctx == 8192
    assert "ctx_warning" in result
    assert "16384" in result["ctx_warning"]
    assert "8192" in result["ctx_warning"]
    assert ["-c", "16384"] == spawned_cmds[0][spawned_cmds[0].index("-c"):spawned_cmds[0].index("-c") + 2]


def test_launch_llama_model_no_warning_when_ctx_matches(monkeypatch, tmp_path):
    import types

    import local_coding_agent.desktop.server._handlers as h

    monkeypatch.setattr(h.DesktopRequestHandler, "_find_llama_server_bin", lambda self, custom=None: "llama-server")
    monkeypatch.setattr(h.DesktopRequestHandler, "_stop_backend", lambda self, name: None)
    monkeypatch.setattr(h.DesktopRequestHandler, "_kill_llama_on_port", lambda self, port: None)
    fake_proc = types.SimpleNamespace(poll=lambda: None, pid=4322)
    monkeypatch.setattr(h.subprocess, "Popen", lambda cmd, **kwargs: fake_proc)
    monkeypatch.setattr(
        h.DesktopRequestHandler,
        "_wait_for_model_loaded",
        lambda self, proc, backend, timeout=90.0: {"ok": True, "status": "started"},
    )
    monkeypatch.setattr(h.DesktopRequestHandler, "_read_effective_ctx", lambda self, port=8080: 8192)

    stub = object.__new__(h.DesktopRequestHandler)
    server_inst = types.SimpleNamespace(
        workspace=str(tmp_path),
        llama_num_ctx=8192,
        llama_gguf_path=None,
        llama_gguf_label=None,
        spawned_processes={},
    )
    stub.server = types.SimpleNamespace(desktop_server=server_inst)
    result = h.DesktopRequestHandler._launch_llama_model(stub, str(tmp_path / "m.gguf"), "m")

    assert result["status"] == "started"
    assert "ctx_warning" not in result


def test_desktop_info_branch_injects_multiple_files(monkeypatch, tmp_path):
    # Single-file context made the model answer "I don't have access" when the
    # question spanned several allowlisted files.
    import local_coding_agent.desktop.server._handlers as h

    seen_messages = []

    class _FakeClient:
        def chat(self, messages):
            seen_messages.append(messages)
            return {"message": {"content": "both files explained"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    (tmp_path / "ui.py").write_text("UI = True\n", encoding="utf-8")
    (tmp_path / "components.py").write_text("COMPONENTS = True\n", encoding="utf-8")
    with DesktopServer(workspace=tmp_path) as server:
        data = _post_chat(server, {
            "prompt": "what do these files do?",
            "profile": "qwen2.5-coder",
            "mode": "chat",
            "files": ["ui.py", "components.py"],
        })
    assert data["status"] == "completed"
    system = seen_messages[0][0]["content"]
    assert "--- ui.py ---" in system and "UI = True" in system
    assert "--- components.py ---" in system and "COMPONENTS = True" in system


def test_desktop_detect_files_prefers_prompt_mention(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def chat(self, messages):
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())

    (tmp_path / "other.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "window.py").write_text("b = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    # Make other.py git-dirty: the old heuristic answered about this file.
    (tmp_path / "other.py").write_text("a = 2\n", encoding="utf-8")

    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/chat",
            data=json.dumps({
                "prompt": "can u tell me what window.py does?",
                "profile": "qwen2.5-coder",
                "mode": "build",
            }).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))

    assert data["file"] == "window.py"


def test_desktop_server_rollback_api(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "status" in data


def test_desktop_server_apply_no_patch(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": ""}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "failed"
            assert "No patch content" in data["error"]


def test_desktop_server_apply_rejects_out_of_scope(tmp_path):
    # ponytail: verify Strict Scope Boundary on the desktop apply seam — a
    # patch touching a file outside the declared `files` list must be rejected
    # before touching the workspace.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    patch = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": ["a.py"], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "applied"
            assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 2\n"

    # Out-of-scope: declare only b.py, patch touches a.py -> rejected
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": ["b.py"], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "rejected"
            assert "outside the declared scope" in data["error"]


def test_desktop_server_rollback_is_scoped_to_applied_files(tmp_path):
    # ponytail: rollback must only restore files this session applied, not
    # wipe unrelated uncommitted work (was `git restore .`).
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("keep = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    patch = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    with DesktopServer(workspace=tmp_path) as server:
        # apply with declared scope
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": ["a.py"], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert json.loads(resp.read().decode("utf-8"))["status"] == "applied"

        # dirty an unrelated file
        (tmp_path / "unrelated.py").write_text("keep = False\n", encoding="utf-8")

        # rollback
        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "rolled_back"
            assert "a.py" in data["restored"]

        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"
        # unrelated uncommitted change must survive scoped rollback
        assert (tmp_path / "unrelated.py").read_text(encoding="utf-8") == "keep = False\n"


def test_desktop_server_rollback_uses_changed_set_not_allowlist(tmp_path):
    # ponytail: last_applied_files must hold the files the patch ACTUALLY
    # changed, not the declared allowlist. Declaring [a.py, b.py] while the
    # patch only touches a.py must not let rollback wipe a dirty b.py.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    patch = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    with DesktopServer(workspace=tmp_path) as server:
        # Declare BOTH a.py and b.py in scope; patch touches only a.py
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": ["a.py", "b.py"], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert json.loads(resp.read().decode("utf-8"))["status"] == "applied"

        # b.py is dirty (uncommitted work the patch never touched)
        (tmp_path / "b.py").write_text("y = 999\n", encoding="utf-8")

        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "rolled_back"
            assert data["restored"] == ["a.py"]

        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"
        # b.py dirty work must survive (it was not in the changed set)
        assert (tmp_path / "b.py").read_text(encoding="utf-8") == "y = 999\n"


def test_desktop_server_apply_requires_declared_scope(tmp_path):
    # ponytail: scope boundary is server-enforced — apply with an empty
    # allowlist is rejected rather than applied to anything.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    patch = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": [], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "rejected"
            assert "No declared file scope" in data["error"]
        # a.py untouched
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_desktop_server_rollback_removes_new_file(tmp_path):
    # ponytail: reverse-apply rollback must also clean up newly-created
    # (untracked) files — `git restore` alone cannot remove them.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    patch = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1 @@\n"
        "+created\n"
    )
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": ["new.py"], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert json.loads(resp.read().decode("utf-8"))["status"] == "applied"
        assert (tmp_path / "new.py").read_text(encoding="utf-8") == "created\n"

        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "rolled_back"
            assert data["restored"] == ["new.py"]

        assert not (tmp_path / "new.py").exists()


def test_desktop_server_rollback_failure_preserves_state(tmp_path, monkeypatch):
    # ponytail: if the rollback (reverse-apply) cannot be applied, the server
    # must report failed and keep last_applied_* so a retry is possible.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=False,
    )
    patch = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    with DesktopServer(workspace=tmp_path) as server:
        req = urllib.request.Request(
            f"{server.url}/api/apply",
            data=json.dumps({"patch": patch, "files": ["a.py"], "checks": []}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert json.loads(resp.read().decode("utf-8"))["status"] == "applied"

        # Force reverse-apply to fail (e.g. file changed so it can't revert)
        import local_coding_agent.desktop.server._handlers as h
        monkeypatch.setattr(
            h, "apply_patch", lambda ws, p, reverse=False: (False, "boom") if reverse else (True, "")
        )
        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "failed"
            assert "Rollback failed" in data["error"]

        # state preserved for a retry
        assert server.last_applied_files == ["a.py"]
        assert server.last_applied_patch == patch


def test_cli_desktop_parser():
    parser = build_parser()
    args = parser.parse_args(["desktop", "--port", "9876", "--browser", "--profile", "qwen3-8b-q6k"])
    assert args.subcommand == "desktop"
    assert args.port == 9876
    assert args.browser is True
    assert args.profile == "qwen3-8b-q6k"


def test_desktop_server_model_scanner_endpoints(tmp_path):
    with DesktopServer() as server:
        # 1. Test POST /api/models/add_dir
        add_req = urllib.request.Request(
            f"{server.url}/api/models/add_dir",
            data=json.dumps({"path": str(tmp_path)}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(add_req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] in ("added", "already_present")

        # 2. Test POST /api/models/scan
        scan_req = urllib.request.Request(
            f"{server.url}/api/models/scan",
            data=json.dumps({"deep": False}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(scan_req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"
            assert "models" in data

        # 3. Test POST /api/models/remove_dir
        remove_req = urllib.request.Request(
            f"{server.url}/api/models/remove_dir",
            data=json.dumps({"path": str(tmp_path)}).encode("utf-8"),
            headers=_mutation_headers(server),
            method="POST",
        )
        with urllib.request.urlopen(remove_req, timeout=3.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] in ("removed", "not_found")


def test_resolve_model_profile_gguf_gives_openai_endpoint_without_v1():
    """A discovered GGUF/display name resolves to an openai profile on :8080 (no /v1)."""
    fake = {
        "name": "custom-llama-9b-q4.gguf",
        "display_name": "custom-llama-9b-q4",
        "path": "/tmp/custom-llama.gguf",
        "size_gb": 2.5,
        "backend": "gguf",
        "source": "custom",
    }

    class FakeRegistry:
        def get_models(self, auto_scan=True):
            from local_coding_agent.model_scanner import DiscoveredModel
            return [DiscoveredModel.from_dict(fake)]

    with patch("local_coding_agent.desktop.server.discover_local_ollama_models", return_value=[]):
        prof = resolve_model_profile("custom-llama-9b-q4", registry=FakeRegistry())

    assert prof.provider == "openai"
    assert prof.endpoint == "http://127.0.0.1:8080"
    assert not prof.endpoint.endswith("/v1")
    assert prof.model == "custom-llama-9b-q4"


def test_resolve_model_profile_known_profile_returns_get_profile():
    """A known profile name still resolves exactly via get_profile (ling is openai :8080)."""
    with patch("local_coding_agent.desktop.server.discover_local_ollama_models", return_value=[]):
        prof = resolve_model_profile("ling-3.0-tiny-q6k")
    assert prof.provider == "openai"
    assert prof.endpoint == "http://127.0.0.1:8080"
    assert not prof.endpoint.endswith("/v1")
    assert prof.model == "ling-3.0-tiny-q6k"


def test_resolve_model_profile_ollama_tag():
    """An installed Ollama tag resolves to an ollama profile on :11434."""
    with patch("local_coding_agent.desktop.server.discover_local_ollama_models", return_value=["qwen2.5:1.5b"]):
        prof = resolve_model_profile("qwen2.5")
    assert prof.provider == "ollama"
    assert prof.endpoint == "http://127.0.0.1:11434"
    assert prof.model == "qwen2.5"


def test_classify_backend_error_by_kind():
    assert _classify_backend_error(OllamaError("boom", kind="transport")) == "offline"
    assert _classify_backend_error(OllamaError("boom", kind="http")) == "server_error"
    assert _classify_backend_error(OllamaError("boom", kind="invalid_json")) is None
    assert _classify_backend_error(RuntimeError("Connection refused")) is None


def test_profile_model_is_available_ollama():
    from local_coding_agent.ollama_adapter import ModelProfile
    prof = ModelProfile(name="x", model="qwen2.5:1.5b")
    with patch("local_coding_agent.desktop.server.discover_local_ollama_models", return_value=["qwen2.5:1.5b"]):
        assert profile_model_is_available(prof) is True
    with patch("local_coding_agent.desktop.server.discover_local_ollama_models", return_value=[]):
        assert profile_model_is_available(prof) is False


def test_select_available_profile_falls_back_to_ollama():
    with patch("local_coding_agent.desktop.server.discover_local_ollama_models", return_value=["qwen2.5:1.5b"]):
        with patch("local_coding_agent.desktop.server.profile_model_is_available", return_value=False):
            assert select_available_profile("qwen2.5-coder") == "qwen2.5:1.5b"


def _make_handler(server: DesktopServer) -> DesktopRequestHandler:
    handler = DesktopRequestHandler.__new__(DesktopRequestHandler)
    handler.server = server._httpd
    handler.headers = {"Content-Length": "0"}
    return handler


def test_server_log_file_creates_parent_and_path(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        handler = _make_handler(server)
        log_path = handler._server_log_file("ollama")
        assert log_path == Path(tmp_path) / ".local_agent" / "logs" / "ollama.log"
        assert log_path.parent.is_dir()


def test_read_log_tail_missing_file_returns_empty(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        handler = _make_handler(server)
        assert handler._read_log_tail("ollama") == ""


def test_read_log_tail_returns_last_lines(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        handler = _make_handler(server)
        log_file = handler._server_log_file("ollama")
        log_file.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        tail = handler._read_log_tail("ollama", n=3)
        assert tail.strip().splitlines() == ["line7", "line8", "line9"]


def test_handle_server_stop_uses_taskkill_on_windows(tmp_path, monkeypatch):
    with DesktopServer(workspace=tmp_path) as server:
        handler = _make_handler(server)
        monkeypatch.setattr(handler, "_send_json", lambda d: None)
        fake_proc = subprocess.Popen.__new__(subprocess.Popen)
        fake_proc.pid = 12345
        server.spawned_processes["ollama"] = fake_proc
        monkeypatch.setattr("local_coding_agent.desktop.server.os.name", "nt")
        runs = []
        monkeypatch.setattr(
            "local_coding_agent.desktop.server.subprocess.run",
            lambda *a, **k: runs.append((a, k)) or subprocess.CompletedProcess(a[0], 0),
        )
        handler._handle_server_stop()
        assert server.spawned_processes == {}
        assert runs and runs[0][0][0] == ["taskkill", "/F", "/T", "/PID", "12345"]


def test_handle_server_stop_uses_terminate_on_posix(tmp_path, monkeypatch):
    with DesktopServer(workspace=tmp_path) as server:
        handler = _make_handler(server)
        monkeypatch.setattr(handler, "_send_json", lambda d: None)
        fake_proc = subprocess.Popen.__new__(subprocess.Popen)
        fake_proc.pid = 12345
        calls = {"terminate": 0, "wait": 0}
        fake_proc.terminate = lambda: calls.__setitem__("terminate", calls["terminate"] + 1)
        fake_proc.wait = lambda timeout: calls.__setitem__("wait", calls["wait"] + 1)
        server.spawned_processes["ollama"] = fake_proc
        monkeypatch.setattr("local_coding_agent.desktop.server.os.name", "posix")
        handler._handle_server_stop()
        assert server.spawned_processes == {}
        assert calls == {"terminate": 1, "wait": 1}


def test_v1_models_merges_backends(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    monkeypatch.setattr(h.DesktopRequestHandler, "_list_llama_model_ids", lambda self: ["Ling-3.0-tiny-Q6_K"])
    monkeypatch.setattr(h.DesktopRequestHandler, "_list_ollama_model_tags", lambda self: ["qwen2.5-coder:latest"])
    with DesktopServer() as server:
        req = urllib.request.Request(f"{server.url}/v1/models")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    ids = [m["id"] for m in data["data"]]
    assert data["object"] == "list"
    assert ids == ["Ling-3.0-tiny-Q6_K", "qwen2.5-coder:latest"]
    owners = {m["id"]: m["owned_by"] for m in data["data"]}
    assert owners["Ling-3.0-tiny-Q6_K"] == "llama-server"
    assert owners["qwen2.5-coder:latest"] == "ollama"


def test_v1_chat_completions_proxies_to_backend(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import local_coding_agent.desktop.server._handlers as h

    captured = {}

    class _Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
            body = json.dumps({
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "proxied"}, "finish_reason": "stop"}],
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(
            h.DesktopRequestHandler,
            "_resolve_v1_target",
            lambda self, model_id: ("127.0.0.1", httpd.server_address[1]),
        )
        with DesktopServer() as server:
            payload = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode("utf-8")
            req = urllib.request.Request(
                f"{server.url}/v1/chat/completions",
                data=payload,
                headers=_mutation_headers(server),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
        assert data["choices"][0]["message"]["content"] == "proxied"
        assert captured["body"]["model"] == "m"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_v1_chat_completions_unknown_model_is_prescriptive_404(monkeypatch):
    import local_coding_agent.desktop.server._handlers as h

    monkeypatch.setattr(h.DesktopRequestHandler, "_list_llama_model_ids", lambda self: [])
    monkeypatch.setattr(h.DesktopRequestHandler, "_list_ollama_model_tags", lambda self: [])
    with DesktopServer() as server:
        payload = json.dumps({"model": "nope", "messages": []}).encode("utf-8")
        req = urllib.request.Request(
            f"{server.url}/v1/chat/completions",
            data=payload,
            headers=_mutation_headers(server),
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=3.0)
            raised = False
        except urllib.error.HTTPError as err:
            raised = True
            assert err.code == 404
            body = json.loads(err.read().decode("utf-8"))
    assert raised
    assert body["error"]["type"] == "model_not_found"
    assert "Available models" in body["error"]["message"]


# ---------------------------------------------------------------------------
# R23 background task queue (Desktop Harness)
# ---------------------------------------------------------------------------

import time as _time


def _post_json(server: DesktopServer, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{server.url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_mutation_headers(server),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _get_json(server: DesktopServer, path: str) -> dict:
    req = urllib.request.Request(f"{server.url}{path}")
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _wait_for_task(server: DesktopServer, task_id: str, statuses: set, timeout: float = 6.0) -> dict:
    deadline = _time.monotonic() + timeout
    last: dict | None = None
    while _time.monotonic() < deadline:
        for record in _get_json(server, "/api/tasks")["tasks"]:
            if record["id"] == task_id:
                last = record
                if record["status"] in statuses:
                    return record
        _time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not reach {statuses}; last={last}")


def test_desktop_task_queue_runs_in_background(tmp_path):
    class _FakeController:
        def run(self, task):
            return {"status": "accepted", "summary": "ok", "patch": "--- a\n+++ b", "checks": [], "risks": []}

    with DesktopServer(workspace=tmp_path) as server:
        server.controller_factory = lambda profile, workspace, cancel_event=None: _FakeController()
        resp = _post_json(server, "/api/tasks", {"goal": "add feature", "files": ["a.py"], "checks": []})
        assert resp["status"] == "queued"
        task_id = resp["task"]["id"]
        assert task_id.startswith("task-")

        record = _wait_for_task(server, task_id, {"accepted"})
        assert record["patch"] == "--- a\n+++ b"
        assert record["summary"] == "ok"
        assert record["files"] == ["a.py"]
        assert record["started_at"] is not None and record["finished_at"] is not None


def test_desktop_task_queue_failure_captures_error(tmp_path):
    class _FailingController:
        def run(self, task):
            return {
                "status": "failed",
                "summary": "",
                "patch": "",
                "checks": [],
                "error": {"kind": "model_error", "message": "boom"},
            }

    with DesktopServer(workspace=tmp_path) as server:
        server.controller_factory = lambda *a, **k: _FailingController()
        task_id = _post_json(server, "/api/tasks", {"goal": "doomed"})["task"]["id"]
        record = _wait_for_task(server, task_id, {"failed"})
        assert record["error"]["message"] == "boom"
        assert record["status"] != "accepted"


def test_desktop_task_queue_cancel_queued_and_running(tmp_path):
    import threading

    gate = threading.Event()
    ran_tasks = []

    class _BlockingController:
        def run(self, task):
            ran_tasks.append(task.id)
            gate.wait(timeout=10)
            return {"status": "accepted", "summary": "ok", "patch": "p", "checks": []}

    with DesktopServer(workspace=tmp_path) as server:
        server.controller_factory = lambda *a, **k: _BlockingController()
        first = _post_json(server, "/api/tasks", {"goal": "first"})["task"]["id"]
        _wait_for_task(server, first, {"running"})

        second = _post_json(server, "/api/tasks", {"goal": "second"})["task"]["id"]
        # Sequential queue: the second task must stay queued while the first
        # occupies the single worker slot.
        snapshot = {t["id"]: t for t in _get_json(server, "/api/tasks")["tasks"]}
        assert snapshot[second]["status"] == "queued"
        assert _get_json(server, "/api/status")["tasks"] == {"queued": 1, "running": 1}

        # Cancel the queued task: takes effect immediately.
        out = _post_json(server, "/api/tasks/cancel", {"id": second})
        assert out["status"] == "cancelled"

        # Cancel the running task cooperatively.
        out = _post_json(server, "/api/tasks/cancel", {"id": first})
        assert out["status"] == "cancelling"

        gate.set()
        record = _wait_for_task(server, first, {"cancelled", "failed"}, timeout=8.0)
        assert record["status"] != "accepted"

    assert ran_tasks == [first]  # cancelled queued task never executed

    # Unknown id mirrors the existing failed-error shape.
    with DesktopServer(workspace=tmp_path) as server:
        out = _post_json(server, "/api/tasks/cancel", {"id": "task-nope"})
        assert out["status"] == "failed"
        assert "Unknown task id" in out["error"]


def test_desktop_task_queue_persists_across_restart(tmp_path):
    class _FakeController:
        def run(self, task):
            return {"status": "accepted", "summary": "ok", "patch": "p", "checks": []}

    with DesktopServer(workspace=tmp_path) as server:
        server.controller_factory = lambda *a, **k: _FakeController()
        task_id = _post_json(server, "/api/tasks", {"goal": "survive restart"})["task"]["id"]
        _wait_for_task(server, task_id, {"accepted"})

    # New server instance over the same workspace sees the persisted store.
    with DesktopServer(workspace=tmp_path) as server:
        records = {t["id"]: t for t in _get_json(server, "/api/tasks")["tasks"]}
    assert records[task_id]["status"] == "accepted"
    assert records[task_id]["goal"] == "survive restart"


def test_desktop_task_queue_rejects_empty_goal(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        data = _post_json(server, "/api/tasks", {"goal": "   "})
        assert data["status"] == "failed"
        assert "goal" in data["error"]

        data = _post_json(server, "/api/tasks", {})
        assert data["status"] == "failed"
        assert "goal" in data["error"]


def test_desktop_app_html_contains_task_queue_panel():
    from local_coding_agent.desktop.ui import DESKTOP_HTML_TEMPLATE

    for element_id in (
        "taskQueueGoal",
        "taskQueueFiles",
        "taskQueueChecks",
        "taskQueueProfile",
        "btnSubmitTask",
        "taskQueueList",
    ):
        assert f'id="{element_id}"' in DESKTOP_HTML_TEMPLATE
    for hook in ("submitQueuedTask", "pollTasks", "applyQueuedTask", "cancelQueuedTask"):
        assert hook in DESKTOP_HTML_TEMPLATE


def test_delegation_mirrors_into_desktop_task_store(tmp_path, monkeypatch):
    """A DelegationService.delegate must surface in the desktop task panel."""
    import local_coding_agent.controller as controller_pkg
    from local_coding_agent.service import DelegationRequest, DelegationService
    from local_coding_agent.task import TaskEnvelope

    monkeypatch.setattr(controller_pkg.Controller, "run", lambda self, task, **kw: {
        "status": "candidate", "summary": "done", "patch": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-pass\n+return 1\n",
        "checks": [], "risks": [],
    })

    class _FakeClient:
        def chat(self, messages, **kwargs):
            return {"message": {"content": "ignored"}}

    service = DelegationService(
        {"workspace": tmp_path},
        model_factory=lambda profile: _FakeClient(),
    )
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    request = DelegationRequest(
        request_id="mirror-test-1",
        workspace_ref="workspace",
        model_profile="qwen2.5-coder",
        task=TaskEnvelope(id="mirror-test-1", goal="add a docstring", files=("a.py",), checks=("python -m pytest -q",)),
    )
    result = service.delegate("mcp-stdio", request)
    assert result["status"] in {"candidate", "rejected"}

    store = tmp_path / ".local_agent_tasks.json"
    assert store.exists()
    tasks = json.loads(store.read_text(encoding="utf-8"))
    record = next(t for t in tasks if t["id"] == "mirror-test-1")
    assert record["status"] == "accepted"
    assert record["goal"] == "add a docstring"
    assert record["files"] == ["a.py"]
    assert record["profile"] == "qwen2.5-coder"


def test_desktop_unload_all_stops_llama_server(monkeypatch):
    """Eject-all must stop a spawned llama-server, not only Ollama's models."""
    import local_coding_agent.desktop.server._handlers as h

    class _FakeClient:
        def loaded_models(self):
            return {"models": []}

        def unload_model(self, model=None):
            return {"models": []}

    class _FakeServerInst:
        def __init__(self):
            self.spawned_processes = {"llama_server": object()}
            self.llama_effective_ctx = 32256
            self.default_profile = "qwen2.5-coder"

    _FakeServerInst.llama_num_ctx = 8192

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    monkeypatch.setattr(h, "resolve_model_profile", lambda name: None)

    class _FakeHandler:
        server_inst = _FakeServerInst()

        def _send_json(self, payload):
            self.sent = payload

        def _read_json_body(self):
            return {}

        def _stop_backend(self, name):
            self.stopped = getattr(self, "stopped", [])
            self.stopped.append(name)
            self.server_inst.spawned_processes.pop(name, None)

    handler = _FakeHandler()
    DesktopRequestHandler._handle_model_unload_all(handler)
    assert handler.sent["status"] == "unloaded_all"
    assert handler.stopped == ["llama_server"]
    assert handler.server_inst.llama_effective_ctx is None


