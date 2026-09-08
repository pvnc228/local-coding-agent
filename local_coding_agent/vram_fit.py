"""GGUF header parsing and VRAM-aware context-window fitting.

The desktop harness uses these helpers to pick ``llama-server -c`` from the
model's own metadata instead of guessing 8192: parse the GGUF header for the
KV-cache geometry, then compute the largest context whose KV cache fits the
free VRAM left next to the weights.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ContextFit",
    "fit_context",
    "kv_bytes_per_token",
    "max_fitting_ctx",
    "read_gguf_ctx_params",
]

_CTX_ALIGN = 256
_DEFAULT_CAP = 32768
_MAX_KEY_BYTES = 1 << 20  # real GGUF keys are <100B; anything larger is corrupt
_CHUNK = 1 << 20

# GGUF container (all little-endian):
#   b"GGUF" | u32 version (2 or 3) | u64 tensor_count | u64 kv_count |
#   kv_count records of:  u64 key_len | key utf-8 | u32 value_type | value
#
# value_type codes:
#   0 u8, 1 i8, 2 u16, 3 i16, 4 u32, 5 i32, 6 f32, 7 bool(1B),
#   8 string (u64 len + bytes),
#   9 array (u32 elem_type + u64 count + elems),
#   10 u64, 11 i64, 12 f64
_SCALAR_FMTS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
    6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
_STRING_TYPE = 8
_ARRAY_TYPE = 9
_INT_TYPES = frozenset(_SCALAR_FMTS) - {6, 12}

_RESULT_KEYS = ("n_layers", "n_head_kv", "head_dim", "native_context_length")


@dataclass(frozen=True)
class ContextFit:
    """Evidence-backed result of a KV-cache context fit calculation.

    ``fits`` means that at least ``min_ctx`` tokens fit and ``context`` is the
    largest aligned context that fits.  ``does_not_fit`` means the available
    resources are known but cannot hold the requested minimum.  ``unknown``
    means the geometry or telemetry is not valid enough to make that claim.
    A numeric floor is deliberately never returned for the latter two states.
    """

    status: Literal["fits", "does_not_fit", "unknown"]
    context: int | None = None
    reason: str | None = None


def read_gguf_ctx_params(gguf_path: str | os.PathLike) -> dict[str, int | None]:
    """Header-only parse of a GGUF file; never raises.

    Returns dict with keys ``n_layers``, ``n_head_kv``, ``head_dim``,
    ``native_context_length``; each value is None when the key is absent or
    the file is missing/unreadable/corrupt.
    """
    try:
        return _parse_header(gguf_path)
    except Exception:
        return dict.fromkeys(_RESULT_KEYS)


def kv_bytes_per_token(n_layers: int, n_kv_heads: int, head_dim: int, bytes_per_elem: int = 2) -> int:
    """K+V cache bytes per token: 2 * n_layers * n_kv_heads * head_dim * bytes_per_elem."""
    return 2 * n_layers * n_kv_heads * head_dim * bytes_per_elem


def max_fitting_ctx(
    free_vram_bytes: int,
    weights_bytes: int,
    kv_per_token: int,
    *,
    min_ctx: int = 512,
    max_ctx: int | None = None,
    reserve_fraction: float = 0.15,
) -> int | None:
    """Compatibility wrapper returning a context only when the fit is proven.

    ``None`` means either that the minimum context cannot fit or that the
    inputs are insufficient to make a safe calculation.  Callers that need to
    distinguish those cases should use :func:`fit_context` directly.
    """
    result = fit_context(
        free_vram_bytes,
        weights_bytes,
        kv_per_token,
        min_ctx=min_ctx,
        max_ctx=max_ctx,
        reserve_fraction=reserve_fraction,
    )
    return result.context if result.status == "fits" else None


def fit_context(
    free_vram_bytes: int,
    weights_bytes: int,
    kv_per_token: int,
    *,
    min_ctx: int = 512,
    max_ctx: int | None = None,
    reserve_fraction: float = 0.15,
) -> ContextFit:
    """Calculate the largest context with an explicit evidence status.

    ``usable = free_vram_bytes * (1 - reserve_fraction) - weights_bytes``.
    A malformed KV geometry is ``unknown``; known insufficient capacity is
    ``does_not_fit``.  Contexts are rounded down to a 256-token boundary, but
    that alignment must not turn an under-sized result into a positive fit.
    """
    if not 0 <= reserve_fraction < 1:
        raise ValueError("reserve_fraction must satisfy 0 <= rf < 1")
    if min_ctx <= 0:
        raise ValueError("min_ctx must be positive")
    if kv_per_token <= 0:
        return ContextFit("unknown", reason="KV-cache geometry is unavailable or invalid")
    cap = _DEFAULT_CAP if max_ctx is None else max_ctx
    if cap <= 0:
        raise ValueError("max_ctx must be positive")
    if free_vram_bytes < 0 or weights_bytes < 0:
        return ContextFit("unknown", reason="VRAM or model weight size is invalid")
    if cap < min_ctx:
        return ContextFit("does_not_fit", reason="Context cap is below the minimum launch context")
    usable = free_vram_bytes * (1 - reserve_fraction) - weights_bytes
    if usable <= 0:
        return ContextFit("does_not_fit", reason="No VRAM remains for the KV cache")
    raw_ctx = min(int(usable) // kv_per_token, cap)
    if raw_ctx < min_ctx:
        return ContextFit("does_not_fit", reason="Available VRAM cannot hold the minimum context")
    ctx = raw_ctx - raw_ctx % _CTX_ALIGN
    return ContextFit("fits", max(ctx, min_ctx))


def _read_exact(fh, n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise ValueError("truncated gguf header")
    return data


def _skip(fh, n: int) -> None:
    while n > 0:
        chunk = fh.read(min(n, _CHUNK))
        if not chunk:
            raise ValueError("truncated gguf header")
        n -= len(chunk)


def _read_key(fh) -> str:
    klen = struct.unpack("<Q", _read_exact(fh, 8))[0]
    if klen > _MAX_KEY_BYTES:
        raise ValueError("implausible gguf key length")
    parts = []
    while klen > 0:
        chunk = fh.read(min(klen, _CHUNK))
        if not chunk:
            raise ValueError("truncated gguf header")
        parts.append(chunk)
        klen -= len(chunk)
    return b"".join(parts).decode("utf-8", errors="replace")


def _consume_value(fh, vtype: int) -> None:
    fmt = _SCALAR_FMTS.get(vtype)
    if fmt is not None:
        _read_exact(fh, struct.calcsize(fmt))
    elif vtype == _STRING_TYPE:
        slen = struct.unpack("<Q", _read_exact(fh, 8))[0]
        _skip(fh, slen)
    elif vtype == _ARRAY_TYPE:
        elem_type = struct.unpack("<I", _read_exact(fh, 4))[0]
        count = struct.unpack("<Q", _read_exact(fh, 8))[0]
        elem_fmt = _SCALAR_FMTS.get(elem_type)
        if elem_fmt is not None:
            _skip(fh, count * struct.calcsize(elem_fmt))
        else:
            for _ in range(count):
                _consume_value(fh, elem_type)
    else:
        raise ValueError(f"unknown gguf value type {vtype}")


def _maybe_int(fh, vtype: int) -> int | None:
    if vtype not in _INT_TYPES:
        _consume_value(fh, vtype)
        return None
    value = struct.unpack(_SCALAR_FMTS[vtype], _read_exact(fh, struct.calcsize(_SCALAR_FMTS[vtype])))[0]
    return value if isinstance(value, int) and value > 0 else None


def _parse_header(gguf_path: str | os.PathLike) -> dict[str, int | None]:
    out: dict[str, int | None] = dict.fromkeys(_RESULT_KEYS)
    emb_len: int | None = None
    head_count: int | None = None
    with open(gguf_path, "rb") as fh:
        if fh.read(4) != b"GGUF":
            raise ValueError("bad gguf magic")
        version = struct.unpack("<I", _read_exact(fh, 4))[0]
        if version not in (2, 3):
            raise ValueError(f"unsupported gguf version {version}")
        _read_exact(fh, 8)  # tensor_count, unused here
        kv_count = struct.unpack("<Q", _read_exact(fh, 8))[0]
        for _ in range(kv_count):
            key = _read_key(fh)
            vtype = struct.unpack("<I", _read_exact(fh, 4))[0]
            # arch prefix varies (llama., qwen2., phi3., ...); match by suffix
            if key.endswith(".context_length"):
                out["native_context_length"] = _maybe_int(fh, vtype)
            elif key.endswith(".block_count"):
                out["n_layers"] = _maybe_int(fh, vtype)
            elif key.endswith(".attention.head_count_kv"):
                out["n_head_kv"] = _maybe_int(fh, vtype)
            elif key.endswith(".attention.key_length"):
                out["head_dim"] = _maybe_int(fh, vtype)
            elif key.endswith(".embedding_length"):
                emb_len = _maybe_int(fh, vtype)
            elif key.endswith(".attention.head_count"):
                head_count = _maybe_int(fh, vtype)
            else:
                _consume_value(fh, vtype)
            # Parse every KV record.  GGUF metadata is not ordered: an explicit
            # attention.key_length may follow the values needed to derive a
            # provisional head dimension, and must still override that value.
    if out["head_dim"] is None and emb_len and head_count:
        out["head_dim"] = emb_len // head_count
    return out
