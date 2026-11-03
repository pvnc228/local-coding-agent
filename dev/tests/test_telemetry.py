from __future__ import annotations

from types import SimpleNamespace

from local_coding_agent.desktop.server import _telemetry


def _result(stdout: str, *, returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_single_gpu_telemetry_keeps_device_identity(monkeypatch):
    monkeypatch.setattr(
        _telemetry.subprocess,
        "run",
        lambda *args, **kwargs: _result("0, GPU-abc, 100, 8192, 20, RTX Test, 55\n"),
    )

    result = _telemetry.get_nvidia_gpu_telemetry()

    assert result["status"] == "ok"
    assert result["gpu_index"] == "0"
    assert result["gpu_uuid"] == "GPU-abc"
    assert result["total_mb"] == 8192.0


def test_multi_gpu_telemetry_is_not_attributed_to_first_device(monkeypatch):
    monkeypatch.setattr(
        _telemetry.subprocess,
        "run",
        lambda *args, **kwargs: _result(
            "0, GPU-a, 100, 8192, 20, RTX A, 55\n1, GPU-b, 200, 16384, 30, RTX B, 60\n"
        ),
    )

    result = _telemetry.get_nvidia_gpu_telemetry()

    assert result["status"] == "unsupported"
    assert result["total_mb"] is None
    assert "single-device" in result["error"]


def test_missing_or_incomplete_telemetry_stays_unknown(monkeypatch):
    monkeypatch.setattr(
        _telemetry.subprocess,
        "run",
        lambda *args, **kwargs: _result("0, GPU-a, 100\n"),
    )

    result = _telemetry.get_nvidia_gpu_telemetry()

    assert result["status"] == "unavailable"
    assert result["used_mb"] is None
    assert result["total_mb"] is None
