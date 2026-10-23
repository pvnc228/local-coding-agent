---
name: Local AI Coding Harness
description: Night Galley. The local model sets copy, the controller proofs it in the margin, the human passes it for press.
colors:
  ground: "#0e1117"
  plate: "#12161e"
  raised: "#191e28"
  hover: "#212734"
  rule: "#252b37"
  rule-strong: "#353d4c"
  bone: "#e8e3d6"
  bone-2: "#b6b1a4"
  bone-3: "#8d897d"
  proof-red: "#ec6a53"
  proof-red-wash: "rgba(236, 106, 83, 0.11)"
  proof-blue: "#8dbcdc"
  proof-blue-wash: "rgba(141, 188, 220, 0.10)"
  wait-amber: "#dca84d"
  wait-amber-wash: "rgba(220, 168, 77, 0.10)"
  pass-green: "#a2d38c"
  pass-ink: "#0f1a0b"
  scrim: "rgba(5, 7, 10, 0.68)"
  ground-light: "#f3f0e8"
  plate-light: "#ebe7dd"
  raised-light: "#fbf9f4"
  hover-light: "#e2ddd1"
  rule-light: "#d9d3c5"
  rule-strong-light: "#c2bba9"
  ink-light: "#1b1e25"
  ink-2-light: "#45474d"
  ink-3-light: "#66655f"
  proof-red-light: "#b0381f"
  proof-red-wash-light: "rgba(176, 56, 31, 0.09)"
  proof-blue-light: "#2b6896"
  proof-blue-wash-light: "rgba(43, 104, 150, 0.09)"
  wait-amber-light: "#8b5f10"
  wait-amber-wash-light: "rgba(139, 95, 16, 0.10)"
  pass-green-light: "#3a7829"
  pass-ink-light: "#f6fbf2"
  scrim-light: "rgba(30, 26, 18, 0.36)"
typography:
  copy:
    fontFamily: "\"Sitka Text\", \"Iowan Old Style\", \"Charter\", \"Georgia\", serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.62
  title:
    fontFamily: "\"Bahnschrift\", \"DIN Alternate\", \"Barlow\", \"Segoe UI\", system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1.45
  body:
    fontFamily: "\"Bahnschrift\", \"DIN Alternate\", \"Barlow\", \"Segoe UI\", system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  control:
    fontFamily: "\"Bahnschrift\", \"DIN Alternate\", \"Barlow\", \"Segoe UI\", system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "0.02em"
  label:
    fontFamily: "\"Bahnschrift\", \"DIN Alternate\", \"Barlow\", \"Segoe UI\", system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "0.06em"
  code:
    fontFamily: "\"Cascadia Mono\", \"Cascadia Code\", \"JetBrains Mono\", \"Consolas\", ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  hairline: "2px"
  sm: "3px"
  md: "4px"
spacing:
  "4": "4px"
  "6": "6px"
  "8": "8px"
  "10": "10px"
  "12": "12px"
  "16": "16px"
  "24": "24px"
  "32": "32px"
components:
  button:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.bone}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "28px"
  button-hover:
    backgroundColor: "{colors.hover}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.bone-2}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "28px"
  button-primary:
    backgroundColor: "{colors.bone}"
    textColor: "{colors.ground}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "28px"
  button-pass:
    backgroundColor: "{colors.pass-green}"
    textColor: "{colors.pass-ink}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "28px"
  button-pass-disabled:
    backgroundColor: "transparent"
    textColor: "{colors.bone-3}"
  button-danger:
    backgroundColor: "{colors.proof-red-wash}"
    textColor: "{colors.proof-red}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "28px"
  input:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.bone}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "30px"
  tag:
    backgroundColor: "transparent"
    textColor: "{colors.bone-3}"
    typography: "{typography.label}"
    rounded: "{rounded.hairline}"
    padding: "0 6px"
    height: "20px"
  segmented-option-pressed:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.bone}"
    typography: "{typography.control}"
    rounded: "{rounded.hairline}"
    padding: "0 10px"
    height: "22px"
  galley-entry:
    backgroundColor: "transparent"
    textColor: "{colors.bone}"
    typography: "{typography.body}"
    padding: "10px 12px 11px"
  galley-entry-current:
    backgroundColor: "{colors.raised}"
  composer:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.bone}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
  proof-panel:
    backgroundColor: "{colors.plate}"
    width: "clamp(380px, 34vw, 580px)"
  dialog:
    backgroundColor: "{colors.raised}"
    rounded: "{rounded.md}"
    width: "min(460px, 100%)"
