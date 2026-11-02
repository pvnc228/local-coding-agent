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
    from .settings import load_ui_settings

    ui_settings = load_ui_settings()
    preset = ui_settings.get("preset", "night-galley")
    scheme = ui_settings.get("scheme", "system")
    initial_dark_class = "dark" if scheme != "light" else ""
    safe_settings = json.dumps(ui_settings, ensure_ascii=False).replace("</", "<\\/")

    parts = [
        "<!DOCTYPE html>",
        f'<html lang="en" class="{initial_dark_class}" data-preset="{preset}">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "  <title>Local AI Coding Harness</title>",
        '  <link rel="stylesheet" href="/assets/tailwind.css">',
        '  <script src="/assets/lucide.min.js"></script>',
        f"  <script>window.INJECTED_UI_SETTINGS = {safe_settings};</script>",
        "  <script>",
        "  (function() {",
        "    try {",
        "      var s = window.INJECTED_UI_SETTINGS || {};",
        "      var root = document.documentElement;",
        "      var scheme = s.scheme || 'system';",
        "      var isDark = true;",
        "      if (scheme === 'system') {",
        "        isDark = !window.matchMedia || window.matchMedia('(prefers-color-scheme: dark)').matches;",
        "      } else {",
        "        isDark = (scheme === 'dark');",
        "      }",
        "      if (isDark) { root.classList.add('dark'); } else { root.classList.remove('dark'); }",
        "      if (s.preset) { root.setAttribute('data-preset', s.preset); }",
        "      if (s.preset === 'custom') {",
        "        var c = (s.custom || {})[isDark ? 'dark' : 'light'] || {};",
        "        var vars = { surface_tint: '--ground', accent: '--blue', diff_added: '--diff-add', diff_removed: '--diff-del' };",
        "        Object.keys(vars).forEach(function(k) { if (c[k]) root.style.setProperty(vars[k], c[k]); });",
        "      }",
        "    } catch (e) {}",
        "  })();",
        "  </script>",
        "  <style>",
        DESKTOP_CSS,
        "  </style>",
        "</head>",
        "<body>",
        render_header(),
        '  <main class="workspace">',
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
