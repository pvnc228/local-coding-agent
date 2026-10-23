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
      return `<svg class="spin ${extraClass}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`;
    }

    function icon(name, cls = 'i-s') {
      return `<i data-lucide="${name}" class="${cls}"></i>`;
    }

    // Galley numbers come from the real session or task id, never a counter.
    function galleyNo(id) {
      const clean = String(id || '').replace(/[^a-z0-9]/gi, '');
      return clean ? `G·${clean.slice(-4).toUpperCase()}` : 'G·—';
    }

    function verdictTone(status, verified) {
      const s = String(status || '').toLowerCase();
      if (verified === true || s === 'verified') return 'pass';
      if (/fail|reject|error/.test(s)) return 'red';
      if (/review|ready|pending|running|queued/.test(s)) return 'amber';
      if (/answer|plan/.test(s)) return 'blue';
      return 'mute';
    }

    function diffStats(rawDiff) {
      let add = 0;
      let del = 0;
      String(rawDiff || '').split('\n').forEach(line => {
        if (line.startsWith('+++') || line.startsWith('---')) return;
        if (line.startsWith('+')) add++;
        else if (line.startsWith('-')) del++;
      });
      return { add, del };
    }

    function statsHtml(rawDiff) {
      const st = diffStats(rawDiff);
      return `<span class="stat-add">+${st.add}</span> <span class="stat-del">−${st.del}</span>`;
    }

    let SESSIONS = [];
    let activeSession = null;
    let activeSessionFilter = 'all';
    let activeProfile = 'qwen2.5-coder';
    let activeCtxOverride = null;
    let SELECTED_MODE = 'hybrid';
    let modelProviders = {};  // model id -> backend ('ollama' | 'llama_server'), from /api/models
    let rollbackAvailable = false;

    function setEvidenceState(id, state) {
      const el = document.getElementById(id);
      if (el) el.dataset.state = state;
    }

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
      const pane = document.getElementById('delegatedPane');
      if (pane) pane.dataset.hasPatch = String(hasPatch);
      const status = document.getElementById('delegatedStatusTag');
      if (status) {
        status.textContent = hasPatch ? 'PROPOSAL READY' : 'NO PROPOSAL';
        status.className = `tag ${hasPatch ? 'tone-blue' : ''}`;
      }
      const state = evidenceVerified ? 'verified' : (hasPatch ? 'pending' : 'none');
      const evidence = document.getElementById('delegatedEvidenceTag');
      if (evidence) evidence.textContent = evidenceVerified ? 'Evidence: Verified by Test Runner' : 'Evidence: Not run';
      setEvidenceState('delegatedEvidence', state);
      const oracle = document.getElementById('diffEvidenceStatus');
      if (oracle) {
        const label = oracle.querySelector('span');
        if (label) label.textContent = evidenceVerified ? 'Oracles: External checks passed' : 'Oracles: Not run';
      }
      setEvidenceState('diffEvidenceStatus', state);
    }

    function showDiffPanel(show, animate = false) {
      const panel = document.getElementById('diffPanel');
      if (!panel) return;
      const wasHidden = panel.classList.contains('hidden');
      panel.classList.toggle('hidden', !show);
      if (show && wasHidden && animate) {
        panel.classList.remove('is-opening');
        void panel.offsetWidth;
        panel.classList.add('is-opening');
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
      document.querySelectorAll('.mode-btn').forEach(b => b.setAttribute('aria-pressed', 'false'));
      if (btn) btn.setAttribute('aria-pressed', 'true');
      const notes = {
        hybrid: 'Auto picks chat, plan or build per prompt',
        chat: 'Answers only, no patch',
        build: 'Proposes a patch for review',
        plan: 'Plans files and steps, no patch'
      };
      const badge = document.getElementById('modeBadge');
      if (badge) badge.textContent = notes[mode] || '';
      const welcomeBadge = document.getElementById('welcomeModeLabel');
      if (welcomeBadge) welcomeBadge.textContent = `Mode: ${mode === 'hybrid' ? 'auto' : mode}`;
    }

    function openModal(modalId) {
      closeModals();
      const m = document.getElementById(modalId);
      if (m) {
        m.classList.remove('hidden');
        const first = m.querySelector('select, input, .dialog-body button');
        if (first) setTimeout(() => first.focus(), 0);
      }
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
        refreshBtn.innerHTML = `${spinnerSvg(13)} <span>Loading registry…</span>`;
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
          optGroup.label = 'Local GGUF → launches llama-server (:8080)';
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
          optGroup.label = 'Active in llama-server (:8080)';
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
          optGroup.label = 'Predefined profiles (requires pulling)';
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
          changeProfile(currentVal, true);
        } else {
          const firstAvailable = [...select.options].find(o => !o.disabled);
          if (firstAvailable) {
            select.value = firstAvailable.value;
            changeProfile(firstAvailable.value, true);
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
          refreshBtn.innerHTML = oldRefreshHtml || `${icon('refresh-cw')} <span>Refresh list</span>`;
          refreshBtn.disabled = false;
          safeCreateIcons();
        }
      }
    }

    async function triggerModelScan(deep = true) {
      const btn = document.getElementById('btnDeepScan');
      const oldHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.innerHTML = `${spinnerSvg(13)} Scanning system…`;
        btn.disabled = true;
      }
      showToast('Running deep drive model scanner…');
      try {
        const res = await fetch('/api/models/scan', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ deep })
        });
        const data = await res.json();
        showToast(`Discovered ${data.total_models || data.models?.length || 0} models across drives`, 'ok');
        fetchAndPopulateModels();
      } catch (e) {
        showToast('Error during model scan', 'err');
      } finally {
        if (btn) {
          btn.innerHTML = oldHtml || 'Deep drive scan';
          btn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    async function addCustomModelFolder() {
      const input = document.getElementById('inputCustomDir');
      const pathVal = input ? input.value.trim() : '';
      if (!pathVal) {
        showToast('Please enter a folder path', 'warn');
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
          showToast(`Added custom folder: ${pathVal}`, 'ok');
          if (input) input.value = '';
          triggerModelScan(false);
        } else {
          showToast(`Issue adding folder: ${data.error || 'failed'}`, 'err');
        }
      } catch (e) {
        showToast('Error saving custom model directory', 'err');
      }
    }

    function renderUnifiedDiff(rawDiff, limit = 0) {
      if (!rawDiff || !rawDiff.trim()) {
        return '<div class="diff-empty">No active diff proposal. Run a prompt or select a task session.</div>';
      }

      const lines = rawDiff.split('\n');
      let html = '<div class="diff">';
      let oldLine = 0;
      let newLine = 0;
      let shown = 0;

      for (const line of lines) {
        if (limit && shown >= limit) break;
        if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('diff --git')) {
          if (!limit) html += `<div class="dl dl--file">${escapeHtml(line)}</div>`;
          continue;
        } else if (line.startsWith('@@')) {
          const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
          if (match) {
            oldLine = parseInt(match[1], 10);
            newLine = parseInt(match[2], 10);
          }
          html += `<div class="dl dl--hunk">${escapeHtml(line)}</div>`;
        } else if (line.startsWith('-')) {
          html += `<div class="dl dl--del"><span class="dl-no num">${oldLine ? oldLine++ : ''}</span><span class="dl-sign">−</span><span class="dl-code">${escapeHtml(line.slice(1))}</span></div>`;
        } else if (line.startsWith('+')) {
          html += `<div class="dl dl--add"><span class="dl-no num">${newLine ? newLine++ : ''}</span><span class="dl-sign">+</span><span class="dl-code">${escapeHtml(line.slice(1))}</span></div>`;
        } else {
          if (!line) continue;
          html += `<div class="dl"><span class="dl-no num">${oldLine ? oldLine++ : ''}</span><span class="dl-sign"></span><span class="dl-code">${escapeHtml(line.startsWith(' ') ? line.slice(1) : line)}</span></div>`;
          if (newLine) newLine++;
        }
        shown++;
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
      document.getElementById('sessionCounter').textContent = `${filtered.length} ${filtered.length === 1 ? 'session' : 'sessions'}`;

      if (filtered.length === 0) {
        list.innerHTML = `
          <div class="rail-empty">
            <strong>No ${filter !== 'all' ? filter + ' ' : ''}sessions yet</strong>
            ${filter === 'agent' ? 'Sessions appear here when a host agent delegates over MCP.' : 'Write an instruction to set the first galley.'}
          </div>
        `;
        return;
      }

      filtered.forEach(s => {
        const isSelected = activeSession && activeSession.id === s.id;
        const entry = document.createElement('button');
        entry.type = 'button';
        entry.className = 'galley-entry session-card';
        entry.setAttribute('aria-current', isSelected ? 'true' : 'false');
        entry.onclick = () => selectSessionById(s.id);

        const kind = s.type === 'user' ? (s.profile || 'Local chat') : `${s.agent || 'MCP'} delegated`;
        // The backend stores a literal "Just now" at creation, so it is not a real timestamp.
        const time = s.time && s.time !== 'Just now' ? s.time : '';
        const tone = verdictTone(s.status, s.evidence_verified);

        entry.innerHTML = `
          <span class="ge-no">${galleyNo(s.id)}</span>
          <span class="ge-kind">${escapeHtml(kind)}</span>
          <span class="ge-time num">${escapeHtml(time)}</span>
          <span class="ge-title">${escapeHtml(s.title || s.goal || 'Session')}</span>
          <span class="ge-meta">${escapeHtml(s.file || 'workspace')}</span>
          <span class="ge-verdict v-${tone}">${escapeHtml(s.status || 'Active')}</span>
        `;
        list.appendChild(entry);
      });
    }

    function selectSessionById(id) {
      const found = SESSIONS.find(s => s.id === id);
      if (!found) return;
      activeSession = found;
      renderSessions(activeSessionFilter);
      updateProposalControls(found);

      if (found.type === 'agent') {
        switchTab('delegated');
        const agent = document.getElementById('delegatedAgent');
        if (agent) agent.innerHTML = `${icon('bot')}<span>${escapeHtml(found.agent ? `${found.agent} (MCP)` : '—')}</span>`;
        document.getElementById('delegatedTaskId').textContent = found.taskId || found.id;
        document.getElementById('delegatedGoal').textContent = found.goal || found.title;
        const files = (found.files && found.files.length) ? found.files.join(', ') : (found.file || '');
        document.getElementById('delegatedFiles').textContent = files || '—';
        document.getElementById('delegatedChecks').textContent = (found.checks && found.checks.join(', ')) || 'None declared';
        document.getElementById('delegatedFileName').textContent = files || 'No patch file';
        const label = document.getElementById('delegatedProposalLabel');
        if (label) label.innerHTML = found.patch ? statsHtml(found.patch) : 'No patch';
        document.getElementById('delegatedDiffContent').innerHTML = renderUnifiedDiff(found.patch);
        safeCreateIcons();
      } else {
        switchTab('chat');
        renderSessionTranscript(found);
        document.getElementById('diffFileName').textContent = found.file || 'No active diff';
        document.getElementById('diffStatsTag').innerHTML = found.patch ? statsHtml(found.patch) : 'Empty';
        document.getElementById('diffContentArea').innerHTML = renderUnifiedDiff(found.patch);
        showDiffPanel(Boolean(found.patch && found.patch.trim()));
      }
    }

    function onCtxOverrideChange(val) {
      const n = parseInt(val, 10);
      if (String(val).trim() && (!Number.isFinite(n) || n < 512)) {
        activeCtxOverride = null;
        showToast('Context override must be at least 512 tokens', 'warn');
        return;
      }
      activeCtxOverride = Number.isFinite(n) ? n : null;
      const ctxEl = document.getElementById('profCtx');
      if (ctxEl) ctxEl.textContent = activeCtxOverride ? `${activeCtxOverride} tokens (override)` : `${ctxEl.textContent.replace(/ \(override\)$/, '')}`;
      showToast(activeCtxOverride ? `Context override: ${activeCtxOverride} tokens (applies on next prompt)` : 'Context override cleared');
    }

    function changeProfile(val, silent = false) {
      const select = document.getElementById('modalProfileSelect');
      const option = select ? [...select.options].find(item => item.value === val) : null;
      if (!val || (option && option.disabled)) {
        showToast('Select an installed local model first', 'warn');
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

      if (!silent) showToast(`Active profile: ${val}`);
    }

    function setDot(el, state) {
      if (el) el.className = `dot dot--${state}`;
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const data = await res.json();
          if (data.workspace_name) document.getElementById('workspaceName').textContent = data.workspace_name;
          if (data.git_branch) document.getElementById('workspaceBranch').textContent = data.git_branch;
          const wsPath = document.getElementById('setWorkspacePath');
          if (wsPath && data.workspace) wsPath.textContent = data.workspace;

          // Real GPU & VRAM from nvidia-smi
          if (data.vram) {
            const v = data.vram;
            const telemetryKnown = Number.isFinite(v.used_gb) && Number.isFinite(v.total_gb);
            document.getElementById('telemetryVram').textContent = telemetryKnown ? `${v.used_gb}/${v.total_gb} GB` : 'VRAM ?';

            const topBar = document.getElementById('topVramBar');
            const topMeter = document.getElementById('topVramMeter');
            const pct = telemetryKnown && Number.isFinite(v.percent) ? v.percent : 0;
            if (topBar) topBar.style.width = `${pct}%`;
            if (topMeter) topMeter.classList.toggle('meter--hot', pct >= 90);

            const devName = document.getElementById('gpuDeviceName');
            if (devName) devName.textContent = v.gpu_name || 'GPU telemetry unavailable';

            const vText = document.getElementById('gpuVramText');
            if (vText) vText.textContent = telemetryKnown ? `${v.used_gb} / ${v.total_gb} GB (${v.percent}%)` : 'VRAM unavailable';

            const vBar = document.getElementById('gpuVramBar');
            if (vBar) vBar.style.width = telemetryKnown && Number.isFinite(v.percent) ? `${v.percent}%` : '0%';

            const gLoad = document.getElementById('gpuLoadPct');
            if (gLoad) gLoad.textContent = Number.isFinite(v.utilization_pct) ? `${v.utilization_pct}%` : '—';

            const gTemp = document.getElementById('gpuTemp');
            if (gTemp) gTemp.textContent = Number.isFinite(v.temp_c) ? `${v.temp_c}°C` : '—';
          }

          // Real Server status
          const ollamaOnline = Boolean(data.servers && data.servers.ollama && data.servers.ollama.online);
          const llamaOnline = Boolean(data.servers && data.servers.llama_server && data.servers.llama_server.online);
          const llamaStatus = (data.servers && data.servers.llama_server && data.servers.llama_server.status) || (llamaOnline ? 'ready' : 'offline');

          setDot(document.getElementById('dotOllama'), ollamaOnline ? 'ok' : 'bad');
          const labelOllama = document.getElementById('labelOllamaStatus');
          if (labelOllama) labelOllama.textContent = ollamaOnline ? 'Online (port 11434)' : 'Offline';

          setDot(document.getElementById('dotLlama'), llamaStatus === 'loading' ? 'wait' : (llamaOnline ? 'ok' : 'bad'));
          const labelLlama = document.getElementById('labelLlamaStatus');
          if (labelLlama) {
            labelLlama.textContent = llamaStatus === 'loading'
              ? 'Loading weights into VRAM (:8080)…'
              : (llamaOnline ? 'Online (port 8080)' : 'Offline');
          }

          const isCurrentLlama = resolveProviderFromRegistry(activeProfile) === 'llama_server';
          const isCurrentOnline = isCurrentLlama ? llamaOnline : ollamaOnline;
          const loading = isCurrentLlama && llamaStatus === 'loading';
          setDot(document.getElementById('serverLiveDot'), loading ? 'wait' : (isCurrentOnline ? 'ok' : 'bad'));
          const topText = document.getElementById('serverLiveText');
          if (topText) topText.textContent = loading ? 'Loading…' : (isCurrentOnline ? 'Online' : 'Offline');
        }
      } catch (e) {}
    }

    async function warmupActiveModel() {
      showToast(`Preloading ${activeProfile} into VRAM…`);
      const btn1 = document.getElementById('btnPreloadModel');
      const btn2 = document.getElementById('btnModalPreload');
      const statusLabel = document.getElementById('labelModelLoadStatus');
      const old1 = btn1 ? btn1.innerHTML : '';
      const old2 = btn2 ? btn2.innerHTML : '';

      if (btn1) {
        btn1.innerHTML = `${spinnerSvg(13)} Loading into VRAM…`;
        btn1.disabled = true;
      }
      if (btn2) {
        btn2.innerHTML = `${spinnerSvg(13)} Loading…`;
        btn2.disabled = true;
      }
      if (statusLabel) statusLabel.textContent = 'Loading model into VRAM…';

      try {
        const res = await fetch('/api/model/load', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ model: activeProfile, ...(activeCtxOverride ? { num_ctx: activeCtxOverride } : {}) })
        });
        const data = await res.json();
        if (data.status === 'loaded') {
          const back = data.backend === 'llama_server' ? 'via llama-server' : (data.backend === 'ollama' ? 'via Ollama' : '');
          showToast(`Model ${data.model || activeProfile} loaded ${back}`.trim(), 'ok');
          if (statusLabel) statusLabel.textContent = `Loaded in VRAM${data.backend === 'llama_server' ? ' (llama-server)' : ''}`;
        } else {
          showToast(`Load issue: ${data.error || 'failed'}`, 'warn');
          if (statusLabel) statusLabel.textContent = 'Offline or error';
        }
        pollStatus();
      } catch (e) {
        showToast('Error loading model', 'err');
        if (statusLabel) statusLabel.textContent = 'Connection error';
      } finally {
        if (btn1) {
          btn1.innerHTML = old1 || `${icon('zap')} Preload model`;
          btn1.disabled = false;
        }
        if (btn2) {
          btn2.innerHTML = old2 || `${icon('zap')} Load into VRAM`;
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
        btn.innerHTML = `${spinnerSvg(13)} Ejecting…`;
        btn.disabled = true;
      }
      showToast('Ejecting models from VRAM…');
      try {
        const res = await fetch('/api/model/unload_all', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'unloaded_all') {
          showToast('All managed models unloaded from VRAM', 'ok');
          pollStatus();
        } else {
          showToast(`Unload failed: ${data.error || data.status || 'unknown error'}`, 'err');
        }
      } catch (e) {
        showToast('Error unloading models', 'err');
      } finally {
        if (btn) {
          btn.innerHTML = oldHtml || 'Eject all from VRAM';
          btn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    function toggleTheme() {
      const html = document.documentElement;
      html.classList.toggle('dark');
      const dark = html.classList.contains('dark');
      try { localStorage.setItem('harness-theme', dark ? 'dark' : 'light'); } catch (e) {}
    }

    function switchTab(tabId) {
      document.querySelectorAll('.tab-btn').forEach(btn => btn.setAttribute('aria-selected', 'false'));
      document.querySelectorAll('.tab-view').forEach(view => view.classList.add('hidden'));
      const btn = document.getElementById(`tab-btn-${tabId}`);
      const view = document.getElementById(`view-${tabId}`);
      if (btn) btn.setAttribute('aria-selected', 'true');
      if (view) view.classList.remove('hidden');
    }

    function toggleSidebar() {
      const sb = document.getElementById('sessionSidebar');
      sb.classList.toggle('hidden');
    }

    let toastTimer = null;
    function showToast(msg, tone = 'info') {
      const toast = document.getElementById('toast');
      const toastText = document.getElementById('toastText');
      toastText.textContent = msg;
      toast.dataset.tone = tone;
      toast.classList.add('is-visible');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2600);
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
      showDiffPanel(false);
      const messages = document.getElementById('chatMessages');
      if (messages) messages.innerHTML = `<div class="welcome"><p class="welcome-copy">Write an instruction below to set this galley.</p></div>`;
      document.getElementById('chatInput').focus();
      showToast('Started a new chat session');
    }

    function filterSessions(type, evt) {
      activeSessionFilter = type;
      document.querySelectorAll('.filter-chip').forEach(c => c.setAttribute('aria-pressed', 'false'));
      const selectedChip = (evt && evt.currentTarget) || document.querySelector(`[data-session-filter="${type}"]`);
      if (selectedChip) selectedChip.setAttribute('aria-pressed', 'true');
      renderSessions(activeSessionFilter);
    }

    function copyActiveDiff() {
      if (activeSession && activeSession.patch) {
        navigator.clipboard.writeText(activeSession.patch).then(() => {
          showToast('Unified diff copied to clipboard', 'ok');
        }).catch(() => {
          showToast('Could not access clipboard', 'err');
        });
      } else {
        showToast('No active diff to copy', 'warn');
      }
    }

    let lastCheckCommand = '';

    function resolveCheckCommands(candidate) {
      if (Array.isArray(candidate) && candidate.length) return candidate;
      // No declared check means no external evidence: ask instead of guessing.
      const entered = prompt('Check command to verify this patch (e.g. pytest tests/)', lastCheckCommand);
      if (!entered || !entered.trim()) return null;
      lastCheckCommand = entered.trim();
      return [lastCheckCommand];
    }

    async function applyProposalAction() {
      if (!activeSession || !activeSession.patch) {
        showToast('No patch proposal available to apply', 'warn');
        return;
      }
      const checks = resolveCheckCommands(activeSession.checks);
      if (!checks) {
        showToast('Apply cancelled: a targeted check command is required', 'warn');
        return;
      }
      const btn1 = document.getElementById('btnApply');
      const btn2 = document.getElementById('btnDelegatedApply');
      const old1 = btn1 ? btn1.innerHTML : '';
      const old2 = btn2 ? btn2.innerHTML : '';
      if (btn1) {
        btn1.innerHTML = `${spinnerSvg(13)} Applying…`;
        btn1.disabled = true;
      }
      if (btn2) {
        btn2.innerHTML = `${spinnerSvg(13)} Applying and verifying…`;
        btn2.disabled = true;
      }

      try {
        const res = await fetch('/api/apply', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ patch: activeSession.patch, checks: checks, files: (activeSession.files && activeSession.files.length) ? activeSession.files : (activeSession.file ? [activeSession.file] : []) })
        });
        const data = await res.json();
        if (data.status === 'applied') {
          rollbackAvailable = true;
          updateProposalControls(activeSession);
          showToast('Patch applied and re-verified by the test runner', 'ok');
        } else {
          showToast(`Apply issue: ${data.error || 'Check failed'}`, 'err');
        }
      } catch (err) {
        showToast('Error sending apply request to server', 'err');
      } finally {
        if (btn1) { btn1.innerHTML = old1 || 'Apply'; btn1.disabled = false; }
        if (btn2) { btn2.innerHTML = old2 || 'Apply proposal'; btn2.disabled = false; }
        safeCreateIcons();
      }
    }

    async function rollbackAction() {
      const btn1 = document.getElementById('btnRollback');
      const btn2 = document.getElementById('btnDelegatedRollback');
      const old1 = btn1 ? btn1.innerHTML : '';
      const old2 = btn2 ? btn2.innerHTML : '';
      if (btn1) {
        btn1.innerHTML = `${spinnerSvg(13)} Restoring…`;
        btn1.disabled = true;
      }
      if (btn2) {
        btn2.innerHTML = `${spinnerSvg(13)} Restoring…`;
        btn2.disabled = true;
      }

      try {
        const res = await fetch('/api/rollback', { method: 'POST', headers: {'Content-Type': 'application/json'} });
        const data = await res.json();
        if (data.status === 'rolled_back') {
          rollbackAvailable = false;
          updateProposalControls(activeSession);
          const restored = (data.restored || []).length;
          showToast(restored ? `Restored ${restored} file(s)` : 'Nothing to roll back', restored ? 'ok' : 'info');
        } else {
          showToast(`Rollback issue: ${data.error || 'failed'}`, 'err');
        }
      } catch (err) {
        showToast('Rollback request failed', 'err');
      } finally {
        if (btn1) { btn1.innerHTML = old1 || 'Rollback'; btn1.disabled = false; }
        if (btn2) { btn2.innerHTML = old2 || 'Rollback'; btn2.disabled = false; }
        updateProposalControls(activeSession);
        safeCreateIcons();
      }
    }

    async function runDoctorCheck() {
      const btn = document.getElementById('btnDoctor');
      const oldHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.innerHTML = `${spinnerSvg(13)} Running doctor…`;
        btn.disabled = true;
      }
      try {
        const res = await fetch('/api/doctor/fix', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.status === 'ok' && data.report && data.report.success) {
          const count = (data.report.actions || []).length;
          showToast(`Doctor completed (${count} configuration action${count === 1 ? '' : 's'})`, 'ok');
        } else if (res.ok && data.status === 'partial') {
          showToast(`Doctor partially applied: ${(data.report.actions || []).length} ok, errors: ${data.error || 'unknown'}`, 'warn');
        } else {
          showToast(`Doctor failed: ${data.error || 'unknown error'}`, 'err');
        }
      } catch (error) {
        showToast('Doctor request failed', 'err');
      } finally {
        if (btn) {
          btn.innerHTML = oldHtml || 'Run doctor';
          btn.disabled = false;
        }
        safeCreateIcons();
      }
    }

    function setPromptAndRun(promptText) {
      document.getElementById('chatInput').value = promptText;
      handleUserSubmit();
    }

    function clearWelcome() {
      const container = document.getElementById('chatMessages');
      if (!container) return;
      container.querySelectorAll('.welcome').forEach(el => el.remove());
    }

    function renderUserPrompt(text) {
      if (!text) return;
      clearWelcome();
      const container = document.getElementById('chatMessages');
      const userDiv = document.createElement('div');
      userDiv.className = 'slug';
      userDiv.innerHTML = `
        <div class="slug-body">
          <div class="slug-meta"><span class="mono">${galleyNo(activeSession && activeSession.id)}</span><span>Instruction</span></div>
          <p class="slug-text">${escapeHtml(text)}</p>
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
        container.innerHTML += '<div class="galley"><div class="galley-cell"><p class="slug-text" style="color: var(--text-3)">No saved transcript for this older session.</p></div><aside class="galley-margin"></aside></div>';
      }
      container.scrollTop = container.scrollHeight;
    }

    async function handleUserSubmit() {
      const input = document.getElementById('chatInput');
      const val = input.value.trim();
      if (!val) {
        showToast('Please enter a prompt', 'warn');
        input.focus();
        return;
      }
      if (!activeProfile) {
        showToast('No local models available. Install or discover a model first.', 'warn');
        openModal('modelModal');
        return;
      }

      const container = document.getElementById('chatMessages');
      const sendBtn = document.getElementById('btnSendChat');
      const oldSendHtml = sendBtn ? sendBtn.innerHTML : '';

      renderUserPrompt(val);
      input.value = '';

      // Live placeholder while the controller works.
      const thinkingCard = document.createElement('div');
      thinkingCard.id = 'chatThinkingCard';
      thinkingCard.className = 'galley';
      thinkingCard.innerHTML = `
        <div class="galley-head"><span class="mono">${escapeHtml(activeProfile)}</span><span>Setting copy</span></div>
        <div class="galley-cell"><div class="setting"><span class="caret"></span><span>The model is working; the controller will proof the result.</span></div></div>
        <aside class="galley-margin"></aside>
      `;
      container.appendChild(thinkingCard);
      container.scrollTop = container.scrollHeight;

      if (sendBtn) {
        sendBtn.innerHTML = spinnerSvg(14);
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

        renderAssistantResponse(data, true);
        if (data.patch) {
          document.getElementById('diffFileName').textContent = data.file || 'patch.diff';
          document.getElementById('diffStatsTag').innerHTML = statsHtml(data.patch);
          document.getElementById('diffContentArea').innerHTML = renderUnifiedDiff(data.patch);
          showDiffPanel(true, true);
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
      errDiv.className = 'galley is-new';
      errDiv.innerHTML = `
        <div class="galley-head"><span class="mono">${escapeHtml(backend)}</span><span>Engine offline</span></div>
        <div class="galley-cell">
          <div class="notice">
            <p>${escapeHtml(errorMsg)}</p>
            <div class="notice-actions">
              <button onclick="warmupActiveModel()" class="btn btn--primary">${icon('zap')} Load model and start</button>
              <button onclick="openModal('modelModal')" class="btn">Change model</button>
            </div>
          </div>
        </div>
        <aside class="galley-margin">
          <div class="mark mark--amber" style="--i:0">${icon('triangle-alert')}<span><b>No reply</b>The local engine did not answer.</span></div>
        </aside>
      `;
      container.appendChild(errDiv);
      safeCreateIcons();
      container.scrollTop = container.scrollHeight;
    }

    function copyCodeSnippet(code) {
      navigator.clipboard.writeText(code).then(() => {
        showToast('Code copied to clipboard', 'ok');
      }).catch(() => {
        showToast('Failed to copy code', 'err');
      });
    }

    function formatMarkdown(text) {
      if (!text) return '';
      let html = escapeHtml(text);

      // Fenced code blocks ```lang ... ```
      html = html.replace(/```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```/g, (match, lang, code) => {
        const langLabel = lang || 'code';
        const cleanCode = code.replace(/^\n+|\n+$/g, '');
        const rawAttr = encodeURIComponent(cleanCode).replace(/'/g, "%27");
        return `<div class="codeblock"><div class="codeblock-head"><span>${langLabel}</span><button onclick="copyCodeSnippet(decodeURIComponent('${rawAttr}'))" class="btn btn--quiet">${icon('copy')} Copy</button></div><pre><code>${cleanCode}</code></pre></div>`;
      });

      // Inline `code`
      html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

      // Bold **text**
      html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

      // Bullet points
      html = html.replace(/^[\*\-]\s+(.+)$/gm, '<li>$1</li>');

      // Paragraph breaks
      html = html.replace(/\n\n/g, '<br><br>');
      html = html.replace(/\n/g, '<br>');

      return html;
    }

    function markHtml(tone, iconName, title, body, index) {
      return `<div class="mark mark--${tone}" style="--i:${index}">${icon(iconName)}<span><b>${escapeHtml(title)}</b>${body}</span></div>`;
    }

    function checkName(item) {
      if (item && typeof item === 'object') return item.command || item.name || 'check';
      return String(item);
    }

    function renderAssistantResponse(data, fresh = false) {
      const container = document.getElementById('chatMessages');
      clearWelcome();
      const aiDiv = document.createElement('div');
      aiDiv.className = `galley${fresh ? ' is-new' : ''}`;
      let markIndex = 0;

      // Reflect the resolved mode next to the mode switch
      const modeLabel = data.mode ? (data.mode === 'hybrid' ? 'auto' : data.mode) : '';
      if (modeLabel) {
        const badge = document.getElementById('modeBadge');
        if (badge) badge.textContent = `Last reply routed to ${modeLabel}`;
        const welcomeBadge = document.getElementById('welcomeModeLabel');
        if (welcomeBadge) welcomeBadge.textContent = `Mode: ${modeLabel}`;
      }

      const rows = [];

      // Copy row: the model's reply, with the controller's note in the margin.
      const copyMarks = [];
      if (data.thinking) copyMarks.push(markHtml('blue', 'message-square-text', 'Note', escapeHtml(data.thinking), markIndex++));
      if (data.status === 'failed' && !data.patch) copyMarks.push(markHtml('red', 'circle-x', 'Not completed', 'The controller did not accept a result.', markIndex++));
      rows.push({ cell: `<div class="copy">${formatMarkdown(data.message || '')}</div>`, marks: copyMarks });

      if (data.plan) {
        const p = data.plan;
        const steps = (p.steps || []).map(s => `<li>${escapeHtml(s)}</li>`).join('');
        const planMarks = [];
        if ((p.files_to_modify || []).length) {
          planMarks.push(markHtml('blue', 'file-diff', 'Files to modify', p.files_to_modify.map(f => `<span class="mono">${escapeHtml(f)}</span>`).join('<br>'), markIndex++));
        }
        (p.risks || []).forEach(r => planMarks.push(markHtml('red', 'triangle-alert', 'Risk', escapeHtml(r), markIndex++)));
        rows.push({
          cell: `<div class="plan">${p.goal ? `<div class="plan-goal"><span class="label">Goal</span> ${escapeHtml(p.goal)}</div>` : ''}${steps ? `<ol>${steps}</ol>` : ''}</div>`,
          marks: planMarks
        });
      }

      const checks = Array.isArray(data.checks) ? data.checks : [];
      const result = data.testResult;
      const checkList = checks.map(c => `<span class="mono">${escapeHtml(checkName(c))}</span>`).join('<br>');
      let checkMark = '';
      if (result === 'PASSED' || result === 'ALL CHECKS GREEN') {
        checkMark = markHtml('pass', 'circle-check', 'Checks passed', `External runner${checkList ? '<br>' + checkList : ''}`, 0);
      } else if (result === 'FAILED') {
        checkMark = markHtml('red', 'circle-x', 'Checks failed', checkList || 'See the reply for details.', 0);
      } else if (data.patch && data.patch.trim()) {
        checkMark = markHtml('amber', 'circle-dashed', 'Checks not run', 'Apply runs the targeted checks.', 0);
      }

      if (data.patch && data.patch.trim()) {
        const files = (data.files && data.files.length) ? data.files : (data.file ? [data.file] : []);
        const patchMarks = [
          markHtml('blue', 'file-diff', 'Proposed', `<span class="mono">${escapeHtml(data.file || 'patch.diff')}</span> ${statsHtml(data.patch)}`, markIndex++)
        ];
        if (files.length) patchMarks.push(markHtml('blue', 'shield', 'Allowlist', files.map(f => `<span class="mono">${escapeHtml(f)}</span>`).join('<br>'), markIndex++));
        if (checkMark) patchMarks.push(checkMark.replace('--i:0', `--i:${markIndex++}`));
        const totalLines = data.patch.split('\n').filter(l => /^[+\- ]/.test(l) && !/^(\+\+\+|---)/.test(l)).length;
        rows.push({
          cell: `
            <div class="patch">
              <div class="patch-head">
                <span class="patch-file">${escapeHtml(data.file || 'patch.diff')}</span>
                ${statsHtml(data.patch)}
                <button onclick="showDiffPanel(true, true)" class="btn btn--quiet">${icon('arrow-up-right')} Open patch</button>
              </div>
              ${renderUnifiedDiff(data.patch, 8)}
              ${totalLines > 8 ? `<div class="patch-more">${totalLines - 8} more lines in the patch panel</div>` : ''}
            </div>`,
          marks: patchMarks
        });
      } else if (checkMark && checks.length && result && result !== 'READY') {
        rows.push({ cell: '', marks: [checkMark.replace('--i:0', `--i:${markIndex++}`)] });
      }

      const head = `
        <div class="galley-head">
          <span class="mono">${galleyNo(data.task_id || data.id || (activeSession && activeSession.id))}</span>
          <span class="mono">${escapeHtml(data.profile || activeProfile)}</span>
          ${modeLabel ? `<span>${escapeHtml(modeLabel.charAt(0).toUpperCase() + modeLabel.slice(1))}</span>` : ''}
        </div>`;

      aiDiv.innerHTML = head + rows.map(r => `<div class="galley-cell">${r.cell}</div><aside class="galley-margin">${r.marks.join('')}</aside>`).join('');
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
        showToast('Please enter a task goal', 'warn');
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
          showToast(`Queued ${data.task.id}`, 'ok');
          pollTasks();
        } else {
          showToast(`Could not queue task: ${data.error || 'unknown error'}`, 'err');
        }
      } catch (e) {
        showToast('Error submitting task', 'err');
      }
    }

    function taskTone(status) {
      const map = { queued: 'mute', running: 'amber', accepted: 'blue', failed: 'red', cancelled: 'mute' };
      return map[status] || 'mute';
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
      const badge = document.getElementById('delegatedBadgeCount');
      if (badge) {
        const active = TASKS.filter(t => t.status === 'queued' || t.status === 'running').length;
        badge.textContent = String(active);
        badge.classList.toggle('hidden', active === 0);
      }
      const list = document.getElementById('taskQueueList');
      if (!list) return;
      if (!TASKS.length) {
        list.innerHTML = '<div class="queue-empty">No background tasks yet.</div>';
        return;
      }
      list.innerHTML = TASKS.map(t => `
        <div class="task task--${escapeHtml(t.status)}">
          <div class="task-top">
            <span class="tag tone-${taskTone(t.status)}">${escapeHtml(String(t.status).toUpperCase())}</span>
            <span class="task-id num">${escapeHtml(t.id || '')} · ${taskDurationText(t)}</span>
          </div>
          <div class="task-goal">${escapeHtml(t.goal || '')}</div>
          ${t.summary ? `<div class="task-summary">${escapeHtml(t.summary)}</div>` : ''}
          ${taskErrorText(t) && taskErrorText(t) !== t.summary ? `<div class="task-error">${escapeHtml(taskErrorText(t))}</div>` : ''}
          <div class="task-actions">${(t.status === 'queued' || t.status === 'running') ? `<button onclick="cancelQueuedTask('${t.id}')" class="btn">Cancel</button>` : ''}${(t.status === 'accepted' && t.patch && t.patch.trim()) ? `<button onclick="rollbackQueuedTask()" class="btn">Rollback</button><button onclick="applyQueuedTask('${t.id}')" class="btn btn--pass">Apply</button>` : ''}</div>
        </div>
      `).join('');
    }

    function taskErrorText(t) {
      if (!t.error) return '';
      return typeof t.error === 'object' ? (t.error.message || '') : String(t.error);
    }

    async function cancelQueuedTask(id) {
      try {
        const res = await fetch('/api/tasks/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: id })
        });
        const data = await res.json();
        showToast(data.status === 'cancelling' ? `Cancelling ${id}…` : `Task ${data.status}: ${id}`);
        pollTasks();
      } catch (e) {
        showToast('Error cancelling task', 'err');
      }
    }

    async function applyQueuedTask(id) {
      const t = TASKS.find(x => x.id === id);
      if (!t || !t.patch) return;
      if (!confirm(`Apply the patch from ${id} to the workspace? Targeted checks will re-run.`)) return;
      const checks = resolveCheckCommands(t.checks);
      if (!checks) {
        showToast('Apply cancelled: a targeted check command is required', 'warn');
        return;
      }
      try {
        const res = await fetch('/api/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ patch: t.patch, files: t.files || [], checks: checks })
        });
        const data = await res.json();
        if (data.status === 'applied') {
          showToast(`Patch applied and verified (${id})`, 'ok');
        } else {
          showToast(`Apply issue: ${(data.error || data.status || 'failed').toString().slice(0, 140)}`, 'err');
        }
      } catch (e) {
        showToast('Error sending apply request to server', 'err');
      }
    }

    async function rollbackQueuedTask() {
      if (!confirm('Roll back the most recently applied patch?')) return;
      try {
        const res = await fetch('/api/rollback', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (data.status === 'rolled_back') showToast('Workspace restored cleanly', 'ok');
        else showToast(`Rollback issue: ${data.error || data.status}`, 'err');
      } catch (e) {
        showToast('Error sending rollback request', 'err');
      }
    }

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        closeModals();
        return;
      }
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === 'b' && event.key.toLowerCase() === 'b') {
        event.preventDefault();
        toggleSidebar();
      } else if (key === 'n' && event.key.toLowerCase() === 'n') {
        event.preventDefault();
        startNewSession();
      } else if (event.key.toLowerCase() === 'enter') {
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