---

# Design System: Local AI Coding Harness

## Overview

**Creative North Star: "Night Galley"**

The harness is a night-shift proof room. The local model sets copy; the controller proofs it in the margin; the human passes it for press. Model replies are typeset as serif copy, not chat bubbles. Each reply sits in a galley with a fixed proof margin on its right where the controller's marks land, and the diff opens as a proof sheet only when there is something to proof.

Density is that of an operator's tool: three type sizes (12/14/16), 28px controls, hairline rules, near-square corners, and a blue-black press-ink ground with bone type. Dark is the base theme (`:root.dark`); light (`:root:not(.dark)`) is a full warm-paper counterpart with the same roles, not a derived afterthought. Colour carries meaning, never decoration: four mark colours, each with one job, and pass-green held back until something has actually passed.

Confirmed rejections from the surface brief: no bubble chat, no neon or cyberpunk costume, no toy-like rounded consumer styling.

**Key Characteristics:**
- Serif copy for the model, compact grotesque for the human and the interface, mono for code and identifiers.
- A 184px proof margin beside every reply galley carries the controller's marks.
- Four mark colours with fixed meanings; pass-green only on Apply and runner-verified evidence.
- Flat tonal layering (ground / plate / raised) separated by 1px rules; shadow only on floating layers.
- Galleys carry a number derived from the real session or task id (`G·XXXX`), never a counter.

## Colors

A blue-black ink and bone-paper palette whose only saturated colours are proofreading marks.

### Primary
- **Bone** ({colors.bone}, light: {colors.ink-light}): body text, and inverted as the fill of the primary button (Send, Queue task, Deep drive scan). The interface's main voice is ink, not a brand hue.

### Secondary
- **Non-photo Blue** ({colors.proof-blue}, light: {colors.proof-blue-light}): the model's proposals and the controller's notes. Added diff lines (on its wash), hunk headers, `+N` stats, plan step numbers, the active galley number, unread counts, focus rings, input focus borders, caret, selection tint, links.
- **Proof Red** ({colors.proof-red}, light: {colors.proof-red-light}): what the controller refused and what a patch deletes. Deleted diff lines (on its wash, struck through), `−N` stats, failed checks, plan risks, task errors, the danger button (Unload VRAM).
- **Wait Amber** ({colors.wait-amber}, light: {colors.wait-amber-light}): waiting, not failing. "Checks not run" marks, pending evidence, ready-for-review and running verdicts, notices, hot GPU meters, the breathing wait dot.

### Tertiary
- **Pass Green** ({colors.pass-green} on {colors.pass-ink}, light: {colors.pass-green-light} on {colors.pass-ink-light}): the Apply button and evidence an external runner produced (`data-state="verified"`, "Checks passed" mark, verified verdict, ok toast). Nowhere else.

### Neutral
- **Press Ink** ({colors.ground}, light: {colors.ground-light}): page ground, transcript, composer dock, inputs, proof footer.
- **Plate** ({colors.plate}, light: {colors.plate-light}): top strip, sessions rail, proof panel, queue, code blocks, patch strip, plan and section boxes.
- **Raised** ({colors.raised}, light: {colors.raised-light}): buttons, composer, current galley entry, pressed segment, dialogs, toast.
- **Hover** ({colors.hover}, light: {colors.hover-light}): hover fill for buttons, rail entries, machine-strip cells.
- **Rule / Rule Strong** ({colors.rule} / {colors.rule-strong}; light {colors.rule-light} / {colors.rule-strong-light}): 1px dividers between regions; the stronger rule frames controls, code, patches and the top of each instruction slug.
- **Bone 2 / Bone 3** ({colors.bone-2} / {colors.bone-3}; light {colors.ink-2-light} / {colors.ink-3-light}): secondary text and metadata; labels, timestamps, line numbers, placeholders.

