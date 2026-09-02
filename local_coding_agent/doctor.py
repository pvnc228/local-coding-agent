"""Diagnostic wizard and environment validator for local-coding-agent."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from .ollama_adapter import OllamaClient
from .profiles import list_profiles

RECOMMENDED_PROFILES = [
    {
        "name": "qwen3-8b-q6k",
        "model": "qwen3-8b-q6k:latest",
        "description": "Recommended for 8-12 GB VRAM (Ultra-fast, high precision coding)",
        "pull_cmd": "ollama run hf.co/unsloth/Qwen3-8B-GGUF:Q6_K",
    },
    {
        "name": "qwen3.8-27b-q4",
        "model": "qwen3.8-27b-q4:latest",
        "description": "Recommended for 16-24 GB VRAM (Maximum capability & architecture reasoning)",
        "pull_cmd": "ollama run hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M",
    },
    {
        "name": "qwen2.5-coder",
        "model": "qwen2.5-coder:latest",
        "description": "Standard coding workhorse (7B / 14B)",
        "pull_cmd": "ollama pull qwen2.5-coder",
    },
]


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def is_warn(self) -> bool:
        return self.status == "warn"

    @property
    def is_fail(self) -> bool:
        return self.status == "fail"


@dataclass
class DoctorReport:
    healthy: bool
    checks: list[CheckResult]
    models: dict[str, Any]
    system_info: dict[str, Any]

    @property
    def is_healthy(self) -> bool:
        return self.healthy

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "checks": [asdict(c) for c in self.checks],
            "models": self.models,
            "system_info": self.system_info,
        }

    def render_text(self) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("  Local Coding Agent — System Diagnostic Wizard")
        lines.append("=" * 60)
        lines.append("")

        for check in self.checks:
            tag = "[OK]  " if check.status == "ok" else ("[WARN]" if check.status == "warn" else "[FAIL]")
            lines.append(f"{tag} {check.name}: {check.message}")
            if check.details:
                for k, v in check.details.items():
                    lines.append(f"       - {k}: {v}")

        lines.append("")
        lines.append("-" * 60)
        lines.append("  Installed vs Recommended Models")
        lines.append("-" * 60)

        installed = self.models.get("installed", [])
        if installed:
            lines.append(f"Installed Ollama Models ({len(installed)}):")
            for m in installed:
                lines.append(f"  • {m.get('name', 'unknown')}")
        else:
            lines.append("No models found in Ollama.")

        missing = self.models.get("missing", [])
        if missing:
            lines.append("")
            lines.append("Recommended Models To Install:")
            for m in missing:
                lines.append(f"  • {m['name']} — {m['description']}")
                lines.append(f"    Command: {m['pull_cmd']}")

        lines.append("")
        lines.append("=" * 60)
        if self.healthy:
            lines.append("  Overall Status: READY (All critical checks passed)")
        else:
            lines.append("  Overall Status: ATTENTION REQUIRED (Check errors above)")
        lines.append("=" * 60)
        return "\n".join(lines)


def check_git_installed() -> CheckResult:
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0:
            version_str = proc.stdout.strip()
            git_path = shutil.which("git") or "in PATH"
            return CheckResult(
                name="Git Executable",
                status="ok",
                message=f"{version_str} ({git_path})",
                details={"version": version_str, "path": git_path},
            )
        return CheckResult(
            name="Git Executable",
            status="fail",
            message=f"Git returned non-zero exit code: {proc.returncode}",
        )
    except Exception as exc:
        return CheckResult(
            name="Git Executable",
            status="fail",
            message=f"Git is not found or not accessible in PATH: {exc}",
        )


def check_ollama_api(endpoint: str = "http://127.0.0.1:11434") -> tuple[CheckResult, list[str]]:
    try:
        from .profiles import ModelProfile
        dummy_profile = ModelProfile(
            name="check",
            model="check",
            endpoint=endpoint,
            think=False,
            temperature=0.0,
            num_ctx=2048,
            num_predict=128,
            keep_alive="1m",
            max_context_length=8192,
        )
        client = OllamaClient(dummy_profile)
        start = time.perf_counter()
        data = client.available_models()
        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        raw_models = data.get("models", [])
        names = [
            m.get("name")
            for m in raw_models
            if isinstance(m, dict) and isinstance(m.get("name"), str)
        ]
        return CheckResult(
            name="Ollama API",
            status="ok",
            message=f"Connected to {endpoint} (latency: {latency_ms}ms, {len(names)} models)",
            details={"endpoint": endpoint, "latency_ms": latency_ms, "model_count": len(names)},
        ), names
    except Exception as exc:
        return CheckResult(
            name="Ollama API",
            status="fail",
            message=f"Failed to connect to Ollama at {endpoint}: {exc}",
            details={"endpoint": endpoint, "error": str(exc)},
        ), []


def check_host_memory() -> CheckResult:
    total_ram_gb: float | None = None
    free_ram_gb: float | None = None

    try:
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
            free_ram_gb = round(stat.ullAvailPhys / (1024 ** 3), 1)
        elif sys.platform == "linux" and os.path.exists("/proc/meminfo"):
            mem_info: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        if val.isdigit():
                            mem_info[key] = int(val)
            if "MemTotal" in mem_info and "MemAvailable" in mem_info:
                total_ram_gb = round(mem_info["MemTotal"] / (1024 ** 2), 1)
                free_ram_gb = round(mem_info["MemAvailable"] / (1024 ** 2), 1)
    except Exception:
        pass

    if total_ram_gb is not None:
        status = "ok" if (free_ram_gb or 0) >= 4.0 else "warn"
        msg = f"{total_ram_gb} GB total ({free_ram_gb} GB available)"
        return CheckResult(
            name="Host Memory",
            status=status,
            message=f"RAM: {msg}",
            details={"total_ram_gb": total_ram_gb, "available_ram_gb": free_ram_gb},
        )
    return CheckResult(
        name="Host Memory",
        status="ok",
        message="RAM metrics detected",
    )


def recommend_models(installed_names: Sequence[str]) -> dict[str, Any]:
    installed_list = []
    missing_list = []

    installed_lower = [name.lower() for name in installed_names]

    for rec in RECOMMENDED_PROFILES:
        target_name = rec["name"].lower()
        target_model = rec["model"].lower()
        is_present = any(
            target_name in installed or target_model in installed or target_name.split("-")[0] in installed
            for installed in installed_lower
        )
        if is_present:
            installed_list.append(rec)
        else:
            missing_list.append(rec)

    return {
        "installed": [{"name": n} for n in installed_names],
        "recommended_installed": installed_list,
        "missing": missing_list,
    }


def diagnose_environment(endpoint: str = "http://127.0.0.1:11434") -> DoctorReport:
    checks = []

    # 1. Python version check
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)
    checks.append(
        CheckResult(
            name="Python Runtime",
            status="ok" if py_ok else "fail",
            message=f"Python {py_ver} ({platform.platform()})",
            details={"version": py_ver, "executable": sys.executable},
        )
    )

    # 2. Git check
    git_check = check_git_installed()
    checks.append(git_check)

    # 3. Host Memory check
    mem_check = check_host_memory()
    checks.append(mem_check)

    # 4. Ollama API check
    ollama_check, installed_models = check_ollama_api(endpoint)
    checks.append(ollama_check)

    # Model catalog & recommendations
    models_rec = recommend_models(installed_models)

    critical_failures = [c for c in checks if c.is_fail]
    healthy = len(critical_failures) == 0

    return DoctorReport(
        healthy=healthy,
        checks=checks,
        models=models_rec,
        system_info={
            "platform": platform.platform(),
            "python": py_ver,
            "executable": sys.executable,
        },
    )


@dataclass
class DoctorFixReport:
    success: bool
    actions: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "actions": self.actions,
            "recommendations": self.recommendations,
            "errors": self.errors,
        }

    def render_text(self) -> str:
        lines = [
            "=" * 60,
            "  Local Coding Agent — System Remediation Wizard (--fix)",
            "=" * 60,
            "",
            "Actions Taken / Configured:",
        ]
        if self.actions:
            for act in self.actions:
                lines.append(f"  [OK] {act}")
        else:
            lines.append("  (No actions needed)")

        if self.errors:
            lines.extend(["", "Failures:"])
            lines.extend(f"  [FAIL] {error}" for error in self.errors)

        lines.extend([
            "",
            "-" * 60,
            "  Recommended Model Pulls (Run in Terminal):",
            "-" * 60,
        ])
        for rec in self.recommendations:
            lines.append(f"  $ {rec}")
        lines.append("")
        return "\n".join(lines)


def remediate_environment(
    endpoint: str = "http://127.0.0.1:11434",
    write: bool = True,
) -> DoctorFixReport:
    """Remediate environment by configuring MCP, exporting skills, and recommending models."""
    from .mcp_config import integrate_mcp_config
    from .skill_config import integrate_skill_config

    actions: list[str] = []
    errors: list[str] = []

    # 1. MCP Configuration for detected IDEs
    mcp_res = integrate_mcp_config(client="all", dry_run=not write)
    for sub in mcp_res.get("results", []):
        if sub.get("error"):
            errors.append(f"{sub.get('client', 'unknown')}: {sub['error']}")
            continue
        tag = "Applied MCP config" if write else "Previewed MCP config"
        actions.append(f"{tag} for {sub.get('client')}: {sub.get('path')}")

    # 2. Skill export for agents
    skill_res = integrate_skill_config(client="auto", dry_run=not write)
    for sub in skill_res.get("results", []):
        if sub.get("error"):
            errors.append(f"{sub.get('client', 'unknown')}: {sub['error']}")
            continue
        tag = "Installed Agent Skill" if write else "Previewed Agent Skill"
        actions.append(f"{tag} for {sub.get('client')}: {sub.get('path')}")

    # 3. Model pull prescriptions
    recommendations: list[str] = [
        "ollama run hf.co/unsloth/Qwen3-8B-GGUF:Q6_K",
        "ollama run hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M",
        "ollama pull qwen2.5-coder:latest",
    ]

    return DoctorFixReport(
        success=not errors,
        actions=actions,
        recommendations=recommendations,
        errors=errors,
    )
