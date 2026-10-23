"""Modular HTML components for the Desktop AI Coding Harness."""

from __future__ import annotations

from .. import __version__


def _with_version(html: str) -> str:
    return html.replace("%VERSION%", __version__)


def render_header() -> str:
    return """
  <header class="topbar">
    <div class="topbar-left">
      <button onclick="toggleSidebar()" class="btn btn--quiet icon-btn" title="Toggle sessions (Ctrl+B)" aria-label="Toggle sessions">
        <i data-lucide="panel-left" class="i"></i>
      </button>
      <div class="brand">
        <svg class="brand-mark" viewBox="0 0 18 18" aria-hidden="true">
          <rect x="0.75" y="0.75" width="16.5" height="16.5" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
          <path d="M4.5 12.5 9 5.5l4.5 7" fill="none" stroke="var(--blue)" stroke-width="1.8" stroke-linejoin="round"/>
        </svg>
        <span>Local Harness</span>
      </div>
      <nav class="tabs" role="tablist" aria-label="Views">
        <button onclick="switchTab('chat')" id="tab-btn-chat" class="tab-btn tab" role="tab" aria-selected="true" aria-controls="view-chat">
          <i data-lucide="message-square-text" class="i"></i>
          <span>Interactive Chat</span>
        </button>
        <button onclick="switchTab('delegated')" id="tab-btn-delegated" class="tab-btn tab" role="tab" aria-selected="false" aria-controls="view-delegated">
          <i data-lucide="inbox" class="i"></i>
          <span>Delegated Tasks</span>
          <span class="count hidden" id="delegatedBadgeCount" title="Queued or running">0</span>
        </button>
      </nav>
    </div>

    <div class="topbar-right">
      <div class="machine" role="group" aria-label="Local machine">
        <button onclick="openModal('modelModal')" title="Switch model profile">
          <span class="machine-key" id="backendLabel">OLLAMA</span>
          <span class="val" id="telemetryModel">qwen2.5-coder</span>
        </button>
        <button onclick="openModal('gpuModal')" title="GPU and VRAM">
          <span class="machine-key">VRAM</span>
          <span class="val num" id="telemetryVram">&mdash;</span>
          <span class="meter" id="topVramMeter"><span id="topVramBar"></span></span>
        </button>
        <button onclick="openModal('serverModal')" title="Local inference servers">
          <span class="dot dot--wait" id="serverLiveDot"></span>
          <span id="serverLiveText">Checking&hellip;</span>
        </button>
      </div>

      <div class="ws" title="Workspace and branch">
        <i data-lucide="git-branch" class="i-s"></i>
        <span id="workspaceName">workspace</span>
        <span class="ws-branch" id="workspaceBranch"></span>
      </div>
      <button onclick="toggleTheme()" class="btn btn--quiet icon-btn" title="Toggle light or dark theme" aria-label="Toggle theme">
        <i data-lucide="sun-medium" id="themeIconSun" class="i theme-sun"></i>
        <i data-lucide="moon" id="themeIconMoon" class="i theme-moon"></i>
      </button>
      <button onclick="openModal('settingsModal')" class="btn btn--quiet icon-btn" title="Preferences, model scanner and doctor" aria-label="Settings">
        <i data-lucide="settings" class="i"></i>
      </button>
    </div>
  </header>
"""


def render_sidebar() -> str:
    _HTML = """
    <aside id="sessionSidebar" class="rail" aria-label="Sessions">
      <div class="rail-head">
        <button onclick="startNewSession()" class="btn new-session">
          <i data-lucide="plus" class="i-s"></i>
          <span>New session</span>
          <kbd>Ctrl+N</kbd>
        </button>
        <div class="seg seg--full" role="group" aria-label="Filter sessions">
          <button data-session-filter="all" onclick="filterSessions('all', event)" class="filter-chip" aria-pressed="true">All</button>
          <button data-session-filter="user" onclick="filterSessions('user', event)" class="filter-chip" aria-pressed="false">User</button>
          <button data-session-filter="agent" onclick="filterSessions('agent', event)" class="filter-chip" aria-pressed="false">Agent</button>
        </div>
      </div>

      <div class="rail-list" id="sessionList"></div>

      <div class="rail-foot">
        <span id="sessionCounter" class="num">0 sessions</span>
        <span class="num">v%VERSION%</span>
      </div>
    </aside>
"""
    return _with_version(_HTML)


