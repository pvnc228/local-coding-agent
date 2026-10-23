# Decision comps: prompts for Codex image generation

Generate one image per block below: landscape, 1536×1024 (or the nearest landscape size), high quality, opaque background.
Save each one to `.impeccable/mocks/decision/<file>`, using exactly the file name given. The decision page at http://127.0.0.1:52040/ checks for these files and shows each image on its card once it appears.
For blocks marked **refs**, attach both reference images (download them first if Codex needs local files).

Every prompt starts with the same shared paragraph, `COMMON`. Paste it, then the block's own text.

---

## COMMON (prepend to every prompt)

A desktop application screen for "Local AI Coding Harness", the Windows desktop app of Local Coding Agent. The developer chats with a coding model running on their own GPU, and a controller checks every patch the model proposes before it can be applied. Render the screen edge to edge in landscape, with crisp readable text and no browser window, device or desk around it.

Real content on screen. The top bar holds the mark and name "Local Harness", a model chip "deepseek-coder:6.7b-instruct-q8_0 · Ollama", "VRAM 1.7 / 8.0 GB", a green "Ollama online" dot, workspace "codex-local-coding-agent · main", tabs "Chat" and "Delegated 2", and a settings icon. The left sessions list shows: "Fix sliding window index — needs review", "Unit test tax logic — applied · 6 passed", "Refactor tax cents — rolled back", and "Add docstring to calc.py — from Claude Code via MCP". The open conversation reads: the user asks "Fix off-by-one error in sliding window index in utils/window.py". The model replies in two sentences ("The range stops one window short. Iterate to len(items) - size + 1.") and proposes a patch to utils/window.py (+2 −2), shown as a small unified diff. The controller's verdict on that proposal: Scope — inside allowlist (utils/window.py), Lint — passed, Checks — not run (python -m pytest tests/test_window.py -q). "Apply with checks" is the primary action and "Rollback" is idle. At the bottom is a composer with a Chat / Build / Plan / Auto switch (Build selected) and a Run button. The diff panel exists only because there is a proposal.

---

## 1. `assigned.png` (Night Galley)

Leads with the conversation; the patch sits inline in the model's reply as a galley strip. Visual rules: a night proofing room. The ground is blue-black press ink and the type is bone white. The model's replies are typeset like galley copy in a calm text serif. The user's words and all interface labels are set in a compact grotesque, with small caps for labels. The signature is a narrow proof margin beside each reply. There the controller's verdicts sit as proofreader's marks, aligned to the exact patch lines they judge: red for refusals and deletions, non-photo blue for notes. A single pass-green is reserved for the Apply action. Each session carries a galley number such as "G-014" in the list and in the reply header.

## 2. `model-pick.png` (Interlocking)

Leads with the conversation; the diff opens as a right panel. Visual rules: a railway signal box at night. The ground is deep green-black enamel, the lettering is cream in a sturdy engineering sans, and there are signal red, amber and green lamps. The signature is a route strip above the composer, drawn as track segments with signal lamps: Proposal, Scope, Lint, Checks, Applied. Lamps are green where the controller has confirmed the step, amber at Checks (not run), and dark at Applied. The Apply route clears only when every lamp is green.

## 3. `challenger-transit.png` (Night Line), **refs**

References: https://impeccable.style/worlds/cards/wayfinding-cartography-signage-midnight-transit-diagram.webp and https://impeccable.style/worlds/cards/wayfinding-cartography-signage-midnight-transit-diagram-hero.webp

Use the attached images as style inspiration adapted into a real working product, and do not copy their layout or content. Leads with the session's lifecycle: one coloured transit line runs across the top of the transcript with the stations Proposal → Scope → Lint → Checks → Applied. Stations are filled where the controller confirmed the step, Checks is a hollow ring, and Applied is unreached. The diff panel sits on the right. The sessions list shows each session as a short line segment in its own colour.

## 4. `challenger-spectral.png` (Emission Rail), **refs**

References: https://impeccable.style/worlds/cards/operate-c-emission-line-rail.webp and https://impeccable.style/worlds/cards/operate-c-emission-line-rail-hero.webp

Use the attached images as style inspiration adapted into a real working product, and do not copy their layout or content. Leads with evidence: an off-centre vertical rail runs the height of the window between the transcript and the diff. Every gate and telemetry value is a thin mark on that rail, and state is shown by line form, not colour: solid for verified (Scope, Lint), dashed for pending (Checks), struck through for refused. The VRAM reading and tokens per second also hang off the rail.

## 5. `canon.png` (category standard)

Leads with the chat panel; the diff opens as an editor tab beside it. Visual rules: the best of today's editor-style AI chat panels, done with full craft. Neutral dark grey chrome, light grey text, a single blue accent and a standard system UI font. The layout has an activity bar, tabs and an inline diff with green and red lines. There is no signature beyond doing the standard perfectly.
