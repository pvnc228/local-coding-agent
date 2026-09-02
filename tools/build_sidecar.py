"""Build and name the frozen sidecar exactly as Tauri v2 expects."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    host = subprocess.run(
        ["rustc", "--print", "host-tuple"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "desktop-sidecar.spec"],
        cwd=ROOT,
        check=True,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    source = ROOT / "dist" / f"local-agent-sidecar{suffix}"
    destination = ROOT / "src-tauri" / "binaries" / f"local-agent-sidecar-{host}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