### Named Rules
**The Four Marks Rule.** Blue is proposal or note, red is refusal or deletion, amber is checks not run or waiting, green is passed. A colour never borrows another's meaning; a neutral state uses Bone 3, not a fifth hue.

**The Earned Green Rule.** Pass-green appears only on the Apply button and on evidence a runner confirmed. A disabled Apply drops to a transparent outline in Bone 3, so green is never shown before it is possible.

## Typography

**Copy Font:** Sitka Text (with Iowan Old Style, Charter, Georgia, serif)
**UI Font:** Bahnschrift (with DIN Alternate, Barlow, Segoe UI, system-ui)
**Code Font:** Cascadia Mono (with Cascadia Code, JetBrains Mono, Consolas, ui-monospace)

**Character:** A text serif for what the model wrote, set apart from the condensed engineering grotesque the human and the controller speak in; mono for every path, id, hash and diff.

All three are Windows system faces referenced by name, not self-hosted (the CSP forbids remote fonts and no font files ship in `/assets`). Off Windows the stacks fall back to Charter/Georgia, Barlow/Segoe/system-ui and JetBrains Mono/Consolas/monospace, which changes the look noticeably. This is a known gap, not a design choice.

### Hierarchy
- **Copy** (400, 16px, 1.62, max 68ch): model replies, the "setting" status line while a reply is being set, and the welcome paragraph. Inline code inside copy drops to mono 14px on a plate chip.
- **Title** (700, 14px): pane titles (Delegated queue), dialog headings, the brand name. Rank comes from weight, not size.
- **Body** (400, 14px, 1.45): instructions (slugs), galley entry titles (500, two-line clamp), task goals, form text.
- **Control** (600, 12px, 0.02em): buttons and segmented options.
- **Label** (600, 12px, 0.06em, uppercase): field labels, tags, machine-strip keys. Margin mark titles use the same treatment at 0.04em.
- **Code** (400, 12px, 1.6): diffs, code blocks, galley numbers, ids, file paths, kbd hints.

### Named Rules
**The Three Sizes Rule.** Only 12, 14 and 16px exist (`--fs-s`, `--fs-m`, `--fs-l`). Hierarchy is made with weight (400/500/600/700), case and colour, never a fourth size.

**The Tracked Caps Rule.** Labels are uppercase with letter-spacing, not true small caps; keep them at 12px and Bone 3 so they read as furniture, not headings.

## Layout

A full-height app shell: a 44px top strip, then a row of rail / galley column / proof panel. No page scroll; each column scrolls on its own.

- **Top strip** (44px, plate): mark and name, sessions toggle, Chat/Delegated tabs, the machine strip (model, VRAM meter, server state as segmented cells), workspace and branch in mono, theme and settings.
- **Sessions rail** (264px; 232px at ≤1180px): a list of galley entries with fixed furniture in a three-column grid: galley number, kind, time; title on its own row; model meta and verdict below. Toggle with Ctrl+B.
- **Galley column**: transcript centred at an 880px measure, padding 32px (24px at ≤1180px), 30px between items. Each item is a two-column grid: content plus a **184px proof margin** (156px at ≤1180px) with a 24px gutter. Instruction slugs use the same grid with an empty margin and a strong top rule.
- **Composer dock**: bottom of the galley column, same 880px measure, mode switch (Chat / Build / Plan / Auto) and primary Send in its bar.
- **Proof panel** (`clamp(380px, 34vw, 580px)`): appears only when a patch exists. At ≤1100px the Chat proof panel becomes a drawer over the galley column (`min(440px, 72vw)`, with shadow). In Delegated, the envelope pane hides its proof blocks while `data-has-patch="false"`.
- **Delegated view**: 380px queue (320px at ≤1040px) beside the envelope pane, which carries a two-column definition grid of the task envelope above the proof blocks.
- **Container queries** keep the galley honest in narrow columns: below 640px the margin folds under the content as a wrapping row; below 760px and 520px the composer drops its hint and mode note; below 460px the proof footer wraps.
- **Spacing rhythm**: 4 / 6 / 8 / 10 / 12 / 16 / 24 / 32px, with 8-12px inside components and 24-32px between regions.

