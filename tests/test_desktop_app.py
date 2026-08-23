"""Unit tests for Desktop AI Coding Harness (R23)."""

import json
import subprocess
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


def test_desktop_server_html_endpoint():
    with DesktopServer() as server:
        req = urllib.request.Request(f"{server.url}/app")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "Local AI Coding Harness" in content
            assert "Geist" in content
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
        headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert json.loads(resp.read().decode("utf-8"))["status"] == "applied"

        # b.py is dirty (uncommitted work the patch never touched)
        (tmp_path / "b.py").write_text("y = 999\n", encoding="utf-8")

        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert json.loads(resp.read().decode("utf-8"))["status"] == "applied"
        assert (tmp_path / "new.py").read_text(encoding="utf-8") == "created\n"

        req = urllib.request.Request(
            f"{server.url}/api/rollback",
            data=b"{}",
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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
