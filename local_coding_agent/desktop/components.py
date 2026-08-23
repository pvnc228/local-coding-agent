"""Modular HTML components for the Desktop AI Coding Harness."""

from __future__ import annotations

from .. import __version__


def _with_version(html: str) -> str:
    return html.replace("%VERSION%", __version__)


def render_header() -> str:
    return """
  <!-- TOP BAR -->
  <header class="h-11 border-b border-[var(--border-main)] bg-[var(--bg-header)] px-3.5 flex items-center justify-between shrink-0">
    
    <!-- Left: Brand + 3 Distinct Dedicated Popover Buttons -->
    <div class="flex items-center gap-2.5">
      <button onclick="toggleSidebar()" class="p-1 rounded hover:bg-zinc-800/40 text-zinc-400 hover:text-zinc-200 transition" title="Toggle Sessions (Ctrl+B)">
        <i data-lucide="panel-left" class="w-3.5 h-3.5"></i>
      </button>

      <div class="flex items-center gap-1.5 pr-2.5 border-r border-[var(--border-main)] font-medium text-xs">
        <span class="w-2 h-2 rounded-sm bg-cyan-500"></span>
        <span class="font-semibold text-[var(--text-main)]">Local Harness</span>
      </div>

      <!-- 1. VRAM Button -> Opens GPU Modal -->
      <button onclick="openModal('gpuModal')" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] hover:border-zinc-500 border border-[var(--border-main)] text-[var(--text-muted)] transition cursor-pointer font-mono text-[11px] num-tabular" title="Open GPU & VRAM Cockpit">
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-500" id="topGpuDot"></span>
        <span class="text-zinc-500 font-sans text-[10px]">VRAM</span> <span id="telemetryVram">0.0/8.0G</span>
      </button>

      <!-- 2. Model Button -> Opens Model Selector Modal -->
      <button onclick="openModal('modelModal')" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] hover:border-zinc-500 border border-[var(--border-main)] text-[var(--text-muted)] transition cursor-pointer font-mono text-[11px]" title="Switch Model Profile">
        <span class="text-cyan-500 font-sans text-[10px]" id="backendLabel">OLLAMA</span>
        <span class="text-[var(--text-main)]" id="telemetryModel">qwen2.5-coder</span>
      </button>

      <!-- 3. Server Status Button -> Opens Server Engine Modal -->
      <button onclick="openModal('serverModal')" class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] hover:border-zinc-500 border border-[var(--border-main)] text-zinc-400 hover:text-zinc-200 transition font-mono text-[11px]" title="Manage Local Inference Servers">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-500" id="serverLiveDot"></span>
        <span id="serverLiveText" class="text-[10px] font-sans">Checking...</span>
      </button>
    </div>

    <!-- Center: Segmented Navigation -->
    <div class="inline-flex items-center p-0.5 bg-[var(--bg-card-subtle)] border border-[var(--border-main)] rounded-md font-sans">
      <button onclick="switchTab('chat')" id="tab-btn-chat" class="tab-btn px-2.5 py-1 text-[11px] font-medium rounded transition flex items-center gap-1.5 bg-[var(--bg-card)] text-[var(--text-main)] shadow-sm border border-[var(--border-main)]">
        <i data-lucide="message-square" class="w-3 h-3 text-cyan-500"></i>
        <span>Interactive Chat</span>
      </button>
      <button onclick="switchTab('delegated')" id="tab-btn-delegated" class="tab-btn px-2.5 py-1 text-[11px] font-medium rounded transition flex items-center gap-1.5 text-[var(--text-muted)] hover:text-[var(--text-main)]">
        <i data-lucide="inbox" class="w-3 h-3"></i>
        <span>Delegated Tasks</span>
        <span class="px-1 py-0.1 rounded bg-cyan-500/10 border border-cyan-500/30 text-cyan-500 text-[9px] font-mono font-semibold" id="delegatedBadgeCount">0</span>
      </button>
    </div>

    <!-- Right: Workspace + Theme Toggle + Settings Button -->
    <div class="flex items-center gap-2 text-xs">
      <div class="flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] font-mono text-[11px] text-[var(--text-muted)]">
        <i data-lucide="git-branch" class="w-3 h-3 text-zinc-500"></i>
        <span class="text-[var(--text-main)]" id="workspaceName">workspace</span>
        <span class="text-emerald-500 text-[10px] font-sans font-medium" id="workspaceBranch">• main*</span>
      </div>

      <!-- Theme Switcher -->
      <button onclick="toggleTheme()" class="p-1.5 rounded hover:bg-zinc-800/40 text-[var(--text-muted)] hover:text-[var(--text-main)] transition" title="Toggle Dark/Light Theme">
        <i data-lucide="sun-medium" id="themeIconSun" class="w-3.5 h-3.5 hidden dark:block"></i>
        <i data-lucide="moon" id="themeIconMoon" class="w-3.5 h-3.5 block dark:hidden"></i>
      </button>

      <!-- Settings Button -> Opens Settings Modal -->
      <button onclick="openModal('settingsModal')" class="p-1.5 rounded hover:bg-zinc-800/40 text-[var(--text-muted)] hover:text-[var(--text-main)] transition" title="Preferences, Model Scanner &amp; Diagnostics">
        <i data-lucide="settings" class="w-3.5 h-3.5"></i>
      </button>
    </div>
  </header>
"""