### Named Rules
**The Earned Panel Rule.** The diff panel is never a permanent empty pane. It opens only when a proposal exists; while it is open the inline patch strip in the reply collapses to its head so the diff is never shown twice.

## Elevation & Depth

Flat by default. Depth is tonal: ground, plate and raised steps separated by 1px rules. Shadow exists only for layers that float over content.

### Shadow Vocabulary
- **Float** (dark `0 18px 40px -12px rgba(0,0,0,0.65), 0 2px 8px rgba(0,0,0,0.35)`; light `0 18px 40px -14px rgba(40,34,20,0.28), 0 2px 8px rgba(40,34,20,0.08)`): dialogs, the toast, and the proof drawer at ≤1100px. Nothing else.
- **Pressed inset** (`inset 0 0 0 1px` rule-strong): the pressed option of a segmented control.

### Named Rules
**The Floating-Only Shadow Rule.** If it does not cover other content, it does not cast a shadow. Separate in-flow regions with a rule or a tonal step.

## Shapes

Near-square, hairline-framed forms. Controls, code blocks, patch strips, notices and sections use a 3px corner; tags, kbd, counts, inline code and segment options 2px; the composer and dialogs 4px. Only status dots are round. Borders are always 1px; the selected tab is a 2px bone underline, the instruction slug a strong top rule, the proof margin a left rule.

## Components

### Buttons
Compact, firm, typographic.
- **Shape:** 3px corner, 28px tall, 0 10px padding, 1px rule-strong border; 12px 600 control type, icon gap 6px. Icon-only buttons are 28px square.
- **Default:** raised fill, bone text. Hover: hover fill, Bone 3 border.
- **Quiet:** transparent, Bone 2 text; hover fills. Used for toolbar icons, Copy and modal close.
- **Primary:** inverted, bone fill with ground text (Send, Queue task, Deep drive scan). Hover mixes 14% ground into the fill.
- **Pass:** pass-green fill, pass-ink text; only Apply (`#btnApply`, `#btnDelegatedApply`). Disabled: transparent with rule-strong border and Bone 3 text at full opacity.
- **Danger:** red wash fill, red text, 45% red border; only Unload VRAM.
- **Disabled (others):** Bone 3 text, 0.6 opacity, not-allowed cursor.
- **Shortcut hints:** kbd chips (mono 12px, 2px corner) take `currentColor` inside buttons.
- **Focus:** a 2px blue outline offset 2px, on `:focus-visible` only.

### Segmented Control
- **Style:** plate track with 1px rule and 2px padding; 22px options, Bone 2 text.
- **Pressed:** raised fill, bone text, inset rule-strong ring. Used for the session filter (All / User / Agent) and the composer mode switch.

### Tags and Verdicts
- **Tag:** 20px, 2px corner, 1px rule-strong border, uppercase label type in Bone 3. Tone variants recolour text and border (45% mix) with the four mark colours.
- **Verdict line** in a galley entry: 12px 600 text coloured by tone: pass only when evidence is verified; red for failed, rejected, error; amber for review, ready, pending, running, queued; blue for answered or planned; Bone 3 otherwise.

### Inputs / Fields
- **Style:** ground fill inside a 1px rule-strong frame, 3px corner, 30px tall, 12px text; textareas 14px with 8px 10px padding. Blue caret.
- **Focus:** border turns blue; no glow.
- **Field help:** 12px Bone 3, inline code in mono Bone 2.