def render_chat_panel() -> str:
    return """
    <section id="view-chat" class="tab-view view" role="tabpanel" aria-labelledby="tab-btn-chat">
      <div class="chat">
        <div id="chatMessages" class="transcript">
          <div class="welcome">
            <p class="welcome-copy">Write an instruction below. The local model proposes a patch, the controller proofs it in the margin, and nothing counts as tested until an external runner says so.</p>
            <dl class="legend">
              <div class="mark--blue"><dt></dt><dd><b>Blue</b> marks the model's proposal and the controller's notes.</dd></div>
              <div class="mark--red"><dt></dt><dd><b>Red</b> marks what the controller refused, and deleted lines.</dd></div>
              <div class="mark--amber"><dt></dt><dd><b>Amber</b> means waiting: checks have not run yet.</dd></div>
              <div class="mark--pass"><dt></dt><dd><b>Green</b> appears only for Apply and for evidence from a test runner.</dd></div>
            </dl>
            <div class="engine-line">
              <span>Engine</span>
              <span class="mono" id="welcomeModelLabel">qwen2.5-coder</span>
              <span>&middot;</span>
              <span id="welcomeModeLabel">Mode: auto</span>
              <span>&middot;</span>
              <button onclick="openModal('modelModal')" class="link">Change model</button>
            </div>
            <div class="presets">
              <span class="label">Try</span>
              <button onclick="setPromptAndRun('Fix off-by-one error in sliding window index')" class="btn">
                <i data-lucide="zap" class="i-s"></i> Fix sliding window
              </button>
              <button onclick="setPromptAndRun('Write pytest unit tests for tax calculation')" class="btn">
                <i data-lucide="flask-conical" class="i-s"></i> Unit test tax logic
              </button>
              <button onclick="setPromptAndRun('Refactor calculate_total to use integer cents')" class="btn">
                <i data-lucide="wrench" class="i-s"></i> Refactor tax cents
              </button>
            </div>
          </div>
        </div>

        <div class="composer-wrap">
          <div class="composer">
            <textarea id="chatInput" rows="1" placeholder="Tell the local model what to fix, refactor or test" aria-label="Instruction for the local model"
              onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();handleUserSubmit();}"></textarea>
            <div class="composer-bar">
              <div class="seg" role="group" aria-label="Mode">
                <button onclick="setMode('chat', this)" id="mode-btn-chat" class="mode-btn" aria-pressed="false">Chat</button>
                <button onclick="setMode('build', this)" id="mode-btn-build" class="mode-btn" aria-pressed="false">Build</button>
                <button onclick="setMode('plan', this)" id="mode-btn-plan" class="mode-btn" aria-pressed="false">Plan</button>
                <button onclick="setMode('hybrid', this)" id="mode-btn-hybrid" class="mode-btn" aria-pressed="true">Auto</button>
              </div>
              <span class="mode-note" id="modeBadge">Auto picks chat, plan or build per prompt</span>
              <span class="composer-hint">Enter to run &middot; Shift+Enter for a new line</span>
              <button onclick="handleUserSubmit()" id="btnSendChat" class="btn btn--primary">
                <span>Run</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <aside id="diffPanel" class="proof hidden" aria-label="Proposed patch">
        <div class="proof-head">
          <div class="proof-title">
            <i data-lucide="file-diff" class="i-s"></i>
            <span class="proof-file" id="diffFileName">No active diff</span>
            <span class="proof-stats" id="diffStatsTag">No proposal</span>
          </div>
          <button onclick="showDiffPanel(false)" class="btn btn--quiet icon-btn" title="Close patch" aria-label="Close patch">
            <i data-lucide="x" class="i"></i>
          </button>
        </div>

        <div id="diffContentArea" class="proof-body">
          <div class="diff-empty">No active diff proposal. Run a prompt or select a task session.</div>
        </div>

        <div class="proof-foot">
          <div class="evidence" id="diffEvidenceStatus" data-state="none">
            <i data-lucide="circle" class="i-s ev-none"></i>
            <i data-lucide="circle-dashed" class="i-s ev-wait"></i>
            <i data-lucide="circle-check" class="i-s ev-ok"></i>
            <span>Oracles: Not run</span>
          </div>
          <div class="proof-actions">
            <button onclick="copyActiveDiff()" class="btn btn--quiet" title="Copy unified diff">
              <i data-lucide="copy" class="i-s"></i> Copy
            </button>
            <button onclick="rollbackAction()" id="btnRollback" disabled class="btn">
              <i data-lucide="rotate-ccw" class="i-s"></i> Rollback
            </button>
            <button onclick="applyProposalAction()" id="btnApply" disabled class="btn btn--pass" title="Apply with targeted checks (Ctrl+Enter)">
              <i data-lucide="check" class="i-s"></i> Apply <kbd>Ctrl+Enter</kbd>
            </button>
          </div>
        </div>
      </aside>
    </section>
"""