def render_sidebar() -> str:
    _HTML = """
    <!-- LEFT SIDEBAR: SESSION HISTORY -->
    <aside id="sessionSidebar" class="w-60 bg-[var(--bg-sidebar)] border-r border-[var(--border-main)] flex flex-col h-full shrink-0 transition-all duration-150">
      
      <div class="p-2.5 border-b border-[var(--border-main)]">
        <button onclick="startNewSession()" class="w-full py-1 px-2.5 rounded bg-[var(--bg-card)] hover:border-zinc-500 border border-[var(--border-main)] text-[11px] font-medium text-[var(--text-main)] transition flex items-center justify-between shadow-sm">
          <span class="flex items-center gap-1.5">
            <i data-lucide="plus" class="w-3 h-3 text-cyan-500"></i>
            <span>New Task Session</span>
          </span>
          <kbd class="text-[9px] font-mono text-zinc-500 bg-[var(--bg-card-subtle)] px-1 rounded border border-[var(--border-main)]">Ctrl+N</kbd>
        </button>
      </div>

      <div class="px-2.5 pt-2 pb-1 flex items-center gap-1 text-[10px] font-medium text-[var(--text-muted)]">
        <button onclick="filterSessions('all', event)" class="filter-chip px-1.5 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-main)] font-semibold">All</button>
        <button onclick="filterSessions('user', event)" class="filter-chip px-1.5 py-0.5 rounded text-zinc-500 hover:text-[var(--text-main)]">User</button>
        <button onclick="filterSessions('agent', event)" class="filter-chip px-1.5 py-0.5 rounded text-zinc-500 hover:text-[var(--text-main)]">Agent</button>
      </div>

      <div class="flex-1 overflow-y-auto p-1.5 space-y-1 text-xs font-sans" id="sessionList">
        <!-- Sessions rendered dynamically -->
      </div>

      <div class="p-2 border-t border-[var(--border-main)] text-[10px] font-mono text-zinc-500 flex items-center justify-between">
        <span id="sessionCounter">0 Sessions</span>
        <span>Harness v%VERSION%</span>
      </div>
    </aside>
"""
    return _with_version(_HTML)


