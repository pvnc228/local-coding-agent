"""P1-3: num_ctx resource bounds and safe llama-server reload."""

import json
import types
import urllib.request

import pytest

from local_coding_agent.desktop.server import DesktopServer

from test_desktop_app import _mutation_headers


def _post(server, path, payload):
    req = urllib.request.Request(
        f"{server.url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_mutation_headers(server),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def test_ctx_override_has_hard_upper_bound():
    from local_coding_agent.desktop.server._handlers import DesktopRequestHandler

    ctx, error = DesktopRequestHandler._parse_ctx_override(10_000_000)
    assert ctx is None
    assert error and "too large" in error.lower()

    ctx, error = DesktopRequestHandler._parse_ctx_override(32768)
    assert ctx == 32768 and error is None


def test_model_load_rejects_ctx_above_hard_cap(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as h

    monkeypatch.setattr(h, "build_client", lambda profile: object())
    with DesktopServer(workspace=tmp_path) as server:
        try:
            _post(server, "/api/model/load", {"model": "qwen2.5-coder", "num_ctx": 10_000_000})
        except urllib.error.HTTPError as error:
            assert error.code == 400
            body = json.loads(error.read().decode("utf-8"))
        else:
            raise AssertionError("ctx above the hard cap was accepted")
    assert "too large" in body["error"]


def test_launch_preflight_clamps_ctx_to_vram_fit(monkeypatch, tmp_path):
    """Requested ctx beyond what free VRAM fits must be clamped before launch."""
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
    monkeypatch.setattr(h.DesktopRequestHandler, "_read_effective_ctx", lambda self, port=8080: 16384)

    # 1 GB weights, no free VRAM -> only the 512-token floor fits.
    monkeypatch.setattr(
        h,
        "read_gguf_ctx_params",
        lambda path: {"n_layers": 32, "n_head_kv": 8, "head_dim": 128, "native_context_length": 131072},
        raising=False,
    )
    # Free VRAM barely fits the 512-token floor: weights 1 GB + reserve leave
    # ~25 MB usable -> 512 tokens at 131072 B/token.
    monkeypatch.setattr(
        h,
        "get_nvidia_gpu_telemetry",
        lambda: {"total_mb": 8192, "used_mb": 8192 - 60},
        raising=False,
    )

    stub = object.__new__(h.DesktopRequestHandler)
    server_inst = types.SimpleNamespace(
        workspace=str(tmp_path),
        llama_num_ctx=32768,
        llama_gguf_path=None,
        llama_gguf_label=None,
        spawned_processes={},
    )
    stub.server = types.SimpleNamespace(desktop_server=server_inst)

    gguf_path = str(tmp_path / "m.gguf")
    result = h.DesktopRequestHandler._launch_llama_model(stub, gguf_path, "m", num_ctx=32768)

    assert result["status"] == "started"
    assert server_inst.llama_num_ctx == 512
    c_value = spawned_cmds[0][spawned_cmds[0].index("-c") + 1]
    assert c_value == "512"
    assert result.get("ctx_warning") and "32768" in result["ctx_warning"]


def test_launch_preflight_leaves_fitting_ctx_untouched(monkeypatch, tmp_path):
    import local_coding_agent.desktop.server._handlers as h

    monkeypatch.setattr(h.DesktopRequestHandler, "_find_llama_server_bin", lambda self, custom=None: "llama-server")
    monkeypatch.setattr(h.DesktopRequestHandler, "_stop_backend", lambda self, name: None)
    monkeypatch.setattr(h.DesktopRequestHandler, "_kill_llama_on_port", lambda self, port: None)
    fake_proc = types.SimpleNamespace(poll=lambda: None, pid=4322)
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
    monkeypatch.setattr(h.DesktopRequestHandler, "_read_effective_ctx", lambda self, port=8080: 8192)
    monkeypatch.setattr(
        h,
        "read_gguf_ctx_params",
        lambda path: {"n_layers": 32, "n_head_kv": 8, "head_dim": 128, "native_context_length": 131072},
        raising=False,
    )
    monkeypatch.setattr(h, "get_nvidia_gpu_telemetry", lambda: {"total_mb": 24576, "used_mb": 4096}, raising=False)

    stub = object.__new__(h.DesktopRequestHandler)
    server_inst = types.SimpleNamespace(
        workspace=str(tmp_path),
        llama_num_ctx=8192,
        llama_gguf_path=None,
        llama_gguf_label=None,
        spawned_processes={},
    )
    stub.server = types.SimpleNamespace(desktop_server=server_inst)

    result = h.DesktopRequestHandler._launch_llama_model(stub, str(tmp_path / "m.gguf"), "m", num_ctx=8192)

    assert result["status"] == "started"
    assert "ctx_warning" not in result
    c_value = spawned_cmds[0][spawned_cmds[0].index("-c") + 1]
    assert c_value == "8192"


def test_failed_relaunch_restores_previous_configuration(monkeypatch, tmp_path):
    """P1: risky reload must not leave the user without a working server.

    When the new-ctx relaunch fails, the previous configuration is relaunched.
    The restore logic lives inside _launch_llama_model, so patch the
    subprocess seam (not the whole method) to exercise it.
    """
    import local_coding_agent.desktop.server._handlers as h

    launches = []
    fake_proc = types.SimpleNamespace(poll=lambda: None, pid=4323)

    def fake_popen(cmd, **kwargs):
        launches.append(list(cmd))
        return fake_proc

    monkeypatch.setattr(h.DesktopRequestHandler, "_find_llama_server_bin", lambda self, custom=None: "llama-server")
    monkeypatch.setattr(h.DesktopRequestHandler, "_stop_backend", lambda self, name: None)
    monkeypatch.setattr(h.DesktopRequestHandler, "_kill_llama_on_port", lambda self, port: None)
    monkeypatch.setattr(h.subprocess, "Popen", fake_popen)
    # First wait (new-ctx relaunch) times out into "loading" -> failure;
    # second wait (restore) succeeds.
    wait_results = iter([
        {"ok": False, "status": "failed", "error": "Server exited during startup (code 1)"},
        {"ok": True, "status": "started"},
    ])
    monkeypatch.setattr(
        h.DesktopRequestHandler,
        "_wait_for_model_loaded",
        lambda self, proc, backend, timeout=90.0: next(wait_results),
    )
    monkeypatch.setattr(h.DesktopRequestHandler, "_read_effective_ctx", lambda self, port=8080: 4096)

    class _FakeClient:
        def chat(self, messages):
            return {"message": {"content": "answered"}}

    monkeypatch.setattr(h, "build_client", lambda profile: _FakeClient())
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "prev.gguf").write_bytes(b"GGUF" + b"\x00" * 512)

    with DesktopServer(workspace=tmp_path) as server:
        server.llama_gguf_path = str(tmp_path / "prev.gguf")
        server.llama_gguf_label = "prev"
        server.llama_num_ctx = 4096
        data = _post(server, "/api/chat", {
            "prompt": "what does calc.py do?",
            "profile": "ling-3.0-tiny-q6k",
            "mode": "chat",
            "num_ctx": 16384,
        })

    llama_launches = [cmd for cmd in launches if "llama-server" in cmd]
    assert len(llama_launches) == 2
    # The failed attempt with the new ctx...
    assert "-c" in llama_launches[0] and llama_launches[0][llama_launches[0].index("-c") + 1] == "16384"
    # ...then the restore relaunch with the previous configuration.
    assert llama_launches[1][llama_launches[1].index("-m") + 1] == str(tmp_path / "prev.gguf")
    assert llama_launches[1][llama_launches[1].index("-c") + 1] == "4096"
    assert server.llama_num_ctx == 4096
    assert data["status"] == "failed"
    assert "16384" in data["error"]
    assert "restored" in data["error"].lower()
