"""Tests for local_coding_agent.vram_fit using synthetic GGUF byte fixtures."""

from __future__ import annotations

import struct

import pytest

from local_coding_agent.vram_fit import (
    kv_bytes_per_token,
    max_fitting_ctx,
    read_gguf_ctx_params,
)

ALL_NONE = {
    "n_layers": None,
    "n_head_kv": None,
    "head_dim": None,
    "native_context_length": None,
}
EXPECTED = {
    "n_layers": 22,
    "n_head_kv": 8,
    "head_dim": 64,
    "native_context_length": 32768,
}
GIB = 1024**3


def _key_blob(key: str) -> bytes:
    kb = key.encode("ascii")
    return struct.pack("<Q", len(kb)) + kb


def _kv_u32(key: str, value: int) -> bytes:
    return _key_blob(key) + struct.pack("<II", 4, value)


def _kv_i32(key: str, value: int) -> bytes:
    return _key_blob(key) + struct.pack("<Ii", 5, value)


def _kv_str(key: str, text: str) -> bytes:
    sb = text.encode("ascii")
    return _key_blob(key) + struct.pack("<I", 8) + struct.pack("<Q", len(sb)) + sb


def _kv_u32_array(key: str, values: list[int]) -> bytes:
    body = b"".join(struct.pack("<I", v) for v in values)
    return _key_blob(key) + struct.pack("<IIQ", 9, 4, len(values)) + body


def _gguf(*records: bytes, version: int = 3, magic: bytes = b"GGUF",
          kv_count: int | None = None, tail: bytes = b"") -> bytes:
    n = len(records) if kv_count is None else kv_count
    return magic + struct.pack("<IQQ", version, 0, n) + b"".join(records) + tail


def _full_gguf(version: int = 3, magic: bytes = b"GGUF", tail: bytes = b"\xde\xad\xbe\xef" * 256) -> bytes:
    return _gguf(
        _kv_u32_array("test.dummy_array", [1, 2, 3]),
        _kv_str("llama.general.architecture", "llama"),
        _kv_u32("llama.block_count", 22),
        _kv_u32("llama.context_length", 32768),
        _kv_u32("llama.attention.head_count_kv", 8),
        _kv_u32("llama.embedding_length", 2048),
        _kv_u32("llama.attention.head_count", 32),
        version=version,
        magic=magic,
        tail=tail,
    )


def _write(tmp_path, data: bytes, name: str = "model.gguf") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


# --- read_gguf_ctx_params -------------------------------------------------


def test_happy_path(tmp_path):
    assert read_gguf_ctx_params(_write(tmp_path, _full_gguf())) == EXPECTED


@pytest.mark.parametrize("version", [2, 3])
def test_versions_accepted(tmp_path, version):
    assert read_gguf_ctx_params(_write(tmp_path, _full_gguf(version=version))) == EXPECTED


def test_version_99_rejected(tmp_path):
    assert read_gguf_ctx_params(_write(tmp_path, _full_gguf(version=99))) == ALL_NONE


def test_corrupt_magic(tmp_path):
    assert read_gguf_ctx_params(_write(tmp_path, _full_gguf(magic=b"XXXX"))) == ALL_NONE


