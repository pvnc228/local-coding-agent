"""Night Galley stylesheet for the Desktop AI Coding Harness.

The model sets copy (serif), the controller proofs it in the margin
(blue notes, red refusals, amber waits), and pass-green is reserved for
Apply and for evidence an external runner produced.
"""

from __future__ import annotations

from .settings import PRESET_TOKENS

DESKTOP_CSS = """
    *, *::before, *::after { box-sizing: border-box; }

    :root {
      --font-ui: "Bahnschrift", "DIN Alternate", "Barlow", "Segoe UI", system-ui, sans-serif;
      --font-copy: "Sitka Text", "Iowan Old Style", "Charter", "Georgia", serif;
      --font-code: "Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", ui-monospace, monospace;
      --fs-s: 12px;
      --fs-m: 14px;
      --fs-l: 16px;
      --rail-w: 264px;
      --margin-w: 184px;
      --proof-w: clamp(380px, 34vw, 580px);
      --measure: 880px;
      --radius: 3px;
      --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    }

    :root.dark {
      color-scheme: dark;
      /* @tokens night-galley dark */
      --red-bg: rgba(236, 106, 83, 0.11);
      --blue-bg: rgba(141, 188, 220, 0.10);
      --amber-bg: rgba(220, 168, 77, 0.10);
      --select: rgba(141, 188, 220, 0.30);
      --scrim: rgba(5, 7, 10, 0.68);
      --shadow: 0 18px 40px -12px rgba(0, 0, 0, 0.65), 0 2px 8px rgba(0, 0, 0, 0.35);
      --diff-add-bg: color-mix(in srgb, var(--diff-add) 20%, transparent);
      --diff-del-bg: color-mix(in srgb, var(--diff-del) 20%, transparent);
    }

    :root:not(.dark) {
      color-scheme: light;
      /* @tokens night-galley light */
      --red-bg: rgba(176, 56, 31, 0.09);
      --blue-bg: rgba(43, 104, 150, 0.09);
      --amber-bg: rgba(139, 95, 16, 0.10);
      --select: rgba(43, 104, 150, 0.22);
      --scrim: rgba(30, 26, 18, 0.36);
      --shadow: 0 18px 40px -14px rgba(40, 34, 20, 0.28), 0 2px 8px rgba(40, 34, 20, 0.08);
      --diff-add-bg: color-mix(in srgb, var(--diff-add) 16%, transparent);
      --diff-del-bg: color-mix(in srgb, var(--diff-del) 14%, transparent);
    }

    /* @presets */

    html, body { height: 100%; }
    body {
      margin: 0;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--ground);
      color: var(--text);
      font-family: var(--font-ui);
      font-size: var(--fs-m);
      line-height: 1.45;
      -webkit-font-smoothing: antialiased;
    }
    ::selection { background: var(--select); color: var(--text); }
    input, textarea { caret-color: var(--blue); }
    :focus { outline: none; }
    :focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--rule-strong); border: 3px solid transparent; background-clip: padding-box; border-radius: 6px; }
    ::-webkit-scrollbar-thumb:hover { background-color: var(--text-3); }
    ::-webkit-scrollbar-corner { background: transparent; }

    .hidden { display: none !important; }
    .num { font-variant-numeric: tabular-nums; }
    .mono { font-family: var(--font-code); }
    .i { width: 16px; height: 16px; flex: none; }
    .i-s { width: 14px; height: 14px; flex: none; }
    svg.lucide { stroke-width: 1.75; }
    .label {
      font-size: var(--fs-s);
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--text-3);
    }
    kbd {
      font-family: var(--font-code);
      font-size: var(--fs-s);
      color: var(--text-3);
      padding: 0 4px;
      border: 1px solid var(--rule-strong);
      border-radius: 2px;
      line-height: 16px;
    }

    /* Controls */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      height: 28px;
      padding: 0 10px;
      border: 1px solid var(--rule-strong);
      border-radius: var(--radius);
      background: var(--raised);
      color: var(--text);
      font: inherit;
      font-size: var(--fs-s);
      font-weight: 600;
      letter-spacing: 0.02em;
      white-space: nowrap;
      cursor: pointer;
      transition: background-color 0.15s, border-color 0.15s, color 0.15s;
    }
    .btn:hover:not(:disabled) { background: var(--hover); border-color: var(--text-3); }
    .btn:disabled { cursor: not-allowed; color: var(--text-3); opacity: 0.6; }
    .btn kbd { margin-left: 2px; border-color: currentColor; color: inherit; opacity: 0.7; }
    .btn--quiet { background: transparent; border-color: transparent; color: var(--text-2); }
    .btn--quiet:hover:not(:disabled) { background: var(--hover); border-color: transparent; color: var(--text); }
    .btn--primary { background: var(--text); border-color: var(--text); color: var(--ground); }
    .btn--primary:hover:not(:disabled) { background: color-mix(in srgb, var(--text) 86%, var(--ground)); border-color: transparent; }
    .btn--pass { background: var(--pass); border-color: var(--pass); color: var(--pass-ink); }
    .btn--pass:hover:not(:disabled) { background: color-mix(in srgb, var(--pass) 88%, var(--ground)); border-color: transparent; }
    .btn--pass:disabled { background: transparent; border-color: var(--rule-strong); color: var(--text-3); opacity: 1; }
    .btn--danger { background: var(--red-bg); border-color: color-mix(in srgb, var(--red) 45%, transparent); color: var(--red); }
    .btn--danger:hover:not(:disabled) { background: color-mix(in srgb, var(--red) 18%, transparent); border-color: var(--red); }
    .icon-btn { width: 28px; padding: 0; }
    .link {
      padding: 0;
      border: 0;
      background: none;
      color: var(--blue);
      font: inherit;
      cursor: pointer;
      text-decoration: underline;
      text-underline-offset: 3px;
      text-decoration-thickness: 1px;
    }

    .seg {
      display: inline-flex;
      gap: 2px;
      padding: 2px;
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      background: var(--plate);
    }
    .seg button {
      height: 22px;
      padding: 0 10px;
      border: 0;
      border-radius: 2px;
      background: transparent;
      color: var(--text-2);
      font: inherit;
      font-size: var(--fs-s);
      font-weight: 600;
      cursor: pointer;
    }
    .seg button:hover { color: var(--text); }
    .seg button[aria-pressed="true"] { background: var(--raised); color: var(--text); box-shadow: inset 0 0 0 1px var(--rule-strong); }
    .seg--full { display: flex; }
    .seg--full button { flex: 1; }

    .input {
      width: 100%;
      height: 30px;
      padding: 0 10px;
      border: 1px solid var(--rule-strong);
      border-radius: var(--radius);
      background: var(--ground);
      color: var(--text);
      font: inherit;
      font-size: var(--fs-s);
    }
    textarea.input { height: auto; padding: 8px 10px; line-height: 1.5; resize: vertical; font-size: var(--fs-m); }
    .input.mono { font-family: var(--font-code); }
    .input::placeholder, .composer textarea::placeholder { color: var(--text-3); }
    .input:focus { border-color: var(--blue); outline: none; }
    select.input { padding-right: 6px; cursor: pointer; }

    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-3); flex: none; }
    .dot--ok { background: var(--blue); }
    .dot--bad { background: var(--red); }
    .dot--wait { background: var(--amber); animation: breathe 1.6s ease-in-out infinite; }

    .tag {
      display: inline-flex;
      align-items: center;
      height: 20px;
      padding: 0 6px;
      border: 1px solid var(--rule-strong);
      border-radius: 2px;
      color: var(--text-3);
      font-size: var(--fs-s);
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .tone-blue { color: var(--blue); border-color: color-mix(in srgb, var(--blue) 45%, transparent); }
    .tone-red { color: var(--red); border-color: color-mix(in srgb, var(--red) 45%, transparent); }
    .tone-amber { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 45%, transparent); }
    .tone-pass { color: var(--pass); border-color: color-mix(in srgb, var(--pass) 45%, transparent); }
    .tone-mute { color: var(--text-3); }

    .spin { animation: spin 0.7s linear infinite; flex: none; }

    /* Top strip */
    .topbar {
      height: 44px;
      flex: none;
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 0 10px;
      border-bottom: 1px solid var(--rule);
      background: var(--plate);
    }
    .topbar-left, .topbar-right { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .topbar-left { flex: none; align-self: stretch; }
    .topbar-right { flex: 1; justify-content: flex-end; }
    .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; letter-spacing: 0.01em; white-space: nowrap; padding-right: 4px; }
    .brand-mark { width: 18px; height: 18px; color: var(--text); flex: none; }
    .machine { display: flex; min-width: 0; border: 1px solid var(--rule); border-radius: var(--radius); overflow: hidden; }
    .machine > button {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      height: 28px;
      padding: 0 10px;
      border: 0;
      border-right: 1px solid var(--rule);
      background: transparent;
      color: var(--text-2);
      font: inherit;
      font-size: var(--fs-s);
      cursor: pointer;
    }
    .machine > button:last-child { border-right: 0; }
    .machine > button:hover { background: var(--hover); color: var(--text); }
    .machine-key { font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-3); }
    .machine .val { font-family: var(--font-code); color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    #telemetryModel { max-width: 240px; }
    .machine > button:first-child { min-width: 0; flex: 0 1 auto; flex-shrink: 8; }
    .machine > button:not(:first-child) { flex: none; }
    .meter { width: 34px; height: 4px; border-radius: 2px; background: var(--rule-strong); overflow: hidden; flex: none; }
    .meter > span { display: block; height: 100%; width: 0; background: var(--text-2); transition: width 0.4s var(--ease-out); }
    .meter--hot > span { background: var(--amber); }

    .tabs { display: flex; align-self: stretch; gap: 2px; margin-left: 8px; }
    .tab {
      position: relative;
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 0 12px;
      border: 0;
      background: none;
      color: var(--text-2);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }
    .tab:hover { color: var(--text); }
    .tab[aria-selected="true"] { color: var(--text); }
    .tab[aria-selected="true"]::after { content: ""; position: absolute; left: 8px; right: 8px; bottom: -1px; height: 2px; background: var(--text); }
    .count {
      min-width: 18px;
      padding: 0 5px;
      border-radius: 2px;
      background: var(--blue-bg);
      color: var(--blue);
      font-family: var(--font-code);
      font-size: var(--fs-s);
      line-height: 18px;
      text-align: center;
    }
    .ws { flex: 0 100 auto; display: flex; align-items: center; gap: 6px; min-width: 0; padding: 0 6px; font-family: var(--font-code); font-size: var(--fs-s); color: var(--text-2); white-space: nowrap; }
    .ws > span { overflow: hidden; text-overflow: ellipsis; }
    .ws-branch { color: var(--text-3); }

    /* Native window: the strip is the title bar. Hidden until the Tauri API sets html.is-native. */
    .topbar-drag { display: none; }
    .window-controls { display: none; flex: none; align-self: stretch; }
    .is-native .window-controls { display: flex; }
    .is-native .topbar { padding-right: 0; gap: 0; }
    .is-native .topbar-left { margin-right: 16px; }
    .is-native .topbar-drag { display: block; flex: 1 0 48px; min-width: 48px; align-self: stretch; }
    .is-native .topbar-right { flex: 0 1 auto; padding-right: 8px; }
    .window-controls button {
      width: 46px;
      height: 44px;
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      color: var(--text-2);
      display: grid;
      place-items: center;
      cursor: default;
    }
    .window-controls button:hover, .max-hover #win-maximize, .max-hover #win-restore { background: var(--hover); color: var(--text); }
    .window-controls button:focus-visible { outline-offset: -2px; }
    .window-controls svg { width: 10px; height: 10px; fill: none; stroke: currentColor; stroke-width: 1; }
    .window-controls #win-close:hover { background: #c42b1c; color: #fff; }  /* system convention, the one colour outside the palette */
    .window-controls #win-restore { display: none; }
    .is-maximized #win-maximize { display: none; }
    .is-maximized #win-restore { display: grid; }
    .dark .theme-moon, :root:not(.dark) .theme-sun { display: none; }

    /* Body */
    .workspace { flex: 1; display: flex; min-height: 0; position: relative; }
    .view { flex: 1; display: flex; min-width: 0; min-height: 0; }

    /* Galley rail */
    .rail { width: var(--rail-w); flex: none; display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--rule); background: var(--plate); }
    .rail-head { display: grid; gap: 8px; padding: 10px; border-bottom: 1px solid var(--rule); }
    .new-session { justify-content: flex-start; height: 32px; }
    .new-session kbd { margin-left: auto; }
    .rail-list { flex: 1; overflow-y: auto; min-height: 0; }
    .galley-entry {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      column-gap: 8px;
      row-gap: 3px;
      width: 100%;
      padding: 10px 12px 11px;
      border: 0;
      border-bottom: 1px solid var(--rule);
      background: transparent;
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }
    .galley-entry:hover { background: var(--hover); }
    .galley-entry[aria-current="true"] { background: var(--raised); }
    .ge-no { font-family: var(--font-code); font-size: var(--fs-s); color: var(--text-3); }
    .galley-entry[aria-current="true"] .ge-no { color: var(--blue); }
    .ge-kind { font-size: var(--fs-s); color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ge-time { font-size: var(--fs-s); color: var(--text-3); }
    .ge-title {
      grid-column: 1 / -1;
      color: var(--text);
      font-weight: 500;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .ge-meta { grid-column: 1 / 3; font-family: var(--font-code); font-size: var(--fs-s); color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ge-verdict { font-size: var(--fs-s); font-weight: 600; white-space: nowrap; }
    .v-pass { color: var(--pass); } .v-red { color: var(--red); } .v-amber { color: var(--amber); } .v-blue { color: var(--blue); } .v-mute { color: var(--text-3); }
    .rail-empty { padding: 28px 16px; color: var(--text-3); font-size: var(--fs-s); text-align: center; }
    .rail-empty strong { display: block; color: var(--text-2); font-size: var(--fs-m); font-weight: 600; margin-bottom: 4px; }
    .rail-foot { display: flex; justify-content: space-between; padding: 8px 12px; border-top: 1px solid var(--rule); font-size: var(--fs-s); color: var(--text-3); }

    /* Chat column */
    .chat { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .composer-wrap { container-type: inline-size; }
    @container (max-width: 760px) { .composer-hint { display: none; } }
    @container (max-width: 520px) { .mode-note { display: none; } }
    .transcript { container-type: inline-size; }
    @container (max-width: 640px) {
      .galley, .slug { grid-template-columns: minmax(0, 1fr); }
      .galley-margin { flex-direction: row; flex-wrap: wrap; gap: 8px 20px; padding: 0 0 16px; border-left: 0; }
      .galley-margin:empty { display: none; }
      .mark { max-width: 220px; }
    }
    .transcript { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 30px; padding: 32px 32px 48px; user-select: text; }
    .transcript > * { width: 100%; max-width: var(--measure); margin-inline: auto; flex: none; }

    .slug { display: grid; grid-template-columns: minmax(0, 1fr) var(--margin-w); column-gap: 24px; }
    .slug-body { padding-top: 10px; border-top: 1px solid var(--rule-strong); }
    .slug-meta { display: flex; gap: 10px; margin-bottom: 5px; font-size: var(--fs-s); color: var(--text-3); }
    .slug-meta .mono { color: var(--text-2); }
    .slug-text { margin: 0; color: var(--text); white-space: pre-wrap; overflow-wrap: anywhere; }

    .galley { display: grid; grid-template-columns: minmax(0, 1fr) var(--margin-w); column-gap: 24px; }
    .galley-head { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; padding-bottom: 10px; font-size: var(--fs-s); color: var(--text-3); }
    .galley-head .mono { color: var(--text-2); }
    .galley-cell { padding-bottom: 16px; min-width: 0; }
    .galley-margin { display: flex; flex-direction: column; gap: 10px; padding: 2px 0 16px 12px; border-left: 1px solid var(--rule); min-width: 0; }
    .copy { font-family: var(--font-copy); font-size: var(--fs-l); line-height: 1.62; color: var(--text); max-width: 68ch; overflow-wrap: anywhere; }
    .copy strong { font-weight: 700; }
    .copy li { margin-left: 1.3em; list-style: disc; }
    .copy code { padding: 1px 4px; border-radius: 2px; background: var(--plate); border: 1px solid var(--rule); font-family: var(--font-code); font-size: var(--fs-m); }
    .codeblock { margin: 12px 0; border: 1px solid var(--rule-strong); border-radius: var(--radius); background: var(--plate); overflow: hidden; font-family: var(--font-code); }
    .codeblock-head { display: flex; justify-content: space-between; align-items: center; height: 28px; padding: 0 4px 0 10px; border-bottom: 1px solid var(--rule); font-family: var(--font-ui); font-size: var(--fs-s); color: var(--text-3); }
    .codeblock pre { margin: 0; padding: 10px 12px; overflow-x: auto; font-size: var(--fs-s); line-height: 1.6; color: var(--text); }
    .codeblock pre code { padding: 0; border: 0; background: none; font-size: inherit; }

    .mark { display: grid; grid-template-columns: 14px minmax(0, 1fr); column-gap: 7px; font-size: var(--fs-s); line-height: 1.4; color: var(--text-2); }
    .mark svg { margin-top: 1px; color: var(--mark-c, var(--text-3)); }
    .mark b { display: block; color: var(--mark-c, var(--text-2)); font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
    .mark span { overflow-wrap: anywhere; }
    .mark .mono { font-size: var(--fs-s); }
    .mark--blue { --mark-c: var(--blue); }
    .mark--red { --mark-c: var(--red); }
    .mark--amber { --mark-c: var(--amber); }
    .mark--pass { --mark-c: var(--pass); }
    .galley.is-new .mark { animation: stamp 0.45s var(--ease-out) both; animation-delay: calc(var(--i, 0) * 90ms + 140ms); }

    .setting { display: flex; align-items: center; gap: 10px; font-family: var(--font-copy); font-size: var(--fs-l); color: var(--text-2); }
    .caret { width: 2px; height: 1.15em; background: var(--blue); animation: blink 1s steps(2, start) infinite; }

    .plan { display: grid; gap: 10px; padding: 12px 14px; border: 1px solid var(--rule-strong); border-radius: var(--radius); background: var(--plate); }
    .plan-goal { color: var(--text); }
    .plan ol { margin: 0; padding: 0; list-style: none; counter-reset: step; display: grid; gap: 6px; }
    .plan li { display: grid; grid-template-columns: 22px 1fr; counter-increment: step; color: var(--text); }
    .plan li::before { content: counter(step); font-family: var(--font-code); font-size: var(--fs-s); color: var(--blue); padding-top: 1px; }

    .patch { border: 1px solid var(--rule-strong); border-radius: var(--radius); background: var(--plate); overflow: hidden; }
    .patch-head { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 6px 0 10px; border-bottom: 1px solid var(--rule); font-size: var(--fs-s); }
    .patch-file { font-family: var(--font-code); color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .patch-head .btn { margin-left: auto; }
    .workspace:has(#diffPanel:not(.hidden)) .patch > .diff,
    .workspace:has(#diffPanel:not(.hidden)) .patch-more { display: none; }
    .patch-more { padding: 4px 10px 8px; font-size: var(--fs-s); color: var(--text-3); }
    .stat-add { color: var(--blue); font-family: var(--font-code); }
    .stat-del { color: var(--red); font-family: var(--font-code); }

    .diff { font-family: var(--font-code); font-size: var(--fs-s); line-height: 1.6; padding: 0 0 6px; overflow-x: auto; background: var(--diff-bg); }
    .diff.is-wrapped .dl { width: 100%; white-space: pre-wrap; word-break: break-all; }
    .df-section { border-bottom: 1px solid var(--rule); }
    .df-section:last-child { border-bottom: none; }
    .df-file { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 12px; background: var(--raised); border-bottom: 1px solid var(--rule); font-size: var(--fs-s); cursor: pointer; user-select: none; }
    .df-file-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .df-filename { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .df-chevron { transition: transform 0.15s ease; color: var(--text-3); }
    .df-file.is-collapsed .df-chevron { transform: rotate(-90deg); }
    .df-stats { display: flex; gap: 8px; font-family: var(--font-code); font-size: var(--fs-s); }
    .df-body.is-collapsed { display: none; }

    .dl { display: grid; grid-template-columns: 46px 20px max-content; min-width: 100%; width: max-content; white-space: pre; }
    .dl-no { padding-right: 8px; text-align: right; color: var(--text-3); background: var(--diff-gutter); user-select: none; border-right: 1px solid var(--rule); }
    .dl-sign { display: flex; align-items: center; justify-content: center; color: var(--text-3); user-select: none; font-weight: 600; }
    .dl-code { padding-right: 16px; color: var(--text-2); }
    .dl--add { background: var(--diff-add-bg); box-shadow: inset 2px 0 0 var(--diff-add); }
    .dl--add .dl-sign { color: var(--diff-add); }
    .dl--add .dl-code { color: var(--text); }
    .dl--del { background: var(--diff-del-bg); box-shadow: inset 2px 0 0 var(--diff-del); }
    .dl--del .dl-sign { color: var(--diff-del); }
    .dl--del .dl-code { color: var(--text); }
    .dl--invalid { background: color-mix(in srgb, var(--diff-del) 16%, transparent); box-shadow: inset 2px 0 0 var(--diff-del); }
    .dl--invalid .dl-sign { color: var(--diff-del); font-weight: 700; }
    .dl--invalid .dl-code { color: var(--text); }
    .dl--hunk { display: block; padding: 6px 12px 4px; color: var(--blue); background: color-mix(in srgb, var(--diff-gutter) 80%, transparent); font-size: var(--fs-s); opacity: 0.88; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
    .dl--gap { display: block; padding: 4px 12px; color: var(--text-3); background: var(--diff-gutter); font-size: var(--fs-s); font-style: italic; border-top: 1px dashed var(--rule); border-bottom: 1px dashed var(--rule); }
    .dl--meta .dl-code { color: var(--text-3); font-style: italic; }
    .dl--file { display: block; padding: 0 12px; color: var(--text-3); }
    .diff-empty { margin: auto; padding: 40px 24px; max-width: 320px; text-align: center; color: var(--text-3); font-size: var(--fs-s); }
    #btnDiffWrap.is-active { color: var(--blue); background: var(--hover); }

    .notice { display: grid; gap: 10px; padding: 12px 14px; border: 1px solid color-mix(in srgb, var(--amber) 40%, transparent); border-radius: var(--radius); background: var(--amber-bg); }
    .notice p { margin: 0; color: var(--text); overflow-wrap: anywhere; }
    .notice-actions { display: flex; gap: 8px; }

    /* Empty galley */
    .welcome { padding-top: 6vh; display: grid; gap: 18px; max-width: 640px !important; }
    .welcome-copy { margin: 0; font-family: var(--font-copy); font-size: var(--fs-l); line-height: 1.62; }
    .legend { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 24px; }
    .legend > div { display: grid; grid-template-columns: 14px 1fr; column-gap: 8px; align-items: start; }
    .legend dt { width: 10px; height: 10px; margin-top: 4px; border-radius: 1px; background: var(--mark-c); }
    .legend dd { margin: 0; color: var(--text-2); font-size: var(--fs-s); line-height: 1.5; }
    .legend dd b { color: var(--text); font-weight: 600; }
    .engine-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; font-size: var(--fs-s); color: var(--text-3); }
    .engine-line .mono { color: var(--text-2); }
    .presets { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .presets .label { margin-right: 4px; }

    /* Composer */
    .composer-wrap { flex: none; padding: 12px 32px 16px; border-top: 1px solid var(--rule); background: var(--ground); }
    .composer { max-width: var(--measure); margin: 0 auto; border: 1px solid var(--rule-strong); border-radius: 4px; background: var(--raised); transition: border-color 0.15s; }
    .composer:focus-within { border-color: var(--blue); }
    .composer textarea {
      display: block;
      width: 100%;
      min-height: 46px;
      max-height: 220px;
      padding: 11px 12px 4px;
      border: 0;
      background: transparent;
      color: var(--text);
      font: inherit;
      line-height: 1.5;
      resize: none;
      field-sizing: content;
    }
    .composer textarea:focus-visible { outline: none; }
    .composer-bar { display: flex; align-items: center; gap: 10px; padding: 6px 8px 8px 10px; overflow: hidden; }
    .mode-switch-wrap { display: flex; align-items: center; gap: 6px; min-width: 0; }
    .mode-toggle-btn { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 8px; font-family: var(--font-ui); font-size: var(--fs-s); border-radius: var(--radius); background: var(--raised); border: 1px solid var(--rule); color: var(--text); cursor: pointer; white-space: nowrap; user-select: none; transition: background 0.12s ease, border-color 0.12s ease; }
    .mode-toggle-btn:hover { background: var(--hover); }
    .mode-toggle-chevron { font-size: 13px; color: var(--text-3); transition: transform 0.18s var(--ease-out); }
    .mode-toggle-btn[aria-expanded="true"] .mode-toggle-chevron { transform: rotate(90deg); }
    .mode-options { display: grid; grid-template-columns: 0fr; transition: grid-template-columns 0.18s var(--ease-out); overflow: hidden; }
    .mode-options.is-open { grid-template-columns: 1fr; }
    .mode-options-inner { min-width: 0; display: flex; align-items: center; gap: 8px; overflow: hidden; white-space: nowrap; }
    .mode-options .mode-btn:focus-visible { outline-offset: -2px; }  /* .mode-options clips anything drawn outside it */
    .mode-note { font-size: var(--fs-s); color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .composer-hint { margin-left: auto; font-size: var(--fs-s); color: var(--text-3); white-space: nowrap; }

    /* Proof panel (diff) */
    .proof { width: var(--proof-w); flex: none; display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--rule); background: var(--plate); }
    .proof.is-opening { animation: unroll 0.42s var(--ease-out); }
    .proof { container-type: inline-size; }
    @container (max-width: 520px) {
      .proof-actions #btnApply kbd { display: none; }
    }
    @container (max-width: 460px) {
      .proof-foot { flex-wrap: wrap; }
      .proof-foot .evidence { width: 100%; }
      .proof-foot .proof-actions { margin-left: 0; width: 100%; justify-content: flex-end; }
    }
    .proof-head { flex: none; display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 8px 0 12px; border-bottom: 1px solid var(--rule); }
    .proof-title { display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1; color: var(--text-3); }
    .proof-file { font-family: var(--font-code); font-size: var(--fs-s); color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .proof-stats { font-size: var(--fs-s); color: var(--text-3); white-space: nowrap; }
    .proof-body { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; user-select: text; background: var(--diff-bg); }
    .proof-body > .diff { padding: 8px 0 16px; overflow: visible; }
    .proof-foot { flex: none; display: flex; align-items: center; gap: 10px; padding: 8px 10px 8px 12px; border-top: 1px solid var(--rule); background: var(--ground); }
    .proof-actions { display: flex; gap: 6px; margin-left: auto; }
    .evidence { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: var(--fs-s); color: var(--text-3); flex-shrink: 0; }
    .evidence > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
    .evidence .ev-ok, .evidence .ev-wait, .evidence .ev-fail { display: none; }
    .evidence[data-state="pending"] { color: var(--amber); }
    .evidence[data-state="pending"] .ev-wait { display: block; }
    .evidence[data-state="pending"] .ev-none { display: none; }
    .evidence[data-state="verified"] { color: var(--pass); }
    .evidence[data-state="verified"] .ev-ok { display: block; }
    .evidence[data-state="verified"] .ev-none { display: none; }
    .evidence[data-state="failed"], .evidence[data-state="rejected"] { color: var(--red); }
    .evidence[data-state="failed"] .ev-fail, .evidence[data-state="rejected"] .ev-fail { display: block; }
    .evidence[data-state="failed"] .ev-none, .evidence[data-state="rejected"] .ev-none { display: none; }
    .evidence[data-state="failed"] .ev-wait, .evidence[data-state="rejected"] .ev-wait { display: none; }

    /* Delegated view */
    .queue { width: 380px; flex: none; display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--rule); background: var(--plate); }
    .pane-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; height: 44px; padding: 0 12px; border-bottom: 1px solid var(--rule); }
    .pane-title { margin: 0; font-size: var(--fs-m); font-weight: 700; }
    .pane-note { font-size: var(--fs-s); color: var(--text-3); }
    .queue-form { display: grid; gap: 8px; padding: 12px; border-bottom: 1px solid var(--rule); }
    .queue-form-row { display: flex; gap: 8px; }
    .queue-list { flex: 1; overflow-y: auto; min-height: 0; }
    .task { display: grid; gap: 4px; padding: 10px 12px 11px; border-bottom: 1px solid var(--rule); cursor: pointer; transition: background 0.12s ease; user-select: text; }
    .task:hover { background: var(--hover); }
    .task:focus-visible { outline: 1px solid var(--blue); outline-offset: -1px; }
    .task.is-current, .task.galley-entry-current { background: var(--raised); }
    .task-top { display: flex; align-items: center; gap: 8px; }
    .task-id { margin-left: auto; font-family: var(--font-code); font-size: var(--fs-s); color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .task-goal { color: var(--text); overflow-wrap: anywhere; }
    .task-summary { font-size: var(--fs-s); color: var(--text-2); overflow-wrap: anywhere; }
    .task-error { font-size: var(--fs-s); color: var(--red); overflow-wrap: anywhere; }
    .task-actions { display: flex; gap: 6px; padding-top: 4px; }
    .task-actions:empty { display: none; }
    .task--running .tag { animation: breathe 1.6s ease-in-out infinite; }
    .task--cancelled .task-goal { text-decoration: line-through; color: var(--text-3); }
    .queue-empty { padding: 28px 16px; text-align: center; font-size: var(--fs-s); color: var(--text-3); }

    /* Help: document list on the left, text at reading width on the right */
    .help-nav { width: 264px; overflow-y: auto; }
    .help-section { padding: 14px 12px 6px; }
    .help-item { display: block; width: 100%; padding: 7px 12px; border: 0; background: transparent; color: var(--text-2); font: inherit; text-align: left; cursor: pointer; }
    .help-item:hover { background: var(--hover); color: var(--text); }
    .help-item[aria-current="true"] { background: var(--raised); color: var(--text); }
    .help-item:focus-visible { outline: 1px solid var(--blue); outline-offset: -1px; }
    .help-body { flex: 1; min-width: 0; overflow-y: auto; padding: 28px 32px 56px; }
    .help-doc { margin-inline: auto; }
    .help-doc h1, .help-doc h2, .help-doc h3, .help-doc h4, .help-doc h5, .help-doc h6 { margin: 28px 0 8px; font-family: var(--font-ui); font-size: var(--fs-m); font-weight: 700; line-height: 1.4; }
    .help-doc h1 { margin-top: 0; padding-bottom: 8px; border-bottom: 1px solid var(--rule-strong); font-size: var(--fs-l); }
    .help-doc h2 { font-size: var(--fs-l); }
    .help-doc h4, .help-doc h5, .help-doc h6 { font-weight: 600; color: var(--text-2); }
    .help-doc p, .help-doc ul, .help-doc ol { margin: 0 0 12px; }
    .help-doc ul, .help-doc ol { padding: 0; }
    .help-doc ol li { list-style: decimal; }
    .help-doc a { color: var(--blue); text-decoration: underline; text-underline-offset: 2px; }
    .help-doc hr { margin: 24px 0; border: 0; border-top: 1px solid var(--rule); }
    .help-doc .codeblock { margin: 12px 0 16px; }
    .help-table { margin: 12px 0 16px; overflow-x: auto; }
    .help-table table { border-collapse: collapse; font-size: var(--fs-m); line-height: 1.5; }
    .help-table th, .help-table td { padding: 6px 12px 6px 0; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: top; }
    .help-table th { font-family: var(--font-ui); font-size: var(--fs-s); font-weight: 600; color: var(--text-3); white-space: nowrap; }
    .help-about { display: grid; grid-template-columns: max-content 1fr; gap: 10px 20px; margin: 0; }
    .help-about dt, .help-about dd { margin: 0; }

    .envelope-pane { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
    .envelope { flex: none; padding: 0 16px 14px; border-bottom: 1px solid var(--rule); }
    .envelope-top { display: flex; align-items: center; justify-content: space-between; height: 44px; }
    .env-grid { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 24px; }
    .env-grid .span2 { grid-column: 1 / -1; }
    .env-grid dt { margin-bottom: 3px; }
    .env-grid dd { margin: 0; color: var(--text); overflow-wrap: anywhere; display: flex; align-items: center; gap: 6px; }
    .env-grid dd.mono { font-size: var(--fs-s); }
    .envelope-pane .proof-head { padding-left: 16px; }
    .envelope-pane[data-has-patch="false"] > :not(.envelope) { display: none; }

    /* Dialogs */
    .modal-dialog { position: fixed; inset: 0; z-index: 50; display: flex; align-items: center; justify-content: center; padding: 16px; background: var(--scrim); }
    .modal-dialog:not(.hidden) .dialog { animation: rise 0.28s var(--ease-out); }
    .dialog { width: min(460px, 100%); max-height: calc(100vh - 32px); display: flex; flex-direction: column; border: 1px solid var(--rule-strong); border-radius: 4px; background: var(--raised); box-shadow: var(--shadow); }
    .dialog--wide { width: min(760px, 94vw); max-width: 760px !important; }

    /* Theme & Appearance (Plan 07) */
    .theme-schemes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 6px; }
    .scheme-card { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 8px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--plate); cursor: pointer; transition: border-color 0.15s, background 0.15s; text-align: center; font-size: var(--fs-s); color: var(--text-2); }
    .scheme-card:hover { border-color: var(--rule-strong); background: var(--hover); color: var(--text); }
    .scheme-card.is-active { border-color: var(--blue); background: var(--blue-bg); font-weight: 600; color: var(--blue); }
    .scheme-preview { width: 100%; height: 26px; border-radius: 2px; border: 1px solid var(--rule); display: flex; overflow: hidden; }
    .scheme-preview-ground { flex: 2; height: 100%; }
    .scheme-preview-accent { flex: 1; height: 100%; }
    .preset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 8px; margin-top: 6px; }
    .preset-card { display: flex; flex-direction: column; gap: 6px; padding: 10px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--plate); cursor: pointer; transition: border-color 0.15s, background 0.15s; font-size: var(--fs-s); }
    .preset-card:hover { border-color: var(--rule-strong); background: var(--hover); }
    .preset-card.is-active { border-color: var(--blue); background: var(--blue-bg); font-weight: 600; color: var(--blue); }
    .preset-swatches { display: flex; gap: 4px; align-items: center; }
    .preset-swatch { width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--rule-strong); }
    .custom-palette-editor { display: grid; gap: 12px; margin-top: 10px; padding: 12px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--plate); }
    .custom-palette-grid { display: grid; grid-template-columns: 160px 1fr; gap: 16px; align-items: center; }
    .custom-palette-fields { display: grid; gap: 8px; }
    .color-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: var(--fs-s); }
    .color-row label { color: var(--text-2); }
    .color-row.is-active { background: var(--blue-bg); border-radius: var(--radius); }
    .color-preview-box { width: 22px; height: 22px; border-radius: 3px; border: 1px solid var(--rule-strong); cursor: pointer; flex-shrink: 0; }
    .color-hex-input { width: 88px; font-family: var(--font-code); font-size: var(--fs-s); padding: 3px 6px; }
    .contrast-warning { color: var(--amber); font-size: var(--fs-s); display: flex; align-items: center; gap: 6px; }
    .color-wheel-canvas { border-radius: 50%; cursor: crosshair; }
    .dialog-head { display: flex; align-items: center; gap: 8px; height: 46px; padding: 0 8px 0 16px; border-bottom: 1px solid var(--rule); }
    .dialog-head h3 { margin: 0; flex: 1; font-size: var(--fs-m); font-weight: 700; }
    .dialog-head > svg { color: var(--text-3); }
    .dialog-body { display: grid; gap: 16px; padding: 16px; overflow-y: auto; }
    .dialog-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px; border-top: 1px solid var(--rule); }
    .field { display: grid; gap: 6px; }
    .field-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .field-help { font-size: var(--fs-s); color: var(--text-3); line-height: 1.5; }
    .field-help code { font-family: var(--font-code); color: var(--text-2); }
    .kv { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--plate); font-size: var(--fs-s); }
    .kv > div { display: flex; justify-content: space-between; gap: 12px; color: var(--text-3); }
    .kv > div > span:last-child { color: var(--text); font-family: var(--font-code); text-align: right; overflow-wrap: anywhere; }
    .kv-title { font-size: var(--fs-m); font-weight: 600; color: var(--text) !important; }
    .gpu-bar { height: 6px; border-radius: 3px; background: var(--rule); overflow: hidden; }
    .gpu-bar > div { height: 100%; width: 0; background: var(--blue); transition: width 0.4s var(--ease-out); }
    .row-split { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .engine { display: grid; gap: 2px; padding: 12px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--plate); }
    .engine-name { display: flex; align-items: center; gap: 8px; font-weight: 600; }
    .engine-status { padding-left: 15px; font-family: var(--font-code); font-size: var(--fs-s); color: var(--text-3); }
    .section { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--plate); }
    .section-title { display: flex; align-items: center; gap: 8px; font-weight: 600; }
    .status-line { font-size: var(--fs-s); color: var(--text-3); }

    /* Toast */
    .toast {
      position: fixed;
      right: 16px;
      top: 56px;
      z-index: 60;
      display: flex;
      align-items: center;
      gap: 8px;
      max-width: min(460px, calc(100vw - 32px));
      padding: 9px 12px;
      border: 1px solid var(--rule-strong);
      border-radius: var(--radius);
      background: var(--raised);
      box-shadow: var(--shadow);
      color: var(--text);
      font-size: var(--fs-s);
      opacity: 0;
      transform: translateY(-8px);
      pointer-events: none;
      transition: opacity 0.2s, transform 0.3s var(--ease-out);
    }
    .toast.is-visible { opacity: 1; transform: none; }
    .toast-mark { width: 8px; height: 8px; border-radius: 1px; background: var(--blue); flex: none; }
    .toast[data-tone="ok"] .toast-mark { background: var(--pass); }
    .toast[data-tone="warn"] .toast-mark { background: var(--amber); }
    .toast[data-tone="err"] .toast-mark { background: var(--red); }

    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes breathe { 50% { opacity: 0.45; } }
    @keyframes blink { to { visibility: hidden; } }
    @keyframes stamp { from { opacity: 0.4; transform: translateX(-8px); } to { opacity: 1; transform: none; } }
    @keyframes unroll { from { clip-path: inset(0 0 0 100%); } to { clip-path: inset(0 0 0 0); } }
    @keyframes rise { from { opacity: 0; transform: translateY(10px) scale(0.985); } to { opacity: 1; transform: none; } }

    @media (max-width: 1180px) {
      .ws-branch { display: none; }
      .is-native .brand span { display: none; }
      .is-native .topbar-left { margin-right: 8px; }
      .is-native .topbar-right { gap: 4px; }
      .is-native .machine > button { gap: 4px; padding: 0 8px; }
      .is-native .meter { width: 24px; }
      :root { --rail-w: 232px; --margin-w: 156px; }
      .composer-hint { display: none; }
      .transcript { padding-inline: 24px; }
      .composer-wrap { padding-inline: 24px; }
    }
    @media (max-width: 1100px) {
      #view-chat > .proof { position: absolute; top: 0; right: 0; bottom: 0; z-index: 20; width: min(440px, 72vw); box-shadow: var(--shadow); }
    }
    @media (max-width: 1040px) {
      .ws { display: none; }
      .machine-key { display: none; }
      .queue { width: 320px; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation: none !important; transition: none !important; }
    }
"""

