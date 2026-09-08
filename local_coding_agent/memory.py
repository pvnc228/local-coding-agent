"""Explicit Ollama loaded-model and VRAM management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class MemoryClient(Protocol):
    def loaded_models(self) -> dict[str, Any]: ...

    def unload_model(self, model: str | None = None) -> dict[str, Any]: ...


class MemoryBudgetError(RuntimeError):
    """The loaded models cannot fit inside the requested VRAM budget."""


class MemoryOperationError(RuntimeError):
    """A memory operation could not be verified against a supported backend."""


@dataclass(frozen=True)
class LoadedModel:
    name: str
    size_vram: int = 0
    size: int = 0
    expires_at: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "LoadedModel":
        name = value.get("name") or value.get("model")
        if not isinstance(name, str) or not name:
            raise ValueError("loaded model has no name")
        size_vram = value.get("size_vram", 0)
        size = value.get("size", 0)
        if not isinstance(size_vram, int) or size_vram < 0:
            raise ValueError(f"invalid size_vram for model {name}")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"invalid size for model {name}")
        expires_at = value.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, str):
            raise ValueError(f"invalid expires_at for model {name}")
        return cls(name=name, size_vram=size_vram, size=size, expires_at=expires_at)


@dataclass(frozen=True)
class MemorySnapshot:
    models: tuple[LoadedModel, ...]
    is_supported: bool = True

    @property
    def total_vram_bytes(self) -> int:
        return sum(model.size_vram for model in self.models)

    def as_dict(self) -> dict[str, Any]:
        return {
            "supported": self.is_supported,
            "total_vram_bytes": self.total_vram_bytes,
            "models": [
                {
                    "name": model.name,
                    "size_vram": model.size_vram,
                    "size": model.size,
                    "expires_at": model.expires_at,
                }
                for model in self.models
            ],
        }


class ModelMemoryManager:
    def __init__(self, client: MemoryClient) -> None:
        self.client = client

    def snapshot(self) -> MemorySnapshot:
        try:
            payload = self.client.loaded_models()
        except Exception:
            return MemorySnapshot(models=(), is_supported=False)
        if not isinstance(payload, dict) or "models" not in payload:
            return MemorySnapshot(models=(), is_supported=False)
        raw_models = payload["models"]
        if not isinstance(raw_models, list):
            return MemorySnapshot(models=(), is_supported=False)
        try:
            models = tuple(LoadedModel.from_mapping(model) for model in raw_models)
        except (TypeError, ValueError):
            return MemorySnapshot(models=(), is_supported=False)
        return MemorySnapshot(models=models)

    @staticmethod
    def _require_supported(snapshot: MemorySnapshot) -> MemorySnapshot:
        if not snapshot.is_supported:
            raise MemoryOperationError(
                "Ollama loaded-model memory state is unavailable; operation was not verified"
            )
        return snapshot

    def unload_model(self, model: str) -> MemorySnapshot:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        before = self._require_supported(self.snapshot())
        if not any(loaded.name == model for loaded in before.models):
            raise MemoryOperationError(f"loaded model is not present: {model}")
        self.client.unload_model(model)
        return self._require_supported(self.snapshot())

    def unload_all(self) -> MemorySnapshot:
        before = self._require_supported(self.snapshot())
        for model in before.models:
            self.client.unload_model(model.name)
        return self._require_supported(self.snapshot())

    def enforce_limit(self, max_vram_bytes: int, *, keep: tuple[str, ...] = ()) -> MemorySnapshot:
        if max_vram_bytes < 0:
            raise ValueError("max_vram_bytes must not be negative")
        protected = set(keep)
        snapshot = self._require_supported(self.snapshot())
        if snapshot.total_vram_bytes <= max_vram_bytes:
            return snapshot
        protected_vram = sum(model.size_vram for model in snapshot.models if model.name in protected)
        if protected_vram > max_vram_bytes:
            raise MemoryBudgetError(
                f"protected models use {protected_vram} bytes, above budget {max_vram_bytes}"
            )
        candidates = sorted(
            (model for model in snapshot.models if model.name not in protected),
            key=lambda model: (-model.size_vram, model.name),
        )
        for model in candidates:
            if snapshot.total_vram_bytes <= max_vram_bytes:
                break
            self.client.unload_model(model.name)
            snapshot = self._require_supported(self.snapshot())
        if snapshot.total_vram_bytes > max_vram_bytes:
            raise MemoryBudgetError(
                f"loaded models still use {snapshot.total_vram_bytes} bytes, above budget {max_vram_bytes}"
            )
        return snapshot
