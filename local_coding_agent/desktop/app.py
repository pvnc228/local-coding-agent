"""Desktop application launcher using pywebview with resilient browser fallback."""

from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from .server import DesktopServer


def launch_desktop_app(
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace: str | Path = ".",
    default_profile: str = "qwen2.5-coder",
    browser: bool = False,
    headless: bool = False,
    exit_on_stdin_close: bool = False,
) -> int:
    """Launch the Desktop AI Coding Harness."""
    server = DesktopServer(
        host=host,
        port=port,
        workspace=workspace,
        default_profile=default_profile,
    )
    server.start()
    app_url = f"{server.url}/app"

    if headless:
        # The Tauri shell decodes this pipe as UTF-8, whatever the locale encoding of stdout is.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "ready",
                    "url": app_url,
                    "workspace": str(Path(workspace).resolve()),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        parent_gone = threading.Event()
        if exit_on_stdin_close and sys.stdin is not None:
            # The parent owns the write end of our stdin; EOF means it died, so do not outlive it.
            def _watch_stdin() -> None:
                try:
                    while sys.stdin.buffer.read(4096):
                        pass
                except (OSError, ValueError):
                    pass
                parent_gone.set()

            threading.Thread(target=_watch_stdin, daemon=True).start()
        try:
            while not parent_gone.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0

    print("=" * 72)
    print("  Local AI Coding Harness — Standalone Desktop Cockpit")
    print(f"  Interface URL: {app_url}")
    print(f"  Workspace:     {Path(workspace).resolve()}")
    print("=" * 72)

    has_webview = False
    if not browser:
        try:
            import webview  # type: ignore[import-not-found]
            has_webview = True
        except ImportError:
            has_webview = False

    if has_webview and not browser:
        try:
            import webview  # type: ignore[import-not-found]

            print("Launching native desktop window (WebView2 / Cocoa / WebKit)...")
            webview.create_window(
                "Local AI Coding Harness",
                app_url,
                width=1360,
                height=840,
                min_size=(960, 640),
            )
            webview.start()
            server.stop()
            print("Desktop window closed.")
            return 0
        except Exception as error:
            print(f"Native desktop window initialization error: {error}. Falling back to default browser...")

    # Fallback to system browser
    print("Opening application in default web browser (Press Ctrl+C to stop)...")
    try:
        webbrowser.open(app_url)
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("\nDesktop Harness stopped.")
    return 0
