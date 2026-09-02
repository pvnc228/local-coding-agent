"""Universal model discovery, indexing, and persistent local registry."""

from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_CONFIG_DIR = Path.home() / ".local_coding_agent"
_REGISTRY_FILE = _CONFIG_DIR / "models.json"

# Common noise directories to skip during deep drive scans
_SKIP_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "winnt",
    "program files (x86)",
    "node_modules",
    ".git",
    ".pytest_cache",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    "site-packages",
    "dist",
    "build",
    "steamlibrary",
    "steamapps",
    "hearthstone",
    "overwatch",
    "epic games",
}

# Non-LLM substrings in GGUF names to ignore (image diffusion, projectors, audio)
_NON_LLM_INDICATORS = (
    "mmproj",
    "flux",
    "seedvr",
    "t5-v1",
    "clip",
    "vae",
    "unet",
    "diffusion",
    "stable-diffusion",
    "sdxl",
    "controlnet",
    "lora",
)


@dataclass
class DiscoveredModel:
    """Metadata for a discovered model on the local system."""

    name: str
    display_name: str
    path: str
    size_gb: float
    backend: str = "gguf"  # 'gguf' or 'ollama'
    source: str = "scanner"  # 'standard', 'custom', 'deep_scan', 'ollama'
    modified_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveredModel:
        return cls(
            name=data.get("name", ""),
            display_name=data.get("display_name", data.get("name", "")),
            path=data.get("path", ""),
            size_gb=float(data.get("size_gb", 0.0)),
            backend=data.get("backend", "gguf"),
            source=data.get("source", "scanner"),
            modified_at=float(data.get("modified_at", 0.0)),
        )


@dataclass
class ModelRegistryData:
    """Persistent storage schema for ~/.local_coding_agent/models.json."""

    custom_directories: list[str] = field(default_factory=list)
    discovered_models: list[dict[str, Any]] = field(default_factory=list)
    last_scanned_at: float = 0.0
    scan_stats: dict[str, Any] = field(default_factory=dict)


