---
version: 1
slug: "local-coding-agent-desktop-components-py"
primary_target: "local_coding_agent/desktop/components.py"
related_targets: ["local_coding_agent/desktop/client_js.py","local_coding_agent/desktop/styles.py"]
---

# Desktop harness (app shell, Chat + Delegated)

Scope: the whole desktop harness page served at `/app` (header, sessions rail, Chat view, Delegated view, modals, toast). Visitor mode: Operate.

Audience and job: developers who run local models on their own GPU and chat with one directly to get small patches. They read the reply, check the proposal and apply it with checks or roll it back. Used in the evening or at night next to a dark IDE: dark is the base theme and light is a secondary theme of equal quality.

Constraints: offline (CSP 'self', no remote fonts); English UI; DOM ids that tests use stay; CLI parity; never show a check as passed without runner evidence. The stack may change.

Things that must not happen: a ChatGPT-clone bubble chat, a cyberpunk/neon hacker costume, toy-like rounded consumer styling.

## Direction contract

THESIS: The local model sets copy; the controller proofs it; the human passes it for press. Refuses the category default of chat bubbles and a permanent empty diff pane.

OWN-WORLD: Blue-black press-ink ground, bone type. Model replies are set in a text serif (Sitka/Charter/Georgia); the human and the interface use a compact grotesque (Bahnschrift/DIN), with small-caps labels; code is in Cascadia Mono. Proof red marks refusals and deletions, non-photo blue marks notes and the model's proposals, and a single pass-green appears only on Apply and on a verified apply. Hairline rules, square corners, three type sizes.

STORY: The visitor writes an instruction, reads the reply as typeset copy, sees exactly which patch lines the controller marked and why, and then applies with checks or rolls back, knowing what was proven and what was not.

FIRST VIEWPORT: A top strip with the mark, model/VRAM/server state, Chat/Delegated tabs, workspace, theme and settings. Left: a rail of numbered galleys (G-xxx) with fixed furniture (number, title, model, verdict line). Centre: a galley column. User instructions sit as compact sans slugs; replies are serif copy with a 168px proof margin on the right that carries the controller's marks (scope, lint, checks) aligned to the patch strip inline in the reply. The diff panel opens on the right only when a proposal exists; the composer and mode switch are docked at the bottom.

FORM: Night Galley, position 3 on the ordered list (1 Interlocking, 2 Darkroom contact sheet, 3 Galley proofs, 4 Lab notebook, 5 Swiss technical manual, 6 Correspondence-chess scoresheet, 7 Notarial seal). seed:23ac0401ed6daea1. Raises: galley numbers (Catalog Sleeve); three sizes ranked by weight, case and reversal (Timetable Rack); pass-green only on Apply (Warm Consumer App); fixed session furniture (Character Catalog).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
