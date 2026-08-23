"""Small, transport-independent adapter for the Ollama HTTP API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterator, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """A normalized error returned by the Ollama adapter."""

    def __init__(self, message: str, *, kind: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


def classify_backend_error(error: Exception) -> str | None:
    """Classify a backend error by normalized kind instead of OS-specific text."""
    if isinstance(error, OllamaError):
        if error.kind == "transport":
            return "offline"
        if error.kind == "http":
            return "server_error"
    return None


BACKEND_OFFLINE_HINT = (
    "Local model backend appears to be offline. "
    "Start it with `ollama serve` (or launch llama-server for llama.cpp/OpenAI-compatible profiles), then retry."
)


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str
    endpoint: str = "http://127.0.0.1:11434"
    provider: str = "ollama"
    think: bool = False
    temperature: float = 0
    num_ctx: int = 4096
    num_predict: int = 256
    keep_alive: str = "10m"
    timeout_seconds: float = 30
    max_context_length: int | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    repeat_penalty: float | None = None
    seed: int | None = None
    stop: tuple[str, ...] | None = None
    system_contract: str | None = None
    stream_idle_timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.stream_idle_timeout_seconds <= 0:
            raise ValueError("stream_idle_timeout_seconds must be positive")
        if self.num_ctx <= 0:
            raise ValueError("num_ctx must be positive")
        if self.provider not in ("ollama", "openai"):
            raise ValueError(f"unsupported provider {self.provider!r}; expected 'ollama' or 'openai'")
        if self.max_context_length is not None:
            if self.max_context_length <= 0:
                raise ValueError("max_context_length must be positive")
            if self.num_ctx > self.max_context_length:
                raise ValueError(
                    f"num_ctx={self.num_ctx} exceeds model context limit {self.max_context_length}"
                )


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]: ...


class UrllibTransport:
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        request = Request(
            f"{self._endpoint}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
        except URLError as error:
            raise OllamaError(f"Ollama transport error: {error.reason}", kind="transport") from error
        except TimeoutError as error:
            raise OllamaError("Ollama request timed out", kind="timeout") from error

    def stream(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> Iterator[bytes]:
        request = Request(
            f"{self._endpoint}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise OllamaError(
                        f"Ollama HTTP {response.status}: {response.read().decode('utf-8', errors='replace')[:500]}",
                        kind="http",
                        status_code=response.status,
                    )
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    yield chunk
        except HTTPError as error:
            raise OllamaError(
                f"Ollama HTTP {error.code}: {error.read().decode('utf-8', errors='replace')[:500]}",
                kind="http",
                status_code=error.code,
            ) from error
        except URLError as error:
            raise OllamaError(f"Ollama transport error: {error.reason}", kind="transport") from error
        except TimeoutError as error:
            raise OllamaError("Ollama request timed out", kind="timeout") from error


class OllamaClient:
    def __init__(self, profile: ModelProfile, *, transport: Transport | None = None) -> None:
        self.profile = profile
        self._transport = transport or UrllibTransport(profile.endpoint)

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": self.profile.temperature,
            "num_ctx": self.profile.num_ctx,
            "num_predict": self.profile.num_predict,
        }
        if self.profile.top_p is not None:
            options["top_p"] = self.profile.top_p
        if self.profile.top_k is not None:
            options["top_k"] = self.profile.top_k
        if self.profile.min_p is not None:
            options["min_p"] = self.profile.min_p
        if self.profile.presence_penalty is not None:
            options["presence_penalty"] = self.profile.presence_penalty
        if self.profile.frequency_penalty is not None:
            options["frequency_penalty"] = self.profile.frequency_penalty
        if self.profile.repeat_penalty is not None:
            options["repeat_penalty"] = self.profile.repeat_penalty
        if self.profile.seed is not None:
            options["seed"] = self.profile.seed
        if self.profile.stop is not None:
            options["stop"] = list(self.profile.stop)

        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": messages,
            "stream": False,
            "think": self.profile.think,
            "keep_alive": self.profile.keep_alive,
            "options": options,
        }
        if tools is not None:
            payload["tools"] = tools
        if not hasattr(self._transport, "stream"):
            return self._request_json("POST", "/api/chat", payload)

        payload["stream"] = True
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        chunks = self._transport.stream(
            "POST",
            "/api/chat",
            body,
            headers,
            self.profile.stream_idle_timeout_seconds,
        )
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        stats: dict[str, Any] = {
            "prompt_eval_count": 0,
            "eval_count": 0,
            "prompt_eval_duration": 0,
            "eval_duration": 0,
            "total_duration": 0,
            "load_duration": 0,
        }
        idle = self.profile.stream_idle_timeout_seconds
        last = time.monotonic()
        done = False
        for chunk in chunks:
            now = time.monotonic()
            if now - last > idle:
                raise OllamaError("Ollama stream idle timeout", kind="timeout")
            last = now
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                message = obj.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        content_parts.append(content)
                    for call in message.get("tool_calls") or []:
                        if isinstance(call, dict):
                            idx = call.get("index")
                            if idx is None:
                                continue
                            idx = int(idx)
                            if idx in tool_calls:
                                self._merge_ollama_tool_call(tool_calls[idx], call)
                            else:
                                tool_calls[idx] = self._make_ollama_tool_call(call)
                for key in stats:
                    value = obj.get(key)
                    if value is not None:
                        stats[key] = value
                if obj.get("done"):
                    done = True
                    break
            if done:
                break
        if not done:
            raise OllamaError("Ollama stream closed before completion", kind="stream_closed")
        return {
            "message": {
                "role": "assistant",
                "content": "".join(content_parts),
                "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
            },
            **stats,
        }

    @staticmethod
    def _make_ollama_tool_call(call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        return {
            "index": call.get("index"),
            "id": call.get("id"),
            "type": call.get("type", "function"),
            "function": {"name": function.get("name", ""), "arguments": function.get("arguments", "")},
        }

    @staticmethod
    def _merge_ollama_tool_call(acc: dict[str, Any], call: dict[str, Any]) -> None:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name")
        if isinstance(name, str) and name:
            acc["function"]["name"] = name
        args = function.get("arguments")
        if isinstance(args, str) and args:
            acc["function"]["arguments"] += args


    def loaded_models(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/ps")

    def available_models(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/tags")

    def unload_model(self, model: str | None = None) -> dict[str, Any]:
        target = model or self.profile.model
        if not isinstance(target, str) or not target.strip():
            raise ValueError("model must be a non-empty string")
        return self._request_json(
            "POST",
            "/api/generate",
            {"model": target, "stream": False, "keep_alive": 0},
        )

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json; charset=utf-8"}
        status, raw_body = self._transport.request(
            method,
            path,
            body,
            headers,
            self.profile.timeout_seconds,
        )
        if status < 200 or status >= 300:
            detail = self._error_detail(raw_body)
            suffix = f": {detail}" if detail else ""
            raise OllamaError(f"Ollama HTTP {status}{suffix}", kind="http", status_code=status)
        try:
            decoded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OllamaError("Ollama returned invalid JSON", kind="invalid_json") from error
        if not isinstance(decoded, dict):
            raise OllamaError("Ollama returned a non-object JSON value", kind="invalid_json")
        return decoded

    @staticmethod
    def _error_detail(raw_body: bytes) -> str:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw_body.decode("utf-8", errors="replace").strip()[:500]
        detail = payload.get("error") if isinstance(payload, dict) else None
        return detail if isinstance(detail, str) else ""


class OpenAICompatibleClient:
    """Adapter for OpenAI-compatible backends (e.g. llama-server `/v1`).

    The controller speaks a neutral message/tool vocabulary (the same one
    ``OllamaClient`` produces). This adapter maps it onto the OpenAI chat
    completions wire format and normalizes the response back so callers see no
    difference. Tool calls arrive as JSON strings in the OpenAI payload and are
    parsed to objects for the controller.
    """

    def __init__(self, profile: ModelProfile, *, transport: Transport | None = None) -> None:
        self.profile = profile
        self._transport = transport or UrllibTransport(profile.endpoint)
        self._active_model_name: str | None = None

    def complete(self, prompt: str, *, system: str = "", max_tokens: int | None = None) -> dict[str, Any]:
        """Convenience completion method for compatibility with controller / warmup callers."""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages)

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        openai_messages: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            converted: dict[str, Any] = {"role": role, "content": content or ""}
            if "tool_calls" in message:
                converted["tool_calls"] = message["tool_calls"]
            if role == "tool":
                converted["tool_call_id"] = message.get("tool_call_id") or "call_0"
            if "name" in message:
                converted["name"] = message["name"]
            openai_messages.append(converted)

        model_name = self._active_model_name or self.profile.model
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": openai_messages,
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.num_predict,
            "chat_template_kwargs": {"thinking_option": "off", "enable_thinking": False},
        }
        if self.profile.top_p is not None:
            payload["top_p"] = self.profile.top_p
        if self.profile.seed is not None:
            payload["seed"] = self.profile.seed
        if self.profile.stop:
            payload["stop"] = list(self.profile.stop)
        if tools is not None:
            payload["tools"] = tools

        try:
            return self._chat_once(payload)
        except OllamaError as err:
            # Only retry when the backend rejected the model name (404, or 400 that mentions the model).
            # ponytail: single retry with a name-prefix heuristic; extend if multi-model resolution needed.
            if not _is_model_resolvable_error(err):
                raise
            avail = self.available_models()
            models_list = [m["name"] for m in avail.get("models", []) if isinstance(m, dict) and "name" in m]
            resolved = _resolve_model_id(models_list, self.profile.model)
            if not resolved:
                raise err
            self._active_model_name = resolved
            payload["model"] = self._active_model_name
            try:
                return self._chat_once(payload)
            except OllamaError:
                raise err from None

    def _chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(self._transport, "stream"):
            decoded = self._request_json("POST", "/v1/chat/completions", payload)
            choice = _first_choice(decoded)
            message = choice.get("message") if isinstance(choice, dict) else None
            content = (message or {}).get("content") or ""
            raw_calls = (message or {}).get("tool_calls") or []
            tool_calls = [_normalize_tool_call(call) for call in raw_calls if isinstance(call, dict)]

            usage = decoded.get("usage") if isinstance(decoded.get("usage"), dict) else {}
            timings = decoded.get("timings") if isinstance(decoded.get("timings"), dict) else {}
            prompt_ms = _as_float(timings.get("prompt_ms", 0))
            predicted_ms = _as_float(timings.get("predicted_ms", 0))
            return {
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                "prompt_eval_count": _as_int(usage.get("prompt_tokens", 0)),
                "eval_count": _as_int(usage.get("completion_tokens", 0)),
                "prompt_eval_duration": _as_nanos_ms(timings.get("prompt_ms", 0)),
                "eval_duration": _as_nanos_ms(timings.get("predicted_ms", 0)),
                "total_duration": _as_nanos_ms(prompt_ms + predicted_ms),
                "load_duration": 0,
            }

        payload = dict(payload)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        chunks = self._transport.stream(
            "POST",
            "/v1/chat/completions",
            body,
            headers,
            self.profile.stream_idle_timeout_seconds,
        )
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        done = False
        idle = self.profile.stream_idle_timeout_seconds
        last = time.monotonic()
        for chunk in chunks:
            now = time.monotonic()
            if now - last > idle:
                raise OllamaError("backend stream idle timeout", kind="timeout")
            last = now
            text = chunk.decode("utf-8", errors="replace")
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    continue
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if isinstance(obj.get("usage"), dict):
                    usage = obj["usage"]
                if isinstance(obj.get("timings"), dict):
                    timings = obj["timings"]
                choices = obj.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str) and content:
                    content_parts.append(content)
                for call in delta.get("tool_calls") or []:
                    if isinstance(call, dict):
                        idx = int(call.get("index", 0))
                        if idx in tool_calls:
                            self._merge_openai_tool_call(tool_calls[idx], call)
                        else:
                            tool_calls[idx] = self._make_openai_tool_call(call)
        if not done:
            raise OllamaError("backend stream closed before [DONE]", kind="stream_closed")
        prompt_ms = _as_float(timings.get("prompt_ms", 0))
        predicted_ms = _as_float(timings.get("predicted_ms", 0))
        return {
            "message": {
                "role": "assistant",
                "content": "".join(content_parts),
                "tool_calls": [_normalize_tool_call(tool_calls[i]) for i in sorted(tool_calls)],
            },
            "prompt_eval_count": _as_int(usage.get("prompt_tokens", 0)),
            "eval_count": _as_int(usage.get("completion_tokens", 0)),
            "prompt_eval_duration": _as_nanos_ms(timings.get("prompt_ms", 0)),
            "eval_duration": _as_nanos_ms(timings.get("predicted_ms", 0)),
            "total_duration": _as_nanos_ms(prompt_ms + predicted_ms),
            "load_duration": 0,
        }

    @staticmethod
    def _make_openai_tool_call(call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        return {
            "id": call.get("id") or f"call_{int(time.time() * 1000)}",
            "type": call.get("type", "function"),
            "function": {"name": function.get("name", ""), "arguments": function.get("arguments", "")},
        }

    @staticmethod
    def _merge_openai_tool_call(acc: dict[str, Any], call: dict[str, Any]) -> None:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name")
        if isinstance(name, str) and name:
            acc["function"]["name"] = name
        args = function.get("arguments")
        if isinstance(args, str) and args:
            acc["function"]["arguments"] += args

    def available_models(self) -> dict[str, Any]:
        decoded = self._request_json("GET", "/v1/models", None)
        data = decoded.get("data") if isinstance(decoded, dict) else None
        models = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append({"name": item["id"]})
        return {"models": models}

    def loaded_models(self) -> dict[str, Any]:
        raise OllamaError(
            "the openai provider does not expose loaded-model/VRAM introspection",
            kind="unsupported",
        )

    def unload_model(self, model: str | None = None) -> dict[str, Any]:
        raise OllamaError(
            "the openai provider does not support unloading models",
            kind="unsupported",
        )

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json; charset=utf-8"}
        status, raw_body = self._transport.request(
            method,
            path,
            body,
            headers,
            self.profile.timeout_seconds,
        )
        if status < 200 or status >= 300:
            detail = _openai_error_detail(raw_body)
            suffix = f": {detail}" if detail else ""
            raise OllamaError(f"backend HTTP {status}{suffix}", kind="http", status_code=status)
        try:
            decoded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OllamaError("backend returned invalid JSON", kind="invalid_json") from error
        if not isinstance(decoded, dict):
            raise OllamaError("backend returned a non-object JSON value", kind="invalid_json")
        return decoded


_MODEL_HINT_WORDS = ("model", "not found", "unknown", "does not exist", "no such")


def _is_model_resolvable_error(err: OllamaError) -> bool:
    """True when an http error plausibly means the model name was rejected."""
    if err.kind != "http":
        return False
    if err.status_code == 404:
        return True
    if err.status_code == 400:
        lower = str(err).lower()
        return any(word in lower for word in _MODEL_HINT_WORDS)
    return False


def _model_base(model_id: str) -> str:
    # strip a :tag, a /path, or a .gguf suffix — whichever comes first
    for sep in (":", "/", ".gguf"):
        idx = model_id.find(sep)
        if idx != -1:
            return model_id[:idx]
    return model_id


def _resolve_model_id(models: list[str], requested: str) -> str | None:
    if not models:
        return None
    if requested in models:
        return requested
    base = _model_base(requested)
    for model in models:
        if _model_base(model) == base:
            return model
    return models[0]


def _openai_error_detail(raw_body: bytes) -> str:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw_body.decode("utf-8", errors="replace").strip()[:500]
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return ""


def _first_choice(decoded: dict[str, Any]) -> dict[str, Any]:
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OllamaError("backend returned no choices", kind="invalid_json")
    first = choices[0]
    if not isinstance(first, dict):
        raise OllamaError("backend returned an invalid choice", kind="invalid_json")
    return first


def _normalize_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    if isinstance(function, dict) and isinstance(function.get("arguments"), str):
        try:
            function = dict(function)
            function["arguments"] = json.loads(function["arguments"])
        except json.JSONDecodeError:
            pass
    call_id = call.get("id") or f"call_{int(time.time() * 1000)}"
    return {**call, "id": call_id, "function": function}


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _as_nanos_ms(value: Any) -> int:
    try:
        return int(_as_float(value) * 1_000_000)
    except (TypeError, ValueError, OverflowError):
        return 0


def build_client(profile: ModelProfile, *, transport: Transport | None = None) -> OllamaClient | OpenAICompatibleClient:
    """Return the transport matching the profile's declared provider."""
    if profile.provider == "openai":
        return OpenAICompatibleClient(profile, transport=transport)
    return OllamaClient(profile, transport=transport)
