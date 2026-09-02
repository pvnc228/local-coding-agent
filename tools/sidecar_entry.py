"""Frozen entry point used by the Tauri-managed desktop sidecar."""

from local_coding_agent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