def render_chat_panel() -> str:
    return """
    <!-- TAB 1: INTERACTIVE CHAT MODE -->
    <div id="view-chat" class="tab-view flex-1 flex h-full">
      
      <!-- Center: Chat Stream -->
      <div class="flex-1 flex flex-col bg-[var(--bg-app)] border-r border-[var(--border-main)] h-full overflow-hidden">
        
        <div id="chatMessages" class="flex-1 overflow-y-auto p-5 space-y-3.5 select-text">
          <!-- Welcome Guidance -->
          <div class="flex items-start gap-2.5 max-w-2xl">
            <div class="w-6 h-6 rounded bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shrink-0 text-[10px] font-mono font-semibold text-cyan-500">
              AI
            </div>
            <div class="flex-1 space-y-2">
              <div class="text-[11px] font-medium text-zinc-500 flex items-center gap-1.5">
                <span>Local Coding Harness</span>
                <span class="text-[9px] font-mono text-zinc-500">• Connected</span>
              </div>
              <div class="p-3.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-[var(--text-main)] leading-relaxed space-y-2">
                <p>Welcome! Enter your coding instructions below or select a preset. The controller will compact the AST, formulate atomic SEARCH/REPLACE diffs, and verify with local test runners.</p>
                <div class="flex items-center gap-2 pt-1">
                  <span class="text-[10px] font-mono text-zinc-500">Active Engine:</span>
                  <span class="px-1.5 py-0.2 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] font-mono text-cyan-400 text-[10px]" id="welcomeModelLabel">qwen2.5-coder</span>
                  <span class="px-1.5 py-0.2 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] font-mono text-cyan-400 text-[10px]" id="welcomeModeLabel">Mode: auto</span>
                  <button onclick="openModal('modelModal')" class="text-[10px] text-cyan-500 hover:underline">Change Model</button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Presets -->
        <div class="px-5 py-1.5 border-t border-[var(--border-main)] bg-[var(--bg-header)] flex items-center gap-1.5 overflow-x-auto text-xs">
          <span class="text-zinc-500 text-[10px] font-mono shrink-0">Presets:</span>
          <button onclick="setPromptAndRun('Fix off-by-one error in sliding window index')" class="px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] hover:bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-muted)] hover:text-[var(--text-main)] text-[11px] transition shrink-0 flex items-center gap-1">
            <i data-lucide="zap" class="w-2.5 h-2.5 text-amber-500"></i> Fix sliding window
          </button>
          <button onclick="setPromptAndRun('Write pytest unit tests for tax calculation')" class="px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] hover:bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-muted)] hover:text-[var(--text-main)] text-[11px] transition shrink-0 flex items-center gap-1">
            <i data-lucide="test-tube" class="w-2.5 h-2.5 text-cyan-500"></i> Unit test tax logic
          </button>
          <button onclick="setPromptAndRun('Refactor calculate_total to use integer cents')" class="px-2 py-0.5 rounded bg-[var(--bg-card-subtle)] hover:bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-muted)] hover:text-[var(--text-main)] text-[11px] transition shrink-0 flex items-center gap-1">
            <i data-lucide="wrench" class="w-2.5 h-2.5 text-emerald-500"></i> Refactor tax cents
          </button>
        </div>

        <!-- Input Bar -->
        <div class="p-3 bg-[var(--bg-app)] border-t border-[var(--border-main)]">
          <!-- Mode Selector -->
          <div class="flex items-center gap-2 mb-2">
            <span class="text-zinc-500 text-[10px] font-mono shrink-0">Mode:</span>
            <div class="inline-flex items-center p-0.5 bg-[var(--bg-card-subtle)] border border-[var(--border-main)] rounded-md font-sans">
              <button onclick="setMode('chat', this)" id="mode-btn-chat" class="mode-btn px-2 py-0.5 text-[10px] font-medium rounded transition text-[var(--text-muted)] hover:text-[var(--text-main)]">Chat</button>
              <button onclick="setMode('build', this)" id="mode-btn-build" class="mode-btn px-2 py-0.5 text-[10px] font-medium rounded transition text-[var(--text-muted)] hover:text-[var(--text-main)]">Build</button>
              <button onclick="setMode('plan', this)" id="mode-btn-plan" class="mode-btn px-2 py-0.5 text-[10px] font-medium rounded transition text-[var(--text-muted)] hover:text-[var(--text-main)]">Plan</button>
              <button onclick="setMode('hybrid', this)" id="mode-btn-hybrid" class="mode-btn px-2 py-0.5 text-[10px] font-medium rounded transition text-[var(--text-muted)] hover:text-[var(--text-main)]">Auto</button>
            </div>
            <span class="px-1.5 py-0.2 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] font-mono text-[10px] text-cyan-400" id="modeBadge">Mode: auto</span>
          </div>
          <div class="relative flex items-center bg-[var(--bg-input)] border border-[var(--border-main)] rounded-lg focus-within:border-cyan-500 focus-within:ring-1 focus-within:ring-cyan-500/20 transition shadow-inner">
            <input id="chatInput" type="text" placeholder="Instruct local model to fix, refactor, or test (Enter to send)..." class="w-full bg-transparent px-3 py-2 text-xs text-[var(--text-main)] placeholder-zinc-500 outline-none" onkeydown="if(event.key==='Enter') handleUserSubmit()">
            <button onclick="handleUserSubmit()" id="btnSendChat" class="mr-1.5 px-2.5 py-1 rounded bg-cyan-600 hover:bg-cyan-500 text-zinc-950 font-semibold text-xs transition flex items-center justify-center">
              <span>Run</span>
            </button>
          </div>
        </div>

      </div>

      <!-- Right: Split Code Diff Studio -->
      <div class="w-[460px] lg:w-[540px] bg-[var(--bg-header)] flex flex-col h-full overflow-hidden">
        
        <div class="h-9 px-3 border-b border-[var(--border-main)] flex items-center justify-between bg-[var(--bg-card-subtle)]">
          <div class="flex items-center gap-2 text-xs font-mono text-[var(--text-main)]">
            <i data-lucide="file-code" class="w-3 h-3 text-cyan-500"></i>
            <span id="diffFileName">No active diff</span>
            <span class="text-[9px] text-emerald-500 font-mono font-semibold" id="diffStatsTag">Ready</span>
          </div>

          <div class="flex items-center gap-1 font-mono text-[10px]">
            <button onclick="copyActiveDiff()" class="px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-muted)] hover:text-[var(--text-main)] transition">
              Copy
            </button>
            <button onclick="applyProposalAction()" id="btnApply" class="px-2.5 py-0.5 rounded bg-emerald-600 hover:bg-emerald-500 text-zinc-950 font-semibold font-sans transition">
              Apply (Ctrl+A)
            </button>
            <button onclick="rollbackAction()" id="btnRollback" class="px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-muted)] hover:text-[var(--text-main)] transition">
              Rollback
            </button>
          </div>
        </div>

        <div id="diffContentArea" class="flex-1 overflow-y-auto font-mono text-[11px] leading-relaxed p-2 select-text">
          <div class="p-8 text-center text-zinc-500 text-xs">No active diff proposal. Run a prompt or select a task session.</div>
        </div>

        <div class="p-2 bg-[var(--bg-app)] border-t border-[var(--border-main)] flex items-center justify-between text-xs font-mono text-[10px]">
          <div class="flex items-center gap-1 text-emerald-500" id="diffEvidenceStatus">
            <i data-lucide="check-circle-2" class="w-3 h-3"></i>
            <span>Oracles: External Test Evidence</span>
          </div>
          <span class="text-zinc-500">Mediated Rollback Active</span>
        </div>
      </div>

    </div>
"""


