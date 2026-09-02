"""Client-side JavaScript application logic and state machine for Desktop AI Coding Harness."""

from __future__ import annotations

DESKTOP_CLIENT_JS = r"""
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options = {}) => {
      const method = String(options.method || (resource && resource.method) || 'GET').toUpperCase();
      if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        const headers = new Headers(options.headers || {});
        headers.set('X-Desktop-Token', window.DESKTOP_MUTATION_TOKEN || '');
        options = { ...options, headers };
      }
      return nativeFetch(resource, options);
    };

    function safeCreateIcons() {
      if (typeof lucide !== 'undefined' && lucide.createIcons) {
        try { lucide.createIcons(); } catch (e) { }
      }
    }

    function spinnerSvg(size = 14, extraClass = '') {
      return `<svg class="spinner-svg ${extraClass}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`;
    }

    let SESSIONS = [];
    let activeSession = null;
    let activeSessionFilter = 'all';
    let activeProfile = 'qwen2.5-coder';
    let activeCtxOverride = null;
    let SELECTED_MODE = 'hybrid';
    let modelProviders = {};  // model id -> backend ('ollama' | 'llama_server'), from /api/models
    let rollbackAvailable = false;

    function updateProposalControls(session = null) {
      const hasPatch = Boolean(session && session.patch && session.patch.trim());
      const evidenceVerified = Boolean(session && session.evidence_verified === true);
      ['btnApply', 'btnDelegatedApply'].forEach(id => {
        const button = document.getElementById(id);
        if (button) button.disabled = !hasPatch;
      });
      ['btnRollback', 'btnDelegatedRollback'].forEach(id => {
        const button = document.getElementById(id);
        if (button) button.disabled = !rollbackAvailable;
      });
      const status = document.getElementById('delegatedStatusTag');
      if (status) status.textContent = hasPatch ? 'PROPOSAL READY' : 'NO PROPOSAL';
      const evidence = document.getElementById('delegatedEvidenceTag');
      if (evidence) evidence.textContent = evidenceVerified ? 'Evidence: Verified by Test Runner' : 'Evidence: Not run';
      const oracle = document.getElementById('diffEvidenceStatus');
      if (oracle) {
        const label = oracle.querySelector('span');
        if (label) label.textContent = evidenceVerified ? 'Oracles: External checks passed' : 'Oracles: Not run';
      }
    }

    function resolveProviderFromRegistry(val) {
      if (Object.hasOwn(modelProviders, val)) return modelProviders[val];
      // Fallback heuristic for profile ids not present in the registry.
      const v = val.toLowerCase();
      if (v.includes('ling') || v.includes('llama-server') || v.endsWith('.gguf')) return 'llama_server';
      return 'ollama';
    }

    function setMode(mode, btn) {
      SELECTED_MODE = mode;
      document.querySelectorAll('.mode-btn').forEach(b => {
        b.classList.remove('bg-[var(--bg-card)]', 'text-[var(--text-main)]', 'shadow-sm', 'border', 'border-[var(--border-main)]');
        b.classList.add('text-[var(--text-muted)]');
      });
      if (btn) {
        btn.classList.add('bg-[var(--bg-card)]', 'text-[var(--text-main)]', 'shadow-sm', 'border', 'border-[var(--border-main)]');
        btn.classList.remove('text-[var(--text-muted)]');
      }
      const badge = document.getElementById('modeBadge');
      if (badge) badge.textContent = `Mode: ${mode === 'hybrid' ? 'auto' : mode}`;
      const welcomeBadge = document.getElementById('welcomeModeLabel');
      if (welcomeBadge) welcomeBadge.textContent = `Mode: ${mode === 'hybrid' ? 'auto' : mode}`;
    }

    function openModal(modalId) {
      closeModals();
      const m = document.getElementById(modalId);
      if (m) m.classList.remove('hidden');
      if (modalId === 'modelModal') {
        const statusLabel = document.getElementById('labelModelLoadStatus');
        if (statusLabel) statusLabel.innerHTML = '';
        fetchAndPopulateModels();
      } else if (modalId === 'serverModal') {
        pollStatus();
      }
      safeCreateIcons();
    }

    function closeModals() {
      document.querySelectorAll('.modal-dialog').forEach(m => m.classList.add('hidden'));
    }

    async function fetchAndPopulateModels() {
      const refreshBtn = document.getElementById('btnRefreshModels');
      const oldRefreshHtml = refreshBtn ? refreshBtn.innerHTML : '';
      if (refreshBtn) {
        refreshBtn.innerHTML = `${spinnerSvg(11, 'text-cyan-400')} <span class="text-cyan-400">Loading Registry...</span>`;
        refreshBtn.disabled = true;
      }
      try {
        const res = await fetch('/api/models');
        if (!res.ok) return;
        const data = await res.json();
        const select = document.getElementById('modalProfileSelect');
        if (!select) return;

        const currentVal = select.value || activeProfile;
        select.innerHTML = '';
        modelProviders = {};

        // 1. Ollama Installed Models (backend reachability is reported honestly)
        const ollamaModels = (data.backends && data.backends.ollama && data.backends.ollama.models) || [];
        const ollamaOnline = Boolean(data.backends && data.backends.ollama && data.backends.ollama.online);
        // 3. llama-server Active Models
        const llamaModels = (data.backends && data.backends.llama_server && data.backends.llama_server.models) || [];

        // Record provider for every known id so the top-bar backend label and
        // online-dot logic read reality instead of guessing from the name.
        const recordProviders = (ids, provider) => {
          (ids || []).forEach(id => { if (id) modelProviders[id] = provider; });
        };
        recordProviders(ollamaModels, 'ollama');
        recordProviders(llamaModels, 'llama_server');
        (data.profiles || []).forEach(p => { if (p && p.name) modelProviders[p.name] = p.provider === 'openai' ? 'llama_server' : 'ollama'; });

        if (ollamaModels.length > 0) {
          const optGroup = document.createElement('optgroup');
          optGroup.label = ollamaOnline
            ? '✅ Installed in Ollama (Ready to Use)'
            : '⚠️ Installed in Ollama (backend offline — start Ollama in Local Inference Servers)';
          ollamaModels.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = `Ollama: ${m}`;
            optGroup.appendChild(opt);
          });
          select.appendChild(optGroup);
        }

        // 2. Local GGUF Models from Persistent Registry (Discovered across system drives)
        const localGgufs = (data.backends && data.backends.local_gguf && data.backends.local_gguf.models) || [];
        recordProviders(localGgufs.map(g => g.id || g.name), 'llama_server');
        if (localGgufs.length > 0) {
          const optGroup = document.createElement('optgroup');
          optGroup.label = '⚡ Local GGUF → launches llama-server (:8080)';
          localGgufs.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.id || g.name;
            opt.textContent = `GGUF → llama-server: ${g.display_name} (${g.size_gb} GB)`;
            optGroup.appendChild(opt);
          });
          select.appendChild(optGroup);
        }

        // 3. llama-server Active Models
        if (llamaModels.length > 0) {
          const optGroup = document.createElement('optgroup');
          optGroup.label = '⚡ Active in llama-server (:8080)';
          llamaModels.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = `llama-server: ${m}`;
            optGroup.appendChild(opt);
          });
          select.appendChild(optGroup);
        }

        // 4. Predefined System Profiles (Not installed locally)
        if (data.profiles && data.profiles.length > 0) {
          const optGroup = document.createElement('optgroup');
          optGroup.label = '📦 Predefined Profiles (Requires Pulling)';
          data.profiles.forEach(p => {
            if (!ollamaModels.includes(p.name) && !llamaModels.includes(p.name) && !localGgufs.some(g => g.name === p.name)) {
              const opt = document.createElement('option');
              opt.value = p.name;
              opt.textContent = `${p.provider === 'openai' ? 'llama-server' : 'Ollama'}: ${p.name} (not installed)`;
              opt.disabled = true;
              optGroup.appendChild(opt);
            }
          });
          select.appendChild(optGroup);
        }

        // Restore active selection or choose best default installed model
        const currentOption = [...select.options].find(o => o.value === currentVal && !o.disabled);
        if (currentOption) {
          select.value = currentVal;
          changeProfile(currentVal);
        } else {
          const firstAvailable = [...select.options].find(o => !o.disabled);
          if (firstAvailable) {
            select.value = firstAvailable.value;
            changeProfile(firstAvailable.value);
          } else {
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'No local models available';
            placeholder.disabled = true;
            placeholder.selected = true;
            select.prepend(placeholder);
            activeProfile = '';
            document.getElementById('telemetryModel').textContent = 'No model';
          }
        }
      } catch (e) {
      } finally {
        if (refreshBtn) {
          refreshBtn.innerHTML = oldRefreshHtml || '<i data-lucide="refresh-cw" class="w-2.5 h-2.5"></i> <span>Refresh List</span>';
          refreshBtn.disabled = false;
          safeCreateIcons();
        }
      }
    }

    async function triggerModelScan(deep = true) {
      const btn = document.getElementById('btnDeepScan');
      const oldHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.innerHTML = `<span class="inline-flex items-center gap-1">${spinnerSvg(10, 'text-zinc-950')} Scanning system...</span>`;
        btn.disabled = true;
      }
      showToast('Running deep drive model scanner...');
      try {
        const res = await fetch('/api/models/scan', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ deep })
        });
        const data = await res.json();
        showToast(`✓ Discovered ${data.total_models || data.models?.length || 0} models across drives`);
        fetchAndPopulateModels();
      } catch (e) {
        showToast('Error during model scan');
      } finally {
        if (btn) {
          btn.innerHTML = oldHtml || 'Deep Drive Scan';
          btn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    async function addCustomModelFolder() {
      const input = document.getElementById('inputCustomDir');
      const pathVal = input ? input.value.trim() : '';
      if (!pathVal) {
        showToast('Please enter a folder path');
        return;
      }
      try {
        const res = await fetch('/api/models/add_dir', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ path: pathVal })
        });
        const data = await res.json();
        if (data.status === 'added' || data.status === 'already_present') {
          showToast(`✓ Added custom folder: ${pathVal}`);
          if (input) input.value = '';
          triggerModelScan(false);
        } else {
          showToast(`Issue adding folder: ${data.error || 'failed'}`);
        }
      } catch (e) {
        showToast('Error saving custom model directory');
      }
    }

    function renderUnifiedDiff(rawDiff) {
      if (!rawDiff || !rawDiff.trim()) {
        return '<div class="p-8 text-center text-zinc-500 text-xs">No active diff proposal. Run a prompt or select a task session.</div>';
      }

      const lines = rawDiff.split('\n');
      let html = '<div class="space-y-0.5 font-mono text-[11px] select-text">';
      let oldLine = 0;
      let newLine = 0;

      for (const line of lines) {
        if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('diff --git')) {
          html += `<div class="px-2 py-0.5 text-zinc-500 font-semibold text-[10px] bg-[var(--bg-card-subtle)]">${escapeHtml(line)}</div>`;
        } else if (line.startsWith('@@')) {
          const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
          if (match) {
            oldLine = parseInt(match[1], 10);
            newLine = parseInt(match[2], 10);
          }
          html += `<div class="px-2 py-0.5 text-cyan-500/80 bg-cyan-500/5 text-[10px] font-semibold">${escapeHtml(line)}</div>`;
        } else if (line.startsWith('-')) {
          html += `<div class="flex px-2 py-0.5 diff-del-bg rounded-xs"><span class="w-7 shrink-0 text-right pr-2 select-none num-tabular text-red-400/60">${oldLine ? oldLine++ : ''} -</span><span class="whitespace-pre">${escapeHtml(line.slice(1))}</span></div>`;
        } else if (line.startsWith('+')) {
          html += `<div class="flex px-2 py-0.5 diff-add-bg rounded-xs"><span class="w-7 shrink-0 text-right pr-2 select-none num-tabular text-emerald-400/60">${newLine ? newLine++ : ''} +</span><span class="whitespace-pre">${escapeHtml(line.slice(1))}</span></div>`;
        } else {
          html += `<div class="flex px-2 py-0.5 text-zinc-400"><span class="w-7 shrink-0 text-right pr-2 select-none num-tabular text-zinc-600">${oldLine ? oldLine++ : ''}</span><span class="whitespace-pre">${escapeHtml(line.startsWith(' ') ? line.slice(1) : line)}</span></div>`;
          if (newLine) newLine++;
        }
      }
      html += '</div>';
      return html;
    }

    async function loadSessions() {
      try {
        const res = await fetch('/api/sessions');
        if (res.ok) {
          const data = await res.json();
          SESSIONS = data.sessions || [];
        }
      } catch (e) {
        SESSIONS = [];
      }

      renderSessions(activeSessionFilter);
      if (SESSIONS.length > 0) {
        selectSessionById(SESSIONS[0].id);
      }
    }

    function renderSessions(filter = activeSessionFilter) {
      const list = document.getElementById('sessionList');
      list.innerHTML = '';
      const filtered = SESSIONS.filter(s => filter === 'all' || s.type === filter);
      document.getElementById('sessionCounter').textContent = `${filtered.length} Sessions`;

      if (filtered.length === 0) {
        list.innerHTML = `
          <div class="p-4 text-center text-zinc-500 font-mono text-[10px] space-y-1">
            <div>No ${filter !== 'all' ? filter : ''} sessions yet</div>
            <div class="text-[9px] text-zinc-600">Type a goal below to start!</div>
          </div>
        `;
        return;
      }

      filtered.forEach(s => {
        const isSelected = activeSession && activeSession.id === s.id;
        const card = document.createElement('div');
        card.className = `session-card p-2 rounded border cursor-pointer transition ${
          isSelected
            ? 'border-cyan-500/40 bg-[var(--bg-card)] text-[var(--text-main)] shadow-xs'
            : 'border-transparent hover:border-[var(--border-main)] hover:bg-[var(--bg-card)] text-[var(--text-muted)]'
        }`;
        card.onclick = () => selectSessionById(s.id);

        const badgeClass = s.type === 'user'
          ? 'bg-blue-500/10 border-blue-500/30 text-blue-500'
          : s.agent === 'Codex'
            ? 'bg-purple-500/10 border-purple-500/30 text-purple-500'
            : 'bg-cyan-500/10 border-cyan-500/30 text-cyan-500';

        const badgeLabel = s.type === 'user' ? 'USER' : `AGENT: ${s.agent || 'MCP'}`;

        card.innerHTML = `
          <div class="flex items-center justify-between mb-0.5">
            <span class="inline-flex items-center gap-1 px-1 py-0.1 rounded border font-mono text-[9px] font-semibold tracking-wider ${badgeClass}">
              ${badgeLabel}
            </span>
            <span class="text-[9px] font-mono text-zinc-500 num-tabular">${s.time || 'Active'}</span>
          </div>
          <div class="text-[11px] font-medium truncate text-[var(--text-main)]">${escapeHtml(s.title || s.goal || 'Session')}</div>
          <div class="text-[10px] text-zinc-500 font-mono mt-0.5 flex items-center justify-between">
            <span>${escapeHtml(s.file || 'workspace')}</span>
            <span class="${s.status && (s.status.includes('Ready') || s.status === 'Verified') ? 'text-emerald-500 font-semibold' : 'text-zinc-500'}">${escapeHtml(s.status || 'Active')}</span>
          </div>
        `;
        list.appendChild(card);
      });
      safeCreateIcons();
    }

    function selectSessionById(id) {
      const found = SESSIONS.find(s => s.id === id);
      if (!found) return;
      activeSession = found;
      renderSessions(activeSessionFilter);
      updateProposalControls(found);

      if (found.type === 'agent') {
        switchTab('delegated');
        document.getElementById('delegatedTaskId').textContent = found.taskId || found.id;
        document.getElementById('delegatedGoal').textContent = found.goal || found.title;
        document.getElementById('delegatedFiles').textContent = found.file || 'src/main.py';
        document.getElementById('delegatedChecks').textContent = (found.checks && found.checks.join(', ')) || 'pytest';
        document.getElementById('delegatedFileName').textContent = found.file || 'src/main.py';
        document.getElementById('delegatedDiffContent').innerHTML = renderUnifiedDiff(found.patch);
      } else {
        switchTab('chat');
        renderSessionTranscript(found);
        document.getElementById('diffFileName').textContent = found.file || 'No active diff';
        document.getElementById('diffStatsTag').textContent = found.patch ? 'Diff Ready' : 'Empty';
        document.getElementById('diffContentArea').innerHTML = renderUnifiedDiff(found.patch);
      }
    }

    function onCtxOverrideChange(val) {
      const n = parseInt(val, 10);
      if (String(val).trim() && (!Number.isFinite(n) || n < 512)) {
        activeCtxOverride = null;
        showToast('Context override must be at least 512 tokens');
        return;
      }
      activeCtxOverride = Number.isFinite(n) ? n : null;
      const ctxEl = document.getElementById('profCtx');
      if (ctxEl) ctxEl.textContent = activeCtxOverride ? `${activeCtxOverride} tokens (override)` : `${ctxEl.textContent.replace(/ \(override\)$/, '')}`;
      showToast(activeCtxOverride ? `Context override: ${activeCtxOverride} tokens (applies on next prompt)` : 'Context override cleared');
    }

    function changeProfile(val) {
      const select = document.getElementById('modalProfileSelect');
      const option = select ? [...select.options].find(item => item.value === val) : null;
      if (!val || (option && option.disabled)) {
        showToast('Select an installed local model first');
        return;
      }
      activeProfile = val;
      if (select && select.value !== val) {
        select.value = val;
      }
      document.getElementById('telemetryModel').textContent = val;
      const isLlama = resolveProviderFromRegistry(val) === 'llama_server';
      document.getElementById('backendLabel').textContent = isLlama ? 'LLAMA-SERVER' : 'OLLAMA';
      const welcome = document.getElementById('welcomeModelLabel');
      if (welcome) welcome.textContent = val;
      
      const provEl = document.getElementById('profProvider');
      const endEl = document.getElementById('profEndpoint');
      if (provEl) provEl.textContent = isLlama ? 'llama-server' : 'ollama';
      if (endEl) endEl.textContent = isLlama ? 'http://127.0.0.1:8080' : 'http://127.0.0.1:11434';

      showToast(`Active profile: ${val}`);
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const data = await res.json();
          if (data.workspace_name) document.getElementById('workspaceName').textContent = data.workspace_name;
          if (data.git_branch) document.getElementById('workspaceBranch').textContent = `• ${data.git_branch}`;
          const wsPath = document.getElementById('setWorkspacePath');
          if (wsPath && data.workspace) wsPath.textContent = data.workspace;

          // Real GPU & VRAM from nvidia-smi
          if (data.vram) {
            const v = data.vram;
            document.getElementById('telemetryVram').textContent = `${v.used_gb}/${v.total_gb}G`;
            
            const devName = document.getElementById('gpuDeviceName');
            if (devName && v.gpu_name) devName.textContent = v.gpu_name;
            
            const vText = document.getElementById('gpuVramText');
            if (vText) vText.textContent = `${v.used_gb} / ${v.total_gb} GB (${v.percent}%)`;
            
            const vBar = document.getElementById('gpuVramBar');
            if (vBar) vBar.style.width = `${v.percent}%`;

            const gLoad = document.getElementById('gpuLoadPct');
            if (gLoad && v.utilization_pct !== undefined) gLoad.textContent = `${v.utilization_pct}%`;

            const gTemp = document.getElementById('gpuTemp');
            if (gTemp && v.temp_c !== undefined) gTemp.textContent = `${v.temp_c}°C`;
          }

          // Real Server status
          const ollamaOnline = Boolean(data.servers && data.servers.ollama && data.servers.ollama.online);
          const llamaOnline = Boolean(data.servers && data.servers.llama_server && data.servers.llama_server.online);
          const llamaStatus = (data.servers && data.servers.llama_server && data.servers.llama_server.status) || (llamaOnline ? 'ready' : 'offline');
          
          const dotOllama = document.getElementById('dotOllama');
          const dotLlama = document.getElementById('dotLlama');
          const labelOllama = document.getElementById('labelOllamaStatus');
          const labelLlama = document.getElementById('labelLlamaStatus');
          
          if (dotOllama) dotOllama.className = `w-2 h-2 rounded-full ${ollamaOnline ? 'bg-emerald-500' : 'bg-red-500'}`;
          if (labelOllama) {
            labelOllama.textContent = ollamaOnline ? 'Online (Port 11434)' : 'Offline';
          }

          if (dotLlama) {
            dotLlama.className = `w-2 h-2 rounded-full ${llamaStatus === 'loading' ? 'bg-amber-500 animate-pulse' : (llamaOnline ? 'bg-emerald-500' : 'bg-red-500')}`;
          }
          if (labelLlama) {
            if (llamaStatus === 'loading') {
              labelLlama.innerHTML = `<span class="text-amber-400 font-medium">Loading weights into VRAM (:8080)...</span>`;
            } else {
              labelLlama.textContent = llamaOnline ? 'Online (Port 8080)' : 'Offline';
            }
          }

          const isCurrentLlama = resolveProviderFromRegistry(activeProfile) === 'llama_server';
          const isCurrentOnline = isCurrentLlama ? llamaOnline : ollamaOnline;
          
          const topDot = document.getElementById('serverLiveDot');
          const topText = document.getElementById('serverLiveText');
          if (topDot) {
            if (isCurrentLlama && llamaStatus === 'loading') {
              topDot.className = 'w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse';
            } else {
              topDot.className = `w-1.5 h-1.5 rounded-full ${isCurrentOnline ? 'bg-emerald-500' : 'bg-red-500'}`;
            }
          }
          if (topText) {
            if (isCurrentLlama && llamaStatus === 'loading') {
              topText.textContent = 'Loading...';
            } else {
              topText.textContent = isCurrentOnline ? 'Online' : 'Offline';
            }
          }
        }
      } catch (e) {}
    }

    async function warmupActiveModel() {
      showToast(`Preloading ${activeProfile} into VRAM...`);
      const btn1 = document.getElementById('btnPreloadModel');
      const btn2 = document.getElementById('btnModalPreload');
      const statusLabel = document.getElementById('labelModelLoadStatus');
      const old1 = btn1 ? btn1.innerHTML : '';
      const old2 = btn2 ? btn2.innerHTML : '';

      if (btn1) {
        btn1.innerHTML = `<span class="inline-flex items-center gap-1.5 text-cyan-400">${spinnerSvg(11, 'text-cyan-400')} Loading into VRAM...</span>`;
        btn1.disabled = true;
      }
      if (btn2) {
        btn2.innerHTML = `<span class="inline-flex items-center gap-1.5 text-cyan-400">${spinnerSvg(11, 'text-cyan-400')} Loading...</span>`;
        btn2.disabled = true;
      }
      if (statusLabel) {
        statusLabel.innerHTML = `<span class="text-cyan-400 inline-flex items-center gap-1">${spinnerSvg(10, 'text-cyan-400')} Loading model into VRAM...</span>`;
      }

      try {
        const res = await fetch('/api/model/load', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ model: activeProfile, ...(activeCtxOverride ? { num_ctx: activeCtxOverride } : {}) })
        });
        const data = await res.json();
        if (data.status === 'loaded') {
          const back = data.backend === 'llama_server' ? 'via llama-server' : (data.backend === 'ollama' ? 'via Ollama' : '');
          showToast(`✓ Model ${data.model || activeProfile} loaded ${back}`.trim());
          if (statusLabel) statusLabel.innerHTML = `<span class="text-emerald-400">✓ Loaded in VRAM${data.backend === 'llama_server' ? ' (llama-server)' : ''}</span>`;
        } else {
          showToast(`⚠️ Load issue: ${data.error || 'failed'}`);
          if (statusLabel) statusLabel.innerHTML = `<span class="text-amber-400">Offline / Error</span>`;
        }
        pollStatus();
      } catch (e) {
        showToast('Error loading model');
        if (statusLabel) statusLabel.innerHTML = `<span class="text-red-400">Connection error</span>`;
      } finally {
        if (btn1) {
          btn1.innerHTML = old1 || '⚡ Preload Model';
          btn1.disabled = false;
        }
        if (btn2) {
          btn2.innerHTML = old2 || '⚡ Load into VRAM';
          btn2.disabled = false;
        }
        safeCreateIcons();
      }
    }

    async function unloadAllVram() {
      if (!confirm('Unload every managed model from VRAM?')) return;
      const btn = document.getElementById('btnUnloadVram');
      const oldHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.innerHTML = `<span class="inline-flex items-center gap-1.5 text-red-400">${spinnerSvg(11, 'text-red-400')} Ejecting...</span>`;
        btn.disabled = true;
      }
      showToast('Ejecting models from VRAM...');
      try {
        const res = await fetch('/api/model/unload_all', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'unloaded_all') {
          showToast('✓ All managed models unloaded from VRAM');
          pollStatus();
        } else {
          showToast(`Unload failed: ${data.error || data.status || 'unknown error'}`);
        }
      } catch (e) {
        showToast('Error unloading models');
      } finally {
        if (btn) {
          btn.innerHTML = oldHtml || 'Eject ALL from VRAM';
          btn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    function toggleTheme() {
      const html = document.documentElement;
      html.classList.toggle('dark');
      safeCreateIcons();
      showToast(`Switched to ${html.classList.contains('dark') ? 'Dark' : 'Light'} theme`);
    }

    function switchTab(tabId) {
      document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('bg-[var(--bg-card)]', 'text-[var(--text-main)]', 'shadow-sm', 'border', 'border-[var(--border-main)]');
        btn.classList.add('text-[var(--text-muted)]');
      });
      document.querySelectorAll('.tab-view').forEach(view => view.classList.add('hidden'));

      if (tabId === 'chat') {
        const btn = document.getElementById('tab-btn-chat');
        btn.classList.add('bg-[var(--bg-card)]', 'text-[var(--text-main)]', 'shadow-sm', 'border', 'border-[var(--border-main)]');
        btn.classList.remove('text-[var(--text-muted)]');
        document.getElementById('view-chat').classList.remove('hidden');
      } else if (tabId === 'delegated') {
        const btn = document.getElementById('tab-btn-delegated');
        btn.classList.add('bg-[var(--bg-card)]', 'text-[var(--text-main)]', 'shadow-sm', 'border', 'border-[var(--border-main)]');
        btn.classList.remove('text-[var(--text-muted)]');
        document.getElementById('view-delegated').classList.remove('hidden');
      }
    }

    function toggleSidebar() {
      const sb = document.getElementById('sessionSidebar');
      sb.classList.toggle('hidden');
    }

    function showToast(msg) {
      const toast = document.getElementById('toast');
      const toastText = document.getElementById('toastText');
      toastText.textContent = msg;
      toast.classList.remove('opacity-0', 'translate-y-2');
      toast.classList.add('opacity-100', 'translate-y-0');
      setTimeout(() => {
        toast.classList.remove('opacity-100', 'translate-y-0');
        toast.classList.add('opacity-0', 'translate-y-2');
      }, 2500);
    }

    async function startNewSession() {
      // Unique across rapid clicks in the same millisecond.
      const newId = `sess-${(window.crypto && crypto.randomUUID) ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2)}`;
      const newSession = {
        id: newId,
        type: 'user',
        title: 'New coding task',
        file: 'workspace',
        patch: '',
        checks: [],
        status: 'Draft',
        time: 'Just now'
      };
      try {
        await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(newSession)
        });
      } catch (e) {}

      SESSIONS.unshift(newSession);
      activeSession = newSession;
      filterSessions('all');
      updateProposalControls(newSession);
      switchTab('chat');
      const messages = document.getElementById('chatMessages');
      if (messages) messages.innerHTML = '<div class="p-8 text-center text-zinc-500 text-xs">New session. Enter a prompt to begin.</div>';
      document.getElementById('chatInput').focus();
      showToast('Started new interactive chat session');
    }

    function filterSessions(type, evt) {
      activeSessionFilter = type;
      document.querySelectorAll('.filter-chip').forEach(c => {
        c.classList.remove('bg-[var(--bg-card)]', 'border', 'border-[var(--border-main)]', 'text-[var(--text-main)]', 'font-semibold');
        c.classList.add('text-zinc-500');
      });
      const selectedChip = (evt && evt.currentTarget) || document.querySelector(`[data-session-filter="${type}"]`);
      if (selectedChip) {
        selectedChip.classList.add('bg-[var(--bg-card)]', 'border', 'border-[var(--border-main)]', 'text-[var(--text-main)]', 'font-semibold');
        selectedChip.classList.remove('text-zinc-500');
      }
      renderSessions(activeSessionFilter);
    }

    function copyActiveDiff() {
      if (activeSession && activeSession.patch) {
        navigator.clipboard.writeText(activeSession.patch).then(() => {
          showToast('✓ Raw unified diff copied to clipboard!');
        }).catch(() => {
          showToast('Could not access clipboard');
        });
      } else {
        showToast('No active diff to copy');
      }
    }

    async function applyProposalAction() {
      if (!activeSession || !activeSession.patch) {
        showToast('No patch proposal available to apply');
        return;
      }
      const btn1 = document.getElementById('btnApply');
      const btn2 = document.getElementById('btnDelegatedApply');
      const old1 = btn1 ? btn1.innerHTML : '';
      const old2 = btn2 ? btn2.innerHTML : '';
      if (btn1) {
        btn1.innerHTML = `<span class="inline-flex items-center gap-1 text-zinc-950 font-semibold">${spinnerSvg(11, 'text-zinc-950')} Applying...</span>`;
        btn1.disabled = true;
      }
      if (btn2) {
        btn2.innerHTML = `<span class="inline-flex items-center gap-1 text-zinc-950 font-semibold">${spinnerSvg(11, 'text-zinc-950')} Applying &amp; Verifying...</span>`;
        btn2.disabled = true;
      }

      try {
        const res = await fetch('/api/apply', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ patch: activeSession.patch, checks: activeSession.checks || ['pytest'], files: (activeSession.files && activeSession.files.length) ? activeSession.files : (activeSession.file ? [activeSession.file] : []) })
        });
        const data = await res.json();
        if (data.status === 'applied') {
          rollbackAvailable = true;
          updateProposalControls(activeSession);
          showToast('✓ Patch applied to workspace and re-verified by test runner!');
        } else {
          showToast(`⚠️ Apply issue: ${data.error || 'Check failed'}`);
        }
      } catch (err) {
        showToast('Error sending apply request to server');
      } finally {
        if (btn1) { btn1.innerHTML = old1 || 'Apply (Ctrl+A)'; btn1.disabled = false; }
        if (btn2) { btn2.innerHTML = old2 || 'Apply Proposal (Ctrl+A)'; btn2.disabled = false; }
        safeCreateIcons();
      }
    }

    async function rollbackAction() {
      const btn1 = document.getElementById('btnRollback');
      const btn2 = document.getElementById('btnDelegatedRollback');
      const old1 = btn1 ? btn1.innerHTML : '';
      const old2 = btn2 ? btn2.innerHTML : '';
      if (btn1) {
        btn1.innerHTML = `<span class="inline-flex items-center gap-1 text-zinc-400">${spinnerSvg(11, 'text-zinc-400')} Restoring...</span>`;
        btn1.disabled = true;
      }
      if (btn2) {
        btn2.innerHTML = `<span class="inline-flex items-center gap-1 text-zinc-400">${spinnerSvg(11, 'text-zinc-400')} Auto-Rollback...</span>`;
        btn2.disabled = true;
      }

      try {
        const res = await fetch('/api/rollback', { method: 'POST', headers: {'Content-Type': 'application/json'} });
        const data = await res.json();
        if (data.status === 'rolled_back') {
          rollbackAvailable = false;
          updateProposalControls(activeSession);
          const restored = (data.restored || []).length;
          showToast(restored ? `↺ Restored ${restored} file(s)` : 'Nothing to roll back');
        } else {
          showToast(`Rollback issue: ${data.error || 'failed'}`);
        }
      } catch (err) {
        showToast('Rollback request failed');
      } finally {
        if (btn1) { btn1.innerHTML = old1 || 'Rollback'; btn1.disabled = false; }
        if (btn2) { btn2.innerHTML = old2 || 'Auto-Rollback (git restore)'; btn2.disabled = false; }
        safeCreateIcons();
      }
    }

    async function runDoctorCheck() {
      const btn = document.getElementById('btnDoctor');
      const oldHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.innerHTML = `<span class="inline-flex items-center gap-1.5 text-cyan-400">${spinnerSvg(11, 'text-cyan-400')} Running Doctor...</span>`;
        btn.disabled = true;
      }
      try {
        const res = await fetch('/api/doctor/fix', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.status === 'ok' && data.report && data.report.success) {
          const count = (data.report.actions || []).length;
          showToast(`✓ Doctor completed (${count} configuration action${count === 1 ? '' : 's'})`);
        } else if (res.ok && data.status === 'partial') {
          showToast(`⚠️ Doctor partially applied: ${(data.report.actions || []).length} ok, errors: ${data.error || 'unknown'}`);
        } else {
          showToast(`Doctor failed: ${data.error || 'unknown error'}`);
        }
      } catch (error) {
        showToast('Doctor request failed');
      } finally {
        if (btn) {
          btn.innerHTML = oldHtml || 'Run Doctor';
          btn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    function setPromptAndRun(promptText) {
      document.getElementById('chatInput').value = promptText;
      handleUserSubmit();
    }

    function renderUserPrompt(text) {
      if (!text) return;
      const container = document.getElementById('chatMessages');
      const userDiv = document.createElement('div');
      userDiv.className = 'flex items-start gap-2.5 max-w-2xl';
      userDiv.innerHTML = `
        <div class="w-6 h-6 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] flex items-center justify-center shrink-0 text-[10px] font-mono font-semibold text-zinc-400">DEV</div>
        <div class="flex-1">
          <div class="text-[11px] font-medium text-zinc-500 mb-1 flex items-center gap-2"><span>Developer</span><span class="text-[9px] font-mono text-zinc-500">Interactive Prompt</span></div>
          <div class="p-3 rounded-lg bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-[var(--text-main)] leading-relaxed shadow-xs">${escapeHtml(text)}</div>
        </div>
      `;
      container.appendChild(userDiv);
    }

    function renderSessionTranscript(session) {
      const container = document.getElementById('chatMessages');
      if (!container) return;
      container.innerHTML = '';
      renderUserPrompt(session.prompt || session.title || '');
      if (session.message || session.thinking || session.plan) {
        renderAssistantResponse({
          ...session,
          checks: session.test_evidence || [],
        });
      } else {
        container.innerHTML += '<div class="p-8 text-center text-zinc-500 text-xs">No saved transcript for this older session.</div>';
      }
      container.scrollTop = container.scrollHeight;
    }

    async function handleUserSubmit() {
      const input = document.getElementById('chatInput');
      const val = input.value.trim();
      if (!val) {
        showToast('Please enter a prompt');
        input.focus();
        return;
      }
      if (!activeProfile) {
        showToast('No local models available. Install or discover a model first.');
        openModal('modelModal');
        return;
      }

      const container = document.getElementById('chatMessages');
      const sendBtn = document.getElementById('btnSendChat');
      const oldSendHtml = sendBtn ? sendBtn.innerHTML : '';

      renderUserPrompt(val);
      input.value = '';

      // Live Thinking / Loading Placeholder Card with Spinner
      const thinkingCard = document.createElement('div');
      thinkingCard.id = 'chatThinkingCard';
      thinkingCard.className = 'flex items-start gap-2.5 max-w-2xl';
      thinkingCard.innerHTML = `
        <div class="w-6 h-6 rounded bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shrink-0 text-[10px] font-mono font-semibold text-cyan-500">
          ${spinnerSvg(12, 'text-cyan-400')}
        </div>
        <div class="flex-1 space-y-2">
          <div class="text-[11px] font-medium text-zinc-500 flex items-center gap-1.5">
            <span>${escapeHtml(activeProfile)}</span>
            <span class="text-[9px] font-mono text-cyan-500">• Processing Task</span>
          </div>
          <div class="rounded border border-[var(--border-main)] bg-[var(--bg-card)] p-3 text-xs text-zinc-300 flex items-center gap-2.5 font-mono text-[11px] shadow-xs">
            ${spinnerSvg(14, 'text-cyan-400 shrink-0')}
            <span>Model is processing prompt, formulating diff &amp; validating test checks...</span>
          </div>
        </div>
      `;
      container.appendChild(thinkingCard);
      container.scrollTop = container.scrollHeight;

      if (sendBtn) {
        sendBtn.innerHTML = spinnerSvg(14, 'text-zinc-950');
        sendBtn.disabled = true;
      }

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: val, profile: activeProfile, mode: SELECTED_MODE, ...(activeCtxOverride ? { num_ctx: activeCtxOverride } : {}) })
        });
        const data = await res.json();
        
        const existingThinking = document.getElementById('chatThinkingCard');
        if (existingThinking) existingThinking.remove();

        if (data.status === 'failed' && data.offline_server) {
          renderOfflineHelperCard(data.offline_server, data.error);
          return;
        }

        renderAssistantResponse(data);
        if (data.patch) {
          document.getElementById('diffFileName').textContent = data.file || 'patch.diff';
          document.getElementById('diffContentArea').innerHTML = renderUnifiedDiff(data.patch);
          if (activeSession) {
            activeSession.patch = data.patch;
            activeSession.file = data.file || 'patch.diff';
            activeSession.files = (data.files && data.files.length) ? data.files : (data.file ? [data.file] : []);
          }
        }
        loadSessions();
      } catch (e) {
        const existingThinking = document.getElementById('chatThinkingCard');
        if (existingThinking) existingThinking.remove();
        renderOfflineHelperCard('ollama', 'Connection error to local server');
      } finally {
        if (sendBtn) {
          sendBtn.innerHTML = oldSendHtml || '<span>Run</span>';
          sendBtn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    function renderOfflineHelperCard(backend, errorMsg) {
      const container = document.getElementById('chatMessages');
      const errDiv = document.createElement('div');
      errDiv.className = 'flex items-start gap-2.5 max-w-2xl';
      errDiv.innerHTML = `
        <div class="w-6 h-6 rounded bg-amber-500/10 border border-amber-500/30 flex items-center justify-center shrink-0 text-[10px] font-mono font-semibold text-amber-500">⚠️</div>
        <div class="flex-1 space-y-2">
          <div class="text-[11px] font-medium text-zinc-500">Engine Offline Notice</div>
          <div class="p-3.5 rounded-lg bg-amber-950/20 border border-amber-500/30 text-xs text-amber-200 leading-relaxed space-y-2">
            <div>${escapeHtml(errorMsg)}</div>
            <div class="flex items-center gap-2 pt-1">
              <button onclick="warmupActiveModel()" class="px-2.5 py-1 rounded bg-amber-500 hover:bg-amber-400 text-zinc-950 font-semibold text-[11px] transition">
                ⚡ Load Model &amp; Start
              </button>
              <button onclick="openModal('modelModal')" class="px-2 py-1 rounded bg-[var(--bg-card)] hover:border-zinc-500 border border-[var(--border-main)] text-zinc-300 text-[10px] transition">
                Change Model
              </button>
            </div>
          </div>
        </div>
      `;
      container.appendChild(errDiv);
      safeCreateIcons();
      container.scrollTop = container.scrollHeight;
    }

    function copyCodeSnippet(code) {
      navigator.clipboard.writeText(code).then(() => {
        showToast('✓ Code copied to clipboard');
      }).catch(() => {
        showToast('Failed to copy code');
      });
    }

    function formatMarkdown(text) {
      if (!text) return '';
      let html = escapeHtml(text);

      // LaTeX math $...$ — dependency-free unicode mapping so local models'
      // math notation renders instead of showing raw markup. Runs BEFORE code
      // blocks / inline code so $ inside code stays untouched. Doubled
      // backslashes are preserved because DESKTOP_CLIENT_JS is a raw Python string.
      html = html.replace(/\$([^$\n]+?)\$/g, (match, inner) => {
        let mapped = false;
        const rendered = inner.replace(/\\/g, '')
          .replace(/\brightarrows?\b/g, () => { mapped = true; return '→'; })
          .replace(/\bleftarrow\b/g, () => { mapped = true; return '←'; })
          .replace(/\bleftrightarrow\b/g, () => { mapped = true; return '↔'; })
          .replace(/\bgeq\b/g, () => { mapped = true; return '≥'; })
          .replace(/\bleq\b/g, () => { mapped = true; return '≤'; })
          .replace(/\bneq\b/g, () => { mapped = true; return '≠'; })
          .replace(/\balpha\b/g, () => { mapped = true; return 'α'; }).replace(/\bbeta\b/g, () => { mapped = true; return 'β'; }).replace(/\bgamma\b/g, () => { mapped = true; return 'γ'; })
          .replace(/\bdelta\b/g, () => { mapped = true; return 'δ'; }).replace(/\btheta\b/g, () => { mapped = true; return 'θ'; }).replace(/\blambda\b/g, () => { mapped = true; return 'λ'; })
          .replace(/\bmu\b/g, () => { mapped = true; return 'μ'; }).replace(/\bpi\b/g, () => { mapped = true; return 'π'; }).replace(/\bsigma\b/g, () => { mapped = true; return 'σ'; })
          .replace(/\bomega\b/g, () => { mapped = true; return 'ω'; }).replace(/\btimes\b/g, () => { mapped = true; return '×'; }).replace(/\bdiv\b/g, () => { mapped = true; return '÷'; })
          .replace(/\bsum\b/g, () => { mapped = true; return '∑'; }).replace(/\bint\b/g, () => { mapped = true; return '∫'; }).replace(/\bsqrt\b/g, () => { mapped = true; return '√'; })
          .replace(/\binfty\b/g, () => { mapped = true; return '∞'; }).replace(/\bapprox\b/g, () => { mapped = true; return '≈'; })
          .trim();
        return mapped && rendered ? `<span class="px-1 font-mono text-[12px] text-cyan-200">${rendered}</span>` : match;
      });

      // Fenced code blocks ```lang ... ```
      html = html.replace(/```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```/g, (match, lang, code) => {
        const langLabel = lang || 'code';
        const cleanCode = code.replace(/^\n+|\n+$/g, '');
        const rawAttr = encodeURIComponent(cleanCode).replace(/'/g, "%27");
        return `
          <div class="my-2.5 rounded-lg border border-[var(--border-main)] bg-[var(--bg-card-subtle)] overflow-hidden font-mono text-[11px]">
            <div class="flex items-center justify-between px-3 py-1.5 bg-[var(--bg-card)] border-b border-[var(--border-main)] text-[10px] text-zinc-400">
              <span class="font-semibold text-cyan-400">${langLabel}</span>
              <button onclick="copyCodeSnippet(decodeURIComponent('${rawAttr}'))" class="hover:text-zinc-200 transition flex items-center gap-1">
                <i data-lucide="copy" class="w-3 h-3"></i> Copy
              </button>
            </div>
            <pre class="p-3 overflow-x-auto text-zinc-200 leading-relaxed font-mono"><code>${cleanCode}</code></pre>
          </div>
        `;
      });

      // Inline `code`
      html = html.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] text-cyan-300 font-mono text-[11px]">$1</code>');

      // Bold **text**
      html = html.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-zinc-100">$1</strong>');

      // Bullet points
      html = html.replace(/^[\*\-]\s+(.+)$/gm, '<li class="ml-4 list-disc text-zinc-300">$1</li>');

      // Paragraph breaks
      html = html.replace(/\n\n/g, '<br><br>');
      html = html.replace(/\n/g, '<br>');

      return html;
    }

    function renderAssistantResponse(data) {
      const container = document.getElementById('chatMessages');
      const aiDiv = document.createElement('div');
      aiDiv.className = 'flex items-start gap-2.5 max-w-2xl';

      // Reflect the resolved mode in the badges
      if (data.mode) {
        const modeLabel = data.mode === 'hybrid' ? 'auto' : data.mode;
        const badge = document.getElementById('modeBadge');
        if (badge) badge.textContent = `Mode: ${modeLabel}`;
        const welcomeBadge = document.getElementById('welcomeModeLabel');
        if (welcomeBadge) welcomeBadge.textContent = `Mode: ${modeLabel}`;
      }

      let planCardHtml = '';
      if (data.plan) {
        const p = data.plan;
        const steps = (p.steps || []).map((s, i) => `<li class="flex items-start gap-1.5"><span class="w-4 h-4 shrink-0 rounded bg-cyan-500/10 border border-cyan-500/30 text-cyan-500 font-mono text-[9px] flex items-center justify-center font-semibold mt-0.5">${i + 1}</span><span class="text-zinc-300">${escapeHtml(s)}</span></li>`).join('');
        const risks = (p.risks || []).map(r => `<li class="text-zinc-400">• ${escapeHtml(r)}</li>`).join('');
        const files = (p.files_to_modify || []).map(f => `<span class="px-1.5 py-0.5 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] font-mono text-[10px] text-cyan-300">${escapeHtml(f)}</span>`).join('');
        planCardHtml = `
          <div class="rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-3 text-xs space-y-2.5">
            <div class="flex items-center gap-1.5 text-cyan-400 font-semibold text-[11px]"><i data-lucide="route" class="w-3 h-3"></i> Execution Plan</div>
            ${p.goal ? `<div class="text-[var(--text-main)]"><span class="text-zinc-500 font-mono text-[10px]">GOAL: </span>${escapeHtml(p.goal)}</div>` : ''}
            ${steps ? `<div class="space-y-1"><div class="text-zinc-500 font-mono text-[10px]">STEPS</div><ol class="space-y-1">${steps}</ol></div>` : ''}
            ${risks ? `<div class="space-y-0.5"><div class="text-zinc-500 font-mono text-[10px]">RISKS</div><ul class="space-y-0.5">${risks}</ul></div>` : ''}
            ${files ? `<div class="space-y-1"><div class="text-zinc-500 font-mono text-[10px]">FILES TO MODIFY</div><div class="flex flex-wrap gap-1">${files}</div></div>` : ''}
          </div>
        `;
      }

      let testCardHtml = '';
      if (data.checks && data.checks.length > 0 && data.testResult && data.testResult !== 'READY') {
        const isPass = data.testResult === 'PASSED' || data.testResult === 'ALL CHECKS GREEN';
        testCardHtml = `
          <div class="rounded border border-[var(--border-main)] bg-[var(--bg-card)] p-2 flex items-center justify-between text-xs font-mono text-[10px]">
            <span class="text-zinc-400">${escapeHtml(data.checks.join(', '))}</span>
            <span class="${isPass ? 'text-emerald-500' : 'text-amber-500'} font-semibold">${escapeHtml(data.testResult)}</span>
          </div>
        `;
      }

      let diffBadgeHtml = '';
      if (data.patch && data.patch.trim()) {
        diffBadgeHtml = `
          <div class="flex items-center justify-between p-2 rounded bg-cyan-950/20 border border-cyan-500/30 text-xs">
            <span class="text-cyan-300 font-mono text-[11px]">⚡ Proposed changes for <strong>${escapeHtml(data.file || 'patch.diff')}</strong></span>
            <button onclick="switchTab('delegated')" class="px-2 py-0.5 rounded bg-cyan-500 hover:bg-cyan-400 text-zinc-950 text-[10px] font-semibold transition">
              Review Diff Studio
            </button>
          </div>
        `;
      }

      aiDiv.innerHTML = `
        <div class="w-6 h-6 rounded bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shrink-0 text-[10px] font-mono font-semibold text-cyan-500">AI</div>
        <div class="flex-1 space-y-2">
          <div class="text-[11px] font-medium text-zinc-500 flex items-center gap-1.5">
            <span>${escapeHtml(data.profile || activeProfile)}</span>
            <span class="text-[9px] font-mono text-zinc-500">• AI Response</span>
          </div>
          ${data.thinking ? `
            <div class="rounded border border-[var(--border-main)] bg-[var(--bg-card)] p-2 text-xs text-zinc-400 space-y-1 font-mono text-[10px]">
              <div class="flex items-center gap-1.5 text-amber-500 font-semibold"><i data-lucide="sparkles" class="w-2.5 h-2.5"></i> Thinking &amp; Analysis</div>
              <div>• ${escapeHtml(data.thinking)}</div>
            </div>
          ` : ''}
          ${testCardHtml}
          ${planCardHtml}
          ${diffBadgeHtml}
          <div class="p-3.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-main)] text-xs text-[var(--text-main)] leading-relaxed space-y-2">
            ${formatMarkdown(data.message || '')}
          </div>
        </div>
      `;
      container.appendChild(aiDiv);
      safeCreateIcons();
      container.scrollTop = container.scrollHeight;
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    // ---- Background Task Queue ----
    let TASKS = [];

    async function submitQueuedTask(evt) {
      evt.preventDefault();
      const goalEl = document.getElementById('taskQueueGoal');
      const goal = (goalEl.value || '').trim();
      if (!goal) {
        showToast('Please enter a task goal');
        return;
      }
      const csv = (id) => (document.getElementById(id).value || '').split(',').map(s => s.trim()).filter(Boolean);
      const body = { goal: goal, files: csv('taskQueueFiles'), checks: csv('taskQueueChecks') };
      const prof = (document.getElementById('taskQueueProfile').value || '').trim();
      if (prof) body.profile = prof;
      try {
        const res = await fetch('/api/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const data = await res.json();
        if (data.status === 'queued') {
          goalEl.value = '';
          showToast(`✓ Queued ${data.task.id}`);
          pollTasks();
        } else {
          showToast(`⚠️ Could not queue task: ${data.error || 'unknown error'}`);
        }
      } catch (e) {
        showToast('Error submitting task');
      }
    }

    function taskStatusBadge(status) {
      const map = {
        queued: 'bg-zinc-500/10 border-zinc-500/30 text-zinc-400',
        running: 'bg-amber-500/10 border-amber-500/30 text-amber-500 animate-pulse',
        accepted: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500',
        failed: 'bg-red-500/10 border-red-500/30 text-red-400',
        cancelled: 'bg-zinc-500/10 border-zinc-500/30 text-zinc-500 line-through'
      };
      return map[status] || map.queued;
    }

    function taskDurationText(t) {
      if (!t.started_at) return '—';
      const end = t.finished_at || Date.now() / 1000;
      return `${Math.max(0, end - t.started_at).toFixed(1)}s`;
    }

    async function pollTasks() {
      try {
        const res = await fetch('/api/tasks');
        if (!res.ok) return;
        TASKS = (await res.json()).tasks || [];
        renderTaskQueue();
      } catch (e) {}
    }

    function renderTaskQueue() {
      const list = document.getElementById('taskQueueList');
      if (!list) return;
      if (!TASKS.length) {
        list.innerHTML = '<div class="p-2 text-center text-zinc-500 font-mono text-[10px]">No background tasks yet.</div>';
        return;
      }
      list.innerHTML = TASKS.map(t => `
        <div class="p-2 rounded bg-[var(--bg-card)] border border-[var(--border-main)] space-y-1">
          <div class="flex items-center justify-between gap-2">
            <span class="inline-flex items-center px-1 py-0.1 rounded border font-mono text-[9px] font-semibold tracking-wider ${taskStatusBadge(t.status)}">${escapeHtml(String(t.status).toUpperCase())}</span>
            <span class="font-mono text-[9px] text-zinc-500 num-tabular truncate">${escapeHtml(t.id || '')} • ${taskDurationText(t)}</span>
          </div>
          <div class="text-[11px] text-[var(--text-main)] truncate">${escapeHtml(t.goal || '')}</div>
          ${t.summary ? `<div class="text-[10px] text-zinc-400 leading-snug">${escapeHtml(t.summary)}</div>` : ''}
          ${t.error ? `<div class="text-[10px] text-red-400 leading-snug">${escapeHtml(typeof t.error === 'object' ? (t.error.message || '') : String(t.error))}</div>` : ''}
          <div class="flex items-center gap-1.5">
            ${(t.status === 'queued' || t.status === 'running') ? `<button onclick="cancelQueuedTask('${t.id}')" class="px-1.5 py-0.5 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] text-[10px] text-zinc-300 hover:text-zinc-100 transition">Cancel</button>` : ''}
            ${(t.status === 'accepted' && t.patch && t.patch.trim()) ? `<button onclick="applyQueuedTask('${t.id}')" class="px-1.5 py-0.5 rounded bg-emerald-600 hover:bg-emerald-500 text-zinc-950 font-semibold text-[10px] transition">Apply</button>
            <button onclick="rollbackQueuedTask()" class="px-1.5 py-0.5 rounded bg-[var(--bg-card-subtle)] border border-[var(--border-main)] text-[10px] text-zinc-300 hover:text-zinc-100 transition">Rollback</button>` : ''}
          </div>
        </div>
      `).join('');
    }

    async function cancelQueuedTask(id) {
      try {
        const res = await fetch('/api/tasks/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: id })
        });
        const data = await res.json();
        showToast(data.status === 'cancelling' ? `Cancelling ${id}...` : `Task ${data.status}: ${id}`);
        pollTasks();
      } catch (e) {
        showToast('Error cancelling task');
      }
    }

    async function applyQueuedTask(id) {
      const t = TASKS.find(x => x.id === id);
      if (!t || !t.patch) return;
      if (!confirm(`Apply the patch from ${id} to the workspace? Targeted checks will re-run.`)) return;
      try {
        const res = await fetch('/api/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ patch: t.patch, files: t.files || [], checks: t.checks || [] })
        });
        const data = await res.json();
        if (data.status === 'applied') {
          showToast(`✓ Patch applied and verified (${id})`);
        } else {
          showToast(`⚠️ Apply issue: ${(data.error || data.status || 'failed').toString().slice(0, 140)}`);
        }
      } catch (e) {
        showToast('Error sending apply request to server');
      }
    }

    async function rollbackQueuedTask() {
      if (!confirm('Roll back the most recently applied patch?')) return;
      try {
        const res = await fetch('/api/rollback', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (data.status === 'rolled_back') showToast('↺ Workspace restored cleanly');
        else showToast(`Rollback issue: ${data.error || data.status}`);
      } catch (e) {
        showToast('Error sending rollback request');
      }
    }

    document.addEventListener('keydown', event => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === 'b' && event.key.toLowerCase() === 'b') {
        event.preventDefault();
        toggleSidebar();
      } else if (key === 'n' && event.key.toLowerCase() === 'n') {
        event.preventDefault();
        startNewSession();
      } else if (key === 'a' && event.key.toLowerCase() === 'a') {
        const tag = (event.target && event.target.tagName) || '';
        if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) {
          event.preventDefault();
          applyProposalAction();
        }
      }
    });

    // Startup
    setMode(SELECTED_MODE, document.getElementById('mode-btn-hybrid'));
    loadSessions();
    fetchAndPopulateModels();
    pollStatus();
    setInterval(pollStatus, 2500);
    pollTasks();
    setInterval(pollTasks, 2000);
    updateProposalControls();
    safeCreateIcons();
"""
