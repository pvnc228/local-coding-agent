"""Named model profiles kept separate from controller logic."""

from __future__ import annotations

from dataclasses import replace

from .ollama_adapter import ModelProfile


_PROFILES = {
    "qwen2.5-1.5b": ModelProfile(
        name="qwen2.5-1.5b",
        model="qwen2.5:1.5b",
        think=False,
        temperature=0,
        num_ctx=4096,
        num_predict=256,
        keep_alive="10m",
        max_context_length=32_768,
    ),
    "qwen2.5-coder": ModelProfile(
        name="qwen2.5-coder",
        model="qwen2.5-coder:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=32_768,
    ),
    "ornith-9b": ModelProfile(
        name="ornith-9b",
        model="codex-ornith-9b:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "qwen3-coder-30b": ModelProfile(
        name="qwen3-coder-30b",
        model="codex-qwen3-coder-30b:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "devstral-small-2-24b": ModelProfile(
        name="devstral-small-2-24b",
        model="codex-devstral-small-2-24b:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=393_216,
    ),
    "qwen3-8b-q6k": ModelProfile(
        name="qwen3-8b-q6k",
        model="codex-qwen3-8b-q6k:latest",
        think=False,
        temperature=0.7,
        top_p=0.80,
        presence_penalty=1.5,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=32_768,
    ),
    "qwen3-coder-30b-iq2": ModelProfile(
        name="qwen3-coder-30b-iq2",
        model="codex-qwen3-coder-30b-ud-iq2:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "qwen3-coder-30b-q4": ModelProfile(
        name="qwen3-coder-30b-q4",
        model="codex-qwen3-coder-30b-ud-q4:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "qwen2.5-coder-14b-q6k": ModelProfile(
        name="qwen2.5-coder-14b-q6k",
        model="codex-qwen2.5-coder-14b-q6k:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=32_768,
    ),
    "nemotron-30b-mxfp4": ModelProfile(
        name="nemotron-30b-mxfp4",
        model="codex-nemotron-30b-mxfp4:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=131_072,
    ),
    "qwen3.8-27b-q4": ModelProfile(
        name="qwen3.8-27b-q4",
        model="codex-qwen3.8-27b-q4:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "qwen3.8-27b-q5": ModelProfile(
        name="qwen3.8-27b-q5",
        model="codex-qwen3.8-27b-q5:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "qwen3.8-27b-think": ModelProfile(
        name="qwen3.8-27b-think",
        model="codex-qwen3.8-27b-q4:latest",
        think=True,
        temperature=1.0,
        num_ctx=8192,
        num_predict=1024,
        thinking_num_predict=2048,
        keep_alive="10m",
        max_context_length=262_144,
    ),
    "qwen2.5-coder-7b-q4": ModelProfile(
        name="qwen2.5-coder-7b-q4",
        model="codex-qwen2.5-coder-7b-q4:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=32_768,
    ),
    "qwen2.5-coder-7b-q5": ModelProfile(
        name="qwen2.5-coder-7b-q5",
        model="local-qwen2.5-coder-7b-q5:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=32_768,
    ),
    "gemma4-e4b-q4": ModelProfile(
        name="gemma4-e4b-q4",
        model="codex-gemma4-e4b-q4:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=131_072,
    ),
    "gemma4-e4b-q5": ModelProfile(
        name="gemma4-e4b-q5",
        model="local-gemma4-e4b-q5:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=131_072,
    ),
    "gemma4-e2b-q4": ModelProfile(
        name="gemma4-e2b-q4",
        model="codex-gemma4-e2b-q4:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=131_072,
    ),
    "gemma4-e2b-q8": ModelProfile(
        name="gemma4-e2b-q8",
        model="local-gemma4-e2b-q8:latest",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=512,
        keep_alive="10m",
        max_context_length=131_072,
    ),
    "ling-3.0-tiny-q6k": ModelProfile(
        name="ling-3.0-tiny-q6k",
        model="ling-3.0-tiny-q6k",
        endpoint="http://127.0.0.1:8080",
        provider="openai",
        think=False,
        temperature=0,
        num_ctx=8192,
        num_predict=2048,
        keep_alive="10m",
        max_context_length=262_144,
        stop=("<|role_end|>", "<role>"),
        system_contract=(
            "You are a local coding sub-agent assigned to a single atomic task.\n"
            "Operate strictly within the provided task envelope.\n"
            "Do not invent missing context or assumptions.\n"
            "Do not claim to have run tests or modified files without tool evidence.\n"
            "Use only the provided tools.\n"
            "For propose_patch prefer SEARCH/REPLACE (edits with file+search+replace). In search copy old code byte-for-byte including leading indentation.\n"
            "Return only structured JSON upon completion: {\"status\":\"candidate\",\"summary\":\"...\",\"patch\":\"\",\"checks\":[],\"risks\":[]} or with \"edits\":[{\"file\":\"...\",\"search\":\"...\",\"replace\":\"...\"}]."
        ),
    ),
}



def list_profiles() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def get_profile(name: str, **overrides: object) -> ModelProfile:
    try:
        profile = _PROFILES[name]
    except KeyError as error:
        available = ", ".join(list_profiles())
        raise ValueError(f"unknown model profile {name!r}; available: {available}") from error
    return replace(profile, **overrides)
