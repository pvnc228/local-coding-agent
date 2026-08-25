"""CSS stylesheets, Tailwind configuration, and design tokens for Desktop AI Coding Harness."""

from __future__ import annotations

DESKTOP_CSS = """
    * { box-sizing: border-box; }
    
    :root.dark {
      --bg-app: #09090b;
      --bg-header: #0e0e11;
      --bg-sidebar: #0e0e11;
      --bg-card: #131316;
      --bg-card-subtle: #18181b;
      --bg-input: #09090b;
      --border-main: #27272a;
      --border-subtle: #1f1f23;
      --text-main: #f4f4f5;
      --text-muted: #a1a1aa;
      --text-subtle: #71717a;
      --diff-add-bg: rgba(16, 185, 129, 0.12);
      --diff-add-text: #86efac;
      --diff-del-bg: rgba(239, 68, 68, 0.12);
      --diff-del-text: #fca5a5;
    }

    :root:not(.dark) {
      --bg-app: #fbfbfb;
      --bg-header: #ffffff;
      --bg-sidebar: #f4f4f5;
      --bg-card: #ffffff;
      --bg-card-subtle: #f4f4f5;
      --bg-input: #ffffff;
      --border-main: #e4e4e7;
      --border-subtle: #ebebef;
      --text-main: #09090b;
      --text-muted: #52525b;
      --text-subtle: #71717a;
      --diff-add-bg: rgba(16, 185, 129, 0.10);
      --diff-add-text: #059669;
      --diff-del-bg: rgba(239, 68, 68, 0.10);
      --diff-del-text: #dc2626;
    }

    body {
      background-color: var(--bg-app);
      color: var(--text-main);
      font-feature-settings: "cv02", "cv03", "cv04", "cv11";
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    ::-webkit-scrollbar { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #3f3f46; border-radius: 2px; }
    :root:not(.dark) ::-webkit-scrollbar-thumb { background: #d4d4d8; }

    .diff-del-bg { background-color: var(--diff-del-bg); color: var(--diff-del-text); }
    .diff-add-bg { background-color: var(--diff-add-bg); color: var(--diff-add-text); }
    .num-tabular { font-variant-numeric: tabular-nums; }

    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    .animate-spin-fast {
      animation: spin 0.6s linear infinite;
    }

    @keyframes pulse-subtle {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.5; }
    }
    .animate-pulse-subtle {
      animation: pulse-subtle 1.8s ease-in-out infinite;
    }
"""
