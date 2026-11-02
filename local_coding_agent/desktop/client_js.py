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
      return clean ? `G·${clean.slice(-4).toUpperCase()}` : '';
    }

    function verdictTone(status, verified) {
      const s = String(status || '').toLowerCase();
      if (verified === true || s === 'verified') return 'pass';
      if (/fail|reject|error/.test(s)) return 'red';
      if (/review|ready|pending|running|queued/.test(s)) return 'amber';
      if (/answer|plan/.test(s)) return 'blue';
      return 'mute';
    }

    // One parse for the viewer, the galley strip and every counter. File headers (---/+++) count only between
    // hunks; inside a hunk the counts on its @@ line decide, so a removed "-- comment" stays a removed line.
    // Lines past those counts are marked invalid: git apply drops them without an error.
    const DIFF_HIDDEN_META = /^(index |new file mode|deleted file mode|similarity index|dissimilarity index|rename from|rename to)/;
    const DIFF_SHOWN_META = /^(Binary files |old mode |new mode |copy from |copy to )/;

    function parseUnifiedDiff(rawDiff) {
      const lines = String(rawDiff || '').split('\n');
      if (lines[lines.length - 1] === '') lines.pop();
      const files = [];
      let file = null;
      let oldLeft = 0;
      let newLeft = 0;
      let oldNo = 0;
      let newNo = 0;
      const openFile = name => {
        file = { name, rows: [], adds: 0, dels: 0, headed: false, prevHunkEndOld: 0 };
        files.push(file);
      };
      const headerPath = header => header.slice(4).split('\t')[0].trim();

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line.startsWith('diff --git ')) {
          const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
          openFile(match ? match[2] : line.slice(11));
          oldLeft = newLeft = 0;
        } else if (line.startsWith('@@')) {
          if (!file) openFile('patch.diff');
          file.headed = true;
          const match = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
          if (match) {
            const startOld = parseInt(match[1], 10);
            const countOld = match[2] !== undefined ? parseInt(match[2], 10) : 1;
            const gap = startOld - (file.prevHunkEndOld || 1);
            if (gap > 0) file.rows.push({ kind: 'gap', text: `${gap} unmodified line${gap === 1 ? '' : 's'}` });
            oldNo = startOld;
            newNo = parseInt(match[3], 10);
            oldLeft = countOld;
            newLeft = match[4] !== undefined ? parseInt(match[4], 10) : 1;
            file.prevHunkEndOld = startOld + countOld;
          } else {
            oldLeft = newLeft = 0;
          }
          file.rows.push({ kind: 'hunk', text: line });
        } else if (oldLeft > 0 || newLeft > 0) {
          if (line.startsWith('+')) {
            file.rows.push({ kind: 'add', text: line.slice(1), no: newNo ? newNo++ : '' });
            file.adds++;
            newLeft--;
          } else if (line.startsWith('-')) {
            file.rows.push({ kind: 'del', text: line.slice(1), no: oldNo ? oldNo++ : '' });
            file.dels++;
            oldLeft--;
          } else if (line.startsWith('\\')) {
            file.rows.push({ kind: 'meta', text: line });
          } else {
            const valid = line.startsWith(' ');
            file.rows.push({ kind: valid ? 'ctx' : 'invalid', text: valid ? line.slice(1) : line, no: oldNo ? oldNo++ : '' });
            if (newNo) newNo++;
            oldLeft--;
            newLeft--;
          }
        } else if (line.startsWith('--- ') && (lines[i + 1] || '').startsWith('+++ ')) {
          if (!file || file.headed) openFile('patch.diff');
          const newPath = headerPath(lines[i + 1]);
          file.name = (newPath === '/dev/null' ? headerPath(line) : newPath).replace(/^[ab]\//, '');
          file.headed = true;
          i++;
        } else if (!line || !file || DIFF_HIDDEN_META.test(line)) {
          continue;
        } else if (DIFF_SHOWN_META.test(line) || line.startsWith('\\')) {
          file.rows.push({ kind: 'meta', text: line });
        } else {
          file.rows.push({ kind: 'invalid', text: line, no: '' });
        }
      }

      if (files.length === 0 && lines.some(Boolean)) {
        openFile('patch.diff');
        lines.filter(Boolean).forEach(line => file.rows.push({ kind: 'invalid', text: line, no: '' }));
      }
      return files;
    }

    function diffStats(rawDiff) {
      return parseUnifiedDiff(rawDiff).reduce((st, f) => ({ add: st.add + f.adds, del: st.del + f.dels }), { add: 0, del: 0 });
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
    let lastAppliedId = null;  // session or task whose patch this page applied last; the server rolls back only that one

    function setEvidenceState(id, state) {
      const el = document.getElementById(id);
      if (el) el.dataset.state = state;
    }

    // Failure text from a chat reply (string) or a task record (object with message).
    function failureText(d) {
      const e = d.rejection || d.error;
      if (e && typeof e === 'object') return String(e.message || e.kind || '');
      return e ? String(e) : '';
    }

    function getEvidenceDescriptor(d) {
      if (!d) return { state: 'none', tagText: 'NO PROPOSAL', label: 'Evidence: Not run', oracleLabel: 'Oracles: Not run', checkMark: null, isUnknown: false, isApplied: false, isRejected: false };
      const hasPatch = Boolean(d.patch && d.patch.trim());
      // d.checks holds the command strings; the results of the last run are only in test_evidence.
      const checks = Array.isArray(d.test_evidence) ? d.test_evidence : [];
      const checkList = checks.map(c => {
        const name = typeof c === 'string' ? checkName(c) : checkName((c && c.command) || '');
        const exitInfo = (c && c.exit_code !== undefined && c.exit_code !== null) ? ` (exit ${c.exit_code})` : '';
        return `<span class="mono">${escapeHtml(name)}${exitInfo}</span>`;
      }).join('<br>');

      const isUnknown = d.workspace_state === 'unknown';
      const unknownTag = isUnknown ? '<br><span class="tag tone-red">Workspace may be modified</span>' : '';
      // Only the explicit flag set by the apply handler means the patch is in the workspace.
      const isApplied = d.applied === true;

      if (d.evidence_verified === true || d.testResult === 'PASSED' || d.testResult === 'ALL CHECKS GREEN') {
        return {
          state: 'verified',
          tagText: 'PROPOSAL VERIFIED',
          label: 'Evidence: Verified by Test Runner',
          oracleLabel: `Oracles: External checks passed${checks.length ? ` (${checks.length})` : ''}`,
          checkMark: markHtml('pass', 'circle-check', 'Checks passed', `External runner${checkList ? '<br>' + checkList : ''}`, 0),
          isUnknown,
          isApplied,
          isRejected: false,
        };
      }
      if (d.status === 'rejected' || d.testResult === 'REJECTED') {
        const reason = failureText(d) || 'Controller rejected proposal';
        return {
          state: 'rejected',
          tagText: 'PATCH REJECTED',
          label: `Evidence: Rejected: ${reason.slice(0, 30)}`,
          oracleLabel: `Oracles: Patch rejected: ${reason.slice(0, 30)}`,
          checkMark: markHtml('red', 'circle-x', 'Patch rejected', escapeHtml(reason) + unknownTag, 0),
          isUnknown,
          isApplied: false,
          isRejected: true,
          rejection: reason,
        };
      }
      if (d.testResult === 'FAILED') {
        const restoredTag = d.rolled_back ? '<br><span class="tag">restored</span>' : '';
        const detail = (checkList ? `${checkList}${restoredTag}` : (failureText(d) ? escapeHtml(failureText(d)) : 'Checks failed')) + unknownTag;
        return {
          state: 'failed',
          tagText: 'CHECKS FAILED',
          label: 'Evidence: Checks failed',
          oracleLabel: 'Oracles: Checks failed',
          checkMark: markHtml('red', 'circle-x', 'Checks failed', detail, 0),
          isUnknown,
          isApplied: false,
          isRejected: false,
        };
      }
      if (hasPatch) {
        return {
          state: 'pending',
          tagText: 'PROPOSAL READY',
          label: 'Evidence: Not run',
          oracleLabel: 'Oracles: Not run',
          checkMark: markHtml('amber', 'circle-dashed', 'Checks not run', 'Apply runs the targeted checks.', 0),
          isUnknown,
          isApplied: false,
          isRejected: false,
        };
      }
      return {
        state: 'none',
        tagText: 'NO PROPOSAL',
        label: 'Evidence: Not run',
        oracleLabel: 'Oracles: Not run',
        checkMark: null,
        isUnknown,
        isApplied: false,
        isRejected: false,
      };
    }

    // The chat panel shows activeSession; the Delegated pane shows the selected task (or the rail agent session).
    // Polling refreshes only the pane that owns the item, so neither pane can write its state into the other.
    function proposalFlags(session) {
      const desc = getEvidenceDescriptor(session);
      const isLastApplied = Boolean(session && session.id && session.id === lastAppliedId && rollbackAvailable);
      return {
        desc,
        hasPatch: Boolean(session && session.patch && session.patch.trim()),
        notAccepted: Boolean(session && session.id && session.id === activeTaskId && session.status !== 'accepted'),
        isLastApplied,
        applied: desc.isApplied || isLastApplied,
      };
    }

    function setApplyButtonState(button, f) {
      if (!button) return;
      if (!f.hasPatch || f.desc.isUnknown || f.applied || f.desc.isRejected || f.notAccepted) {
        button.disabled = true;
        if (f.desc.isUnknown) {
          button.title = 'Workspace may be modified: rollback failed or uncertain';
        } else if (f.applied) {
          button.title = f.isLastApplied
            ? 'Patch already applied. Rollback to revert.'
            : 'Patch already applied. Rollback works only for the patch applied in this app run.';
        } else if (f.desc.isRejected) {
          button.title = `Patch rejected: ${f.desc.rejection || 'controller refused patch'}`;
        } else if (f.notAccepted) {
          button.title = 'Only an accepted background task can be applied';
        } else {
          button.title = 'Apply with targeted checks (Ctrl+Enter)';
        }
      } else {
        button.disabled = false;
        button.title = 'Apply with targeted checks (Ctrl+Enter)';
      }
    }

    function setRollbackButtonState(button, f) {
      if (!button) return;
      button.disabled = !f.isLastApplied;
      if (f.isLastApplied) {
        button.title = 'Roll back the patch this page applied last';
      } else if (f.desc.isApplied) {
        button.title = 'Applied before the app restarted: rollback works only in the run that applied it';
      } else {
        button.title = 'Nothing applied in this app run to roll back';
      }
    }

    function updateChatControls(session = null) {
      const f = proposalFlags(session);
      setApplyButtonState(document.getElementById('btnApply'), f);
      setRollbackButtonState(document.getElementById('btnRollback'), f);
      const oracle = document.getElementById('diffEvidenceStatus');
      if (oracle) {
        const label = oracle.querySelector('span');
        if (label) label.textContent = f.desc.oracleLabel;
      }
      setEvidenceState('diffEvidenceStatus', f.desc.state);
    }

    function updateDelegatedControls(item = null) {
      const f = proposalFlags(item);
      setApplyButtonState(document.getElementById('btnDelegatedApply'), f);
      setRollbackButtonState(document.getElementById('btnDelegatedRollback'), f);
      const pane = document.getElementById('delegatedPane');
      if (pane) pane.dataset.hasPatch = String(f.hasPatch);
      const status = document.getElementById('delegatedStatusTag');
      if (status) {
        status.textContent = f.desc.tagText;
        status.className = `tag ${f.hasPatch ? (f.desc.state === 'verified' ? 'tone-pass' : (f.desc.state === 'failed' || f.desc.state === 'rejected' ? 'tone-red' : 'tone-blue')) : ''}`;
      }
      const evidence = document.getElementById('delegatedEvidenceTag');
      if (evidence) evidence.textContent = f.desc.label;
      setEvidenceState('delegatedEvidence', f.desc.state);
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

    // returnFocus: only Escape and a mode the user picked hand focus back to the toggle.
    function toggleModeList(open, returnFocus = false) {
      const toggleBtn = document.getElementById('modeToggle');
      const options = document.getElementById('modeOptions');
      if (!toggleBtn || !options) return;
      const isOpen = open !== undefined ? Boolean(open) : !options.classList.contains('is-open');
      options.classList.toggle('is-open', isOpen);
      options.inert = !isOpen;
      toggleBtn.setAttribute('aria-expanded', String(isOpen));
      if (isOpen) {
        const active = options.querySelector('.mode-btn[aria-pressed="true"]') || document.getElementById('mode-btn-hybrid');
        if (active) active.focus();
      } else if (returnFocus) {
        toggleBtn.focus();
      }
    }

    function setMode(mode, btn) {
      SELECTED_MODE = mode;
      document.querySelectorAll('.mode-btn').forEach(b => b.setAttribute('aria-pressed', 'false'));
      const targetBtn = btn || document.getElementById(`mode-btn-${mode}`);
      if (targetBtn) targetBtn.setAttribute('aria-pressed', 'true');
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
      const modeNames = { hybrid: 'Auto', chat: 'Chat', build: 'Build', plan: 'Plan' };
      const toggleLabel = document.getElementById('modeToggleLabel');
      if (toggleLabel) toggleLabel.textContent = modeNames[mode] || mode;
      toggleModeList(false, Boolean(btn));
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

    let askDialogResolve = null;

    function askDialog({ title, body, input, confirmLabel, danger } = {}) {
      return new Promise(resolve => {
        askDialogResolve = resolve;
        const modal = document.getElementById('askModal');
        if (!modal) {
          if (input !== undefined) {
            resolve(prompt(body || title, typeof input === 'string' ? input : ''));
          } else {
            resolve(confirm(body || title));
          }
          return;
        }
        const titleEl = document.getElementById('askModalTitle');
        if (titleEl) titleEl.textContent = title || 'Confirmation';
        const bodyEl = document.getElementById('askModalBody');
        if (bodyEl) bodyEl.textContent = body || '';
        const inputContainer = document.getElementById('askModalInputContainer');
        const inputEl = document.getElementById('askModalInput');
        if (input !== undefined) {
          if (inputContainer) inputContainer.classList.remove('hidden');
          if (inputEl) {
            inputEl.value = typeof input === 'string' ? input : '';
            setTimeout(() => { inputEl.focus(); inputEl.select(); }, 40);
          }
        } else {
          if (inputContainer) inputContainer.classList.add('hidden');
          if (inputEl) inputEl.value = '';
        }
        const confirmBtn = document.getElementById('btnAskConfirm');
        if (confirmBtn) {
          confirmBtn.textContent = confirmLabel || 'Confirm';
          confirmBtn.className = `btn ${danger ? 'btn--danger' : 'btn--primary'}`;
        }
        modal.classList.remove('hidden');
        safeCreateIcons();
      });
    }

    function confirmAskDialog() {
      const modal = document.getElementById('askModal');
      if (modal) modal.classList.add('hidden');
      if (askDialogResolve) {
        const inputContainer = document.getElementById('askModalInputContainer');
        const inputEl = document.getElementById('askModalInput');
        const res = (inputContainer && !inputContainer.classList.contains('hidden') && inputEl) ? inputEl.value : true;
        const r = askDialogResolve;
        askDialogResolve = null;
        r(res);
      }
    }

    function cancelAskDialog() {
      const modal = document.getElementById('askModal');
      if (modal) modal.classList.add('hidden');
      if (askDialogResolve) {
        const r = askDialogResolve;
        askDialogResolve = null;
        r(null);
      }
    }

    function closeModals() {
      cancelAskDialog();
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

    let diffWrapEnabled = false;

    function toggleDiffWrap() {
      diffWrapEnabled = !diffWrapEnabled;
      document.querySelectorAll('.diff').forEach(el => {
        el.classList.toggle('is-wrapped', diffWrapEnabled);
      });
      const btn = document.getElementById('btnDiffWrap');
      if (btn) btn.classList.toggle('is-active', diffWrapEnabled);
    }

    function toggleDiffFile(headerEl) {
      const section = headerEl.closest('.df-section');
      if (!section) return;
      const body = section.querySelector('.df-body');
      if (!body) return;
      const isCol = body.classList.toggle('is-collapsed');
      headerEl.classList.toggle('is-collapsed', isCol);
    }

    function renderDiffRow(row) {
      const code = `<span class="dl-code">${escapeHtml(row.text)}</span>`;
      switch (row.kind) {
        case 'hunk': return `<div class="dl dl--hunk"><span class="dl-no"></span><span class="dl-sign"></span>${code}</div>`;
        case 'gap': return `<div class="dl dl--gap"><span class="dl-no">…</span><span class="dl-sign"></span>${code}</div>`;
        case 'meta': return `<div class="dl dl--meta"><span class="dl-no"></span><span class="dl-sign"></span>${code}</div>`;
        case 'add': return `<div class="dl dl--add"><span class="dl-no num">${row.no}</span><span class="dl-sign">+</span>${code}</div>`;
        case 'del': return `<div class="dl dl--del"><span class="dl-no num">${row.no}</span><span class="dl-sign">−</span>${code}</div>`;
        case 'invalid': return `<div class="dl dl--invalid"><span class="dl-no num">${row.no}</span><span class="dl-sign">!</span>${code}</div>`;
        default: return `<div class="dl"><span class="dl-no num">${row.no}</span><span class="dl-sign"></span>${code}</div>`;
      }
    }

    function renderUnifiedDiff(rawDiff, limit = 0) {
      if (!rawDiff || !rawDiff.trim()) {
        return '<div class="diff-empty">No active diff proposal. Run a prompt or select a task session.</div>';
      }

      const files = parseUnifiedDiff(rawDiff);
      let html = `<div class="diff ${diffWrapEnabled ? 'is-wrapped' : ''}">`;

      // Galley preview strip (limit > 0): compact flat stream
      if (limit > 0) {
        const rows = files.flatMap(f => f.rows).filter(row => row.kind !== 'gap').slice(0, limit);
        return html + rows.map(renderDiffRow).join('') + '</div>';
      }

      for (const file of files) {
        html += `<div class="df-section">`;
        html += `<div class="df-file" onclick="toggleDiffFile(this)">`;
        html += `<span class="df-file-title"><i data-lucide="chevron-down" class="i-s df-chevron"></i><span class="df-filename mono">${escapeHtml(file.name)}</span></span>`;
        html += `<span class="df-stats"><span class="stat-add">+${file.adds}</span> <span class="stat-del">−${file.dels}</span></span>`;
        html += `</div>`;
        html += `<div class="df-body">${file.rows.map(renderDiffRow).join('')}</div></div>`;
      }

      html += '</div>';
      return html;
    }

    async function loadSessions(selectFirst = false) {
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
      if (selectFirst && SESSIONS.length > 0 && !activeSession) {
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
          ${galleyNo(s.id) ? `<span class="ge-no">${galleyNo(s.id)}</span>` : ""}
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
      updateChatControls(found);

      if (found.type === 'agent') {
        // One source for the Delegated pane: the rail session replaces any selected task.
        activeTaskId = null;
        renderTaskQueue();
        switchTab('delegated');
        renderTaskEnvelope(found);
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
      const ok = await askDialog({ title: 'Eject VRAM', body: 'Unload every managed model from VRAM?', confirmLabel: 'Eject all', danger: true });
      if (!ok) return;
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

    // ---- Theming & Appearance (Plan 07) ----
    let activeCustomRole = 'surface_tint';

    function initTheming() {
      const s = window.INJECTED_UI_SETTINGS || { scheme: 'system', preset: 'night-galley' };
      applySchemeUI(s.scheme || 'system');
      applyPresetUI(s.preset || 'night-galley');

      if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
          if ((window.INJECTED_UI_SETTINGS || {}).scheme === 'system') {
            applySchemeUI('system', false);
          }
        });
      }

      initColorWheel();
      updateCustomPaletteBoxes();
      markActiveCustomRole();
    }

    function applySchemeUI(scheme, persist = false) {
      if (!window.INJECTED_UI_SETTINGS) window.INJECTED_UI_SETTINGS = {};
      window.INJECTED_UI_SETTINGS.scheme = scheme;

      let isDark = true;
      if (scheme === 'system') {
        isDark = !window.matchMedia || window.matchMedia('(prefers-color-scheme: dark)').matches;
      } else {
        isDark = (scheme === 'dark');
      }

      const root = document.documentElement;
      if (root && root.classList) {
        if (isDark) {
          root.classList.add('dark');
        } else {
          root.classList.remove('dark');
        }
      }

      const cardSystem = document.getElementById('schemeCardSystem');
      const cardLight = document.getElementById('schemeCardLight');
      const cardDark = document.getElementById('schemeCardDark');
      if (cardSystem) cardSystem.classList.toggle('is-active', scheme === 'system');
      if (cardLight) cardLight.classList.toggle('is-active', scheme === 'light');
      if (cardDark) cardDark.classList.toggle('is-active', scheme === 'dark');

      // The scheme picks the Custom variant, so its colours and editor fields follow the switch.
      applyCustomStyles();
      updateCustomPaletteBoxes();

      if (persist) {
        saveUiSettingsToServer();
      }
    }

    function selectScheme(scheme) {
      applySchemeUI(scheme, true);
    }

    function applyPresetUI(preset, persist = false) {
      if (!window.INJECTED_UI_SETTINGS) window.INJECTED_UI_SETTINGS = {};
      window.INJECTED_UI_SETTINGS.preset = preset;

      const root = document.documentElement;
      if (root && root.setAttribute) {
        root.setAttribute('data-preset', preset);
      }

      const customEditor = document.getElementById('customPaletteEditor');
      if (customEditor) {
        customEditor.classList.toggle('hidden', preset !== 'custom');
      }

      const presetMap = {
        'night-galley': 'presetCardNightGalley',
        'graphite': 'presetCardGraphite',
        'ocean': 'presetCardOcean',
        'grove': 'presetCardGrove',
        'ember': 'presetCardEmber',
        'custom': 'presetCardCustom',
      };
      Object.entries(presetMap).forEach(([p, id]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('is-active', preset === p);
      });

      applyCustomStyles();
      updateCustomPaletteBoxes();

      if (persist) {
        saveUiSettingsToServer();
      }
    }

    function selectPreset(preset) {
      applyPresetUI(preset, true);
    }

    function isDarkMode() {
      const root = document.documentElement;
      return (root && root.classList && typeof root.classList.contains === 'function')
        ? root.classList.contains('dark')
        : true;
    }

    function toggleTheme() {
      selectScheme(isDarkMode() ? 'light' : 'dark');
    }

    function updateCustomPaletteBoxes() {
      const s = window.INJECTED_UI_SETTINGS || {};
      const isDark = isDarkMode();
      const modeKey = isDark ? 'dark' : 'light';
      const custom = (s.custom && s.custom[modeKey]) || {};

      const surface = custom.surface_tint || (isDark ? '#0e1117' : '#f3f0e8');
      const accent = custom.accent || (isDark ? '#8dbcdc' : '#2b6896');
      const diffAdd = custom.diff_added || (isDark ? '#8dbcdc' : '#2b6896');
      const diffDel = custom.diff_removed || (isDark ? '#ec6a53' : '#b0381f');

      const setBox = (boxId, inputId, val) => {
        const box = document.getElementById(boxId);
        const input = document.getElementById(inputId);
        if (box) box.style.background = val;
        if (input && input.value !== val) input.value = val;
      };

      setBox('boxColorSurface', 'customColorSurface', surface);
      setBox('boxColorAccent', 'customColorAccent', accent);
      setBox('boxColorDiffAdd', 'customColorDiffAdd', diffAdd);
      setBox('boxColorDiffDel', 'customColorDiffDel', diffDel);
    }

    // Custom role -> CSS token it drives, and -> the editor field that shows it.
    const CUSTOM_ROLE_VARS = { surface_tint: '--ground', accent: '--blue', diff_added: '--diff-add', diff_removed: '--diff-del' };
    const CUSTOM_ROLE_INPUTS = { surface_tint: 'customColorSurface', accent: 'customColorAccent', diff_added: 'customColorDiffAdd', diff_removed: 'customColorDiffDel' };

    function customVariantKey() {
      return isDarkMode() ? 'dark' : 'light';
    }

    function selectCustomRole(role) {
      activeCustomRole = role;
      markActiveCustomRole();
    }

    function markActiveCustomRole() {
      document.querySelectorAll('.color-row[data-role]').forEach(row => {
        row.classList.toggle('is-active', row.dataset.role === activeCustomRole);
      });
    }

    function showThemeWarning(message) {
      const warning = document.getElementById('themeContrastWarning');
      if (!warning) return;
      const text = warning.querySelector('span');
      if (text) text.textContent = message;
      warning.classList.remove('hidden');
    }

    function hideThemeWarning() {
      const warning = document.getElementById('themeContrastWarning');
      if (warning) warning.classList.add('hidden');
    }

    // Saves the whole settings object. Resolves to null when saved, otherwise the server's reason.
    async function postUiSettings() {
      const res = await fetch('/api/ui-settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Desktop-Token': window.DESKTOP_MUTATION_TOKEN || '' },
        body: JSON.stringify(window.INJECTED_UI_SETTINGS || {}),
      });
      if (res.ok) return null;
      const data = await res.json().catch(() => ({}));
      return data.error || `Settings were not saved (HTTP ${res.status})`;
    }

    // The server owns the contrast rules: a refused colour is shown and reverted, never kept.
    async function onCustomColorFieldChange(role, hex) {
      if (!/^#[0-9a-fA-F]{6}$/.test(hex)) {
        showToast('Color must be #rrggbb hex format', 'error');
        return;
      }
      const s = window.INJECTED_UI_SETTINGS || {};
      if (!s.custom) s.custom = {};
      const modeKey = customVariantKey();
      if (!s.custom[modeKey]) s.custom[modeKey] = {};
      const previous = s.custom[modeKey][role];
      s.custom[modeKey][role] = hex.toLowerCase();
      updateCustomPaletteBoxes();
      applyCustomStyles();

      const error = await postUiSettings().catch(err => `Settings were not saved: ${err}`);
      if (error) {
        if (previous === undefined) delete s.custom[modeKey][role];
        else s.custom[modeKey][role] = previous;
        updateCustomPaletteBoxes();
        applyCustomStyles();
        showThemeWarning(error);
        return;
      }
      hideThemeWarning();
    }

    // Runs on every scheme and preset change. Outside Custom no inline override may remain.
    function applyCustomStyles() {
      const s = window.INJECTED_UI_SETTINGS || {};
      const custom = (s.preset === 'custom' && s.custom && s.custom[customVariantKey()]) || {};
      const root = document.documentElement;
      if (!root || !root.style) return;
      Object.entries(CUSTOM_ROLE_VARS).forEach(([role, cssVar]) => {
        if (custom[role]) root.style.setProperty(cssVar, custom[role]);
        else root.style.removeProperty(cssVar);
      });
    }

    // The server computes the adjusted colours and checks them again on save; success is reported only after that.
    async function autoAdjustCustomBrightness() {
      const s = window.INJECTED_UI_SETTINGS || {};
      const modeKey = customVariantKey();
      const colors = {};
      Object.entries(CUSTOM_ROLE_INPUTS).forEach(([role, inputId]) => {
        const input = document.getElementById(inputId);
        colors[role] = input ? input.value.trim() : '';
      });

      let res;
      let data = {};
      try {
        res = await fetch('/api/ui-settings/contrast-fix', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Desktop-Token': window.DESKTOP_MUTATION_TOKEN || '' },
          body: JSON.stringify({ variant: modeKey, custom: colors }),
        });
        data = await res.json().catch(() => ({}));
      } catch (err) {
        showThemeWarning('Could not reach the local server to adjust brightness');
        return;
      }
      if (!res.ok || !data.custom) {
        showThemeWarning(data.error || 'No colour in this palette can meet the contrast thresholds');
        return;
      }

      if (!s.custom) s.custom = {};
      const previous = s.custom[modeKey];
      s.custom[modeKey] = { ...(previous || {}), ...data.custom };
      updateCustomPaletteBoxes();
      applyCustomStyles();
      const error = await postUiSettings().catch(err => `Settings were not saved: ${err}`);
      if (error) {
        s.custom[modeKey] = previous;
        updateCustomPaletteBoxes();
        applyCustomStyles();
        showThemeWarning(error);
        return;
      }
      hideThemeWarning();
      showToast('Colors adjusted to meet WCAG contrast thresholds', 'info');
    }

    function initColorWheel() {
      const canvas = document.getElementById('themeColorWheel');
      if (!canvas || !canvas.getContext) return;
      const ctx = canvas.getContext('2d');
      const width = canvas.width;
      const height = canvas.height;
      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.min(cx, cy) - 4;

      for (let angle = 0; angle < 360; angle += 1) {
        const startRad = (angle - 0.5) * Math.PI / 180;
        const endRad = (angle + 0.5) * Math.PI / 180;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, radius, startRad, endRad);
        ctx.closePath();
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(1, `hsl(${angle}, 100%, 50%)`);
        ctx.fillStyle = grad;
        ctx.fill();
      }

      function pickColor(e) {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const y = (e.clientY - rect.top) * (canvas.height / rect.height);
        const dx = x - cx;
        const dy = y - cy;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist > radius) return;

        const pixel = ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data;
        const hex = '#' + [pixel[0], pixel[1], pixel[2]].map(c => c.toString(16).padStart(2, '0')).join('');
        onCustomColorFieldChange(activeCustomRole, hex);
      }

      canvas.addEventListener('click', pickColor);
    }

    let saveUiTimer = null;
    function saveUiSettingsToServer() {
      if (saveUiTimer) clearTimeout(saveUiTimer);
      saveUiTimer = setTimeout(() => {
        postUiSettings()
          .then(error => { if (error) showToast(error, 'err'); })
          .catch(err => { console.error('Failed to persist UI settings to server', err); });
      }, 150);
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
      updateChatControls(newSession);
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

    // Apply takes command strings, but an earlier apply stores its check results as objects.
    function checkCommands(items) {
      return (Array.isArray(items) ? items : []).map(c => (typeof c === 'string' ? c : (c && c.command) || '')).filter(Boolean);
    }

    async function resolveCheckCommands(candidate) {
      if (Array.isArray(candidate) && candidate.length) return candidate;
      // No declared check means no external evidence: ask instead of guessing.
      const entered = await askDialog({
        title: 'Verification check command',
        body: 'Check command to verify this patch (e.g. pytest tests/)',
        input: lastCheckCommand || '',
        confirmLabel: 'Run check',
      });
      if (!entered || !entered.trim()) return null;
      lastCheckCommand = entered.trim();
      return [lastCheckCommand];
    }

    async function applyProposalAction() {
      if (!activeSession || !activeSession.patch) {
        showToast('No patch proposal available to apply', 'warn');
        return;
      }
      const checks = await resolveCheckCommands(checkCommands(activeSession.checks));
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
          body: JSON.stringify({
            session_id: activeSession.id,
            patch: activeSession.patch,
            checks: checks,
            files: (activeSession.files && activeSession.files.length) ? activeSession.files : (activeSession.file ? [activeSession.file] : [])
          })
        });
        const data = await res.json();
        if (data.status === 'applied') {
          rollbackAvailable = true;
          lastAppliedId = activeSession.id;
          activeSession.status = 'Verified';
          activeSession.evidence_verified = true;
          activeSession.applied = true;
          activeSession.checks = checks;
          activeSession.test_evidence = data.checks || [];
          activeSession.testResult = 'PASSED';
          activeSession.workspace_state = data.workspace_state || 'original';
          activeSession.risks = data.risks || [];
          updateChatControls(activeSession);
          renderSessions(activeSessionFilter);
          refreshSessionGalley(activeSession);
          showToast('Patch applied and re-verified by the test runner', 'ok');
        } else {
          activeSession.evidence_verified = false;
          activeSession.applied = false;
          activeSession.rolled_back = Boolean(data.rolled_back);
          activeSession.workspace_state = data.workspace_state || 'unknown';
          activeSession.risks = data.risks || [];
          activeSession.checks = checks;
          activeSession.test_evidence = data.checks || [];
          if (data.checks && data.checks.length && data.checks.some(c => !c.passed)) {
            activeSession.status = 'Failed';
            activeSession.testResult = 'FAILED';
          } else {
            activeSession.status = 'Rejected';
            activeSession.testResult = 'REJECTED';
            activeSession.rejection = data.rejection || data.error || 'Apply rejected';
          }
          updateChatControls(activeSession);
          renderSessions(activeSessionFilter);
          refreshSessionGalley(activeSession);
          showToast(`Apply issue: ${data.error || 'Check failed'}`, 'err');
        }
      } catch (err) {
        showToast('Error sending apply request to server', 'err');
      } finally {
        if (btn1) { btn1.innerHTML = old1 || 'Apply'; }
        if (btn2) { btn2.innerHTML = old2 || 'Apply proposal'; }
        updateChatControls(activeSession);
        updateDelegatedControls(delegatedItem());
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
          lastAppliedId = null;
          const restored = (data.restored || []).length;
          // Mark only the session the server rolled back, and only when files were actually restored.
          const target = restored ? SESSIONS.find(s => s.id === data.session_id) : null;
          if (target) {
            target.status = 'Rolled back';
            target.applied = false;
            target.evidence_verified = false;
            target.testResult = 'NOT RUN';
            target.test_evidence = [];
            renderSessions(activeSessionFilter);
            refreshSessionGalley(target);
          }
          updateChatControls(activeSession);
          showToast(restored ? `Restored ${restored} file(s)` : 'Nothing to roll back', restored ? 'ok' : 'info');
        } else {
          showToast(`Rollback issue: ${data.error || 'failed'}`, 'err');
        }
      } catch (err) {
        showToast('Rollback request failed', 'err');
      } finally {
        if (btn1) { btn1.innerHTML = old1 || 'Rollback'; }
        if (btn2) { btn2.innerHTML = old2 || 'Rollback'; }
        updateChatControls(activeSession);
        updateDelegatedControls(delegatedItem());
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

    function setPrompt(promptText) {
      const input = document.getElementById('chatInput');
      if (!input) return;
      input.value = promptText;
      input.focus();
    }

    function clearWelcome() {
      const container = document.getElementById('chatMessages');
      if (!container) return;
      container.querySelectorAll('.welcome').forEach(el => el.remove());
    }

    function renderUserPrompt(text, sessionId = null) {
      if (!text) return;
      clearWelcome();
      const container = document.getElementById('chatMessages');
      const userDiv = document.createElement('div');
      userDiv.className = 'slug';
      userDiv.innerHTML = `
        <div class="slug-body">
          <div class="slug-meta">${galleyNo(sessionId) ? `<span class="mono">${galleyNo(sessionId)}</span>` : ""}<span>Instruction</span></div>
          <p class="slug-text">${escapeHtml(text)}</p>
        </div>
      `;
      container.appendChild(userDiv);
    }

    function renderSessionTranscript(session) {
      const container = document.getElementById('chatMessages');
      if (!container) return;
      container.innerHTML = '';
      renderUserPrompt(session.prompt || session.title || '', session.id);
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
        }

        // Update sessions and rail without clearing current transcript
        try {
          const sres = await fetch('/api/sessions');
          if (sres.ok) {
            const sdata = await sres.json();
            SESSIONS = sdata.sessions || [];
          }
        } catch (e) {}

        // The server stores the reply under task_id and never sends session_id. A reply
        // without task_id (needs_context, offline) leaves the active session alone.
        if (data.task_id) {
          activeSession = SESSIONS.find(s => s.id === data.task_id) || {
            id: data.task_id, type: 'user', title: val, file: 'workspace', files: [], patch: '', checks: [], status: 'Active',
          };
          if (data.patch) {
            activeSession.patch = data.patch;
            activeSession.file = data.file || 'patch.diff';
            activeSession.files = (data.files && data.files.length) ? data.files : (data.file ? [data.file] : []);
          }
        }
        renderSessions(activeSessionFilter);
        updateChatControls(activeSession);
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
      container.appendChild(buildAssistantGalley(data, fresh));
      safeCreateIcons();
      container.scrollTop = container.scrollHeight;
    }

    // Apply and Rollback change a session's marks: rebuild only its galley so unsaved replies above it stay.
    function refreshSessionGalley(session) {
      const container = document.getElementById('chatMessages');
      if (!container || !session || !(session.message || session.thinking || session.plan)) return;
      const galley = [...container.children].find(el => el.dataset && el.dataset.replyId === String(session.id));
      if (!galley) return;
      galley.replaceWith(buildAssistantGalley({ ...session, checks: session.test_evidence || [] }));
      safeCreateIcons();
    }

    function buildAssistantGalley(data, fresh = false) {
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
        if (SELECTED_MODE === 'hybrid') {
          const toggleLabel = document.getElementById('modeToggleLabel');
          if (toggleLabel) toggleLabel.textContent = `Auto · ${modeLabel.charAt(0).toUpperCase() + modeLabel.slice(1)}`;
        }
      }

      const rows = [];

      // Copy row: the model's reply, with the controller's note in the margin.
      const copyMarks = [];
      if (data.thinking) copyMarks.push(markHtml('blue', 'message-square-text', 'Note', escapeHtml(data.thinking), markIndex++));
      // A reopened Plan carries its saved status ("Plan failed"), a live one carries "failed".
      const planFailed = data.mode === 'plan' && /fail/i.test(String(data.status || ''));
      if (planFailed) copyMarks.push(markHtml('red', 'circle-x', 'Plan failed', escapeHtml(failureText(data) || 'The model could not produce a plan.'), markIndex++));
      else if (data.status === 'failed' && !data.patch) copyMarks.push(markHtml('red', 'circle-x', 'Not completed', escapeHtml(failureText(data) || 'The controller did not accept a result.'), markIndex++));
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

      const desc = getEvidenceDescriptor(data);
      // A failed Plan has no checks to report; its reason is already in the Plan mark.
      const checkMark = planFailed ? null : desc.checkMark;

      if (data.patch && data.patch.trim()) {
        const files = (data.files && data.files.length) ? data.files : (data.file ? [data.file] : []);
        const patchMarks = [
          markHtml('blue', 'file-diff', 'Proposed', `<span class="mono">${escapeHtml(data.file || 'patch.diff')}</span> ${statsHtml(data.patch)}`, markIndex++)
        ];
        if (files.length) patchMarks.push(markHtml('blue', 'shield', 'Allowlist', files.map(f => `<span class="mono">${escapeHtml(f)}</span>`).join('<br>'), markIndex++));
        if (checkMark) patchMarks.push(checkMark.replace('--i:0', `--i:${markIndex++}`));
        const totalLines = parseUnifiedDiff(data.patch).flatMap(f => f.rows).filter(row => row.kind !== 'gap').length;
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
      } else if (checkMark) {
        rows.push({ cell: '', marks: [checkMark.replace('--i:0', `--i:${markIndex++}`)] });
      }

      // Only the reply's own id: a reply without one must not inherit the active session's number.
      const replyId = data.task_id || data.id;
      const head = `
        <div class="galley-head">
          ${galleyNo(replyId) ? `<span class="mono">${galleyNo(replyId)}</span>` : ""}
          <span class="mono">${escapeHtml(data.profile || activeProfile)}</span>
          ${modeLabel ? `<span>${escapeHtml(modeLabel.charAt(0).toUpperCase() + modeLabel.slice(1))}</span>` : ''}
        </div>`;

      aiDiv.innerHTML = head + rows.map(r => `<div class="galley-cell">${r.cell}</div><aside class="galley-margin">${r.marks.join('')}</aside>`).join('');
      if (replyId) aiDiv.dataset.replyId = String(replyId);
      return aiDiv;
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

    let activeTaskId = null;
    let renderedTaskQueueHtml = null;
    let renderedDelegatedDiff = null;

    function renderTaskEnvelope(t) {
      if (!t) return;
      const agent = document.getElementById('delegatedAgent');
      if (agent) agent.innerHTML = `${icon('bot')}<span>${escapeHtml(t.agent ? `${t.agent} (MCP)` : (t.type === 'agent' ? 'MCP agent' : 'Local controller'))}</span>`;
      document.getElementById('delegatedTaskId').textContent = t.taskId || t.id || '—';
      document.getElementById('delegatedGoal').textContent = t.goal || t.title || '—';
      const files = (t.files && t.files.length) ? t.files.join(', ') : (t.file || '');
      document.getElementById('delegatedFiles').textContent = files || '—';
      document.getElementById('delegatedChecks').textContent = (t.checks && t.checks.join(', ')) || 'None declared';
      document.getElementById('delegatedFileName').textContent = files || (t.patch ? 'patch.diff' : 'No patch file');
      const label = document.getElementById('delegatedProposalLabel');
      if (label) label.innerHTML = t.patch ? statsHtml(t.patch) : 'No patch';
      // Polling calls this every 2 s; rebuild the diff only when it changed, so collapsed files and scroll survive.
      const diffHtml = renderUnifiedDiff(t.patch);
      if (diffHtml !== renderedDelegatedDiff) {
        document.getElementById('delegatedDiffContent').innerHTML = diffHtml;
        renderedDelegatedDiff = diffHtml;
      }
      updateDelegatedControls(t);
      safeCreateIcons();
    }

    function selectTaskById(id) {
      activeTaskId = id;
      const t = TASKS.find(x => x.id === id);
      if (t) {
        renderTaskEnvelope(t);
      }
      renderTaskQueue();
    }

    async function pollTasks() {
      try {
        const res = await fetch('/api/tasks');
        if (!res.ok) return;
        TASKS = (await res.json()).tasks || [];
        renderTaskQueue();
        if (activeTaskId) {
          const selected = TASKS.find(x => x.id === activeTaskId);
          if (selected) {
            renderTaskEnvelope(selected);
          }
        }
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
        renderedTaskQueueHtml = null;
        return;
      }
      const html = TASKS.map(t => {
        const isCurrent = t.id === activeTaskId;
        const errorText = failureText(t);
        return `
        <div class="task task--${escapeHtml(t.status)}${isCurrent ? ' galley-entry-current is-current' : ''}"
             onclick="selectTaskById('${escapeHtml(t.id)}')"
             onkeydown="if(event.key==='Enter')selectTaskById('${escapeHtml(t.id)}')"
             tabindex="0"
             role="button"
             aria-pressed="${isCurrent}">
          <div class="task-top">
            <span class="tag tone-${taskTone(t.status)}">${escapeHtml(String(t.status).toUpperCase())}</span>
            <span class="task-id num">${escapeHtml(t.id || '')} · ${taskDurationText(t)}</span>
          </div>
          <div class="task-goal">${escapeHtml(t.goal || '')}</div>
          ${t.summary ? `<div class="task-summary">${escapeHtml(t.summary)}</div>` : ''}
          ${errorText && errorText !== t.summary ? `<div class="task-error">${escapeHtml(errorText)}</div>` : ''}
          <div class="task-actions" onclick="event.stopPropagation()">${(t.status === 'queued' || t.status === 'running') ? `<button onclick="event.stopPropagation(); cancelQueuedTask('${t.id}')" class="btn">Cancel</button>` : ''}${(t.status === 'accepted' && t.patch && t.patch.trim()) ? `<button onclick="event.stopPropagation(); rollbackQueuedTask()" class="btn" ${t.id === lastAppliedId && rollbackAvailable ? '' : 'disabled'}>Rollback</button><button onclick="event.stopPropagation(); applyQueuedTask('${t.id}')" class="btn btn--pass" ${t.id === lastAppliedId && rollbackAvailable ? 'disabled' : ''}>Apply</button>` : ''}</div>
        </div>
      `}).join('');
      // Rebuild only when the markup changed: polling replacing the list under a pointer press swallows the click.
      if (html !== renderedTaskQueueHtml) {
        list.innerHTML = html;
        renderedTaskQueueHtml = html;
      }
    }

    function refreshSelectedTask() {
      const t = TASKS.find(x => x.id === activeTaskId);
      if (t) renderTaskEnvelope(t);
    }

    // The item the Delegated pane shows: the selected task, else the rail agent session.
    function delegatedItem() {
      if (activeTaskId) return TASKS.find(x => x.id === activeTaskId) || null;
      return (activeSession && activeSession.type === 'agent') ? activeSession : null;
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

    // The Delegated pane acts on the item it shows: the selected task, or else the rail session.
    function applyDelegatedAction() {
      if (activeTaskId) return applyQueuedTask(activeTaskId);
      if (!activeSession || activeSession.type !== 'agent') {
        showToast('Select a delegated task or session to apply', 'warn');
        return undefined;
      }
      return applyProposalAction();
    }

    function rollbackDelegatedAction() {
      if (activeTaskId) return rollbackQueuedTask();
      if (!activeSession || activeSession.type !== 'agent') {
        showToast('Select a delegated task or session to roll back', 'warn');
        return undefined;
      }
      return rollbackAction();
    }

    async function applyQueuedTask(id) {
      const t = TASKS.find(x => x.id === id);
      if (!t || t.status !== 'accepted' || !t.patch) return;
      const ok = await askDialog({ title: 'Apply proposal', body: `Apply the patch from ${id} to the workspace? Targeted checks will re-run.`, confirmLabel: 'Apply' });
      if (!ok) return;
      const checks = await resolveCheckCommands(checkCommands(t.checks));
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
          rollbackAvailable = true;
          lastAppliedId = id;
          refreshSelectedTask();
          showToast(`Patch applied and verified (${id})`, 'ok');
        } else {
          showToast(`Apply issue: ${(data.error || data.status || 'failed').toString().slice(0, 140)}`, 'err');
        }
      } catch (e) {
        showToast('Error sending apply request to server', 'err');
      }
    }

    async function rollbackQueuedTask() {
      const ok = await askDialog({ title: 'Rollback', body: 'Roll back the most recently applied patch?', confirmLabel: 'Roll back', danger: true });
      if (!ok) return;
      try {
        const res = await fetch('/api/rollback', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (data.status === 'rolled_back') {
          rollbackAvailable = false;
          lastAppliedId = null;
          const restored = (data.restored || []).length;
          showToast(restored ? 'Workspace restored cleanly' : 'Nothing to roll back', restored ? 'ok' : 'info');
          refreshSelectedTask();
        } else {
          showToast(`Rollback issue: ${data.error || data.status}`, 'err');
        }
      } catch (e) {
        showToast('Error sending rollback request', 'err');
      }
    }

    document.addEventListener('click', (event) => {
      const wrap = document.getElementById('modeSwitchWrap');
      if (wrap && !wrap.contains(event.target)) {
        const opts = document.getElementById('modeOptions');
        if (opts && opts.classList.contains('is-open')) {
          toggleModeList(false);
        }
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        const opts = document.getElementById('modeOptions');
        if (opts && opts.classList.contains('is-open')) {
          event.preventDefault();
          toggleModeList(false, true);
          return;
        }
        closeModals();
        return;
      }
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        const opts = document.getElementById('modeOptions');
        if (opts && opts.classList.contains('is-open') && opts.contains(event.target)) {
          event.preventDefault();
          const btns = Array.from(opts.querySelectorAll('.mode-btn'));
          const idx = btns.indexOf(event.target);
          if (idx !== -1) {
            const nextIdx = event.key === 'ArrowRight' ? (idx + 1) % btns.length : (idx - 1 + btns.length) % btns.length;
            btns[nextIdx].focus();
          }
          return;
        }
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
          const delegatedTab = document.getElementById('tab-btn-delegated');
          if (delegatedTab && delegatedTab.getAttribute('aria-selected') === 'true') applyDelegatedAction();
          else applyProposalAction();
        }
      }
    });

    // Startup
    initTheming();
    setMode(SELECTED_MODE);
    loadSessions(true);
    fetchAndPopulateModels();
    pollStatus();
    setInterval(pollStatus, 2500);
    pollTasks();
    setInterval(pollTasks, 2000);
    updateChatControls();
    updateDelegatedControls();
    safeCreateIcons();
"""