def render_delegated_panel() -> str:
    return """
    <section id="view-delegated" class="tab-view view hidden" role="tabpanel" aria-labelledby="tab-btn-delegated">
      <div class="queue">
        <div class="pane-head">
          <h2 class="pane-title">Background queue</h2>
          <span class="pane-note">Runs one task at a time</span>
        </div>
        <form id="taskQueueForm" onsubmit="submitQueuedTask(event)" class="queue-form">
          <textarea id="taskQueueGoal" rows="3" class="input" placeholder="Describe one atomic coding task to delegate"></textarea>
          <input id="taskQueueFiles" type="text" class="input mono" placeholder="Files, comma-separated">
          <input id="taskQueueChecks" type="text" class="input mono" placeholder="Checks, comma-separated commands">
          <div class="queue-form-row">
            <input id="taskQueueProfile" type="text" class="input mono" placeholder="Profile (default)">
            <button type="submit" id="btnSubmitTask" class="btn btn--primary">Queue task</button>
          </div>
        </form>
        <div id="taskQueueList" class="queue-list"></div>
      </div>

      <div class="envelope-pane" id="delegatedPane" data-has-patch="false">
        <div class="envelope">
          <div class="envelope-top">
            <h2 class="pane-title">Task envelope</h2>
            <span class="tag" id="delegatedStatusTag">NO PROPOSAL</span>
          </div>
          <dl class="env-grid">
            <div>
              <dt class="label">Delegating host agent</dt>
              <dd id="delegatedAgent"><i data-lucide="bot" class="i-s"></i><span>&mdash;</span></dd>
            </div>
            <div>
              <dt class="label">Task ID</dt>
              <dd class="mono" id="delegatedTaskId">None</dd>
            </div>
            <div class="span2">
              <dt class="label">Goal</dt>
              <dd id="delegatedGoal">No delegated task selected.</dd>
            </div>
            <div>
              <dt class="label">Allowlisted files</dt>
              <dd class="mono" id="delegatedFiles">&mdash;</dd>
            </div>
            <div>
              <dt class="label">Targeted checks</dt>
              <dd class="mono" id="delegatedChecks">Not selected</dd>
            </div>
          </dl>
        </div>

        <div class="proof-head">
          <div class="proof-title">
            <i data-lucide="file-diff" class="i-s"></i>
            <span class="proof-file" id="delegatedFileName">No proposal selected</span>
            <span class="proof-stats" id="delegatedProposalLabel">Not applied</span>
          </div>
        </div>

        <div id="delegatedDiffContent" class="proof-body">
          <div class="diff-empty">Select an agent session in the sidebar to inspect its patch.</div>
        </div>

        <div class="proof-foot">
          <div class="evidence" id="delegatedEvidence" data-state="none">
            <i data-lucide="shield" class="i-s ev-none"></i>
            <i data-lucide="circle-dashed" class="i-s ev-wait"></i>
            <i data-lucide="shield-check" class="i-s ev-ok"></i>
            <span id="delegatedEvidenceTag">Evidence: Not run</span>
          </div>
          <div class="proof-actions">
            <button onclick="rollbackAction()" id="btnDelegatedRollback" disabled class="btn">
              <i data-lucide="rotate-ccw" class="i-s"></i> Rollback
            </button>
            <button onclick="applyProposalAction()" id="btnDelegatedApply" disabled class="btn btn--pass" title="Apply with targeted checks (Ctrl+Enter)">
              <i data-lucide="check" class="i-s"></i> Apply proposal <kbd>Ctrl+Enter</kbd>
            </button>
          </div>
        </div>
      </div>
    </section>
"""


