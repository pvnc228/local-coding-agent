"""HTTP request handler serving the Desktop UI and REST endpoints."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.request
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...doctor import diagnose_environment
from ...memory import ModelMemoryManager
from ...model_scanner import discover_llama_server_binary, get_live_system_path
from ...ollama_adapter import OllamaError, build_client
from ...profiles import ModelProfile, get_profile, list_profiles
from ...task import TaskEnvelope
from ...validators import (
    _normalize_task_path,
    apply_patch,
    check_patch_applies,
    parse_unified_diff,
)
from ..ui import DESKTOP_HTML_TEMPLATE
from ._models import (
    _classify_backend_error,
    discover_local_ollama_models,
    find_discovered_gguf,
    resolve_model_profile,
    select_available_profile,
)
from ._telemetry import get_nvidia_gpu_telemetry

# Bounded recent-prompt history feeding the hybrid mode router's isolated
# context. Kept module-level (not on server state) so DesktopServer.__init__
# needs no change. Mutations are guarded by server_inst.apply_lock; a deque
# could replace the list if it ever needs to grow past a small bound.
_MAX_RECENT_PROMPTS = 6
_recent_prompts: list[str] = []


class DesktopRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving the Desktop UI and REST endpoints."""

    @property
    def server_inst(self) -> Any:
        return getattr(self.server, "desktop_server", self.server)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in {"", "/app", "/index.html"}:
            self._send_response(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                DESKTOP_HTML_TEMPLATE.encode("utf-8"),
            )
        elif path in {"/api/status", "/status"}:
            self._handle_status()
        elif path in {"/api/gpu", "/gpu", "/api/gpu/telemetry"}:
            self._handle_gpu_telemetry()
        elif path in {"/api/models", "/models", "/api/profiles"}:
            self._handle_models()
        elif path in {"/api/sessions", "/sessions"}:
            self._handle_sessions()
        elif path in {"/api/workspace/files", "/workspace/files"}:
            self._handle_workspace_files()
        elif path in {"/api/health", "/health"}:
            self._send_json({"status": "ok", "uptime": round(time.monotonic() - self.server_inst.started_at, 2)})
        else:
            self._send_response(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"404 Not Found\n")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in {"/api/chat", "/chat"}:
            self._handle_chat()
        elif path in {"/api/delegate", "/delegate"}:
            self._handle_delegate()
        elif path in {"/api/apply", "/apply"}:
            self._handle_apply()
        elif path in {"/api/rollback", "/rollback"}:
            self._handle_rollback()
        elif path in {"/api/server/start", "/server/start"}:
            self._handle_server_start()
        elif path in {"/api/server/stop", "/server/stop"}:
            self._handle_server_stop()
        elif path in {"/api/model/load", "/model/load"}:
            self._handle_model_load()
        elif path in {"/api/model/unload", "/model/unload"}:
            self._handle_model_unload()
        elif path in {"/api/model/unload_all", "/model/unload_all"}:
            self._handle_model_unload_all()
        elif path in {"/api/doctor/fix", "/doctor/fix"}:
            self._handle_doctor_fix()
        elif path in {"/api/sessions", "/sessions"}:
            self._handle_create_session()
        elif path in {"/api/models/scan", "/models/scan"}:
            self._handle_model_scan()
        elif path in {"/api/models/add_dir", "/models/add_dir"}:
            self._handle_model_add_dir()
        elif path in {"/api/models/remove_dir", "/models/remove_dir"}:
            self._handle_model_remove_dir()
        else:
            self._send_response(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"404 Not Found\n")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    def _handle_status(self) -> None:
        workspace = self.server_inst.workspace
        branch = "main"
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                branch = res.stdout.strip()
        except Exception:
            pass

        # Check server endpoints with loading / ready distinction
        ollama_online, ollama_status = self._probe_server_status("http://127.0.0.1:11434/api/tags")
        llama_online, llama_status = self._probe_server_status("http://127.0.0.1:8080/v1/models")

        # 1. First priority: Real Hardware readings from nvidia-smi
        gpu_telemetry = get_nvidia_gpu_telemetry()

        # 2. Fallback: Ollama memory manager if nvidia-smi is unavailable
        if gpu_telemetry:
            vram_info = gpu_telemetry
        else:
            vram_info = {"used_gb": 0.0, "total_gb": 16.0, "percent": 0.0, "gpu_name": "System GPU"}
            if ollama_online:
                try:
                    client = build_client(get_profile(self.server_inst.default_profile))
                    manager = ModelMemoryManager(client)
                    snap = manager.snapshot()
                    if snap.is_supported:
                        used_gb = round(snap.total_vram_bytes / (1024**3), 2)
                        vram_info = {
                            "used_gb": used_gb,
                            "total_gb": 16.0,
                            "percent": min(100.0, round((used_gb / 16.0) * 100, 1)),
                            "gpu_name": "Ollama VRAM Manager",
                            "loaded_models": [m.to_dict() for m in snap.models],
                        }
                except Exception:
                    pass

        payload = {
            "status": "healthy",
            "uptime_seconds": round(time.monotonic() - self.server_inst.started_at, 2),
            "workspace": str(Path(workspace).resolve()),
            "workspace_name": Path(workspace).resolve().name,
            "git_branch": branch,
            "profile": self.server_inst.default_profile,
            "servers": {
                "ollama": {"online": ollama_online, "status": ollama_status, "endpoint": "http://127.0.0.1:11434"},
                "llama_server": {"online": llama_online, "status": llama_status, "endpoint": "http://127.0.0.1:8080"},
            },
            "vram": vram_info,
            "stats": self.server_inst.stats.snapshot() if self.server_inst.stats else {},
        }
        self._send_json(payload)

    def _handle_gpu_telemetry(self) -> None:
        gpu = get_nvidia_gpu_telemetry()
        if gpu:
            self._send_json({"status": "ok", "gpu": gpu})
        else:
            self._send_json({"status": "unavailable", "message": "nvidia-smi telemetry not available on this host"})

    def _handle_models(self) -> None:
        profiles_data = []
        for name in list_profiles():
            prof = get_profile(name)
            profiles_data.append({
                "name": name,
                "model": prof.model,
                "provider": prof.provider,
                "endpoint": prof.endpoint,
                "num_ctx": prof.num_ctx,
            })

        ollama_online, _ = self._probe_server_status("http://127.0.0.1:11434/api/tags")
        ollama_models = discover_local_ollama_models()

        llama_online, _ = self._probe_server_status("http://127.0.0.1:8080/v1/models")
        llama_models: list[str] = []
        if llama_online:
            try:
                req = urllib.request.Request("http://127.0.0.1:8080/v1/models")
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    if resp.status == 200:
                        models_data = json.loads(resp.read().decode("utf-8"))
                        raw_list = models_data.get("data") or models_data.get("models") or []
                        for m in raw_list:
                            if isinstance(m, dict) and "id" in m:
                                llama_models.append(m["id"])
                            elif isinstance(m, dict) and "name" in m:
                                llama_models.append(m["name"])
            except Exception:
                pass

        from ...model_scanner import get_model_registry
        discovered_ggufs = [m.to_dict() for m in get_model_registry().get_models(auto_scan=True)]

        self._send_json({
            "profiles": profiles_data,
            "active_profile": self.server_inst.default_profile,
            "backends": {
                "ollama": {"online": ollama_online, "endpoint": "http://127.0.0.1:11434", "models": ollama_models},
                "llama_server": {"online": llama_online, "endpoint": "http://127.0.0.1:8080", "models": llama_models},
                "local_gguf": {"models": discovered_ggufs},
            },
        })

    def _handle_model_scan(self) -> None:
        from ...model_scanner import get_model_registry
        data = self._read_json_body()
        deep = bool(data.get("deep", False))
        registry = get_model_registry()
        models = registry.scan(deep=deep)
        self._send_json({"status": "ok", "total_models": len(models), "models": [m.to_dict() for m in models]})

    def _handle_model_add_dir(self) -> None:
        from ...model_scanner import get_model_registry
        data = self._read_json_body()
        path_val = data.get("path", "").strip()
        if not path_val or not Path(path_val).is_dir():
            self._send_json({"status": "failed", "error": f"Directory does not exist: {path_val}"})
            return
        added = get_model_registry().add_custom_directory(path_val)
        self._send_json({"status": "added" if added else "already_present", "path": path_val})

    def _handle_model_remove_dir(self) -> None:
        from ...model_scanner import get_model_registry
        data = self._read_json_body()
        path_val = data.get("path", "").strip()
        removed = get_model_registry().remove_custom_directory(path_val)
        self._send_json({"status": "removed" if removed else "not_found", "path": path_val})

    def _handle_workspace_files(self) -> None:
        workspace = Path(self.server_inst.workspace)
        files = []
        for p in workspace.rglob("*"):
            if p.is_file() and not any(part.startswith(".") or part in ("__pycache__", "venv", ".git", "build", "dist", "node_modules") for part in p.parts):
                rel = str(p.relative_to(workspace).as_posix())
                files.append({
                    "path": rel,
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "is_code": p.suffix in (".py", ".ts", ".js", ".go", ".rs", ".json", ".md"),
                })
        files.sort(key=lambda x: (not x["is_code"], x["path"]))
        self._send_json({"workspace": str(workspace), "files": files[:80]})

    def _handle_sessions(self) -> None:
        self._send_json({"sessions": self.server_inst.load_sessions()})

    def _handle_create_session(self) -> None:
        data = self._read_json_body()
        session_id = data.get("id") or f"sess-{int(time.time())}"
        session_type = data.get("type", "user")
        title = data.get("title", "New Task Session")
        file_path = data.get("file", "src/main.py")
        patch = data.get("patch", "")
        checks = data.get("checks", [])

        session = {
            "id": session_id,
            "type": session_type,
            "title": title,
            "file": file_path,
            "patch": patch,
            "checks": checks,
            "status": data.get("status", "Active"),
            "time": "Just now",
        }
        self.server_inst.save_session(session)
        self._send_json({"status": "created", "session": session})

    def _server_log_file(self, backend: str) -> Path:
        log_dir = Path(self.server_inst.workspace) / ".local_agent" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{backend}.log"

    def _read_log_tail(self, backend: str, n: int = 40) -> str:
        log_file = self._server_log_file(backend)
        if not log_file.exists():
            return ""
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            return "".join(lines[-n:])
        except Exception:
            return ""

    def _wait_for_ready(self, url: str, proc: subprocess.Popen, backend: str, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            online, status = self._probe_server_status(url)
            if online and status == "ready":
                return {"ok": True, "status": "started"}
            if proc.poll() is not None:
                return {
                    "ok": False,
                    "status": "failed",
                    "error": f"Server exited during startup (code {proc.returncode}). Log tail:\n{self._read_log_tail(backend)}",
                }
            time.sleep(0.5)

        if proc.poll() is not None:
            return {
                "ok": False,
                "status": "failed",
                "error": f"Server exited during startup (code {proc.returncode}). Log tail:\n{self._read_log_tail(backend)}",
            }
        return {
            "ok": False,
            "status": "loading",
            "message": "server started but not ready yet",
        }

    def _handle_server_start(self) -> None:
        data = self._read_json_body()
        backend = data.get("backend", "ollama")
        custom_path = data.get("custom_path")
        model_path = data.get("model_path")

        if backend == "ollama":
            ollama_bin = self._find_ollama_bin()
            if not ollama_bin:
                self._send_json({"status": "failed", "error": "Ollama executable not found. Please install Ollama from ollama.com"})
                return
            try:
                log_handle = open(self._server_log_file("ollama"), "ab")
                proc = subprocess.Popen(
                    [ollama_bin, "serve"],
                    stdout=log_handle,
                    stderr=log_handle,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                log_handle.close()
                self.server_inst.spawned_processes["ollama"] = proc
                result = self._wait_for_ready("http://127.0.0.1:11434/api/tags", proc, "ollama")
                if result.get("status") == "started":
                    self._send_json({"status": "started", "backend": "ollama", "pid": proc.pid})
                else:
                    self._send_json(result)
            except Exception as error:
                self._send_json({"status": "failed", "error": f"{error}\nLog tail:\n{self._read_log_tail('ollama')}"})

        elif backend in ("llama_server", "llama.cpp"):
            if self._probe_models_loaded("http://127.0.0.1:8080/v1/models"):
                self._send_json({
                    "status": "already_running",
                    "backend": "llama_server",
                    "endpoint": "http://127.0.0.1:8080",
                })
                return

            llama_bin = self._find_llama_server_bin(custom_path)
            if not llama_bin:
                self._send_json({
                    "status": "failed",
                    "error": (
                        "llama-server executable not found in system PATH. "
                        "Add your llama-server directory to PATH or set LLAMA_SERVER_PATH."
                    ),
                })
                return

            self._kill_llama_on_port(8080)
            gguf_path = self._find_gguf_model(model_path)
            cmd = [llama_bin, "--port", "8080"]
            if gguf_path:
                alias = Path(gguf_path).stem
                cmd.extend(["-m", gguf_path, "-c", str(self.server_inst.llama_num_ctx), "--alias", alias])
                self.server_inst.llama_gguf_path = gguf_path
                self.server_inst.llama_gguf_label = alias

            try:
                log_handle = open(self._server_log_file("llama_server"), "ab")
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_handle,
                    stderr=log_handle,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                log_handle.close()
                self.server_inst.spawned_processes["llama_server"] = proc
                if gguf_path:
                    result = self._wait_for_model_loaded(proc, "llama_server")
                else:
                    result = self._wait_for_ready("http://127.0.0.1:8080/v1/models", proc, "llama_server")
                if result.get("status") == "started":
                    self._send_json({
                        "status": "started",
                        "backend": "llama_server",
                        "pid": proc.pid,
                        "bin": llama_bin,
                        "model": gguf_path or "unspecified",
                    })
                else:
                    self._send_json(result)
            except Exception as error:
                self._send_json({"status": "failed", "error": f"{error}\nLog tail:\n{self._read_log_tail('llama_server')}"})
        else:
            self._send_json({"status": "failed", "error": f"Unknown backend: {backend}"})

    def _find_llama_server_bin(self, custom: str | None = None) -> str | None:
        return discover_llama_server_binary(custom)

    def _find_ollama_bin(self) -> str | None:
        live_path = get_live_system_path()
        ollama_bin = shutil.which("ollama", path=live_path) or shutil.which("ollama.exe", path=live_path)
        if not ollama_bin:
            appdata_ollama = Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"))
            if appdata_ollama.exists():
                ollama_bin = str(appdata_ollama)
        return ollama_bin

    def _ensure_ollama_running(self) -> bool:
        """Start ollama serve if offline; return True when the API is reachable."""
        online, _ = self._probe_server_status("http://127.0.0.1:11434/api/tags")
        if online:
            return True
        ollama_bin = self._find_ollama_bin()
        if not ollama_bin:
            return False
        try:
            log_handle = open(self._server_log_file("ollama"), "ab")
            proc = subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=log_handle,
                stderr=log_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log_handle.close()
            self.server_inst.spawned_processes["ollama"] = proc
        except Exception:
            return False
        result = self._wait_for_ready("http://127.0.0.1:11434/api/tags", proc, "ollama")
        return result.get("status") == "started"

    def _find_gguf_model(self, custom: str | None = None) -> str | None:
        if custom and custom.strip() and Path(custom.strip()).is_file():
            return str(Path(custom.strip()).resolve())

        env_model = os.environ.get("LLAMA_MODEL_PATH") or os.environ.get("GGUF_MODEL_PATH")
        if env_model and Path(env_model.strip()).is_file():
            return str(Path(env_model.strip()).resolve())

        return None

    def _handle_server_stop(self) -> None:
        data = self._read_json_body()
        backend = data.get("backend", "all")
        stopped = []

        for name in list(self.server_inst.spawned_processes):
            if backend in (name, "all"):
                self._stop_backend(name)
                stopped.append(name)

        self._send_json({"status": "stopped", "backends": stopped})

    def _handle_model_load(self) -> None:
        data = self._read_json_body()
        model_name = data.get("model") or self.server_inst.default_profile
        requested_ctx = data.get("num_ctx")
        if requested_ctx is not None:
            try:
                self.server_inst.llama_num_ctx = max(512, int(requested_ctx))
            except (TypeError, ValueError):
                pass
        try:
            gguf = find_discovered_gguf(model_name)
            if gguf and gguf.get("path"):
                result = self._launch_llama_model(gguf["path"], gguf.get("display_name") or gguf.get("name") or model_name)
                if result.get("status") == "started":
                    try:
                        warmup = ModelProfile(
                            name=gguf.get("display_name") or model_name,
                            model=gguf.get("display_name") or model_name,
                            provider="openai",
                            endpoint="http://127.0.0.1:8080",
                            num_ctx=self.server_inst.llama_num_ctx,
                        )
                        build_client(warmup).complete("warmup", system="warmup", max_tokens=1)
                    except Exception:
                        pass
                    self._send_json({
                        "status": "loaded",
                        "model": model_name,
                        "backend": "llama_server",
                        "path": gguf["path"],
                    })
                else:
                    self._send_json(result)
                return

            prof = resolve_model_profile(model_name)
            client = build_client(prof)
            if prof.provider == "ollama":
                if not self._ensure_ollama_running():
                    self._send_json({"status": "failed", "error": "Ollama (port 11434) could not be started. Start it manually or check PATH."})
                    return
                if hasattr(client, "_request_json"):
                    try:
                        client._request_json("POST", "/api/generate", {"model": prof.model, "prompt": "", "keep_alive": "10m"})
                    except OllamaError as oe:
                        if "not found" in str(oe).lower():
                            base = prof.model.split(":", 1)[0]
                            candidates = [prof.model, f"{base}:latest", base]
                            avail = set()
                            try:
                                for m in client.available_models().get("models", []):
                                    if isinstance(m, dict) and "name" in m:
                                        avail.add(m["name"])
                            except Exception:
                                avail = set(discover_local_ollama_models())
                            alt = next((c for c in candidates if c in avail), None)
                            if alt is None:
                                raise
                            client._request_json("POST", "/api/generate", {"model": alt, "prompt": "", "keep_alive": "10m"})
                        else:
                            raise
                else:
                    client.complete("warmup", system="warmup", max_tokens=1)
            elif prof.provider == "openai":
                if hasattr(client, "complete"):
                    client.complete("warmup", system="warmup", max_tokens=1)
                elif hasattr(client, "chat"):
                    client.chat([{"role": "user", "content": "warmup"}])
            else:
                if hasattr(client, "complete"):
                    client.complete("warmup", system="warmup", max_tokens=1)
            self._send_json({"status": "loaded", "model": model_name, "backend": prof.provider})
        except Exception as error:
            kind = _classify_backend_error(error)
            if kind == "offline":
                is_llama = prof.provider == "openai"
                backend_name = "llama-server (port 8080)" if is_llama else "Ollama (port 11434)"
                self._send_json({"status": "failed", "error": f"{backend_name} is OFFLINE. Start it in Local Inference Servers."})
            else:
                self._send_json({"status": "failed", "error": str(error)})

    def _launch_llama_model(self, gguf_path: str, model_label: str, num_ctx: int | None = None) -> dict:
        """Launch llama-server with a specific GGUF file (single-model server).

        Uses ``--alias`` so the model id exposed by ``/v1/models`` is the clean
        stem (e.g. ``Ling-3.0-tiny-Q6_K``) rather than the on-disk filename.
        ``-ngl`` is intentionally omitted: forcing ``-ngl 99`` aborts the load
        when free VRAM cannot fit every layer, whereas the default auto-fit
        spills only the overflow to CPU.
        """
        llama_bin = self._find_llama_server_bin(None)
        if not llama_bin:
            return {
                "status": "failed",
                "error": (
                    "llama-server executable not found in PATH. "
                    "Add your llama-server directory to PATH or set LLAMA_SERVER_PATH."
                ),
            }
        if num_ctx is not None:
            self.server_inst.llama_num_ctx = max(512, int(num_ctx))
        self._stop_backend("llama_server")
        self._kill_llama_on_port(8080)
        cmd = [
            llama_bin, "--port", "8080", "-m", gguf_path,
            "-c", str(self.server_inst.llama_num_ctx), "--alias", model_label,
        ]
        try:
            log_handle = open(self._server_log_file("llama_server"), "ab")
            proc = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=log_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log_handle.close()
            self.server_inst.spawned_processes["llama_server"] = proc
            self.server_inst.llama_gguf_path = gguf_path
            self.server_inst.llama_gguf_label = model_label
            result = self._wait_for_model_loaded(proc, "llama_server")
            if result.get("status") == "started":
                result.update({"backend": "llama_server", "pid": proc.pid, "model": model_label})
            return result
        except Exception as error:
            return {"status": "failed", "error": f"{error}\nLog tail:\n{self._read_log_tail('llama_server')}"}

    def _wait_for_model_loaded(self, proc: subprocess.Popen, backend: str, timeout: float = 90.0) -> dict:
        """Wait until llama-server actually exposes a loaded model in /v1/models."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._probe_models_loaded("http://127.0.0.1:8080/v1/models"):
                return {"ok": True, "status": "started"}
            if proc.poll() is not None:
                return {
                    "ok": False,
                    "status": "failed",
                    "error": f"Server exited during startup (code {proc.returncode}). Log tail:\n{self._read_log_tail(backend)}",
                }
            time.sleep(1.0)
        if proc.poll() is not None:
            return {
                "ok": False,
                "status": "failed",
                "error": f"Server exited during startup (code {proc.returncode}). Log tail:\n{self._read_log_tail(backend)}",
            }
        return {"ok": False, "status": "loading", "message": "server started but model not loaded yet"}

    def _probe_models_loaded(self, url: str) -> bool:
        """Return True only when the backend lists at least one loaded model."""
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False
        models = data.get("data") or data.get("models") or []
        return isinstance(models, list) and len(models) > 0

    def _find_pid_on_port(self, port: int) -> int | None:
        if os.name == "nt":
            try:
                out = subprocess.run(
                    ["netstat", "-ano", "-p", "tcp"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                ).stdout
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and f":{port}" in parts[1] and parts[3].upper() == "LISTENING":
                        try:
                            return int(parts[4])
                        except ValueError:
                            continue
            except Exception:
                pass
        else:
            try:
                out = subprocess.run(
                    ["lsof", "-ti", f":{port}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                ).stdout
                pid = out.strip().splitlines()[0] if out.strip() else ""
                if pid.isdigit():
                    return int(pid)
            except Exception:
                pass
        return None

    def _looks_like_llama(self, pid: int) -> bool:
        if os.name == "nt":
            try:
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                ).stdout
                return "llama" in out.lower()
            except Exception:
                return False
        return True

    def _kill_llama_on_port(self, port: int) -> None:
        pid = self._find_pid_on_port(port)
        if pid is None or pid == os.getpid() or not self._looks_like_llama(pid):
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                subprocess.run(["kill", "-9", str(pid)], capture_output=True, check=False)
        except Exception:
            pass

    def _stop_backend(self, name: str) -> None:
        proc = self.server_inst.spawned_processes.pop(name, None)
        if proc is None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                proc.terminate()
                proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _handle_model_unload(self) -> None:
        data = self._read_json_body()
        model_name = data.get("model")
        if not model_name:
            self._send_json({"status": "failed", "error": "model name required"})
            return
        try:
            client = build_client(resolve_model_profile(self.server_inst.default_profile))
            manager = ModelMemoryManager(client)
            snap = manager.unload_model(model_name)
            self._send_json({"status": "unloaded", "model": model_name, "remaining": [m.name for m in snap.models]})
        except Exception as error:
            self._send_json({"status": "failed", "error": str(error)})

    def _handle_model_unload_all(self) -> None:
        try:
            client = build_client(resolve_model_profile(self.server_inst.default_profile))
            manager = ModelMemoryManager(client)
            snap = manager.unload_all()
            self._send_json({"status": "unloaded_all", "remaining_bytes": snap.total_vram_bytes})
        except Exception as error:
            self._send_json({"status": "failed", "error": str(error)})

    def _apply_ctx_override(self, profile: ModelProfile, requested_ctx: Any) -> tuple[ModelProfile, str | None]:
        """Clamp and apply a per-request context-window override.

        Returns (profile, relaunch_error). For openai-provider (llama-server)
        profiles the context window is fixed at server launch (``-c``), so a
        changed value triggers a relaunch of the remembered GGUF; when the
        server was started externally the caller gets a prescriptive error.
        """
        if requested_ctx is None:
            return profile, None
        try:
            ctx = max(512, int(requested_ctx))
        except (TypeError, ValueError):
            return profile, None
        max_len = getattr(profile, "max_context_length", None)
        if max_len and ctx > max_len:
            ctx = int(max_len)
        profile = replace(profile, num_ctx=ctx)
        if profile.provider != "openai":
            return profile, None  # Ollama receives options.num_ctx per request
        if self.server_inst.llama_num_ctx == ctx:
            return profile, None
        gguf_path = self.server_inst.llama_gguf_path
        if not gguf_path:
            return profile, (
                f"llama-server is running with -c {self.server_inst.llama_num_ctx}. "
                f"To use a {ctx}-token context window, reload the model from the "
                "model panel (Load into VRAM) with the Context Override set."
            )
        label = self.server_inst.llama_gguf_label or Path(gguf_path).stem
        result = self._launch_llama_model(gguf_path, label, num_ctx=ctx)
        if result.get("status") != "started":
            return profile, (
                f"llama-server relaunch with -c {ctx} failed: {result.get('error', 'unknown error')}"
            )
        return profile, None

    def _handle_chat(self) -> None:
        data = self._read_json_body()
        prompt = str(data.get("prompt", "")).strip()
        profile_name = data.get("profile", self.server_inst.default_profile)
        if profile_name == self.server_inst.default_profile:
            profile_name = select_available_profile(profile_name)
        files = data.get("files") or []
        checks = data.get("checks") or []
        requested_ctx = data.get("num_ctx")

        if not prompt:
            self._send_json({"status": "failed", "error": "Prompt cannot be empty"})
            return

        from ...mode_router import MODES, classify_fast, classify_mode

        # Resolve the user-selected interaction mode. Invalid / missing falls
        # back to "hybrid" (auto-classify to chat/build/plan).
        raw_mode = data.get("mode")
        mode = raw_mode if raw_mode in MODES else "hybrid"
        if mode == "hybrid":
            # Keep a bounded recent-prompt history for the router's isolated context.
            with self.server_inst.apply_lock:
                _recent_prompts.append(prompt)
                del _recent_prompts[:-_MAX_RECENT_PROMPTS]
                recent_snapshot = list(_recent_prompts)
            router = None
            try:
                from ...mode_router import build_mode_router

                # Small default profile in the isolated context, never the main profile.
                router = build_mode_router()
            except Exception:
                router = None  # ponytail: deterministic fallback, never break chat
            mode = classify_mode(
                prompt,
                current_mode=None,
                router=router,
                n_every=3,
                counter=self.server_inst.hybrid_counter,
                recent_prompts=recent_snapshot,
            )
            with self.server_inst.apply_lock:
                self.server_inst.hybrid_counter += 1

        workspace = self.server_inst.workspace
        if not files:
            files = self._detect_relevant_files(workspace, prompt)
        if not checks:
            checks = self._detect_test_checks(workspace)

        task_id = f"task-{int(time.time())}"

        # Read-only planning for plan mode — never produces a patch, always
        # returns a PlanArtifact. Failures degrade to an empty plan, never raise.
        if mode == "plan":
            try:
                from ...plan_mode import PlanArtifact
                from ...controller import Controller
                profile = resolve_model_profile(profile_name)
                profile, ctx_error = self._apply_ctx_override(profile, requested_ctx)
                if ctx_error:
                    self._send_json({"status": "failed", "error": ctx_error})
                    return
                client = build_client(profile)
                task = TaskEnvelope(
                    id=task_id,
                    goal=prompt,
                    files=tuple(files),
                    checks=tuple(checks),
                )
                # Read-only enforcement at the tool-policy layer: block the two
                # tools that mutate the workspace so plan mode cannot patch.
                controller = Controller(
                    client, workspace,
                    blocked_tools={"propose_patch", "run_tests"},
                )
                result = controller.run(task, apply=False)

                summary = result.get("summary") or ""
                steps = [summary] if summary else []
                risks = result.get("risks") or []
                files_to_modify = list(files)
                plan = PlanArtifact(
                    goal=prompt,
                    steps=[str(s) for s in steps],
                    risks=[str(r) if isinstance(r, str) else str(r.get("message", "")) for r in risks],
                    files_to_modify=[str(f) for f in files_to_modify],
                )
                plan_dict = plan.to_dict()
                message = summary or f"Plan generated for '{prompt}'."
            except Exception as error:
                plan_dict = {
                    "goal": prompt,
                    "steps": [],
                    "risks": [],
                    "files_to_modify": list(files),
                }
                message = f"Could not generate a detailed plan: {error}"
            self._send_json({
                "status": "completed",
                "mode": "plan",
                "task_id": task_id,
                "prompt": prompt,
                "profile": profile_name,
                "plan": plan_dict,
                "file": "workspace",
                "patch": "",
                "thinking": "Plan mode: read-only exploration produced a plan.",
                "testResult": "READY",
                "checks": [],
                "message": message,
            })
            return

        # Friendly conversational / small-talk handling. The Controller tool-loop
        # demands structured JSON that small models can't reliably emit for
        # non-coding prompts, so route small talk to a plain completion.
        greetings = {"hi", "hello", "hey", "привет", "здравствуйте", "yo", "sup", "help", "test", "ok", "thanks", "спасибо"}
        greeting_phrases = ("how are you", "what's up", "whats up", "wassup", "how are u")
        lowered = prompt.lower().strip("!.,? ")
        tokens = lowered.split()
        is_small_talk = (
            lowered in greetings
            or any(phrase in lowered for phrase in greeting_phrases)
            or (len(tokens) <= 4 and any(tok.strip("!.,?") in greetings for tok in tokens))
        )
        if is_small_talk:
            try:
                profile = resolve_model_profile(profile_name)
                profile, ctx_error = self._apply_ctx_override(profile, requested_ctx)
                if ctx_error:
                    self._send_json({"status": "failed", "error": ctx_error})
                    return
                client = build_client(profile)
                resp = client.chat([
                    {"role": "system", "content": "You are a concise local coding assistant."},
                    {"role": "user", "content": prompt},
                ])
                reply = (resp.get("message") or {}).get("content")
            except Exception as error:
                self._send_offline_or_error(error, profile_name)
                return
            self._send_json({
                "status": "completed",
                "mode": mode,
                "task_id": f"greet-{int(time.time())}",
                "prompt": prompt,
                "profile": profile_name,
                "file": "workspace",
                "patch": "",
                "thinking": "Conversational intent detected.",
                "testResult": "READY",
                "checks": [],
                "message": reply or (
                    f"Hello! Connected to `{profile_name}`. "
                    "Please give me a specific coding task, bug fix, or refactoring goal "
                    "(e.g. 'Fix off-by-one in sliding window' or 'Write unit tests for tax logic')."
                ),
            })
            return

        # Informational / Code Inquiry Handling (e.g. "read main.py and tell me what it does",
        # "explain foo", "can u tell me what main.py does?"). The shared classifier detects
        # questions anywhere in the prompt, not just as prefixes; a question never needs a
        # patch, so it bypasses the Controller AND the blind chat completion — the model
        # must see the file it is asked about, whatever mode the user selected.
        if classify_fast(prompt) == "chat":
            try:
                profile = resolve_model_profile(profile_name)
                profile, ctx_error = self._apply_ctx_override(profile, requested_ctx)
                if ctx_error:
                    self._send_json({"status": "failed", "error": ctx_error})
                    return
                if profile.num_predict < 2048:
                    profile = replace(profile, num_predict=2048)
                client = build_client(profile)
                target_file = files[0] if files else "src/main.py"
                # Strict Scope Boundary (server-enforced, mirrors apply): normalize
                # and verify the resolved path stays inside the workspace before
                # reading. An absolute path or `../` escapes the workspace -> skip.
                # Up to 3 allowlisted files are injected: "what component does X"
                # usually spans more than one file and a single-file context makes
                # the model answer "I don't have access".
                workspace_root = Path(workspace).resolve()
                snippets: list[str] = []
                for candidate_file in (files[:3] or ["src/main.py"]):
                    try:
                        resolved = (workspace_root / candidate_file).resolve()
                        if resolved.is_relative_to(workspace_root) and resolved.is_file():
                            body = resolved.read_text(encoding="utf-8", errors="replace")[:6000]
                            snippets.append(f"--- {candidate_file} ---\n{body}")
                    except Exception:
                        continue
                content_snippet = "\n\n".join(snippets)

                messages = [
                    {"role": "system", "content": f"You are a helpful coding assistant answering a question about this workspace. Relevant files: {', '.join(files[:3]) or target_file}\n\n{content_snippet}"},
                    {"role": "user", "content": prompt},
                ]
                resp = client.chat(messages)
                msg_content = (resp.get("message") or {}).get("content") or "No response received."

                session_record = {
                    "id": task_id,
                    "type": "user",
                    "mode": mode,
                    "title": prompt[:50] + ("..." if len(prompt) > 50 else ""),
                    "file": target_file,
                    "patch": "",
                    "checks": checks,
                    "status": "Verified",
                    "time": "Just now",
                }
                self.server_inst.save_session(session_record)

                self._send_json({
                    "status": "completed",
                    "mode": mode,
                    "task_id": task_id,
                    "prompt": prompt,
                    "profile": profile_name,
                    "file": target_file,
                    "patch": "",
                    "thinking": f"Read context from {target_file} and formulated code explanation.",
                    "testResult": "READY",
                    "checks": [],
                    "message": msg_content,
                })
                return
            except Exception as error:
                self._send_offline_or_error(error, profile_name)
                return

        # Plain conversational completion for chat mode — never runs the
        # Controller. Reached only for chat-mode prompts that are neither
        # small talk nor questions (opinions, jokes, ...); questions above
        # already got the file-aware answer.
        if mode == "chat":
            try:
                profile = resolve_model_profile(profile_name)
                client = build_client(profile)
                resp = client.chat([
                    {"role": "system", "content": "You are a concise local coding assistant."},
                    {"role": "user", "content": prompt},
                ])
                reply = (resp.get("message") or {}).get("content")
            except Exception as error:
                self._send_offline_or_error(error, profile_name)
                return
            self._send_json({
                "status": "completed",
                "mode": "chat",
                "task_id": f"greet-{int(time.time())}",
                "prompt": prompt,
                "profile": profile_name,
                "file": "workspace",
                "patch": "",
                "thinking": "Conversational intent detected.",
                "testResult": "READY",
                "checks": [],
                "message": reply or (
                    f"Hello! Connected to `{profile_name}`. "
                    "Please give me a specific coding task, bug fix, or refactoring goal "
                    "(e.g. 'Fix off-by-one in sliding window' or 'Write unit tests for tax logic')."
                ),
            })
            return

        task = TaskEnvelope(
            id=task_id,
            goal=prompt,
            files=tuple(files),
            checks=tuple(checks),
        )

        try:
            from ...controller import Controller
            profile = resolve_model_profile(profile_name)
            profile, ctx_error = self._apply_ctx_override(profile, requested_ctx)
            if ctx_error:
                self._send_json({"status": "failed", "error": ctx_error})
                return
            client = build_client(profile)
            controller = Controller(client, workspace)
            result = controller.run(task, apply=False)

            patch_content = result.get("patch", "")
            target_file = files[0] if files else "src/main.py"

            summary = result.get("summary") or ""
            error = result.get("error")
            err_kind = error.get("kind") if isinstance(error, dict) else None
            if err_kind == "duplicate_tool_call":
                friendly = "The model got stuck repeating the same step. Try a simpler single-step task, or a larger model."
            elif err_kind == "context_overflow":
                friendly = summary
            elif err_kind == "retry_budget_exhausted":
                friendly = "The model couldn't produce a valid result after several attempts. Simplify the request or switch to a larger model."
            elif result.get("status") == "failed":
                friendly = f"The task could not be completed: {summary}"
            else:
                friendly = summary

            session_record = {
                "id": task_id,
                "type": "user",
                "mode": mode,
                "title": prompt[:50] + ("..." if len(prompt) > 50 else ""),
                "file": target_file,
                "patch": patch_content,
                "checks": checks,
                "status": "Verified" if result.get("status") == "accepted" else "Needs Review",
                "time": "Just now",
            }
            self.server_inst.save_session(session_record)

            self._send_json({
                "status": result.get("status", "completed"),
                "mode": mode,
                "task_id": task_id,
                "prompt": prompt,
                "profile": profile_name,
                "file": target_file,
                "files": list(files),
                "patch": patch_content,
                "thinking": friendly or "AST context compacted, generated candidate patch, ran external tests.",
                "testResult": "PASSED" if result.get("status") == "accepted" else "FAILED",
                "checks": result.get("checks", []),
                "message": friendly or f"Task processed for '{prompt}'.",
            })
        except Exception as error:
            kind = _classify_backend_error(error)
            if kind == "offline":
                is_llama = profile.provider == "openai"
                server_name = "llama-server on port 8080" if is_llama else "Ollama on port 11434"
                prescript = f"Local backend server ({server_name}) is currently OFFLINE. Click 'Start {('llama-server' if is_llama else 'Ollama')}' or launch your local engine."
                self._send_json({"status": "failed", "error": prescript, "offline_server": "llama_server" if is_llama else "ollama"})
            else:
                self._send_json({"status": "failed", "error": str(error)})

    def _handle_delegate(self) -> None:
        data = self._read_json_body()
        raw_task = data.get("task", {})
        profile_name = data.get("profile", self.server_inst.default_profile)
        apply_flag = bool(data.get("apply", False))

        try:
            from ...controller import Controller
            task = TaskEnvelope.from_mapping(raw_task)
            profile = resolve_model_profile(profile_name)
            client = build_client(profile)
            controller = Controller(client, self.server_inst.workspace)
            result = controller.run(task, apply=apply_flag)
            self._send_json(result)
        except Exception as error:
            self._send_json({"status": "failed", "error": str(error)})

    def _handle_apply(self) -> None:
        data = self._read_json_body()
        patch_str = data.get("patch", "")
        checks = data.get("checks", [])
        files = data.get("files", [])
        workspace = Path(self.server_inst.workspace)

        if not isinstance(patch_str, str) or not patch_str.strip():
            self._send_json({"status": "failed", "error": "No patch content provided to apply"})
            return
        if not isinstance(files, list):
            self._send_json({"status": "rejected", "error": "files must be a list"})
            return
        if not all(isinstance(f, str) for f in files):
            self._send_json({"status": "rejected", "error": "files entries must be strings"})
            return
        if not isinstance(checks, list):
            self._send_json({"status": "rejected", "error": "checks must be a list"})
            return

        # Strict Scope Boundary (server-enforced, mirrors validate_candidate):
        # the caller must declare the allowlist, and the patch must touch only
        # files within it. The boundary lives on the server, not the client.
        if not files:
            self._send_json({
                "status": "rejected",
                "error": "No declared file scope; refusing to apply a patch with an empty allowlist",
            })
            return
        changed, parse_issues = parse_unified_diff(patch_str)
        if parse_issues:
            self._send_json({"status": "rejected", "error": "; ".join(parse_issues)})
            return
        allowed = {_normalize_task_path(f) for f in files}
        out_of_scope = [p for p in changed if _normalize_task_path(p) not in allowed]
        if out_of_scope:
            self._send_json({
                "status": "rejected",
                "error": f"Patch touches files outside the declared scope: {', '.join(out_of_scope)}",
            })
            return

        applies, err = check_patch_applies(workspace, patch_str)
        if not applies:
            self._send_json({"status": "rejected", "error": f"Patch cannot apply cleanly: {err}"})
            return

        # Mediated apply under the lock: record only the files the patch actually
        # changes (not the declared allowlist), so a later rollback never wipes
        # unrelated work. Holding the lock across apply->checks->(reverse) closes
        # the race on last_applied_files.
        with self.server_inst.apply_lock:
            applied, detail = apply_patch(workspace, patch_str)
            if not applied:
                self._send_json({"status": "failed", "error": f"Apply failed: {detail}"})
                return

            self.server_inst.last_applied_files = list(changed)
            self.server_inst.last_applied_patch = patch_str

            check_results = []
            checks_passed = True
            for cmd in checks:
                if not isinstance(cmd, str) or not cmd.strip():
                    check_results.append({"command": cmd, "passed": False, "evidence": "invalid check command"})
                    checks_passed = False
                    break
                try:
                    # ponytail: split ourselves (shell=False) so client-supplied
                    # checks can't smuggle shell metacharacters. `posix` is False
                    # on Windows so backslash path separators survive splitting.
                    cp = subprocess.run(
                        shlex.split(cmd, posix=(os.name != "nt")),
                        cwd=workspace,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    passed = cp.returncode == 0
                    check_results.append({
                        "command": cmd,
                        "passed": passed,
                        "evidence": (cp.stdout + cp.stderr).strip()[:400],
                    })
                    if not passed:
                        checks_passed = False
                        break
                except Exception as e:
                    check_results.append({"command": cmd, "passed": False, "evidence": str(e)})
                    checks_passed = False
                    break

            if not checks_passed:
                reverted, revert_detail = apply_patch(workspace, patch_str, reverse=True)
                if reverted:
                    self.server_inst.last_applied_files = []
                    self.server_inst.last_applied_patch = ""
                self._send_json({
                    "status": "rejected",
                    "error": "Targeted checks failed after applying patch. Changes were automatically rolled back."
                    if reverted else f"Targeted checks failed; rollback also failed: {revert_detail}",
                    "checks": check_results,
                    "rolled_back": reverted,
                })
                return

            self._send_json({"status": "applied", "checks": check_results})

    def _handle_rollback(self) -> None:
        workspace = Path(self.server_inst.workspace)
        with self.server_inst.apply_lock:
            targets = list(self.server_inst.last_applied_files)
            patch = self.server_inst.last_applied_patch
            if not targets or not patch:
                self._send_json({"status": "rolled_back", "restored": []})
                return
            # Scoped rollback via reverse-apply of the stored patch: handles both
            # modified (tracked) and newly-created (untracked) files uniformly,
            # unlike `git restore` which cannot remove untracked files. Restores
            # only the files this apply touched — never unrelated work.
            reverted, detail = apply_patch(workspace, patch, reverse=True)
            if not reverted:
                self._send_json({
                    "status": "failed",
                    "error": f"Rollback failed: {detail}",
                    "restored": [],
                })
                return
            self.server_inst.last_applied_files = []
            self.server_inst.last_applied_patch = ""
            self._send_json({"status": "rolled_back", "restored": targets})

    def _handle_doctor_fix(self) -> None:
        report = diagnose_environment(fix=True)
        self._send_json({"status": "ok", "report": report})

    def _probe_port(self, url: str) -> bool:
        online, _ = self._probe_server_status(url)
        return online

    def _probe_server_status(self, url: str) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    return True, "ready"
                return True, "loading"
        except urllib.error.HTTPError as e:
            if e.code == 503:
                return True, "loading"
            return False, "offline"
        except Exception:
            return False, "offline"

    def _detect_relevant_files(self, workspace: str, prompt: str) -> list[str]:
        ws_path = Path(workspace)

        # 1. Files explicitly named in the prompt win over every heuristic:
        # "what does main.py do?" must not answer about an unrelated dirty file.
        lowered = prompt.lower()
        # ponytail: dot-token candidates let "__main__.py" match a "main.py"
        # mention via suffix; drop the stem tier if false positives appear.
        candidates = {
            tok.strip("./\\'\"(),:?!")
            for tok in re.findall(r"[\w./\\-]+\.\w{1,5}", lowered)
        } - {""}
        # Keyword scoring turns "desktop ui components" into desktop/ui.py +
        # desktop/components.py when no filename is mentioned outright.
        # ponytail: substring scoring, no stopwords — noise only widens the
        # allowlist; tighten if wrong-file answers ever come back.
        keywords = re.findall(r"[a-z_]{2,}", lowered)
        mentioned: list[str] = []
        scored: list[tuple[int, str]] = []
        try:
            # os.walk with pruned dirs: rglob("*") descends into .git and
            # friends, making every chat message pay a full-repo enumeration.
            for root, dirs, files_os in os.walk(ws_path):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in ("__pycache__", "venv", ".venv", "build", "dist", "node_modules")
                ]
                for fname in files_os:
                    p = Path(root) / fname
                    if p.suffix.lower() not in {
                        ".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".toml",
                        ".yaml", ".yml", ".rs", ".go", ".c", ".h", ".cpp", ".java",
                        ".rb", ".php", ".sh", ".ps1", ".sql", ".css", ".html",
                    }:
                        continue
                    rel = p.relative_to(ws_path).as_posix()
                    name = fname.lower()
                    if (
                        len(mentioned) < 3
                        and (
                            name in lowered
                            or any(name == cand or name.endswith(cand) or cand.endswith(name) for cand in candidates)
                        )
                    ):
                        mentioned.append(rel)
                        continue
                    if keywords:
                        stem = name.rsplit(".", 1)[0]
                        parent = Path(root).name.lower()
                        score = 0
                        for kw in keywords:
                            if len(kw) >= 4:
                                if kw in stem:
                                    score += 2
                                elif kw in rel.lower():
                                    score += 1
                            elif kw == stem or kw == parent:
                                score += 2
                        if score:
                            scored.append((score, rel))
        except Exception:
            pass
        if mentioned:
            return mentioned
        if scored:
            scored.sort(key=lambda t: (-t[0], t[1]))
            return [rel for _, rel in scored[:3]]

        # 2. Git-dirty files, 3. shallow glob fallbacks.
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                dirty = [line.strip().split()[-1] for line in res.stdout.strip().splitlines() if line.strip()]
                if dirty:
                    return dirty[:3]
        except Exception:
            pass

        for p in (ws_path / "src").glob("*.py"):
            return [str(p.relative_to(ws_path).as_posix())]
        for p in ws_path.glob("*.py"):
            if not p.name.startswith("test_"):
                return [p.name]
        # Fallback: any real Python file anywhere in the workspace (skip venvs/dirs)
        for p in ws_path.rglob("*.py"):
            if p.is_file() and not any(
                part.startswith(".") or part in ("__pycache__", "venv", ".venv", "build", "dist", "node_modules")
                for part in p.parts
            ):
                return [str(p.relative_to(ws_path).as_posix())]
        return ["src/main.py"]

    def _detect_test_checks(self, workspace: str) -> list[str]:
        ws_path = Path(workspace)
        if (ws_path / "tests").is_dir():
            return ["pytest tests/"]
        if (ws_path / "test").is_dir():
            return ["pytest test/"]
        return ["pytest"]

    def _send_json(self, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_response(HTTPStatus.OK, "application/json; charset=utf-8", body)

    def _send_offline_or_error(self, error: Exception, profile_name: Any) -> None:
        """Prescriptive failure for chat completions — never mask a dead
        backend behind the canned 'Connected' greeting."""
        try:
            profile = resolve_model_profile(profile_name)
        except Exception:
            profile = None
        if profile is not None and _classify_backend_error(error) == "offline":
            is_llama = profile.provider == "openai"
            server_name = "llama-server on port 8080" if is_llama else "Ollama on port 11434"
            self._send_json({
                "status": "failed",
                "error": (
                    f"Local backend server ({server_name}) is currently OFFLINE. "
                    f"Click 'Start {('llama-server' if is_llama else 'Ollama')}' or launch your local engine."
                ),
                "offline_server": "llama_server" if is_llama else "ollama",
            })
        else:
            self._send_json({"status": "failed", "error": str(error)})

    def _send_response(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args