def render_delegated_panel() -> str:
    return """
    <!-- TAB 2: DELEGATED TASKS MODE -->
    <div id="view-delegated" class="tab-view flex-1 hidden h-full">
      <div class="flex-1 grid grid-cols-1 md:grid-cols-3 h-full overflow-hidden">
        
        <!-- Left: TaskEnvelope Card -->
        <div class="p-5 bg-[var(--bg-sidebar)] border-r border-[var(--border-main)] space-y-3.5 overflow-y-auto text-xs">
          <div class="flex items-center justify-between pb-2 border-b border-[var(--border-main)]">
            <h2 class="text-xs font-semibold text-[var(--text-main)] flex items-center gap-1.5">
              <i data-lucide="inbox" class="w-3.5 h-3.5 text-cyan-500"></i>
              <span>Task Envelope</span>
            </h2>
            <span class="px-1.5 py-0.2 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 font-mono text-[9px] font-semibold" id="delegatedStatusTag">
              READY FOR APPLY
            </span>
          </div>

          <div class="space-y-1">
            <label class="text-[10px] font-mono uppercase text-zinc-500">Delegating Host Agent</label>
            <div class="p-2 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-[var(--text-main)] font-mono flex items-center gap-1.5" id="delegatedAgent">
              <i data-lucide="bot" class="w-3 h-3 text-purple-500"></i>
              <span>Codex / Claude Code (MCP)</span>
            </div>
          </div>

          <div class="space-y-1">
            <label class="text-[10px] font-mono uppercase text-zinc-500">Task ID</label>
            <div class="p-2 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-[var(--text-main)] font-mono" id="delegatedTaskId">
              None
            </div>
          </div>

          <div class="space-y-1">
            <label class="text-[10px] font-mono uppercase text-zinc-500">Goal</label>
            <div class="p-2 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-[var(--text-main)] leading-relaxed" id="delegatedGoal">
              No delegated task selected.
            </div>
          </div>

          <div class="space-y-1">
            <label class="text-[10px] font-mono uppercase text-zinc-500">Allowlisted Files</label>
            <div class="p-2 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-cyan-500 font-mono" id="delegatedFiles">
              -
            </div>
          </div>

          <div class="space-y-1">
            <label class="text-[10px] font-mono uppercase text-zinc-500">Targeted Checks</label>
            <div class="p-2 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-emerald-500 font-mono" id="delegatedChecks">
              pytest
            </div>
          </div>

          <div class="pt-2 space-y-1.5">
            <button onclick="applyProposalAction()" id="btnDelegatedApply" class="w-full py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-zinc-950 font-semibold text-xs transition flex items-center justify-center gap-1.5">
              <i data-lucide="check" class="w-3.5 h-3.5"></i>
              <span>Apply Proposal (Ctrl+A)</span>
            </button>
            <button onclick="rollbackAction()" id="btnDelegatedRollback" class="w-full py-1.5 rounded bg-[var(--bg-card)] hover:border-zinc-500 border border-[var(--border-main)] text-[var(--text-muted)] font-medium text-xs transition flex items-center justify-center gap-1.5">
              <i data-lucide="rotate-ccw" class="w-3.5 h-3.5"></i>
              <span>Auto-Rollback (git restore)</span>
            </button>
          </div>
        </div>

        <!-- Right: Monaco Split Diff -->
        <div class="col-span-2 bg-[var(--bg-app)] flex flex-col h-full overflow-hidden">
          <div class="h-9 px-3 border-b border-[var(--border-main)] flex items-center justify-between bg-[var(--bg-card-subtle)]">
            <div class="text-xs font-mono text-[var(--text-main)] flex items-center gap-1.5">
              <i data-lucide="file-code" class="w-3 h-3 text-cyan-500"></i>
              <span id="delegatedFileName">src/tax.py</span>
              <span class="text-zinc-500 text-[10px]">(Proposal Accepted)</span>
            </div>
          </div>

          <div id="delegatedDiffContent" class="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed select-text space-y-0.5">
            <div class="p-8 text-center text-zinc-500">Select an agent delegated session on the left to inspect diff.</div>
          </div>

          <div class="p-2.5 bg-[var(--bg-header)] border-t border-[var(--border-main)] flex items-center justify-between text-xs">
            <div class="flex items-center gap-1.5 text-emerald-500 font-mono text-[11px]">
              <i data-lucide="shield-check" class="w-3.5 h-3.5"></i>
              <span id="delegatedEvidenceTag">Evidence: Verified by Test Runner</span>
            </div>
          </div>
        </div>

      </div>
    </div>
"""