### Navigation
- **Tabs:** 14px 600 Bone 2 in the top strip; selected is bone with a 2px bone underline inset 8px. Unread counts sit in a blue-wash mono chip.
- **Machine strip:** joined cells divided by 1px rules; uppercase key in Bone 3 (hidden at ≤1040px), mono value in bone.

### Galley Entry (signature)
Fixed furniture for every session: mono galley number (`G·` plus the last four characters of the real id; blue when current), kind, time; a two-line title; mono model meta; verdict. Hover fills; current is raised.

### Galley and Proof Margin (signature)
Each reply is a galley: a mono header line with galley number and model, serif copy, then optional plan box and inline patch strip, each row with its own cell in the proof margin. Marks are a 14px icon plus an uppercase tone-coloured title and 12px body:
- Blue: Note (model reasoning), Proposed (file and +/− stats), Allowlist (scoped files), Files to modify.
- Red: Not completed, Risk, Checks failed.
- Amber: Checks not run ("Apply runs the targeted checks."), No reply.
- Pass: Checks passed (external runner).
New galleys stamp their marks in from the left, staggered by 90ms.

### Patch Strip and Proof Panel (signature)
- **Patch strip:** plate box, 3px corner, 34px head with mono file name, blue/red stats and an Open patch button; diff below. Collapses to its head while the proof panel is open.
- **Diff:** mono 12px/1.6 in a 42px line-number / 18px sign / code grid. Additions on blue wash with a blue sign; deletions on red wash with a red sign and a 60%-red strike-through; hunk headers blue.
- **Proof panel:** plate column, 44px head (file, stats, close), scrolling body, ground footer with evidence status and actions (Copy, Rollback, Apply). Unrolls from the right edge with a clip-path reveal (0.42s).
- **Evidence status** (`data-state`): `none` Bone 3 with a hollow circle or shield; `pending` amber with a dashed circle; `verified` green with a check. Only `verified` may be green.

### Composer
Raised box, 4px corner, rule-strong border turning blue on focus-within; auto-growing textarea (46-220px), bar with mode switch, routed-mode note, hint (Enter to run, Shift+Enter for a new line) and primary Send.

### Dialogs and Toast
- **Dialog:** scrim over the app, raised panel 460px max, 4px corner, float shadow, 46px head with a 14px 700 title, 16px body gap, footer actions right-aligned. Rises in over 0.28s. Escape closes.
- **Toast:** top right below the strip, raised with float shadow, 12px text, an 8px square tone mark (blue info, green ok, amber warn, red error).

### Keyboard
Ctrl+Enter applies the proposal when focus is outside a field; Ctrl+N starts a new session; Ctrl+B toggles the sessions rail; Escape closes dialogs. New session and Apply print their hotkey as a kbd hint; the sidebar toggle carries it in its title.

## Do's and Don'ts

### Do:
- **Do** set every model reply in the copy serif at 16px/1.62 within 68ch, and every instruction in the UI grotesque at 14px.
- **Do** put the controller's findings in the 184px proof margin as tone-coloured marks, using the four mark colours by their fixed meaning.
- **Do** keep pass-green for the Apply button and runner-verified evidence only; show unverified states as amber or Bone 3.
- **Do** open the proof panel only when a patch exists, and collapse the inline patch strip to its head while it is open.
- **Do** derive galley numbers from real session or task ids (`G·XXXX`).
- **Do** separate regions with 1px rules and tonal steps; give shadow only to floating layers.
- **Do** keep the test-pinned DOM ids and copy (`tests/test_desktop_app.py`, `tests/test_desktop_release_gate.py`) when restyling.
- **Do** design both themes together; every token has a dark and a light value.

### Don't:
- **Don't** render the conversation as chat bubbles or keep a permanently empty diff pane.
- **Don't** add a fourth type size; rank with weight, case and colour.
- **Don't** use green for success-looking states that no runner confirmed, or any mark colour as decoration.
- **Don't** round controls past 4px or use pill shapes; only status dots are circular.
- **Don't** reach for neon, glow or cyberpunk effects, or toy-like rounded consumer styling.
- **Don't** load remote fonts or assets; the CSP is `default-src 'self'`.
