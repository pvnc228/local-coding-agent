"""Real hardware GPU telemetry for the Desktop Harness (from nvidia-smi)."""

from __future__ import annotations

import subprocess
from typing import Any


def get_nvidia_gpu_telemetry() -> dict[str, Any] | None:
    """Query live GPU hardware metrics directly from nvidia-smi."""
    try:
        res = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu,name,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
        )
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 4:
                used_mb = float(parts[0])
                total_mb = float(parts[1])
                util_gpu = float(parts[2])
                name = parts[3]
                temp_c = float(parts[4]) if len(parts) > 4 else None
                used_gb = round(used_mb / 1024, 1)
                total_gb = round(total_mb / 1024, 1)
                percent = round((used_mb / total_mb) * 100, 1) if total_mb > 0 else 0.0
                return {
                    "gpu_name": name,
                    "used_mb": round(used_mb, 1),
                    "total_mb": round(total_mb, 1),
                    "used_gb": used_gb,
                    "total_gb": total_gb,
                    "percent": percent,
                    "utilization_pct": util_gpu,
                    "temp_c": temp_c,
                    "source": "nvidia-smi",
                }
    except Exception:
        pass
    return None