def render_modals() -> str:
    _HTML = """
  <!-- MODAL 1: GPU & NVIDIA-SMI TELEMETRY DIALOG -->
  <div id="gpuModal" class="modal-dialog fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 z-50 hidden">
    <div class="bg-[var(--bg-card)] border border-[var(--border-main)] rounded-xl w-full max-w-md overflow-hidden shadow-xl">
      <div class="px-4 py-3 border-b border-[var(--border-main)] flex items-center justify-between bg-[var(--bg-card-subtle)]">
        <div class="flex items-center gap-2">
          <i data-lucide="hard-drive" class="w-3.5 h-3.5 text-emerald-500"></i>
          <h3 class="text-xs font-semibold text-[var(--text-main)]">GPU &amp; VRAM Hardware Cockpit</h3>
        </div>
        <button onclick="closeModals()" class="p-1 rounded hover:bg-zinc-800/40 text-zinc-400 hover:text-zinc-200"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>
      </div>

      <div class="p-4 space-y-3.5 text-xs">
        <div class="p-3 rounded-lg bg-[var(--bg-app)] border border-[var(--border-main)] space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-[var(--text-main)]" id="gpuDeviceName">NVIDIA GPU</span>
            <span class="px-1.5 py-0.2 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 font-mono text-[9px]">nvidia-smi</span>
          </div>

          <div class="flex items-center justify-between text-[11px]">
            <span class="text-zinc-400">VRAM Usage:</span>
            <span class="font-mono font-bold text-[var(--text-main)]" id="gpuVramText">0.0 / 8.0 GB (0%)</span>
          </div>

          <div class="w-full h-2 rounded bg-[var(--bg-card-subtle)] overflow-hidden">
            <div class="h-full bg-emerald-500 rounded transition-all duration-300" id="gpuVramBar" style="width: 0%"></div>
          </div>

          <div class="grid grid-cols-2 gap-2 pt-1 font-mono text-[10px] text-zinc-400 border-t border-[var(--border-main)]">
            <div>GPU Load: <span class="text-[var(--text-main)] font-semibold" id="gpuLoadPct">0%</span></div>
            <div>Temp: <span class="text-[var(--text-main)] font-semibold" id="gpuTemp">0°C</span></div>
          </div>
        </div>

        <div class="flex items-center justify-between pt-1">
          <button onclick="warmupActiveModel()" id="btnPreloadModel" class="px-2.5 py-1 rounded bg-[var(--bg-card-subtle)] hover:bg-[var(--bg-card)] border border-[var(--border-main)] text-[10px] font-medium text-[var(--text-main)] transition">
            ⚡ Preload Model
          </button>
          <button onclick="unloadAllVram()" id="btnUnloadVram" class="px-2.5 py-1 rounded bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-400 text-[10px] font-medium transition">
            Eject ALL from VRAM
          </button>
        </div>
      </div>

      <div class="px-4 py-2 border-t border-[var(--border-main)] bg-[var(--bg-card-subtle)] flex justify-end">
        <button onclick="closeModals()" class="px-3 py-1 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs font-medium text-[var(--text-main)]">Done</button>
      </div>
    </div>
  </div>

  <!-- MODAL 2: MODEL & PROFILE SELECTOR DIALOG -->
  <div id="modelModal" class="modal-dialog fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 z-50 hidden">
    <div class="bg-[var(--bg-card)] border border-[var(--border-main)] rounded-xl w-full max-w-md overflow-hidden shadow-xl">
      <div class="px-4 py-3 border-b border-[var(--border-main)] flex items-center justify-between bg-[var(--bg-card-subtle)]">
        <div class="flex items-center gap-2">
          <i data-lucide="cpu" class="w-3.5 h-3.5 text-cyan-500"></i>
          <h3 class="text-xs font-semibold text-[var(--text-main)]">Model &amp; Profile Selector</h3>
        </div>
        <button onclick="closeModals()" class="p-1 rounded hover:bg-zinc-800/40 text-zinc-400 hover:text-zinc-200"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>
      </div>

      <div class="p-4 space-y-3.5 text-xs">
        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-[11px] font-medium text-[var(--text-muted)]">Active Model Profile</label>
            <button onclick="fetchAndPopulateModels()" id="btnRefreshModels" class="text-[10px] text-cyan-500 hover:underline flex items-center gap-1">
              <i data-lucide="refresh-cw" class="w-2.5 h-2.5"></i> <span>Refresh List</span>
            </button>
          </div>
          <select id="modalProfileSelect" onchange="changeProfile(this.value)" class="w-full bg-[var(--bg-app)] border border-[var(--border-main)] rounded px-2.5 py-2 text-xs text-[var(--text-main)] outline-none focus:border-cyan-500 font-mono">
            <option value="qwen2.5-coder">Loading discovered models...</option>
          </select>
        </div>

        <div class="space-y-1.5">
          <label class="text-[11px] font-medium text-[var(--text-muted)]">Context Window Override <span class="text-zinc-500">(tokens, optional)</span></label>
          <input id="modalCtxInput" type="number" min="512" step="512" placeholder="profile default" onchange="onCtxOverrideChange(this.value)" class="w-full bg-[var(--bg-app)] border border-[var(--border-main)] rounded px-2.5 py-2 text-xs text-[var(--text-main)] outline-none focus:border-cyan-500 font-mono">
          <div class="text-[10px] text-zinc-500">For llama-server models the backend restarts with the new -c on your next prompt. Use this when you hit "model context too small".</div>
        </div>

        <div class="p-3 rounded bg-[var(--bg-app)] border border-[var(--border-main)] space-y-1 font-mono text-[10px] text-zinc-400">
          <div class="flex justify-between"><span>Provider:</span><span class="text-[var(--text-main)] font-semibold" id="profProvider">ollama</span></div>
          <div class="flex justify-between"><span>Context Limit:</span><span class="text-[var(--text-main)] font-semibold" id="profCtx">8192 tokens</span></div>
          <div class="flex justify-between"><span>Endpoint:</span><span class="text-cyan-400 font-semibold" id="profEndpoint">http://127.0.0.1:11434</span></div>
        </div>

        <div class="pt-1 flex items-center justify-between">
          <button onclick="warmupActiveModel()" id="btnModalPreload" class="px-2.5 py-1 rounded bg-[var(--bg-card-subtle)] hover:bg-[var(--bg-card)] border border-[var(--border-main)] text-[10px] font-medium text-[var(--text-main)] transition flex items-center gap-1.5">
            <span>⚡ Load into VRAM</span>
          </button>
          <span class="text-[10px] text-zinc-500 font-mono" id="labelModelLoadStatus"></span>
        </div>
      </div>

      <div class="px-4 py-2 border-t border-[var(--border-main)] bg-[var(--bg-card-subtle)] flex justify-end">
        <button onclick="closeModals()" class="px-3 py-1 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs font-medium text-[var(--text-main)]">Apply</button>
      </div>
    </div>
  </div>

  <!-- MODAL 3: INFERENCE SERVER PROCESS MANAGER DIALOG -->
  <div id="serverModal" class="modal-dialog fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 z-50 hidden">
    <div class="bg-[var(--bg-card)] border border-[var(--border-main)] rounded-xl w-full max-w-md overflow-hidden shadow-xl">
      <div class="px-4 py-3 border-b border-[var(--border-main)] flex items-center justify-between bg-[var(--bg-card-subtle)]">
        <div class="flex items-center gap-2">
          <i data-lucide="server" class="w-3.5 h-3.5 text-amber-500"></i>
          <h3 class="text-xs font-semibold text-[var(--text-main)]">Local Inference Servers</h3>
        </div>
        <button onclick="closeModals()" class="p-1 rounded hover:bg-zinc-800/40 text-zinc-400 hover:text-zinc-200"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>
      </div>

      <div class="p-4 space-y-3 text-xs">
        <div class="p-2.5 rounded bg-[var(--bg-app)] border border-[var(--border-main)]">
          <div class="font-medium text-[11px] flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-zinc-500" id="dotOllama"></span>
            <span>Ollama Engine (:11434)</span>
          </div>
          <div class="text-[10px] text-zinc-500 font-mono mt-0.5" id="labelOllamaStatus">Checking...</div>
        </div>

        <div class="p-2.5 rounded bg-[var(--bg-app)] border border-[var(--border-main)]">
          <div class="font-medium text-[11px] flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-zinc-500" id="dotLlama"></span>
            <span>llama-server Engine (:8080)</span>
          </div>
          <div class="text-[10px] text-zinc-500 font-mono mt-0.5" id="labelLlamaStatus">Checking...</div>
        </div>

        <p class="text-[10px] text-zinc-500 leading-relaxed">
          Servers launch together with a model. Pick a model in the Model &amp; Profile Selector and press Load into VRAM — the matching engine starts automatically.
        </p>
      </div>

      <div class="px-4 py-2 border-t border-[var(--border-main)] bg-[var(--bg-card-subtle)] flex justify-end">
        <button onclick="closeModals()" class="px-3 py-1 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs font-medium text-[var(--text-main)]">Close</button>
      </div>
    </div>
  </div>

  <!-- MODAL 4: SYSTEM SETTINGS & MODEL SCANNER DIALOG -->
  <div id="settingsModal" class="modal-dialog fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 z-50 hidden">
    <div class="bg-[var(--bg-card)] border border-[var(--border-main)] rounded-xl w-full max-w-md overflow-hidden shadow-xl">
      <div class="px-4 py-3 border-b border-[var(--border-main)] flex items-center justify-between bg-[var(--bg-card-subtle)]">
        <div class="flex items-center gap-2">
          <i data-lucide="settings" class="w-3.5 h-3.5 text-cyan-500"></i>
          <h3 class="text-xs font-semibold text-[var(--text-main)]">Preferences &amp; Model Registry</h3>
        </div>
        <button onclick="closeModals()" class="p-1 rounded hover:bg-zinc-800/40 text-zinc-400 hover:text-zinc-200"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>
      </div>

      <div class="p-4 space-y-3 text-xs">
        <!-- Model Scanner Action Card -->
        <div class="p-3 rounded-lg bg-[var(--bg-app)] border border-[var(--border-main)] space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs font-medium text-[var(--text-main)] flex items-center gap-1.5">
              <i data-lucide="search" class="w-3 h-3 text-cyan-500"></i> Universal Model Scanner
            </span>
            <button onclick="triggerModelScan(true)" id="btnDeepScan" class="px-2 py-0.5 rounded bg-cyan-600 hover:bg-cyan-500 text-zinc-950 font-semibold text-[10px] transition">
              Deep Drive Scan
            </button>
          </div>
          <p class="text-[10px] text-zinc-500 leading-relaxed">
            Scans all system drives for local GGUF models. Discovered models are saved to <code class="font-mono text-zinc-400">~/.local_coding_agent/models.json</code> without leaking off-machine.
          </p>
          <div class="flex items-center gap-1 pt-1">
            <input id="inputCustomDir" type="text" placeholder="Add custom folder (e.g. D:/my_models)..." class="flex-1 bg-[var(--bg-card)] border border-[var(--border-main)] rounded px-2 py-1 text-[10px] text-[var(--text-main)] outline-none focus:border-cyan-500 font-mono">
            <button onclick="addCustomModelFolder()" class="px-2.5 py-1 rounded bg-[var(--bg-card-subtle)] hover:bg-[var(--bg-card)] border border-[var(--border-main)] text-[10px] font-semibold text-zinc-300 transition">
              + Add
            </button>
          </div>
        </div>

        <!-- Doctor Action Card -->
        <div class="p-3 rounded-lg bg-[var(--bg-app)] border border-[var(--border-main)] flex items-center justify-between">
          <div>
            <div class="text-xs font-medium text-[var(--text-main)]">Self-Healing Doctor (doctor --fix)</div>
            <div class="text-[10px] text-zinc-500 font-mono">Sync MCP configs &amp; IDE skills.</div>
          </div>
          <button onclick="runDoctorCheck()" id="btnDoctor" class="px-2.5 py-1 rounded bg-[var(--bg-card)] hover:border-zinc-500 border border-[var(--border-main)] text-[11px] font-medium text-[var(--text-main)] transition">
            Run Doctor
          </button>
        </div>

        <div class="p-3 rounded-lg bg-[var(--bg-app)] border border-[var(--border-main)] space-y-1 font-mono text-[10px] text-zinc-400">
          <div class="text-[11px] font-semibold text-[var(--text-main)] font-sans mb-1">Workspace Environment</div>
          <div>Path: <span class="text-zinc-300" id="setWorkspacePath">.</span></div>
          <div>Harness Core: <span class="text-emerald-400">v%VERSION% (R23 Desktop)</span></div>
        </div>
      </div>

      <div class="px-4 py-2 border-t border-[var(--border-main)] bg-[var(--bg-card-subtle)] flex justify-end">
        <button onclick="closeModals()" class="px-3 py-1 rounded bg-[var(--bg-card)] border border-[var(--border-main)] text-xs font-medium text-[var(--text-main)]">Done</button>
      </div>
    </div>
  </div>
"""
    return _with_version(_HTML)


def render_toast() -> str:
    return """
  <!-- Notification Toast -->
  <div id="toast" class="fixed bottom-4 right-4 px-3.5 py-2 rounded-md bg-[var(--bg-card)] border border-[var(--border-main)] text-[var(--text-main)] text-xs shadow-lg flex items-center gap-1.5 transform translate-y-2 opacity-0 transition-all pointer-events-none z-50 font-mono text-[11px]">
    <i data-lucide="check" class="w-3 h-3 text-emerald-500"></i>
    <span id="toastText">Action completed</span>
  </div>
"""
