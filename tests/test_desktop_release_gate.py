"""Release-gate regressions for the installable desktop harness."""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from local_coding_agent.desktop.server import DesktopServer


def _post(
    server: DesktopServer,
    path: str,
    payload: dict,
    *,
    origin: str,
    content_type: str = "application/json",
    include_token: bool = True,
):
    headers = {"Content-Type": content_type, "Origin": origin}
    if include_token:
        headers["X-Desktop-Token"] = server.mutation_token
    request = urllib.request.Request(
        f"{server.url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=3.0)


def test_cross_origin_mutation_is_rejected_without_changing_workspace_state(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        try:
            _post(
                server,
                "/api/sessions",
                {"id": "csrf-created", "title": "hostile"},
                origin="https://evil.example",
                content_type="text/plain",
            )
        except urllib.error.HTTPError as error:
            assert error.code == 403
            body = json.loads(error.read().decode("utf-8"))
            assert body["status"] == "rejected"
        else:
            raise AssertionError("cross-origin state mutation was accepted")

        assert server.load_sessions() == []


def test_same_origin_json_mutation_remains_available(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        with _post(
            server,
            "/api/sessions",
            {"id": "same-origin", "title": "legitimate"},
            origin=server.url,
        ) as response:
            assert response.status == 200
            body = json.loads(response.read().decode("utf-8"))

        assert body["status"] == "created"
        assert [session["id"] for session in server.load_sessions()] == ["same-origin"]


def test_same_origin_mutation_without_process_token_is_rejected(tmp_path):
    with DesktopServer(workspace=tmp_path) as server:
        try:
            _post(
                server,
                "/api/sessions",
                {"id": "missing-token", "title": "must not persist"},
                origin=server.url,
                include_token=False,
            )
        except urllib.error.HTTPError as error:
            assert error.code == 403
            assert json.loads(error.read().decode("utf-8"))["status"] == "rejected"
        else:
            raise AssertionError("mutation without the per-process token was accepted")

        assert server.load_sessions() == []


def test_doctor_fix_endpoint_uses_the_remediation_contract(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as handlers

    calls = []

    class _Report:
        success = True

        def to_dict(self):
            return {"success": True, "actions": ["configured"], "recommendations": []}

    def fake_remediate(*, write):
        calls.append(write)
        return _Report()

    monkeypatch.setattr(handlers, "remediate_environment", fake_remediate, raising=False)
    with DesktopServer(workspace=tmp_path) as server:
        with _post(server, "/api/doctor/fix", {}, origin=server.url) as response:
            assert response.status == 200
            body = json.loads(response.read().decode("utf-8"))

    assert calls == [True]
    assert body == {
        "status": "ok",
        "report": {"success": True, "actions": ["configured"], "recommendations": []},
    }


def test_doctor_fix_endpoint_reports_partial_remediation_failure(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as handlers

    class _Report:
        success = False
        errors = ["antigravity: permission denied: .gemini"]

        def to_dict(self):
            return {
                "success": False,
                "actions": ["configured cursor"],
                "recommendations": [],
                "errors": self.errors,
            }

    monkeypatch.setattr(
        handlers,
        "remediate_environment",
        lambda *, write: _Report(),
        raising=False,
    )
    with DesktopServer(workspace=tmp_path) as server:
        try:
            _post(server, "/api/doctor/fix", {}, origin=server.url)
        except urllib.error.HTTPError as error:
            assert error.code == 500
            body = json.loads(error.read().decode("utf-8"))
        else:
            raise AssertionError("partial Doctor failure was reported as success")

    assert body["status"] == "failed"
    assert body["report"]["actions"] == ["configured cursor"]
    assert ".gemini" in body["error"]


def test_chat_api_rejects_context_below_minimum_before_model_call(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as handlers

    calls = []

    class _Client:
        def chat(self, messages):
            calls.append(messages)
            return {"message": {"content": "must not run"}}

    monkeypatch.setattr(handlers, "build_client", lambda profile: _Client())
    with DesktopServer(workspace=tmp_path) as server:
        try:
            _post(
                server,
                "/api/chat",
                {
                    "prompt": "hello",
                    "profile": "qwen2.5-coder",
                    "mode": "chat",
                    "num_ctx": 0,
                },
                origin=server.url,
            )
        except urllib.error.HTTPError as error:
            assert error.code == 400
            body = json.loads(error.read().decode("utf-8"))
        else:
            raise AssertionError("ctx=0 was accepted by the chat API")

    assert body["status"] == "failed"
    assert "at least 512" in body["error"]
    assert calls == []


def test_chat_history_persists_the_transcript_needed_for_restore(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as handlers

    class _Client:
        def chat(self, messages):
            return {"message": {"content": "VALUE is a module constant."}}

    monkeypatch.setattr(handlers, "build_client", lambda profile: _Client())
    (tmp_path / "example.py").write_text("VALUE = 1\n", encoding="utf-8")

    with DesktopServer(workspace=tmp_path) as server:
        with _post(
            server,
            "/api/chat",
            {
                "prompt": "what does example.py define?",
                "profile": "qwen2.5-coder",
                "mode": "chat",
                "files": ["example.py"],
            },
            origin=server.url,
        ) as response:
            assert response.status == 200
        session = server.load_sessions()[0]

    assert session["prompt"] == "what does example.py define?"
    assert session["profile"] == "qwen2.5-coder"
    assert session["message"] == "VALUE is a module constant."
    assert session["thinking"]


def test_plan_inference_never_allowlists_harness_metadata(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as handlers
    import local_coding_agent.controller as controller_module

    class _Controller:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, task, **kwargs):
            return {"status": "candidate", "summary": "inspect the change", "risks": []}

    monkeypatch.setattr(handlers, "build_client", lambda profile: object())
    monkeypatch.setattr(controller_module, "Controller", _Controller)

    source = tmp_path / "hello.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "hello.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.test", "-c", "user.name=test", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=True,
    )
    source.write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / ".local_agent_sessions.json").write_text("[]", encoding="utf-8")
    (tmp_path / ".local_agent_tasks.json").write_text("[]", encoding="utf-8")

    with DesktopServer(workspace=tmp_path) as server:
        with _post(
            server,
            "/api/chat",
            {"prompt": "prepare a plan for the current change", "mode": "plan"},
            origin=server.url,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))

    assert body["status"] == "completed"
    assert body["plan"]["files_to_modify"] == ["hello.py"]


def test_initial_desktop_state_never_claims_unearned_evidence():
    from local_coding_agent.desktop.components import render_chat_panel, render_delegated_panel

    html = render_chat_panel() + render_delegated_panel()

    assert "READY FOR APPLY" not in html
    assert "Evidence: Verified by Test Runner" not in html
    assert "Oracles: External Test Evidence" not in html
    assert "src/tax.py" not in html
    assert "• Connected" not in html
    assert "NO PROPOSAL" in html
    assert "Evidence: Not run" in html
    assert "Oracles: Not run" in html
    assert re.search(r'id="btnDelegatedApply"[^>]*\bdisabled\b', html)


def test_client_only_reports_success_from_proven_api_results():
    from local_coding_agent.desktop.client_js import DESKTOP_CLIENT_JS

    assert "All systems operational" not in DESKTOP_CLIENT_JS
    assert "Doctor request failed" in DESKTOP_CLIENT_JS
    assert "Rollback request failed" in DESKTOP_CLIENT_JS
    assert "Please enter a prompt" in DESKTOP_CLIENT_JS
    assert "data.status === 'ok' && data.report && data.report.success" in DESKTOP_CLIENT_JS
    assert "data.status === 'unloaded_all'" in DESKTOP_CLIENT_JS


def test_history_selection_restores_transcript_and_preserves_filter():
    from local_coding_agent.desktop.client_js import DESKTOP_CLIENT_JS

    assert "let activeSessionFilter = 'all';" in DESKTOP_CLIENT_JS
    assert "activeSessionFilter = type;" in DESKTOP_CLIENT_JS
    assert "renderSessions(activeSessionFilter);" in DESKTOP_CLIENT_JS
    assert "renderSessionTranscript(found);" in DESKTOP_CLIENT_JS
    assert "renderUserPrompt(session.prompt || session.title || '')" in DESKTOP_CLIENT_JS


def test_unavailable_profiles_cannot_be_selected_as_ready_models():
    from local_coding_agent.desktop.client_js import DESKTOP_CLIENT_JS

    assert "opt.disabled = true;" in DESKTOP_CLIENT_JS
    assert "const firstAvailable = [...select.options].find(o => !o.disabled);" in DESKTOP_CLIENT_JS
    assert "No local models available" in DESKTOP_CLIENT_JS
    assert "if (!activeProfile)" in DESKTOP_CLIENT_JS


def test_advertised_keyboard_shortcuts_have_a_real_handler():
    from local_coding_agent.desktop.client_js import DESKTOP_CLIENT_JS

    assert "document.addEventListener('keydown'" in DESKTOP_CLIENT_JS
    assert "event.key.toLowerCase() === 'b'" in DESKTOP_CLIENT_JS
    assert "event.key.toLowerCase() === 'n'" in DESKTOP_CLIENT_JS
    assert "event.key.toLowerCase() === 'a'" in DESKTOP_CLIENT_JS


def test_desktop_html_uses_only_packaged_frontend_assets():
    from local_coding_agent.desktop.ui import render_desktop_html

    html = render_desktop_html()

    assert "https://" not in html
    assert 'href="/assets/tailwind.css"' in html
    assert 'src="/assets/lucide.min.js"' in html


def test_desktop_server_serves_packaged_frontend_assets():
    with DesktopServer() as server:
        for path, expected_type, marker in (
            ("/assets/tailwind.css", "text/css", b"--tw-"),
            ("/assets/lucide.min.js", "javascript", b"lucide"),
        ):
            with urllib.request.urlopen(f"{server.url}{path}", timeout=3.0) as response:
                assert response.status == 200
                assert expected_type in response.headers["Content-Type"]
                assert marker in response.read()


def test_desktop_cli_exposes_headless_sidecar_mode():
    from local_coding_agent.cli import build_parser

    args = build_parser().parse_args(["desktop", "--headless", "--port", "0"])

    assert args.headless is True
    assert args.port == 0


def test_headless_desktop_prints_one_machine_readable_ready_record(monkeypatch, capsys, tmp_path):
    import local_coding_agent.desktop.app as desktop_app

    instances = []

    class _Server:
        url = "http://127.0.0.1:43123"

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.stopped = False
            instances.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(desktop_app, "DesktopServer", _Server)
    monkeypatch.setattr(desktop_app.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    code = desktop_app.launch_desktop_app(port=0, workspace=tmp_path, headless=True)

    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "status": "ready",
        "url": "http://127.0.0.1:43123/app",
        "workspace": str(Path(tmp_path).resolve()),
    }
    assert instances[0].stopped is True


def test_tauri_scaffold_owns_the_bundled_sidecar_lifecycle():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    cargo = (root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    rust = (root / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    assert config["build"]["frontendDist"] == "../desktop-shell"
    assert config["build"]["beforeBuildCommand"] == "npm run build"
    assert config["bundle"]["externalBin"] == ["binaries/local-agent-sidecar"]
    assert config["bundle"]["targets"] == ["nsis"]
    assert package["scripts"]["build"] == "npm run build:frontend && npm run build:sidecar"
    assert package["scripts"]["build:sidecar"] == "python tools/build_sidecar.py"
    assert 'tauri-plugin-shell = "2"' in cargo
    assert 'tauri-plugin-dialog = "2"' in cargo
    assert '.sidecar("local-agent-sidecar")' in rust
    assert '"--headless"' in rust
    assert '"--port"' in rust and '"0"' in rust
    assert "blocking_pick_folder" not in rust
    assert ".pick_folder(" in rust
    assert "CommandEvent::Stdout" in rust
    assert ".navigate(" in rust
    assert ".kill()" in rust
    assert "fn stop_sidecar_tree" in rust
    assert 'Command::new("taskkill")' in rust
    assert '["/PID", &pid, "/T", "/F"]' in rust