class LocalModelRegistry:
    """Manages discovery and local caching of local LLM models (GGUF / Ollama)."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = registry_file or _REGISTRY_FILE
        self._lock = threading.Lock()
        self._ensure_config_dir()

    def _ensure_config_dir(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ModelRegistryData:
        """Load registry from disk or return default data."""
        if not self.registry_file.exists():
            return ModelRegistryData()
        with self._lock:
            try:
                raw = self.registry_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    return ModelRegistryData(
                        custom_directories=data.get("custom_directories", []),
                        discovered_models=data.get("discovered_models", []),
                        last_scanned_at=data.get("last_scanned_at", 0.0),
                        scan_stats=data.get("scan_stats", {}),
                    )
            except (OSError, json.JSONDecodeError):
                pass
            return ModelRegistryData()

    def save(self, data: ModelRegistryData) -> None:
        """Save registry data to local machine JSON file atomically."""
        self._ensure_config_dir()
        with self._lock:
            try:
                payload = {
                    "custom_directories": data.custom_directories,
                    "discovered_models": data.discovered_models,
                    "last_scanned_at": data.last_scanned_at,
                    "scan_stats": data.scan_stats,
                }
                tmp_file = self.registry_file.with_suffix(".tmp")
                tmp_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp_file.replace(self.registry_file)
            except OSError:
                pass

    def add_custom_directory(self, path: str | Path) -> bool:
        """Add a custom directory to scan and persist it."""
        norm = str(Path(path).resolve())
        data = self.load()
        if norm not in data.custom_directories:
            data.custom_directories.append(norm)
            self.save(data)
            return True
        return False

    def remove_custom_directory(self, path: str | Path) -> bool:
        """Remove a custom directory from the scan list."""
        norm = str(Path(path).resolve())
        data = self.load()
        if norm in data.custom_directories:
            data.custom_directories.remove(norm)
            self.save(data)
            return True
        return False

    def list_custom_directories(self) -> list[str]:
        return self.load().custom_directories

    def get_models(self, auto_scan: bool = True) -> list[DiscoveredModel]:
        """Return discovered models. If empty and auto_scan=True, performs quick scan."""
        data = self.load()
        if not data.discovered_models and auto_scan:
            return self.scan(deep=False)
        return [DiscoveredModel.from_dict(m) for m in data.discovered_models]

    @staticmethod
    def get_system_drives() -> list[Path]:
        """Detect all available drive roots on Windows or root mount points on POSIX."""
        drives: list[Path] = []
        if os.name == "nt":
            # Check available Windows drive letters
            for letter in string.ascii_uppercase:
                drive_path = Path(f"{letter}:\\")
                try:
                    if drive_path.is_dir():
                        drives.append(drive_path)
                except OSError:
                    continue
        else:
            drives.append(Path("/"))
            # Check common mount roots
            for m in (Path("/media"), Path("/mnt"), Path("/Volumes")):
                if m.is_dir():
                    drives.append(m)
        return drives

    def get_standard_search_roots(self) -> list[Path]:
        """Get standard well-known directories where users typically store local models."""
        roots: list[Path] = []
        home = Path.home()

        # 1. LM Studio locations
        roots.append(home / ".cache" / "lm-studio" / "models")
        roots.append(home / ".lmstudio" / "models")

        # 2. Ollama & GPT4All locations
        roots.append(home / ".ollama" / "models")
        roots.append(home / ".local" / "share" / "nomic.ai" / "GPT4All")

        # 3. User home models directory
        roots.append(home / "models")
        roots.append(home / "LLM")
        roots.append(home / "AI")

        # 4. Standard per-drive candidate folders
        for d in self.get_system_drives():
            roots.append(d / "models")
            roots.append(d / "AI" / "models")
            roots.append(d / "LLM")
            roots.append(d / "llama.cpp" / "models")

        # 5. Environment variable overrides
        env_dirs = os.environ.get("LOCAL_GGUF_DIRS") or os.environ.get("LOCAL_MODELS_PATH") or os.environ.get("GGUF_MODEL_DIR")
        if env_dirs:
            for part in env_dirs.split(os.pathsep):
                if part.strip() and Path(part.strip()).is_dir():
                    roots.append(Path(part.strip()))

        available: list[Path] = []
        for root in roots:
            try:
                if root.is_dir():
                    available.append(root)
            except OSError:
                continue
        return available

    def scan(
        self,
        deep: bool = False,
        target_drives: list[str] | None = None,
        max_depth: int = 6,
    ) -> list[DiscoveredModel]:
        """Perform a scan for local GGUF models across standard roots and drives."""
        start_time = time.monotonic()
        data = self.load()

        search_dirs: list[tuple[Path, str]] = []

        # 1. Custom user directories (highest priority)
        for cd in data.custom_directories:
            p = Path(cd)
            try:
                if p.is_dir():
                    search_dirs.append((p, "custom"))
            except OSError:
                continue

        # 2. Standard known roots
        for sr in self.get_standard_search_roots():
            search_dirs.append((sr, "standard"))

        # 3. If deep scan requested, include drive roots
        if deep:
            drives = self.get_system_drives()
            if target_drives:
                allowed_letters = {td.upper().rstrip(":\\/") for td in target_drives}
                drives = [d for d in drives if d.drive.upper().rstrip(":") in allowed_letters or str(d) in allowed_letters]

            for d in drives:
                search_dirs.append((d, "deep_scan"))

        found_models: list[DiscoveredModel] = []
        seen_paths: set[str] = set()

        for base_dir, source in search_dirs:
            self._scan_directory(
                base_dir=base_dir,
                source=source,
                found_models=found_models,
                seen_paths=seen_paths,
                max_depth=max_depth if source != "deep_scan" else 4,
            )

        found_models.sort(key=lambda m: m.name.lower())

        # Update and save registry
        data.discovered_models = [m.to_dict() for m in found_models]
        data.last_scanned_at = time.time()
        data.scan_stats = {
            "elapsed_seconds": round(time.monotonic() - start_time, 2),
            "total_models": len(found_models),
            "scanned_roots_count": len(search_dirs),
            "deep": deep,
        }
        self.save(data)

        return found_models

    def _scan_directory(
        self,
        base_dir: Path,
        source: str,
        found_models: list[DiscoveredModel],
        seen_paths: set[str],
        max_depth: int = 5,
        current_depth: int = 0,
    ) -> None:
        if current_depth > max_depth:
            return

        try:
            if not base_dir.is_dir():
                return
            for entry in base_dir.iterdir():
                # Check for noise directory skipping
                if entry.is_dir():
                    dir_name_lower = entry.name.lower()
                    if dir_name_lower in _SKIP_DIR_NAMES or dir_name_lower.startswith("."):
                        continue
                    self._scan_directory(
                        base_dir=entry,
                        source=source,
                        found_models=found_models,
                        seen_paths=seen_paths,
                        max_depth=max_depth,
                        current_depth=current_depth + 1,
                    )
                elif entry.is_file() and entry.name.lower().endswith(".gguf"):
                    name_lower = entry.name.lower()
                    if any(ind in name_lower for ind in _NON_LLM_INDICATORS):
                        continue

                    norm_path = str(entry.resolve())
                    if norm_path in seen_paths:
                        continue
                    seen_paths.add(norm_path)

                    try:
                        stat = entry.stat()
                        size_gb = round(stat.st_size / (1024**3), 2)
                        # Filter out tiny test files < 200MB (unless custom directory)
                        if size_gb >= 0.2 or source == "custom":
                            found_models.append(
                                DiscoveredModel(
                                    name=entry.name,
                                    display_name=entry.stem,
                                    path=norm_path,
                                    size_gb=size_gb,
                                    backend="gguf",
                                    source=source,
                                    modified_at=stat.st_mtime,
                                )
                            )
                    except Exception:
                        pass
        except (PermissionError, OSError):
            pass


# Global singleton registry
_GLOBAL_REGISTRY: LocalModelRegistry | None = None
_GLOBAL_REGISTRY_LOCK = threading.Lock()


def get_model_registry() -> LocalModelRegistry:
    """Return singleton instance of LocalModelRegistry."""
    global _GLOBAL_REGISTRY
    with _GLOBAL_REGISTRY_LOCK:
        if _GLOBAL_REGISTRY is None or _GLOBAL_REGISTRY.registry_file != _REGISTRY_FILE:
            _GLOBAL_REGISTRY = LocalModelRegistry()
        return _GLOBAL_REGISTRY


def discover_all_gguf_models(deep: bool = False, force_rescan: bool = False) -> list[dict[str, Any]]:
    """Helper function to discover all GGUF models across local machine."""
    reg = get_model_registry()
    if force_rescan:
        models = reg.scan(deep=deep)
    else:
        models = reg.get_models(auto_scan=True)
        if not models and not force_rescan:
            models = reg.scan(deep=deep)
    return [m.to_dict() for m in models]


def get_live_system_path() -> str:
    """Read fresh Windows User and System PATH from Registry so dynamically added paths work immediately."""
    paths: list[str] = []
    if os.name == "nt":
        try:
            import winreg
            for root, subkey in [
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ]:
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        val, _ = winreg.QueryValueEx(key, "Path")
                        if val:
                            paths.extend(val.split(";"))
                except Exception:
                    pass
        except Exception:
            pass

    current_p = os.environ.get("PATH", "")
    paths.extend(current_p.split(os.pathsep))
    expanded = [os.path.expandvars(p.strip()) for p in paths if p.strip()]
    cleaned = [p for p in expanded if p and Path(p).exists()]
    return os.pathsep.join(list(dict.fromkeys(cleaned)))


def discover_llama_server_binary(custom_path: str | None = None) -> str | None:
    """Universally discover llama-server executable without any hardcoded drive letters or paths."""
    # 1. Custom explicit path if provided
    if custom_path and custom_path.strip():
        p = Path(custom_path.strip()).expanduser()
        if p.is_file():
            return str(p.resolve())

    # 2. Environment variables
    for env_var in ("LLAMA_SERVER_PATH", "LLAMA_CPP_PATH", "LLAMA_BIN", "LLAMACPP_SERVER_PATH"):
        env_val = os.environ.get(env_var)
        if env_val and env_val.strip():
            p = Path(env_val.strip()).expanduser()
            if p.is_file():
                return str(p.resolve())
            if p.is_dir():
                for name in ("llama-server.exe", "llama-server", "server.exe", "server"):
                    cand = p / name
                    if cand.is_file():
                        return str(cand.resolve())

    # 3. Live system PATH across the machine
    live_path = get_live_system_path()
    for name in ("llama-server", "llama-server.exe", "server", "server.exe"):
        found = shutil.which(name, path=live_path)
        if found:
            return str(Path(found).resolve())

    # 4. Universal relative subpaths across all dynamically detected system drives and user home
    exe_names = ("llama-server.exe", "llama-server", "server.exe", "server") if os.name == "nt" else ("llama-server", "server")

    subpaths = [
        Path("AI") / "llama-server",
        Path("AI") / "llama.cpp",
        Path("llama-server"),
        Path("llama.cpp"),
        Path("tools") / "llama-server",
        Path("tools") / "llama.cpp",
        Path("bin"),
        Path("Program Files") / "llama.cpp",
        Path("Program Files") / "llama-server",
        Path(".docker") / "bin" / "inference",
        Path(".local") / "bin",
        Path("AppData") / "Local" / "Programs" / "llama-server",
        Path("AppData") / "Local" / "Programs" / "llama.cpp",
    ]

    # Search user home directory standard paths safely
    try:
        home = Path.home()
    except Exception:
        home = None

    if home:
        for sp in subpaths:
            for name in exe_names:
                cand = home / sp / name
                if cand.is_file():
                    return str(cand.resolve())

    # Search across all dynamically detected drives (C:\, D:\, E:\, etc. on Windows, or / on POSIX)
    for drive in LocalModelRegistry.get_system_drives():
        for sp in subpaths:
            for name in exe_names:
                cand = drive / sp / name
                if cand.is_file():
                    return str(cand.resolve())

    # POSIX standard directories
    if os.name != "nt":
        for posix_p in (
            Path("/usr/local/bin/llama-server"),
            Path("/opt/homebrew/bin/llama-server"),
            Path("/usr/bin/llama-server"),
            Path("/opt/llama.cpp/llama-server"),
        ):
            if posix_p.is_file():
                return str(posix_p.resolve())

    return None
