"""Embedded production-grade HTML template and dynamic assembler for Desktop AI Coding Harness."""

from __future__ import annotations

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


def render_desktop_html() -> str:
    """Assemble complete standalone single-page HTML application from modular components."""
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en" class="dark">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "  <title>Local AI Coding Harness</title>",
        '  <link rel="preconnect" href="https://fonts.googleapis.com">',
        '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">',
        '  <script src="https://cdn.tailwindcss.com"></script>',
        '  <script src="https://unpkg.com/lucide@latest"></script>',
        "  <script>",
        "    tailwind.config = {",
        "      darkMode: 'class',",
        "      theme: {",
        "        extend: {",
        "          fontFamily: {",
        "            sans: ['Geist', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],",
        "            mono: ['Geist Mono', 'JetBrains Mono', 'ui-monospace', 'monospace'],",
        "          },",
        "          letterSpacing: {",
        "            tighter: '-0.03em',",
        "            tight: '-0.015em',",
        "          }",
        "        }",
        "      }",
        "    };",
        "  </script>",
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
        "  <script>",
        DESKTOP_CLIENT_JS,
        "  </script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


DESKTOP_HTML_TEMPLATE = render_desktop_html()
