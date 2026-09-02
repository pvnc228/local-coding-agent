"""Desktop application launcher using pywebview with resilient browser fallback."""

from __future__ import annotations

import json
import sys
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
        try:
            while True:
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