def render_modals() -> str:
    _HTML = """
  <div id="gpuModal" class="modal-dialog hidden" onclick="if(event.target===this)closeModals()">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="gpuModalTitle">
      <div class="dialog-head">
        <i data-lucide="hard-drive" class="i"></i>
        <h3 id="gpuModalTitle">GPU and VRAM</h3>
        <button onclick="closeModals()" class="btn btn--quiet icon-btn" aria-label="Close"><i data-lucide="x" class="i"></i></button>
      </div>
      <div class="dialog-body">
        <div class="kv">
          <div><span class="kv-title" id="gpuDeviceName">NVIDIA GPU</span><span class="tag">nvidia-smi</span></div>
          <div><span>VRAM usage</span><span class="num" id="gpuVramText">VRAM unavailable</span></div>
          <div class="gpu-bar"><div id="gpuVramBar" style="width: 0%"></div></div>
          <div><span>GPU load</span><span class="num" id="gpuLoadPct">&mdash;</span></div>
          <div><span>Temperature</span><span class="num" id="gpuTemp">&mdash;</span></div>
        </div>
        <div class="row-split">
          <button onclick="warmupActiveModel()" id="btnPreloadModel" class="btn">
            <i data-lucide="zap" class="i-s"></i> Preload model
          </button>
          <button onclick="unloadAllVram()" id="btnUnloadVram" class="btn btn--danger">
            <i data-lucide="power" class="i-s"></i> Eject all from VRAM
          </button>
        </div>
      </div>
      <div class="dialog-foot">
        <button onclick="closeModals()" class="btn">Done</button>
      </div>
    </div>
  </div>

  <div id="modelModal" class="modal-dialog hidden" onclick="if(event.target===this)closeModals()">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="modelModalTitle">
      <div class="dialog-head">
        <i data-lucide="cpu" class="i"></i>
        <h3 id="modelModalTitle">Model and profile</h3>
        <button onclick="closeModals()" class="btn btn--quiet icon-btn" aria-label="Close"><i data-lucide="x" class="i"></i></button>
      </div>
      <div class="dialog-body">
        <div class="field">
          <div class="field-top">
            <label class="label" for="modalProfileSelect">Active model profile</label>
            <button onclick="fetchAndPopulateModels()" id="btnRefreshModels" class="btn btn--quiet">
              <i data-lucide="refresh-cw" class="i-s"></i> <span>Refresh list</span>
            </button>
          </div>
          <select id="modalProfileSelect" onchange="changeProfile(this.value)" class="input mono">
            <option value="qwen2.5-coder">Loading discovered models&hellip;</option>
          </select>
        </div>

        <div class="field">
          <label class="label" for="modalCtxInput">Context window override (tokens, optional)</label>
          <input id="modalCtxInput" type="number" min="512" step="512" placeholder="Profile default" onchange="onCtxOverrideChange(this.value)" class="input mono">
          <div class="field-help">For llama-server models the backend restarts with the new -c on your next prompt. Use this when you hit "model context too small".</div>
        </div>

        <div class="kv">
          <div><span>Provider</span><span id="profProvider">ollama</span></div>
          <div><span>Context limit</span><span id="profCtx">8192 tokens</span></div>
          <div><span>Endpoint</span><span id="profEndpoint">http://127.0.0.1:11434</span></div>
        </div>

        <div class="row-split">
          <button onclick="warmupActiveModel()" id="btnModalPreload" class="btn">
            <i data-lucide="zap" class="i-s"></i> <span>Load into VRAM</span>
          </button>
          <span class="status-line" id="labelModelLoadStatus"></span>
        </div>
      </div>
      <div class="dialog-foot">
        <button onclick="closeModals()" class="btn">Done</button>
      </div>
    </div>
  </div>

  <div id="serverModal" class="modal-dialog hidden" onclick="if(event.target===this)closeModals()">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="serverModalTitle">
      <div class="dialog-head">
        <i data-lucide="server" class="i"></i>
        <h3 id="serverModalTitle">Local inference servers</h3>
        <button onclick="closeModals()" class="btn btn--quiet icon-btn" aria-label="Close"><i data-lucide="x" class="i"></i></button>
      </div>
      <div class="dialog-body">
        <div class="engine">
          <div class="engine-name"><span class="dot" id="dotOllama"></span><span>Ollama engine</span><span class="mono status-line">:11434</span></div>
          <div class="engine-status" id="labelOllamaStatus">Checking&hellip;</div>
        </div>
        <div class="engine">
          <div class="engine-name"><span class="dot" id="dotLlama"></span><span>llama-server engine</span><span class="mono status-line">:8080</span></div>
          <div class="engine-status" id="labelLlamaStatus">Checking&hellip;</div>
        </div>
        <p class="field-help">Servers start together with a model. Pick a model in Model and profile and press Load into VRAM; the matching engine starts automatically.</p>
      </div>
      <div class="dialog-foot">
        <button onclick="closeModals()" class="btn">Close</button>
      </div>
    </div>
  </div>

  <div id="settingsModal" class="modal-dialog hidden" onclick="if(event.target===this)closeModals()">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="settingsModalTitle">
      <div class="dialog-head">
        <i data-lucide="settings" class="i"></i>
        <h3 id="settingsModalTitle">Preferences and model registry</h3>
        <button onclick="closeModals()" class="btn btn--quiet icon-btn" aria-label="Close"><i data-lucide="x" class="i"></i></button>
      </div>
      <div class="dialog-body">
        <div class="section">
          <div class="row-split">
            <span class="section-title"><i data-lucide="search" class="i-s"></i> Universal Model Scanner</span>
            <button onclick="triggerModelScan(true)" id="btnDeepScan" class="btn btn--primary">Deep drive scan</button>
          </div>
          <p class="field-help">Scans all system drives for local GGUF models. Discovered models are saved to <code>~/.local_coding_agent/models.json</code> and never leave this machine.</p>
          <div class="queue-form-row">
            <input id="inputCustomDir" type="text" placeholder="Add a folder, e.g. D:/my_models" class="input mono">
            <button onclick="addCustomModelFolder()" class="btn"><i data-lucide="folder-plus" class="i-s"></i> Add</button>
          </div>
        </div>

        <div class="section">
          <div class="row-split">
            <div>
              <div class="section-title"><i data-lucide="stethoscope" class="i-s"></i> Doctor</div>
              <div class="field-help">Runs doctor --fix: syncs MCP configs and IDE skills.</div>
            </div>
            <button onclick="runDoctorCheck()" id="btnDoctor" class="btn">Run doctor</button>
          </div>
        </div>

        <div class="kv">
          <div><span class="kv-title">Workspace</span><span></span></div>
          <div><span>Path</span><span id="setWorkspacePath">.</span></div>
          <div><span>Harness core</span><span>v%VERSION%</span></div>
        </div>
      </div>
      <div class="dialog-foot">
        <button onclick="closeModals()" class="btn">Done</button>
      </div>
    </div>
  </div>
"""
    return _with_version(_HTML)


def render_toast() -> str:
    return """
  <div id="toast" class="toast" role="status" aria-live="polite" data-tone="info">
    <span class="toast-mark" aria-hidden="true"></span>
    <span id="toastText">Action completed</span>
  </div>
"""