def test_truncated_mid_kv(tmp_path):
    data = _full_gguf(tail=b"")
    assert read_gguf_ctx_params(_write(tmp_path, data[: len(data) // 2])) == ALL_NONE


def test_missing_file(tmp_path):
    assert read_gguf_ctx_params(str(tmp_path / "absent.gguf")) == ALL_NONE


def test_empty_file(tmp_path):
    assert read_gguf_ctx_params(_write(tmp_path, b"")) == ALL_NONE


def test_garbage_tail_ignored(tmp_path):
    data = _gguf(
        _kv_u32("llama.block_count", 22),
        _kv_u32("llama.context_length", 4096),
        _kv_u32("llama.attention.head_count_kv", 8),
        _kv_u32("llama.embedding_length", 2048),
        _kv_u32("llama.attention.head_count", 32),
        tail=b"\xff" * 4096,
    )
    result = dict(EXPECTED, native_context_length=4096)
    assert read_gguf_ctx_params(_write(tmp_path, data)) == result


def test_early_exit_skips_broken_later_records(tmp_path):
    # All four targets resolve after record 4; record 5 is deliberately
    # malformed (u64 key_len of 2**40). Early exit must never read it.
    data = _gguf(
        _kv_u32("llama.block_count", 22),
        _kv_u32("llama.context_length", 32768),
        _kv_u32("llama.attention.head_count_kv", 8),
        _kv_u32("llama.attention.key_length", 64),
        struct.pack("<Q", 1 << 40),
        kv_count=5,
        tail=b"\x00" * 64,
    )
    assert read_gguf_ctx_params(_write(tmp_path, data)) == dict(EXPECTED, head_dim=64)


def test_key_length_direct_overrides_derivation(tmp_path):
    data = _gguf(
        _kv_u32("llama.block_count", 22),
        _kv_u32("llama.context_length", 32768),
        _kv_u32("llama.attention.head_count_kv", 8),
        _kv_u32("llama.attention.key_length", 128),
        _kv_u32("llama.embedding_length", 2048),
        _kv_u32("llama.attention.head_count", 32),
    )
    assert read_gguf_ctx_params(_write(tmp_path, data))["head_dim"] == 128


def test_qwen2_arch_prefix_matched_by_suffix(tmp_path):
    data = _gguf(
        _kv_u32("qwen2.block_count", 28),
        _kv_u32("qwen2.context_length", 131072),
        _kv_u32("qwen2.attention.head_count_kv", 4),
        _kv_u32("qwen2.embedding_length", 3584),
        _kv_u32("qwen2.attention.head_count", 28),
    )
    expected = {
        "n_layers": 28,
        "n_head_kv": 4,
        "head_dim": 128,
        "native_context_length": 131072,
    }
    assert read_gguf_ctx_params(_write(tmp_path, data)) == expected


@pytest.mark.parametrize("bad_heads", [-1, 0])
def test_nonpositive_head_count_kv_is_none(tmp_path, bad_heads):
    data = _gguf(
        _kv_i32("llama.attention.head_count_kv", bad_heads),
        _kv_u32("llama.block_count", 22),
        _kv_u32("llama.context_length", 32768),
        _kv_u32("llama.attention.key_length", 64),
    )
    result = dict(ALL_NONE, n_layers=22, head_dim=64, native_context_length=32768)
    assert read_gguf_ctx_params(_write(tmp_path, data)) == result


def test_zero_block_count_is_none(tmp_path):
    data = _gguf(
        _kv_u32("llama.block_count", 0),
        _kv_u32("llama.context_length", 32768),
        _kv_u32("llama.attention.head_count_kv", 8),
        _kv_u32("llama.attention.key_length", 64),
    )
    result = dict(ALL_NONE, n_head_kv=8, head_dim=64, native_context_length=32768)
    assert read_gguf_ctx_params(_write(tmp_path, data)) == result


# --- kv_bytes_per_token ---------------------------------------------------


def test_kv_bytes_per_token_math():
    assert kv_bytes_per_token(22, 8, 64) == 45056
    assert kv_bytes_per_token(22, 8, 64, bytes_per_elem=4) == 90112
    assert kv_bytes_per_token(1, 1, 1, bytes_per_elem=1) == 2
    assert kv_bytes_per_token(0, 8, 64) == 0


# --- max_fitting_ctx -------------------------------------------------------


def test_default_cap_applies():
    # usable = 10GiB * 0.85 - 6GiB ~= 2.5GiB -> ~59k tokens, but capped at 32768
    assert max_fitting_ctx(10 * GIB, 6 * GIB, 45_056) == 32768


def test_exact_arithmetic_and_rounding_down_to_256():
    # 200000 // 100 = 2000 tokens -> floor multiple of 256 = 1792
    assert max_fitting_ctx(200_000, 0, 100, reserve_fraction=0) == 1792
    result = max_fitting_ctx(10**7, 0, 997, reserve_fraction=0)
    assert result == 9984 and result % 256 == 0


def test_clamped_up_to_min_ctx():
    # 90000 // 300 = 300 -> aligned 256 -> below min_ctx 512 -> 512
    assert max_fitting_ctx(90_000, 0, 300, reserve_fraction=0) == 512


def test_weights_exceed_usable_returns_min_ctx():
    assert max_fitting_ctx(10 * GIB, 20 * GIB, 45_056) == 512
    assert max_fitting_ctx(0, 0, 100, reserve_fraction=0) == 512


def test_explicit_cap_respected():
    assert max_fitting_ctx(10**9, 0, 1, max_ctx=1024, reserve_fraction=0) == 1024
    # cap not a multiple of 256 -> rounds down to 768
    assert max_fitting_ctx(10**9, 0, 1, max_ctx=1000, reserve_fraction=0) == 768


def test_custom_min_ctx_respected():
    assert max_fitting_ctx(1000, 1000, 100, min_ctx=4096) == 4096
    assert max_fitting_ctx(10**9, 0, 0, min_ctx=2048) == 2048


@pytest.mark.parametrize("kv_per_token", [0, -5])
def test_nonpositive_kv_per_token_returns_min_ctx(kv_per_token):
    assert max_fitting_ctx(10**9, 0, kv_per_token) == 512


@pytest.mark.parametrize("rf", [-0.01, 1.0, 1.5])
def test_invalid_reserve_fraction_raises(rf):
    with pytest.raises(ValueError):
        max_fitting_ctx(10**9, 0, 100, reserve_fraction=rf)


def test_reserve_fraction_boundaries_valid():
    # rf=0: 100000 // 100 = 1000 -> 768 after alignment
    assert max_fitting_ctx(100_000, 0, 100, reserve_fraction=0) == 768
    # rf=0.999: 100 usable -> 1 token -> floors to min_ctx
    assert max_fitting_ctx(100_000, 0, 100, reserve_fraction=0.999) == 512
