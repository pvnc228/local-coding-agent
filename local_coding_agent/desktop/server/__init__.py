"""Desktop API and Webview HTTP Server for Local AI Coding Harness.

Provides real hardware GPU telemetry directly from nvidia-smi, multi-backend probing
(Ollama & llama-server), live model discovery, server process controls, model load/unload
VRAM management, real workspace file introspection, and mediated execution with auto-rollback.
"""

from __future__ import annotations

# Re-exported module references so tests that patch
# `local_coding_agent.desktop.server.os` / `.subprocess` keep working.
import os
import subprocess

from ._handlers import DesktopRequestHandler
from ._models import (
    _classify_backend_error,
    discover_local_ollama_models,
    find_discovered_gguf,
    profile_model_is_available,
    resolve_model_profile,
    select_available_profile,
)
from ._server import DesktopServer
from ._telemetry import get_nvidia_gpu_telemetry

__all__ = [
    "DesktopServer",
    "DesktopRequestHandler",
    "get_nvidia_gpu_telemetry",
    "discover_local_ollama_models",
    "find_discovered_gguf",
    "resolve_model_profile",
    "_classify_backend_error",
    "profile_model_is_available",
    "select_available_profile",
]
