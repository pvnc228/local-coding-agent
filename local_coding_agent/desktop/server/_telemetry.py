"""Real hardware GPU telemetry for the Desktop Harness (from nvidia-smi)."""

from __future__ import annotations

import subprocess
import math
from typing import Any


def get_nvidia_gpu_telemetry() -> dict[str, Any] | None:
    """Query live single-device GPU metrics directly from ``nvidia-smi``.

    The VRAM fitter is intentionally single-device.  A multi-GPU response is
    therefore reported as unsupported instead of silently selecting the first
    row and applying that device's capacity to an unspecified backend.
    """
    try:
        res = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu,name,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
        )
        if res.returncode != 0 or not res.stdout.strip():
            detail = (res.stderr or "nvidia-smi returned no telemetry").strip()
            return _unavailable(detail)
        lines = [line for line in res.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            return _unavailable(
                f"nvidia-smi reported {len(lines)} GPUs; single-device VRAM attribution is unsupported",
                status="unsupported",
            )
        parts = [p.strip() for p in lines[0].split(",")]
        if len(parts) < 7:
            return _unavailable("nvidia-smi returned an incomplete telemetry row")
        index, uuid, used_raw, total_raw, util_raw, name, temp_raw = parts[:7]
        used_mb = float(used_raw)
        total_mb = float(total_raw)
        util_gpu = float(util_raw)
        temp_c = float(temp_raw)
        if not all(math.isfinite(value) for value in (used_mb, total_mb, util_gpu, temp_c)):
            return _unavailable("nvidia-smi returned non-finite telemetry")
        if total_mb <= 0 or used_mb < 0 or used_mb > total_mb:
            return _unavailable("nvidia-smi returned invalid VRAM totals")
        return {
            "status": "ok",
            "gpu_index": index,
            "gpu_uuid": uuid,
            "gpu_name": name,
            "used_mb": round(used_mb, 1),
            "total_mb": round(total_mb, 1),
            "used_gb": round(used_mb / 1024, 1),
            "total_gb": round(total_mb / 1024, 1),
            "percent": round((used_mb / total_mb) * 100, 1),
            "utilization_pct": round(util_gpu, 1),
            "temp_c": round(temp_c, 1),
            "source": "nvidia-smi",
        }
    except subprocess.TimeoutExpired:
        return _unavailable("nvidia-smi telemetry timed out")
    except FileNotFoundError:
        return _unavailable("nvidia-smi executable is unavailable")
    except (OSError, ValueError) as error:
        return _unavailable(f"nvidia-smi telemetry is invalid: {error}")


def _unavailable(error: str, *, status: str = "unavailable") -> dict[str, Any]:
    return {
        "status": status,
        "source": "nvidia-smi",
        "error": error,
        "gpu_index": None,
        "gpu_uuid": None,
        "gpu_name": None,
        "used_mb": None,
        "total_mb": None,
        "used_gb": None,
        "total_gb": None,
        "percent": None,
        "utilization_pct": None,
        "temp_c": None,
    }
