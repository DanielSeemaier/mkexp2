      const requestPath = apiPath(path);
      const response = await fetch(requestPath, { headers: { 'X-MKEXP2-Token': token() } });
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`GET ${requestPath} failed`, text);
        throw new Error(text);
      }
      return await response.blob();
    }
    async function fetchDownload(path) {
      const requestPath = apiPath(path);
      const response = await fetch(requestPath, { headers: { 'X-MKEXP2-Token': token() } });
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`GET ${requestPath} failed`, text);
        throw new Error(text);
      }
      const disposition = response.headers.get('content-disposition') || '';
      const filenameMatch = disposition.match(/filename="([^"]+)"/);
      return {
        blob: await response.blob(),
        filename: filenameMatch ? filenameMatch[1] : ''
      };
    }
    function clearPlotPdfUrl() {
      if (state.plotPdfUrl) URL.revokeObjectURL(state.plotPdfUrl);
      state.plotPdfUrl = '';
      state.plotPdfUrlFor = null;
      state.plotPdfVersion = '';
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
    }
    function slugifyName(value) {
      return String(value || 'experiment')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'experiment';
    }
    function renderTemplateForDate(template, name) {
      const now = new Date();
      const pad = number => String(number).padStart(2, '0');
      return String(template ?? '%Y.%m.%d-<name>')
        .replaceAll('%Y', String(now.getFullYear()))
        .replaceAll('%m', pad(now.getMonth() + 1))
        .replaceAll('%d', pad(now.getDate()))
        .replaceAll('%H', pad(now.getHours()))
        .replaceAll('%M', pad(now.getMinutes()))
        .replaceAll('%S', pad(now.getSeconds()))
        .replaceAll('<name>', slugifyName(name));
    }
    function activeCreateTemplate() {
      const override = document.getElementById('create-template-override').checked;
      const custom = document.getElementById('create-template').value.trim();
      return override && custom ? custom : (state.config.name_template || '%Y.%m.%d-<name>');
    }
    function activeCopyTemplate() {
      const override = document.getElementById('copy-template-override').checked;
      const custom = document.getElementById('copy-template').value.trim();
      return override && custom ? custom : (state.config.name_template || '%Y.%m.%d-<name>');
    }
    function updateCreatePreview() {
      const template = activeCreateTemplate();
      const name = document.getElementById('create-name').value || 'experiment';
      const renderedName = slugifyName(name);
      const tokenIndex = template.indexOf('<name>');
      const prefix = document.getElementById('create-name-prefix');
      const suffix = document.getElementById('create-name-suffix');
      if (tokenIndex >= 0) {
        prefix.textContent = renderTemplateForDate(template.slice(0, tokenIndex), '');
        suffix.textContent = renderTemplateForDate(template.slice(tokenIndex + '<name>'.length), '');
      } else {
        prefix.textContent = renderTemplateForDate(template, '');
        suffix.textContent = '';
      }
      document.getElementById('create-preview').textContent = `Will create: ${renderTemplateForDate(template, renderedName)}`;
      document.getElementById('create-template-controls').classList.toggle(
        'hidden',
        !document.getElementById('create-template-override').checked
      );
    }
    function updateCopyPreview() {
      const template = activeCopyTemplate();
      const name = document.getElementById('copy-name').value || suggestedCopyName();
      const renderedName = slugifyName(name);
      const tokenIndex = template.indexOf('<name>');
      const prefix = document.getElementById('copy-name-prefix');
      const suffix = document.getElementById('copy-name-suffix');
      if (tokenIndex >= 0) {
        prefix.textContent = renderTemplateForDate(template.slice(0, tokenIndex), '');
        suffix.textContent = renderTemplateForDate(template.slice(tokenIndex + '<name>'.length), '');
      } else {
        prefix.textContent = renderTemplateForDate(template, '');
        suffix.textContent = '';
      }
      document.getElementById('copy-preview').textContent = `Will create: ${renderTemplateForDate(template, renderedName)}`;
      document.getElementById('copy-template-controls').classList.toggle(
        'hidden',
        !document.getElementById('copy-template-override').checked
      );
    }
    function renderGitStatus(status) {
      const repoSummary = document.getElementById('git-repo-summary');
      const grid = document.getElementById('git-status');
      const output = document.getElementById('git-output');
      repoSummary.textContent = `${status.repo || 'experiment repo'}${status.branch ? ` on ${status.branch}` : ''}`;
      grid.innerHTML = '';
      const groups = status.groups || {};
      const list = document.createElement('div');
      list.className = 'git-file-list';
      let total = 0;
      for (const [key, label] of [['added', 'A'], ['modified', 'M'], ['deleted', 'D']]) {
        const files = groups[key] || [];
        total += files.length;
        for (const file of files) {
          const item = document.createElement('div');
          item.className = `git-file ${key}`;
          item.title = `${file.status} ${file.path}`;
          const kind = document.createElement('span');
          kind.className = 'git-file-kind';
          kind.textContent = label;
          const path = document.createElement('span');
          path.className = 'git-file-path';
          path.textContent = file.path;
          item.appendChild(kind);
          item.appendChild(path);
          list.appendChild(item);
        }
      }
      if (!total) {
        const empty = document.createElement('div');
        empty.className = 'csv-summary';
        empty.textContent = 'No added, modified, or deleted files.';
        list.appendChild(empty);
      }
      grid.appendChild(list);
      output.className = status.dirty ? 'csv-empty' : 'csv-empty status-ok';
      output.textContent = status.dirty
        ? 'Enter a commit message, then push to commit and push the experiment repo.'
        : 'No local experiment repo changes. Push will still run git push.';
    }
    async function loadGitStatus() {
      const output = document.getElementById('git-output');
      output.className = 'csv-empty';
      output.textContent = 'Loading experiment repo status...';
      const status = await api('/api/git/status');
      renderGitStatus(status);
      return status;
    }
    async function openGitDialog() {
      document.getElementById('git-modal').classList.remove('hidden');
      await loadGitStatus().catch(err => {
        const output = document.getElementById('git-output');
        output.className = 'csv-empty status-bad';
        output.textContent = String(err);
      });
    }
    function closeGitDialog() {
      document.getElementById('git-modal').classList.add('hidden');
    }
    function queueStateClass(state) {
      const raw = String(state || '').toUpperCase();
      if (raw === 'R' || raw === 'RUNNING') return 'queue-state-running';
      if (raw === 'PD' || raw === 'PENDING') return 'queue-state-pending';
      return 'queue-state-other';
    }
    function renderQueue(data) {
      const summary = document.getElementById('queue-summary');
      const output = document.getElementById('queue-output');
      const rows = data.rows || [];
      state.queueServerUser = data.server_user || '';
      const cancelAllButton = document.getElementById('queue-cancel-all');
      if (cancelAllButton) {
        cancelAllButton.disabled = !state.queueServerUser;
        cancelAllButton.title = state.queueServerUser
          ? `Cancel all Slurm jobs owned by ${state.queueServerUser}`
          : 'Load the queue before canceling jobs.';
      }
      summary.textContent = `${rows.length} job${rows.length === 1 ? '' : 's'} from ${data.source || 'squeue'}; refreshed ${data.generated_at || 'now'}.`;
      if (!rows.length) {
        output.className = 'csv-empty';
        output.textContent = 'No queued or running Slurm jobs.';
        return;
      }
      output.className = 'queue-table-wrap';
      output.innerHTML = '';
      const table = document.createElement('table');
      table.className = 'queue-table';
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const label of ['Job ID', 'Partition', 'Name', 'User', 'State', 'Time', 'Nodes', 'Node list / reason', 'Action']) {
        const th = document.createElement('th');
        th.textContent = label;
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const row of rows) {
        const tr = document.createElement('tr');
        for (const key of ['job_id', 'partition', 'name', 'user', 'state', 'time', 'nodes', 'nodelist']) {
          const td = document.createElement('td');
          td.textContent = row[key] || '';
          if (key === 'state') td.className = `queue-state ${queueStateClass(row[key])}`;
          tr.appendChild(td);
        }
        const action = document.createElement('td');
        if (row.user === data.server_user) {
          const button = document.createElement('button');
          button.className = 'queue-cancel';
          button.textContent = 'x';
          button.setAttribute('aria-label', `Cancel Slurm job ${row.job_id}`);
          button.title = `Cancel Slurm job ${row.job_id}`;
          button.onclick = () => cancelQueueJob(row.job_id, button).catch(err => out(String(err)));
          action.appendChild(button);
        } else {
          action.className = 'csv-summary';
          action.textContent = '';
        }
        tr.appendChild(action);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      output.appendChild(table);
    }
    async function loadQueue() {
      const output = document.getElementById('queue-output');
      output.className = 'csv-empty';
      output.textContent = 'Loading Slurm queue...';
      const cancelAllButton = document.getElementById('queue-cancel-all');
      if (cancelAllButton) cancelAllButton.disabled = true;
      const data = await api('/api/status/squeue');
      renderQueue(data);
      return data;
    }
    async function openQueueDialog() {
      document.getElementById('queue-modal').classList.remove('hidden');
      await loadQueue().catch(err => {
        const output = document.getElementById('queue-output');
        output.className = 'csv-empty status-bad';
        output.textContent = String(err);
      });
    }
    function closeQueueDialog() {
      document.getElementById('queue-modal').classList.add('hidden');
    }
    async function cancelQueueJob(jobId, button = null) {
      if (!confirm(`Cancel Slurm job ${jobId}?`)) return;
      await withBusyButton(button, '', async () => {
        await api('/api/status/squeue/cancel', {
          method: 'POST',
          body: JSON.stringify({ job_id: jobId })
        });
        await loadQueue();
        await refreshStatus().catch(err => out(String(err)));
      });
    }
    async function cancelAllQueueJobs(button = null) {
      const owner = state.queueServerUser || '';
      if (!owner) {
        alert('Load the Slurm queue before canceling jobs.');
        return;
      }
      const message = `Cancel all Slurm jobs owned by ${owner}?\n\nThis runs: scancel -u ${owner}`;
      if (!confirm(message)) return;
      await withBusyButton(button || 'queue-cancel-all', 'Canceling...', async () => {
        await api('/api/status/squeue/cancel-all', {
          method: 'POST',
          body: JSON.stringify({ confirm_user: owner })
        });
        await loadQueue();
        await refreshStatus().catch(err => out(String(err)));
      });
    }
    function systemPrefersDark() {
      return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    }
    function normalizeTheme(theme) {
      return ['light', 'dark', 'system'].includes(theme) ? theme : 'light';
    }
    function effectiveTheme(theme) {
      const normalized = normalizeTheme(theme);
      return normalized === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : normalized;
    }
    function applyTheme(theme, persistLocal = true) {
      const normalized = normalizeTheme(theme);
      const effective = effectiveTheme(normalized);
      state.settings = Object.assign({}, state.settings || {}, { theme: normalized });
      if (effective === 'dark') document.documentElement.dataset.theme = 'dark';
      else delete document.documentElement.dataset.theme;
      if (persistLocal) localStorage.setItem(THEME_STORAGE_KEY, normalized);
      renderThemeSetting();
      return normalized;
    }
    function renderThemeSetting() {
      const select = document.getElementById('theme-select');
      if (!select) return;
      select.value = normalizeTheme(state.settings?.theme);
    }
    function renderGuidedSettings() {
      const input = document.getElementById('benchmark-base-path');
      if (input) input.value = state.settings?.benchmark_base_path || '';
      renderPostprocessSettings();
    }
    function postprocessDefaults() {
      return Object.assign({
        email_to: '',
        plots: 'default',
        email_subject: 'mkexp2 {status}: {experiment_id}',
        email_body: ''
      }, state.settings?.postprocess_defaults || {});
    }
    function renderPostprocessSettings() {
      const defaults = postprocessDefaults();
      const emailTo = document.getElementById('postprocess-email-to');
      const plots = document.getElementById('postprocess-plots');
      const subject = document.getElementById('postprocess-email-subject');
      const body = document.getElementById('postprocess-email-body');
      if (emailTo) emailTo.value = defaults.email_to || '';
      if (plots) plots.value = defaults.plots || 'default';
      if (subject) subject.value = defaults.email_subject || 'mkexp2 {status}: {experiment_id}';
      if (body) body.value = defaults.email_body || '';
    }
    async function loadUiSettings() {
      if (state.shared) {
        applyTheme(localStorage.getItem(THEME_STORAGE_KEY) || 'light', false);
        return state.settings;
      }
      const settings = await api('/api/settings');
      state.settingsLoaded = true;
      state.settings = Object.assign({}, state.settings || {}, settings || {});
      applyTheme(settings.theme || 'light');
      renderGuidedSettings();
      return settings;
    }
    async function saveUiSettingsPatch(patch) {
      if (state.shared) return state.settings;
      const next = Object.assign({}, state.settings || {}, patch || {});
      const saved = await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify(next)
      });
      state.settingsLoaded = true;
      state.settings = Object.assign({}, state.settings || {}, saved || {});
      applyTheme(state.settings.theme || 'light');
      renderGuidedSettings();
      return state.settings;
    }
    async function saveTheme(theme) {
      const normalized = applyTheme(theme);
      if (state.shared) return;
      try {
        await saveUiSettingsPatch({ theme: normalized });
      } catch (err) {
        out(`Theme save failed: ${String(err)}`);
      }
    }
    async function saveBenchmarkBasePath() {
      const input = document.getElementById('benchmark-base-path');
      await saveUiSettingsPatch({ benchmark_base_path: input?.value.trim() || '' });
      state.benchmarkSets = [];
      state.benchmarkSetsFor = '';
    }
    function postprocessDefaultsFromFields() {
      return {
        email_to: document.getElementById('postprocess-email-to')?.value.trim() || '',
        plots: document.getElementById('postprocess-plots')?.value.trim() || 'default',
        email_subject: document.getElementById('postprocess-email-subject')?.value.trim() || 'mkexp2 {status}: {experiment_id}',
        email_body: document.getElementById('postprocess-email-body')?.value || ''
      };
    }
    async function savePostprocessDefaults() {
      await saveUiSettingsPatch({ postprocess_defaults: postprocessDefaultsFromFields() });
    }
    function zshSingleQuote(value) {
      return `'${String(value ?? '').replace(/'/g, `'\\''`)}'`;
    }
    function postprocessDslSnippet(defaults) {
      const resolved = defaults || postprocessDefaults();
      const lines = [
        '# Automatic cleanup-job postprocessing',
        'Property postprocess.auto true',
        'Property postprocess.parse true',
        `Property postprocess.plots ${zshSingleQuote(resolved.plots || 'default')}`,
      ];
      if (resolved.email_to) lines.push(`Property postprocess.email.to ${zshSingleQuote(resolved.email_to)}`);
      if (resolved.email_subject) lines.push(`Property postprocess.email.subject ${zshSingleQuote(resolved.email_subject)}`);
      if (resolved.email_body) {
        const body = resolved.email_body.replace(/\r?\n/g, '\\n');
        lines.push(`Property postprocess.email.body ${zshSingleQuote(body)}`);
      }
      return `${lines.join('\n')}\n`;
    }
    function insertPostprocessDslAtCursor() {
      if (!state.selected || state.shared || state.selectedArchived) return;
      if (state.editorMode === 'guided') return;
      const snippet = postprocessDslSnippet(postprocessDefaults());
      const start = Number.isInteger(editor.selectionStart) ? editor.selectionStart : editor.value.length;
      const end = Number.isInteger(editor.selectionEnd) ? editor.selectionEnd : start;
      const prefix = editor.value.slice(0, start);
      const suffix = editor.value.slice(end);
      const leadingNewline = prefix && !prefix.endsWith('\n') ? '\n' : '';
      const trailingNewline = suffix && !suffix.startsWith('\n') ? '\n' : '';
      const inserted = `${leadingNewline}${snippet}${trailingNewline}`;
      const next = `${prefix}${inserted}${suffix}`;
      setEditorValue(next);
      const cursor = prefix.length + inserted.length;
      editor.focus();
      editor.setSelectionRange(cursor, cursor);
      state.editorDirty = true;
      renderEditorMode();
    }
    function decodeColumnSignature(signature) {
      const text = String(signature || '');
      return text ? uniqueHeaders(text.split('\u001f')) : [];
    }
    function hiddenColumnGroups() {
      const visibility = state.columnVisibility || {};
      return Object.entries(visibility)
        .map(([signature, visibleColumnsForSignature]) => {
          const headers = decodeColumnSignature(signature);
          if (!headers.length || !Array.isArray(visibleColumnsForSignature)) return null;
          const allowed = new Set(headers);
          const visible = new Set(uniqueHeaders(visibleColumnsForSignature).filter(column => allowed.has(column)));
          const hidden = headers.filter(column => !visible.has(column));
          if (!hidden.length) return null;
          return { signature, headers, visible, hidden };
        })
        .filter(Boolean)
        .sort((left, right) => left.signature.localeCompare(right.signature));
    }
    function renderSettingsColumnVisibility() {
      const container = document.getElementById('settings-hidden-columns');
      if (!container) return;
      const groups = hiddenColumnGroups();
      container.innerHTML = '';
      if (!groups.length) {
        container.className = 'settings-hidden-columns csv-empty';
        container.textContent = 'No globally hidden columns.';
        return;
      }
      container.className = 'settings-hidden-columns';
      for (const group of groups) {
        const section = document.createElement('section');
        section.className = 'hidden-column-group';
        const heading = document.createElement('div');
        heading.className = 'hidden-column-heading';
        const title = document.createElement('div');
        title.className = 'hidden-column-title';
        title.textContent = `${group.hidden.length} hidden of ${group.headers.length} column${group.headers.length === 1 ? '' : 's'}`;
        title.title = group.headers.join(', ');
        const preview = document.createElement('div');
        preview.className = 'csv-summary';
        preview.textContent = group.headers.slice(0, 4).join(', ') + (group.headers.length > 4 ? ', ...' : '');
        heading.appendChild(title);
        heading.appendChild(preview);
        const chips = document.createElement('div');
        chips.className = 'hidden-column-chips';
        for (const column of group.hidden) {
          const chip = document.createElement('span');
          chip.className = 'hidden-column-chip';
          const name = document.createElement('span');
          name.className = 'hidden-column-chip-name';
          name.textContent = column || '(empty)';
          name.title = column;
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'hidden-column-remove';
          remove.textContent = 'x';
          remove.title = `Show ${column || '(empty)'} by default`;
          remove.setAttribute('aria-label', `Show ${column || '(empty)'} by default`);
          remove.onclick = () => removeHiddenColumnDefault(group.signature, column, remove).catch(err => out(String(err)));
          chip.appendChild(name);
          chip.appendChild(remove);
          chips.appendChild(chip);
        }
        section.appendChild(heading);
        section.appendChild(chips);
        container.appendChild(section);
      }
    }
    async function loadSettingsColumnVisibility() {
      if (state.shared) return state.columnVisibility;
      const data = await api('/api/columns');
      state.columnVisibility = data.visibility || {};
      state.columnVisibilityFor = 'global';
      renderSettingsColumnVisibility();
      return state.columnVisibility;
    }
    async function saveGlobalColumnVisibility() {
      const result = await api('/api/columns', {
        method: 'PUT',
        body: JSON.stringify({ visibility: state.columnVisibility || {} })
      });
      state.columnVisibility = result.visibility || {};
      state.columnVisibilityFor = 'global';
      renderSettingsColumnVisibility();
      if (state.results?.length) renderResultsWorkspace();
      return result;
    }
    async function removeHiddenColumnDefault(signature, column, button) {
      await withBusyButton(button, '...', async () => {
        const headers = decodeColumnSignature(signature);
        const allowed = new Set(headers);
        const visible = new Set(uniqueHeaders(state.columnVisibility?.[signature] || []).filter(item => allowed.has(item)));
        visible.add(column);
        const next = Object.assign({}, state.columnVisibility || {});
        const orderedVisible = headers.filter(header => visible.has(header));
        if (orderedVisible.length >= headers.length) {
          delete next[signature];
        } else {
          next[signature] = orderedVisible;
        }
        state.columnVisibility = next;
        renderSettingsColumnVisibility();
        await saveGlobalColumnVisibility();
      });
    }
    function openSettingsDialog() {
      document.getElementById('settings-modal').classList.remove('hidden');
      renderThemeSetting();
      loadWorkspaces().catch(err => {
        setWorkspaceOutput(String(err), true);
        out(String(err));
      });
      loadUiSettings().catch(err => out(String(err)));
      loadSettingsColumnVisibility().catch(err => out(String(err)));
      refreshTags().catch(err => out(String(err)));
      loadExperimentSubdirectories().catch(err => out(String(err)));
      loadSpackCacheInfo().catch(err => out(String(err)));
    }
    function closeSettingsDialog() {
      document.getElementById('settings-modal').classList.add('hidden');
    }
    function renderSpackCacheInfo() {
      const summary = document.getElementById('spack-cache-summary');
      if (!summary) return;
      const info = state.spackCache;
      if (!info) {
        summary.textContent = 'Not loaded.';
        return;
      }
      summary.textContent = info.exists
        ? `${info.entry_count || 0} R library paths, ${formatBytes(info.size || 0)}, modified ${info.modified_at || 'unknown time'}`
        : `No cache file at ${info.path || 'the mkexp2 plots cache'}.`;
    }
    async function loadSpackCacheInfo() {
      state.spackCache = await api('/api/plot/spack-r-libs');
      renderSpackCacheInfo();
      return state.spackCache;
    }
    async function refreshSpackCache() {
      const button = document.getElementById('spack-cache-refresh');
      await withBusyButton(button, 'Resolving...', async () => {
        const action = await api('/api/plot/spack-r-libs/resolve', {
          method: 'POST',
          body: JSON.stringify({ force: true })
        });
        const completed = await watchAction(action.id);
        if (completed?.status === 'completed' && completed.result?.cache) {
          state.spackCache = completed.result.cache;
        } else {
          await loadSpackCacheInfo();
        }
        renderSpackCacheInfo();
      });
    }
    async function pushGitChanges() {
      const message = document.getElementById('git-message').value.trim();
      const output = document.getElementById('git-output');
      const button = document.getElementById('git-push');
      if (!message) {
        output.className = 'csv-empty status-bad';
        output.textContent = 'Commit message is required.';
        return;
      }
      await withBusyButton(button, 'Pushing...', async () => {
        output.className = 'csv-empty';
        output.textContent = 'Committing and pushing experiment repo...';
        try {
          const result = await api('/api/git/push', {
            method: 'POST',
            body: JSON.stringify({ message })
          });
          if (result.ok) {
            closeGitDialog();
            out('Experiment repo pushed.');
          } else {
            output.className = 'output rendered-output';
            output.textContent = JSON.stringify(result, null, 2);
          }
        } catch (err) {
          output.className = 'csv-empty status-bad';
          output.textContent = String(err);
        }
      });
    }
