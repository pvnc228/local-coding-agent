"""Model discovery, profile resolution and availability checks for the Desktop Harness."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from ...model_scanner import get_model_registry
from ...ollama_adapter import OllamaError, OpenAICompatibleClient, classify_backend_error as _classify_backend_error
from ...profiles import ModelProfile, get_profile

# NOTE: `discover_local_ollama_models` and `profile_model_is_available` are
# imported at call time from the `server` package (re-exports) so that tests
# that patch `local_coding_agent.desktop.server.<name>` take effect.


def discover_local_ollama_models() -> list[str]:
    """Query live Ollama API and scan local disk manifests for installed models."""
    models: list[str] = []
    # 1. Probe live endpoint
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=0.3) as resp:
            if resp.status == 200:
                tags_data = json.loads(resp.read().decode("utf-8"))
                for m in tags_data.get("models", []):
                    if isinstance(m, dict) and "name" in m:
                        models.append(m["name"])
                if models:
                    return sorted(list(set(models)))
    except Exception:
        pass

    # 2. Disk manifests inspection in ~/.ollama/models/manifests
    manifest_root = Path.home() / ".ollama" / "models" / "manifests"
    if manifest_root.exists():
        try:
            for reg in manifest_root.iterdir():
                if reg.is_dir():
                    for user_or_lib in reg.iterdir():
                        if user_or_lib.is_dir():
                            for model_dir in user_or_lib.iterdir():
                                if model_dir.is_dir():
                                    for tag_file in model_dir.iterdir():
                                        if tag_file.is_file():
                                            prefix = "" if user_or_lib.name == "library" else f"{user_or_lib.name}/"
                                            models.append(f"{prefix}{model_dir.name}:{tag_file.name}")
        except Exception:
            pass

    return sorted(list(set(models)))


def resolve_model_profile(name: str, registry: Any = None) -> ModelProfile:
    """Resolve a profile by known profile name, installed Ollama tag, or discovered GGUF model."""
    if registry is None:
        registry = get_model_registry()
    clean_name = name.strip()

    # 1. Exact known profile (carries correct provider/endpoint)
    try:
        return get_profile(clean_name)
    except ValueError:
        pass

    # 2. Installed Ollama tag
    from ..server import discover_local_ollama_models
    ollama_models = discover_local_ollama_models()
    base = clean_name.split(":", 1)[0].split("/", 1)[0]
    if clean_name in ollama_models or base in ollama_models:
        matched = clean_name if clean_name in ollama_models else base
        return ModelProfile(
            name=clean_name,
            model=matched,
            provider="ollama",
            endpoint="http://127.0.0.1:11434",
            num_ctx=8192,
        )

    # 3. Discovered GGUF model
    for discovered in registry.get_models(auto_scan=True):
        if clean_name.lower() in (discovered.name.lower(), discovered.display_name.lower()):
            return ModelProfile(
                name=clean_name,
                model=discovered.display_name,
                provider="openai",
                endpoint="http://127.0.0.1:8080",
                num_ctx=8192,
            )

    # 4. Fallback to Ollama profile using name as model
    return ModelProfile(
        name=clean_name,
        model=clean_name,
        provider="ollama",
        endpoint="http://127.0.0.1:11434",
        num_ctx=8192,
    )


def profile_model_is_available(profile: ModelProfile) -> bool:
    """Return True if the profile's model is actually installed/available on a live backend."""
    from ..server import discover_local_ollama_models
    try:
        if profile.provider == "ollama":
            ollama_models = discover_local_ollama_models()
            target = profile.model
            if target in ollama_models:
                return True
            return target.split(":", 1)[0] in ollama_models
        if profile.provider == "openai":
            try:
                avail = OpenAICompatibleClient(profile).available_models()
                live_names = {m["name"] for m in avail.get("models", []) if isinstance(m, dict) and "name" in m}
                if profile.model in live_names:
                    return True
            except Exception:
                pass
            gguf_names = {m.display_name for m in get_model_registry().get_models(auto_scan=True)}
            gguf_names.update(m.name for m in get_model_registry().get_models(auto_scan=True))
            return profile.model in gguf_names
        return False
    except Exception:
        return False


def select_available_profile(preferred: str) -> str:
    """Return a profile name whose model is actually installed, falling back to discovered models."""
    from ..server import discover_local_ollama_models, profile_model_is_available
    try:
        if _is_known_profile(preferred) and profile_model_is_available(resolve_model_profile(preferred)):
            return preferred
    except Exception:
        pass
    try:
        ollama_models = discover_local_ollama_models()
        if ollama_models:
            return ollama_models[0]
    except Exception:
        pass
    try:
        discovered = get_model_registry().get_models(auto_scan=True)
        if discovered:
            return discovered[0].display_name
    except Exception:
        pass
    return preferred


def _is_known_profile(name: str) -> bool:
    try:
        get_profile(name)
        return True
    except ValueError:
        return False


def find_discovered_gguf(name: str, registry: Any = None) -> dict[str, Any] | None:
    """Return the registry entry (incl. on-disk path) for a discovered GGUF model."""
    if registry is None:
        registry = get_model_registry()
    clean = name.strip()
    for discovered in registry.get_models(auto_scan=True):
        if clean.lower() in (discovered.name.lower(), discovered.display_name.lower()):
            return discovered.to_dict()
    return None
