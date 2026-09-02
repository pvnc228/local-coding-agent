"""Embedded production-grade HTML template and dynamic assembler for Desktop AI Coding Harness."""

from __future__ import annotations

import json

from .client_js import DESKTOP_CLIENT_JS
from .components import (
    render_chat_panel,
    render_delegated_panel,
    render_header,
    render_modals,
    render_sidebar,
    render_toast,
)
from .styles import DESKTOP_CSS


def render_desktop_html(mutation_token: str = "") -> str:
    """Assemble complete standalone single-page HTML application from modular components."""
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en" class="dark">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "  <title>Local AI Coding Harness</title>",
        '  <link rel="stylesheet" href="/assets/tailwind.css">',
        '  <script src="/assets/lucide.min.js"></script>',
        "  <style>",
        DESKTOP_CSS,
        "  </style>",
        "</head>",
        '<body class="h-screen flex flex-col font-sans tracking-tight overflow-hidden">',
        render_header(),
        '  <main class="flex-1 flex overflow-hidden relative">',
        render_sidebar(),
        render_chat_panel(),
        render_delegated_panel(),
        "  </main>",
        render_modals(),
        render_toast(),
        f"  <script>window.DESKTOP_MUTATION_TOKEN = {json.dumps(mutation_token)};</script>",
        "  <script>",
        DESKTOP_CLIENT_JS,
        "  </script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


DESKTOP_HTML_TEMPLATE = render_desktop_html()