# Washes behind marks and diff lines in the generated presets: (dark %, light %) of the base colour.
_PRESET_WASH = {
    "red-bg": ("red", 11, 9),
    "blue-bg": ("blue", 10, 9),
    "amber-bg": ("amber", 10, 10),
    "diff-add-bg": ("diff-add", 16, 12),
    "diff-del-bg": ("diff-del", 16, 12),
}


def _token_decls(tokens: dict[str, str]) -> str:
    return " ".join(
        f"--{name.replace('_', '-')}: {value};" for name, value in tokens.items()
    )


def _preset_blocks() -> str:
    blocks = []
    for preset, schemes in PRESET_TOKENS.items():
        if preset == "night-galley":
            continue  # the :root blocks above carry Night Galley
        for scheme, selector in (("dark", ".dark"), ("light", ":not(.dark)")):
            washes = " ".join(
                f"--{name}: color-mix(in srgb, var(--{base}) {dark if scheme == 'dark' else light}%, transparent);"
                for name, (base, dark, light) in _PRESET_WASH.items()
            )
            blocks.append(
                f'    :root[data-preset="{preset}"]{selector} {{ {_token_decls(schemes[scheme])} {washes} }}\n'
            )
    return "".join(blocks)


DESKTOP_CSS = (
    DESKTOP_CSS.replace(
        "/* @tokens night-galley dark */",
        _token_decls(PRESET_TOKENS["night-galley"]["dark"]),
    )
    .replace(
        "/* @tokens night-galley light */",
        _token_decls(PRESET_TOKENS["night-galley"]["light"]),
    )
    .replace("    /* @presets */\n", _preset_blocks())
)
