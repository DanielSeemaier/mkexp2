    async function setView(viewId) {
      state.activeView = viewId;
      document.querySelectorAll('.view-tab').forEach(button => {
        button.classList.toggle('active', button.dataset.view === viewId);
      });
      document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === viewId);
      });
      if (viewId === 'results-view') {
        await activateCsvView(viewId);
      }
      if (viewId === 'logs-view') {
        await ensureLogsLoaded();
      }
      if (viewId === 'plots-view') {
        await loadPlotInfo();
        renderPlotPanel();
      }
    }
    function treeNode() {
      return { folders: new Map(), experiments: [], count: 0, latest: 0 };
    }
    function experimentCreationKey(exp) {
      const epoch = Number(exp.created_at_epoch);
      if (Number.isFinite(epoch)) return epoch * 1000;
      const parsed = Date.parse(exp.created_at || exp.modified_at || '');
      return Number.isFinite(parsed) ? parsed : 0;
    }
    function experimentNameDateKey(exp) {
      const text = String(exp?.id || exp?.label || exp?.name || '');
      const leaf = text.split('/').filter(Boolean).pop() || text;
      let match = leaf.match(/^(\d{4})\.(\d{2})\.(\d{2})(?:[-_.](\d{2})(\d{2}))?/);
      if (match) {
        const [, year, month, day, hour = '00', minute = '00'] = match;
        return new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute)).getTime();
      }
      match = leaf.match(/^(\d{2})(\d{2})-/);
      const parentYear = text.match(/(?:^|\/)(\d{4})(?:\/|$)/);
      if (match && parentYear) {
        const [, month, day] = match;
        return new Date(Number(parentYear[1]), Number(month) - 1, Number(day)).getTime();
      }
      return 0;
    }
    function formatExperimentDate(exp) {
      const timestamp = experimentCreationKey(exp);
      if (!timestamp) return '';
      const date = new Date(timestamp);
      if (Number.isNaN(date.getTime())) return '';
      const pad = value => String(value).padStart(2, '0');
      return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    function selectedDateText(exp) {
      return formatExperimentDate(exp || {});
    }
    function setSelectedExperimentMetadata(title, path = '', exp = {}) {
      document.getElementById('selected-title').textContent = title || 'Experiment';
      document.getElementById('selected-date').textContent = selectedDateText(exp);
    }
    function compareExperimentsByCreatedDesc(left, right) {
      const createdDelta = experimentCreationKey(right) - experimentCreationKey(left);
      if (Math.abs(createdDelta) > 1000) return createdDelta;
      return experimentNameDateKey(right) - experimentNameDateKey(left)
        || String(right.label || right.id).localeCompare(String(left.label || left.id));
    }
    function mostRecentExperiment(experiments) {
      return Array.from(experiments || []).sort(compareExperimentsByCreatedDesc)[0] || null;
    }
    function experimentTree(experiments) {
      const root = treeNode();
      const sorted = Array.from(experiments).sort((left, right) => left.id.localeCompare(right.id));
      for (const exp of sorted) {
        const parts = exp.id.split('/').filter(Boolean);
        if (!parts.length) continue;
        const created = experimentCreationKey(exp);
        let node = root;
        node.count += 1;
        node.latest = Math.max(node.latest, created);
        for (let index = 0; index < parts.length - 1; index += 1) {
          const part = parts[index];
          if (!node.folders.has(part)) node.folders.set(part, treeNode());
          node = node.folders.get(part);
          node.count += 1;
          node.latest = Math.max(node.latest, created);
        }
        node.experiments.push(Object.assign({}, exp, { label: exp.name || parts[parts.length - 1] }));
      }
      return root;
    }
    function openExperimentAncestors(id) {
      const parts = id.split('/').filter(Boolean);
      let current = '';
      for (let index = 0; index < parts.length - 1; index += 1) {
        current = current ? `${current}/${parts[index]}` : parts[index];
        state.openDirs.add(current);
      }
    }
    function validTagColor(color) {
      return /^#[0-9a-f]{6}$/i.test(String(color || ''));
    }
    function tagByName(name) {
      return (state.tags || []).find(tag => tag.name === name) || null;
    }
    function experimentTag(exp) {
      if (exp?.tag?.name) return exp.tag;
      const tagName = exp?.tag_name || state.tagAssignments?.[exp?.id || ''] || '';
      return tagName ? tagByName(tagName) : null;
    }
    function setExperimentTagInState(experimentId, tag) {
      const tagName = tag?.name || '';
      if (tagName) state.tagAssignments[experimentId] = tagName;
      else delete state.tagAssignments[experimentId];
      const experiment = state.experiments.find(item => item.id === experimentId);
      if (experiment) {
        experiment.tag = tag || null;
        experiment.tag_name = tagName;
      }
    }
    function renderTagSelect() {
      const select = document.getElementById('experiment-tag-select');
      if (!select) return;
      const currentExperiment = state.experiments.find(item => item.id === state.selected);
      const currentTag = currentExperiment ? experimentTag(currentExperiment) : null;
      select.innerHTML = '';
      const none = document.createElement('option');
      none.value = '';
      none.textContent = 'No tag';
      select.appendChild(none);
      for (const tag of state.tags || []) {
        const option = document.createElement('option');
        option.value = tag.name;
        option.textContent = tag.name;
        select.appendChild(option);
      }
      select.value = currentTag?.name || '';
      select.disabled = !state.selected || state.shared || state.selectedArchived;
      select.title = state.selectedArchived
        ? 'Unarchive before changing tags.'
        : (state.selected ? 'Experiment tag' : 'Select an experiment first');
      renderExperimentActionButtons();
    }
    function renderExperimentActionButtons() {
      const copyButton = document.getElementById('copy-experiment');
      if (copyButton) {
        copyButton.disabled = !state.selected || state.shared || state.selectedArchived;
        copyButton.title = state.shared
          ? 'Shared experiments cannot be copied from this view.'
          : (state.selectedArchived ? 'Unarchive before copying.' : (state.selected ? 'Copy experiment' : 'Select an experiment first'));
      }
      const shareButton = document.getElementById('share-experiment');
      if (shareButton) {
        shareButton.disabled = !state.selected || state.shared || state.selectedArchived;
        shareButton.title = state.shared
          ? 'Already viewing a shared experiment.'
          : (state.selectedArchived ? 'Unarchive before sharing.' : (state.selected ? 'Share experiment' : 'Select an experiment first'));
      }
      const downloadButton = document.getElementById('download-experiment');
      if (downloadButton) {
        downloadButton.disabled = !state.selected;
        downloadButton.title = state.selected ? 'Download experiment archive' : 'Select an experiment first';
      }
    }
    function tagPaletteEntries() {
      const raw = Array.isArray(state.tagPalette) && state.tagPalette.length ? state.tagPalette : DEFAULT_TAG_COLOR_PALETTE;
      return raw
        .map(item => ({
          name: String(item?.name || item?.color || '').trim(),
          color: String(item?.color || '').trim().toLowerCase()
        }))
        .filter(item => item.name && validTagColor(item.color));
    }
    function renderTagColorPalette(selectedColor = '') {
      const input = document.getElementById('tag-color');
      const palette = document.getElementById('tag-color-palette');
      if (!input || !palette) return;
      const entries = tagPaletteEntries();
      const firstColor = entries[0]?.color || '#2563eb';
      let active = validTagColor(selectedColor || input.value) ? String(selectedColor || input.value).toLowerCase() : firstColor;
      if (!entries.some(item => item.color === active)) active = firstColor;
      input.value = active;
      palette.innerHTML = '';
      for (const entry of entries) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `tag-color-choice${entry.color === active ? ' active' : ''}`;
        button.style.setProperty('--tag-color', entry.color);
        button.title = entry.name;
        button.setAttribute('role', 'radio');
        button.setAttribute('aria-label', entry.name);
        button.setAttribute('aria-checked', entry.color === active ? 'true' : 'false');
        button.onclick = () => renderTagColorPalette(entry.color);
        palette.appendChild(button);
      }
    }
    function renderTagManager() {
      const list = document.getElementById('tag-list');
      if (!list) return;
      list.innerHTML = '';
      if (!state.tags.length) {
        list.className = 'csv-empty';
        list.textContent = 'No tags configured.';
        return;
      }
      list.className = 'tag-list';
      for (const tag of state.tags) {
        const row = document.createElement('div');
        row.className = 'tag-row';
        row.tabIndex = 0;
        const dot = document.createElement('span');
        dot.className = 'tag-dot';
        if (validTagColor(tag.color)) dot.style.setProperty('--tag-color', tag.color);
        const name = document.createElement('span');
        name.className = 'tag-row-name';
        name.textContent = tag.name;
        const color = document.createElement('span');
        color.className = 'muted';
        color.textContent = tag.color;
        row.appendChild(dot);
        row.appendChild(name);
        row.appendChild(color);
        const isDefault = (state.defaultTagNames || []).includes(tag.name);
        let deleteButton = null;
        if (isDefault) {
          const label = document.createElement('span');
          label.className = 'muted tag-row-status';
          label.textContent = 'Default';
          row.appendChild(label);
        } else {
          deleteButton = document.createElement('button');
          deleteButton.type = 'button';
          deleteButton.className = 'icon-button danger-icon-button';
          deleteButton.title = `Delete tag ${tag.name}`;
          deleteButton.setAttribute('aria-label', `Delete tag ${tag.name}`);
          deleteButton.textContent = 'x';
          row.appendChild(deleteButton);
        }
        const selectTagForEdit = () => {
          document.getElementById('tag-name').value = tag.name;
          renderTagColorPalette(tag.color);
        };
        row.onclick = event => {
          if (deleteButton && event.target === deleteButton) return;
          selectTagForEdit();
        };
        row.onkeydown = event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            selectTagForEdit();
          }
        };
        if (deleteButton) {
          deleteButton.onclick = event => {
            event.stopPropagation();
            deleteTag(tag.name, deleteButton).catch(err => out(String(err)));
          };
        }
        list.appendChild(row);
      }
    }
    async function refreshTags() {
      const data = await api('/api/tags');
      state.tags = data.tags || [];
      state.tagPalette = data.palette || DEFAULT_TAG_COLOR_PALETTE;
      state.defaultTagNames = data.default_tags || [];
      state.tagAssignments = data.assignments || {};
      renderTagSelect();
      renderTagColorPalette(document.getElementById('tag-color')?.value);
      renderTagManager();
      renderExperimentsList();
      return data;
    }
    async function assignSelectedTag() {
      const select = document.getElementById('experiment-tag-select');
      if (!state.selected || state.shared || !select) return;
      select.disabled = true;
      try {
        const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/tag`, {
          method: 'PUT',
          body: JSON.stringify({ tag: select.value || '' })
        });
        state.tags = result.tags?.tags || state.tags;
        state.tagAssignments = result.tags?.assignments || state.tagAssignments;
        setExperimentTagInState(state.selected, result.tag || null);
      } finally {
        renderTagSelect();
        renderExperimentsList();
      }
    }
    async function saveTag() {
      const name = document.getElementById('tag-name').value.trim();
      const color = document.getElementById('tag-color').value;
      await withBusyButton('tag-save', 'Saving...', async () => {
        const result = await api('/api/tags', {
          method: 'POST',
          body: JSON.stringify({ name, color })
        });
        state.tags = result.tags || [];
        state.tagPalette = result.palette || state.tagPalette || DEFAULT_TAG_COLOR_PALETTE;
        state.defaultTagNames = result.default_tags || state.defaultTagNames || [];
        state.tagAssignments = result.assignments || {};
        renderTagManager();
        renderTagSelect();
        renderExperimentsList();
      });
    }
    async function deleteTag(name, button) {
      if (!confirm(`Delete tag ${name}? This clears it from experiments using it.`)) return;
      await withBusyButton(button, '...', async () => {
        const result = await api(`/api/tags/${encodeURIComponent(name)}`, { method: 'DELETE' });
        state.tags = result.tags || [];
        state.tagPalette = result.palette || state.tagPalette || DEFAULT_TAG_COLOR_PALETTE;
        state.defaultTagNames = result.default_tags || state.defaultTagNames || [];
        state.tagAssignments = result.assignments || {};
        const nameInput = document.getElementById('tag-name');
        if (nameInput?.value.trim() === name) nameInput.value = '';
        renderTagManager();
        renderTagSelect();
        renderExperimentsList();
      });
    }
    function renderArchiveCodexResult(result) {
      const output = document.getElementById('archive-codex-output');
      if (!output) return;
      const archivedCount = result?.archived_count || 0;
      const lockedCount = result?.skipped_locked_count || 0;
      const pinnedCount = result?.skipped_pinned_count || 0;
      const failedCount = result?.failed_count || 0;
      const parts = [
        `${archivedCount} archived`,
        `${pinnedCount} starred skipped`,
        `${lockedCount} submit-locked skipped`,
      ];
      if (failedCount) parts.push(`${failedCount} failed`);
      output.className = `csv-empty${failedCount ? ' status-bad' : ''}`;
      output.textContent = parts.join('; ') + '.';
      if (failedCount) {
        output.title = (result.failed || []).map(item => `${item.id}: ${item.error}`).join('\n');
      } else {
        output.title = '';
      }
    }
    function renderArchiveBulkResult(outputId, result) {
      const output = document.getElementById(outputId);
      if (!output) return;
      const archivedCount = result?.archived_count || 0;
      const lockedCount = result?.skipped_locked_count || 0;
      const pinnedCount = result?.skipped_pinned_count || 0;
      const failedCount = result?.failed_count || 0;
      const matching = result?.matching || 0;
      const parts = [
        `${archivedCount} archived`,
        `${pinnedCount} starred skipped`,
        `${lockedCount} submit-locked skipped`,
      ];
      if (failedCount) parts.push(`${failedCount} failed`);
      output.className = `csv-empty${failedCount ? ' status-bad' : ''}`;
      output.textContent = `${matching} matching; ${parts.join('; ')}.`;
      output.title = failedCount ? (result.failed || []).map(item => `${item.id}: ${item.error}`).join('\n') : '';
    }
    async function archiveCodexExperiments() {
      const message = 'Archive all active, unstarred, unlocked experiments tagged Codex? Starred or submitted experiments will be skipped.';
      if (!confirm(message)) return;
      const button = document.getElementById('archive-codex-experiments');
      await withBusyButton(button, 'Archiving...', async () => {
        const result = await api('/api/tags/Codex/archive-experiments', {
          method: 'POST',
          body: JSON.stringify({})
        });
        renderArchiveCodexResult(result);
        const archivedIds = new Set((result.archived || []).map(item => item.id));
        if (state.selected && archivedIds.has(state.selected)) clearSelectedExperiment();
        await Promise.all([
          refreshExperiments({ force: true }),
          loadArchivedExperiments({ force: true }).catch(err => out(String(err)))
        ]);
      });
    }
    function renderExperimentSubdirectories() {
      const select = document.getElementById('archive-subdir-select');
      const button = document.getElementById('archive-subdir-experiments');
      if (!select || !button) return;
      select.innerHTML = '';
      const directories = state.experimentSubdirectories || [];
      if (!directories.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No subdirectories';
        select.appendChild(option);
        select.disabled = true;
        button.disabled = true;
        return;
      }
      for (const directory of directories) {
        const option = document.createElement('option');
        option.value = directory.id;
        option.textContent = `${directory.id} (${directory.count})`;
        select.appendChild(option);
      }
      select.disabled = false;
      button.disabled = false;
    }
    async function loadExperimentSubdirectories() {
      const data = await api('/api/experiments/subdirectories');
      state.experimentSubdirectories = data.directories || [];
      renderExperimentSubdirectories();
      return state.experimentSubdirectories;
    }
    function workspaceStatus(workspace) {
      if (workspace.active) return 'Active';
      if (workspace.valid) return 'Git repository';
      if (!workspace.exists) return 'Missing directory';
      if (!workspace.directory) return 'Not a directory';
      return workspace.error || 'Not a Git repository';
    }
    function setWorkspaceOutput(message, bad = false) {
      const output = document.getElementById('workspace-output');
      if (!output) return;
      if (!message) {
        output.className = 'csv-empty hidden';
        output.textContent = '';
        output.title = '';
        return;
      }
      output.className = `csv-empty${bad ? ' status-bad' : ''}`;
      output.textContent = message;
      output.title = message;
    }
    function renderWorkspaces() {
      const container = document.getElementById('workspace-list');
      if (!container) return;
      container.innerHTML = '';
      if (state.shared) {
        container.className = 'workspace-list csv-empty';
        container.textContent = 'Workspace management is disabled for share links.';
        return;
      }
      const workspaces = state.workspaces || [];
      if (!workspaces.length) {
        container.className = 'workspace-list csv-empty';
        container.textContent = state.workspacesLoaded ? 'No workspaces saved.' : 'No workspaces loaded.';
        return;
      }
      container.className = 'workspace-list';
      for (const workspace of workspaces) {
        const row = document.createElement('div');
        row.className = `workspace-row${workspace.active ? ' active' : ''}`;
        const body = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'workspace-name';
        name.textContent = workspace.name || workspace.path || 'Workspace';
        const path = document.createElement('div');
        path.className = 'workspace-path';
        path.textContent = workspace.path || '';
        const meta = document.createElement('div');
        meta.className = 'workspace-meta';
        meta.textContent = workspaceStatus(workspace);
        if (workspace.error && !workspace.valid) meta.title = workspace.error;
        body.appendChild(name);
        body.appendChild(path);
        body.appendChild(meta);
        const actions = document.createElement('div');
        actions.className = 'workspace-actions';
        const switchButton = document.createElement('button');
        switchButton.type = 'button';
        switchButton.textContent = workspace.active ? 'Active' : 'Switch';
        switchButton.disabled = workspace.active || !workspace.valid;
        switchButton.title = workspace.active
          ? 'Current workspace'
          : workspace.valid
            ? `Switch to ${workspace.path}`
            : (workspace.error || 'Workspace is not switchable');
        switchButton.onclick = () => switchWorkspace(workspace.path, switchButton).catch(err => {
          setWorkspaceOutput(String(err), true);
          out(String(err));
        });
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'danger';
        removeButton.textContent = 'Remove';
        removeButton.disabled = workspace.active;
        removeButton.title = workspace.active ? 'Cannot remove the active workspace' : 'Remove from the workspace list';
        removeButton.onclick = () => removeWorkspace(workspace.path, removeButton).catch(err => {
          setWorkspaceOutput(String(err), true);
          out(String(err));
        });
        actions.appendChild(switchButton);
        actions.appendChild(removeButton);
        row.appendChild(body);
        row.appendChild(actions);
        container.appendChild(row);
      }
    }
    async function loadWorkspaces() {
      if (state.shared) {
        state.workspaces = [];
        state.workspacesLoaded = true;
        renderWorkspaces();
        return state.workspaces;
      }
      const result = await api('/api/workspaces');
      state.workspaces = result.workspaces || [];
      state.workspacesLoaded = true;
      renderWorkspaces();
      return state.workspaces;
    }
    function resetWorkspaceState() {
      stopProgressPolling();
      state.experiments = [];
      state.archivedExperiments = [];
      state.pinnedExperiments = new Set();
      state.tags = [];
      state.defaultTagNames = [];
      state.tagAssignments = {};
      state.openDirs = new Set();
      state.archivedOpenDirs = new Set();
      state.archiveQuery = '';
      state.archivePaneOpen = false;
      state.experimentSubdirectories = [];
      state.describeCatalog = null;
      state.describeLoaded = false;
      state.plotBackend = null;
      state.plotCatalog = null;
      state.plotCatalogInitialized = false;
      state.settingsLoaded = false;
      const archiveSearch = document.getElementById('archive-search');
      if (archiveSearch) archiveSearch.value = '';
      clearSelectedExperiment();
      renderArchivedExperiments();
      renderTagManager();
      renderExperimentSubdirectories();
      renderWorkspaces();
    }
    async function reloadWorkspaceAfterSwitch(result) {
      state.workspaces = result.workspaces || [];
      state.workspacesLoaded = true;
      if (result.config) state.config = result.config;
      resetWorkspaceState();
      await refreshConfig().catch(err => out(String(err)));
      await loadUiSettings().catch(err => out(String(err)));
      await refreshPresets().catch(err => out(String(err)));
      await loadSettingsColumnVisibility().catch(err => out(String(err)));
      await refreshExperiments({ force: true, selectMostRecent: true }).catch(err => out(String(err)));
      await loadArchivedExperiments({ force: true }).catch(err => out(String(err)));
      await refreshTags().catch(err => out(String(err)));
      await loadExperimentSubdirectories().catch(err => out(String(err)));
      renderWorkspaces();
    }
    async function switchWorkspace(path, button = null) {
      if (!path || state.shared) return;
      await withBusyButton(button, 'Switching...', async () => {
        const result = await api('/api/workspaces/switch', {
          method: 'POST',
          body: JSON.stringify({ path })
        });
        await reloadWorkspaceAfterSwitch(result);
        setWorkspaceOutput(`Switched to ${result.repo || path}.`);
      });
    }
    async function createWorkspace() {
      if (state.shared) return;
      const input = document.getElementById('workspace-path');
      const path = input?.value.trim() || '';
      if (!path) {
        setWorkspaceOutput('Enter a workspace directory.', true);
        return;
      }
      await withBusyButton('workspace-create', 'Creating...', async () => {
        const result = await api('/api/workspaces', {
          method: 'POST',
          body: JSON.stringify({ path, switch: true })
        });
        if (input) input.value = '';
        await reloadWorkspaceAfterSwitch(result);
        const init = result.initialized_git ? ' Initialized Git repository.' : '';
        const action = result.created_directory ? 'Created workspace' : 'Added workspace';
        setWorkspaceOutput(`${action} at ${result.path}.${init}`);
      });
    }
    async function removeWorkspace(path, button = null) {
      if (!path || state.shared) return;
      if (!confirm('Remove this workspace from the list? This does not delete files.')) return;
      await withBusyButton(button, 'Removing...', async () => {
        const result = await api(`/api/workspaces?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
        state.workspaces = result.workspaces || [];
        state.workspacesLoaded = true;
        renderWorkspaces();
        setWorkspaceOutput(`Removed ${result.path} from the workspace list.`);
      });
    }
    async function archiveSubdirectoryExperiments() {
      const select = document.getElementById('archive-subdir-select');
      const directory = select?.value || '';
      if (!directory) return;
      const message = `Archive all active, unstarred, unlocked experiments in "${directory}"? Starred or submitted experiments will be skipped.`;
      if (!confirm(message)) return;
      const button = document.getElementById('archive-subdir-experiments');
      await withBusyButton(button, 'Archiving...', async () => {
        const result = await api('/api/experiments/archive-subdirectory', {
          method: 'POST',
          body: JSON.stringify({ directory })
        });
        renderArchiveBulkResult('archive-subdir-output', result);
        const archivedIds = new Set((result.archived || []).map(item => item.id));
        if (state.selected && archivedIds.has(state.selected)) clearSelectedExperiment();
        await Promise.all([
          refreshExperiments({ force: true }),
          loadExperimentSubdirectories().catch(err => out(String(err))),
          loadArchivedExperiments({ force: true }).catch(err => out(String(err)))
        ]);
      });
    }
    function renderExperimentTree(container, node, prefix = '') {
      const folders = Array.from(node.folders.entries()).sort((left, right) => {
        return (right[1].latest || 0) - (left[1].latest || 0) || left[0].localeCompare(right[0]);
      });
      for (const [name, child] of folders) {
        const id = prefix ? `${prefix}/${name}` : name;
        const details = document.createElement('details');
        details.className = 'experiment-folder';
        details.open = state.openDirs.has(id) || Boolean(state.selected && state.selected.startsWith(`${id}/`));
        details.addEventListener('toggle', () => {
          if (details.open) state.openDirs.add(id);
          else state.openDirs.delete(id);
        });
        const summary = document.createElement('summary');
        summary.className = 'folder-summary';
        const label = document.createElement('span');
        label.className = 'folder-name';
        label.textContent = name;
        const count = document.createElement('span');
        count.className = 'folder-count';
        count.textContent = `${child.count}`;
        summary.appendChild(label);
        summary.appendChild(count);
        const children = document.createElement('div');
        children.className = 'folder-children';
        renderExperimentTree(children, child, id);
        details.appendChild(summary);
        details.appendChild(children);
        container.appendChild(details);
      }
      const experiments = Array.from(node.experiments).sort(compareExperimentsByCreatedDesc);
      for (const exp of experiments) {
        renderExperimentItem(container, exp, exp.label);
      }
    }
    function renderExperimentItem(container, exp, label) {
      const pinned = state.pinnedExperiments.has(exp.id);
      const locked = Boolean(exp.submit_lock?.locked);
      const tag = experimentTag(exp);
      const item = document.createElement('div');
      item.className = 'experiment-item';
      const button = document.createElement('button');
      button.className = 'experiment-row'
        + (state.sidebarFocus === 'experiments' && state.selected === exp.id ? ' active' : '')
        + (locked ? ' locked' : '')
        + (tag ? ' tagged' : '');
      if (tag && validTagColor(tag.color)) {
        button.style.setProperty('--experiment-tag-color', tag.color);
      }
      const nameRow = document.createElement('span');
      nameRow.className = 'experiment-name-row';
      const name = document.createElement('span');
      name.className = 'experiment-name';
      name.textContent = label;
      nameRow.appendChild(name);
      const date = document.createElement('span');
      date.className = 'experiment-date';
      const created = formatExperimentDate(exp);
      date.textContent = created || 'unknown';
      const title = [exp.id, created, tag ? `Tag: ${tag.name}` : '', locked ? submitLockText(exp.submit_lock) : ''].filter(Boolean).join('\n');
      button.title = title || exp.id;
      button.appendChild(nameRow);
      button.appendChild(date);
      button.onclick = () => withBusyButton(button, 'Loading...', () => selectExperiment(exp.id)).catch(err => out(String(err)));
      const pin = document.createElement('button');
      pin.className = 'pin-button' + (pinned ? ' active' : '');
      pin.type = 'button';
      pin.textContent = pinned ? '★' : '☆';
      pin.title = pinned ? 'Unpin experiment' : 'Pin experiment';
      pin.setAttribute('aria-label', `${pinned ? 'Unpin' : 'Pin'} ${exp.id}`);
      pin.onclick = () => withBusyButton(pin, '', () => togglePinnedExperiment(exp.id)).catch(err => out(String(err)));
      item.appendChild(button);
      item.appendChild(pin);
      container.appendChild(item);
    }
    function updateSelectedExperimentLock(lock) {
      if (!state.selected) return;
      const experiment = state.experiments.find(item => item.id === state.selected);
      if (!experiment) return;
      experiment.submit_lock = {
        locked: Boolean(lock?.locked),
        fields: lock?.fields || {},
        modified_at: lock?.modified_at || ''
      };
      renderExperimentsList();
    }
    function renderPinnedExperiments(container) {
      const order = Array.from(state.pinnedExperiments);
      const byId = new Map(state.experiments.map(exp => [exp.id, exp]));
      const pinned = order.map(id => byId.get(id)).filter(Boolean);
      if (!pinned.length) return;
      const section = document.createElement('section');
      section.className = 'pinned-experiments';
      const title = document.createElement('div');
      title.className = 'pinned-title';
      title.textContent = 'Pinned';
      section.appendChild(title);
      for (const exp of pinned) {
        renderExperimentItem(section, exp, exp.id);
      }
      container.appendChild(section);
    }
    function renderExperimentsList() {
      const list = document.getElementById('experiments');
      list.innerHTML = '';
      renderPinnedExperiments(list);
      const unpinned = state.experiments.filter(exp => !state.pinnedExperiments.has(exp.id));
      renderExperimentTree(list, experimentTree(unpinned));
      renderArchivePaneState();
    }
    function renderArchivePaneState() {
      document.querySelector('.app')?.classList.toggle('archive-mode', Boolean(state.archivePaneOpen));
      const button = document.getElementById('archive-open');
      if (button) {
        button.classList.toggle('active', Boolean(state.archivePaneOpen));
        button.setAttribute('aria-expanded', state.archivePaneOpen ? 'true' : 'false');
      }
    }
    function renderArchivedExperimentTree(container, node, prefix = '') {
      const folders = Array.from(node.folders.entries()).sort((left, right) => left[0].localeCompare(right[0]));
      for (const [name, child] of folders) {
        const id = prefix ? `${prefix}/${name}` : name;
        const details = document.createElement('details');
        details.className = 'experiment-folder archive-folder';
        details.open = Boolean(state.archiveQuery) || state.archivedOpenDirs.has(id);
        details.addEventListener('toggle', () => {
          if (details.open) state.archivedOpenDirs.add(id);
          else state.archivedOpenDirs.delete(id);
        });
        const summary = document.createElement('summary');
        summary.className = 'folder-summary';
        const label = document.createElement('span');
        label.className = 'folder-name';
        label.textContent = name;
        const count = document.createElement('span');
        count.className = 'folder-count';
        count.textContent = `${child.count}`;
        summary.appendChild(label);
        summary.appendChild(count);
        const children = document.createElement('div');
        children.className = 'folder-children';
        renderArchivedExperimentTree(children, child, id);
        details.appendChild(summary);
        details.appendChild(children);
        container.appendChild(details);
      }
      const experiments = Array.from(node.experiments).sort((left, right) => left.label.localeCompare(right.label));
      for (const exp of experiments) {
        renderArchivedExperimentItem(container, exp);
      }
    }
    function renderArchivedExperimentItem(container, exp) {
      const item = document.createElement('div');
      item.className = 'archive-item' + (state.selectedArchived && state.selected === exp.id ? ' active' : '');
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      item.setAttribute('aria-label', `Open archived experiment ${exp.id}`);
      item.onclick = () => selectArchivedExperiment(exp.id).catch(err => out(String(err)));
      item.onkeydown = event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        selectArchivedExperiment(exp.id).catch(err => out(String(err)));
      };
      const text = document.createElement('div');
      text.className = 'archive-open-button';
      const name = document.createElement('div');
      name.className = 'archive-name';
      name.textContent = exp.label || exp.name || exp.id;
      const path = document.createElement('div');
      path.className = 'archive-path';
      path.textContent = exp.id;
      text.appendChild(name);
      text.appendChild(path);
      const actions = document.createElement('div');
      actions.className = 'archive-actions';
      const button = document.createElement('button');
      button.textContent = 'Unarchive';
      button.title = `Unarchive ${exp.id}`;
      button.onclick = event => {
        event.stopPropagation();
        unarchiveExperiment(exp.id, button).catch(err => out(String(err)));
      };
      item.appendChild(text);
      actions.appendChild(button);
      item.appendChild(actions);
      container.appendChild(item);
    }
    function fuzzyMatch(text, query) {
      const haystack = String(text || '').toLowerCase();
      const needle = String(query || '').trim().toLowerCase();
      if (!needle) return true;
      if (haystack.includes(needle)) return true;
      let index = 0;
      for (const char of needle) {
        index = haystack.indexOf(char, index);
        if (index === -1) return false;
        index += 1;
      }
      return true;
    }
    function archivedExperimentMatches(exp, query) {
      return fuzzyMatch(exp.id, query) || fuzzyMatch(exp.name, query) || fuzzyMatch(exp.label, query);
    }
    function renderArchivedExperiments() {
      const list = document.getElementById('archive-list');
      const summary = document.getElementById('archive-summary');
      const archived = state.archivedExperiments || [];
      const query = state.archiveQuery || '';
      const filtered = archived.filter(exp => archivedExperimentMatches(exp, query));
      summary.textContent = query
        ? `${filtered.length} of ${archived.length} archived experiment${archived.length === 1 ? '' : 's'} matched.`
        : `${archived.length} archived experiment${archived.length === 1 ? '' : 's'}.`;
      list.innerHTML = '';
      if (!archived.length) {
        list.className = 'archive-list csv-empty';
        list.textContent = 'No archived experiments.';
        return;
      }
      if (!filtered.length) {
        list.className = 'archive-list csv-empty';
        list.textContent = 'No archived experiments match the search.';
        return;
      }
      list.className = 'archive-list';
      renderArchivedExperimentTree(list, experimentTree(filtered));
    }
    async function loadArchivedExperiments(options = {}) {
      const query = options.force ? '?refresh=1' : '';
      const data = await api(`/api/experiments/archived${query}`);
      state.archivedExperiments = data.experiments || [];
      renderArchivedExperiments();
      return state.archivedExperiments;
    }
    async function openArchivePane() {
      state.archivePaneOpen = true;
      state.sidebarFocus = 'archive';
      renderExperimentsList();
      renderArchivePaneState();
      const search = document.getElementById('archive-search');
      if (search) search.value = state.archiveQuery || '';
      await loadArchivedExperiments({ force: true }).catch(err => {
        const list = document.getElementById('archive-list');
        list.className = 'archive-list csv-empty status-bad';
        list.textContent = String(err);
      });
    }
    async function togglePinnedExperiment(id) {
      if (state.pinnedExperiments.has(id)) state.pinnedExperiments.delete(id);
      else state.pinnedExperiments.add(id);
      renderExperimentsList();
      try {
        const result = await api('/api/pins', {
          method: 'PUT',
          body: JSON.stringify({ pinned: Array.from(state.pinnedExperiments) })
        });
        state.pinnedExperiments = new Set(result.pinned || []);
      } finally {
        renderExperimentsList();
      }
    }
    async function refreshExperiments(options = {}) {
      const query = options.force ? '?refresh=1' : '';
      const [data, pins, tags] = await Promise.all([
        api(`/api/experiments${query}`),
        api('/api/pins'),
        api('/api/tags')
      ]);
      clearTransientOutput();
      state.experiments = data.experiments;
      state.pinnedExperiments = new Set(pins.pinned || []);
      state.tags = tags.tags || [];
      state.tagPalette = tags.palette || DEFAULT_TAG_COLOR_PALETTE;
      state.defaultTagNames = tags.default_tags || [];
      state.tagAssignments = tags.assignments || {};
      renderTagSelect();
      renderExperimentsList();
      if (options.selectMostRecent && !state.selected && state.experiments.length) {
        const latest = mostRecentExperiment(state.experiments);
        if (latest) await selectExperiment(latest.id);
      }
    }
    async function refreshConfig() {
      const data = await api('/api/config');
      state.config = data || state.config;
      document.getElementById('create-template').value = state.config.name_template || '%Y.%m.%d-<name>';
      updateCreatePreview();
      return state.config;
    }
    async function refreshPresets() {
      const data = await api('/api/presets');
      clearTransientOutput();
      state.presets = data.presets || [];
      const select = document.getElementById('create-preset');
      select.innerHTML = '';
      if (!state.presets.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No presets found';
        option.disabled = true;
        select.appendChild(option);
        return;
      }
      for (const preset of state.presets) {
        const option = document.createElement('option');
        option.value = preset.name;
        option.textContent = preset.name;
        select.appendChild(option);
      }
    }
    function describeValue(value, fallback = 'none') {
      const text = String(value ?? '').trim();
      return text || fallback;
    }
    function describeQuery() {
      return String(state.describeQuery || '').trim().toLowerCase();
    }
    function describeMatchesText(query, ...values) {
      if (!query) return true;
      return values.some(value => String(value ?? '').toLowerCase().includes(query));
    }
    function describePropertiesText(properties) {
      const props = Array.isArray(properties) ? properties : [];
      return props.map(prop => [
        prop.key,
        prop.value,
        prop.allowed,
        prop.when,
        ...(Array.isArray(prop.values) ? prop.values : [])
      ].filter(Boolean).join(' ')).join(' ');
    }
    function describeAliasMatches(alias, query) {
      return describeMatchesText(
        query,
        alias?.name,
        alias?.base,
        alias?.parent,
        alias?.args,
        alias?.own_args,
        describePropertiesText(alias?.properties)
      );
    }
    function describeCommandMatches(command, query) {
      return describeMatchesText(query, command?.name, command?.usage, command?.description);
    }
    function describePartitionerBaseMatches(partitioner, query) {
      return describeMatchesText(
        query,
        partitioner?.name,
        (partitioner?.hooks || []).join(' '),
        describePropertiesText(partitioner?.defaults),
        partitioner?.notes
      );
    }
    function appendDescribeChip(container, text, title = '', options = {}) {
      const chip = document.createElement('span');
      chip.className = 'describe-chip';
      chip.textContent = text;
      chip.title = title || text;
      if (Object.prototype.hasOwnProperty.call(options, 'copyValue')) {
        const value = String(options.copyValue ?? '');
        chip.classList.add('copyable');
        chip.setAttribute('role', 'button');
        chip.tabIndex = 0;
        chip.title = `${chip.title || text} — click to copy value`;
        const baseTitle = chip.title;
        const copy = async () => {
          try {
            const copied = await copyTextToClipboard(value);
            if (!copied) return;
            chip.classList.add('copied');
            chip.title = 'Copied value';
            window.setTimeout(() => {
              chip.classList.remove('copied');
              chip.title = baseTitle;
            }, 1100);
          } catch (err) {
            out(String(err));
          }
        };
        chip.onclick = copy;
        chip.onkeydown = event => {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          copy();
        };
      }
      container.appendChild(chip);
    }
    function appendDescribeProperties(container, properties, emptyText = 'No defaults') {
      const row = document.createElement('div');
      row.className = 'describe-chip-row';
      const props = Array.isArray(properties) ? properties : [];
      if (!props.length) {
        appendDescribeChip(row, emptyText);
      } else {
        for (const prop of props) {
          const allowed = prop.closed && Array.isArray(prop.values) && prop.values.length
            ? ` values: ${prop.values.join('|')}`
            : (prop.allowed && prop.allowed !== 'any' ? ` values: ${prop.allowed}` : '');
          const value = describeValue(prop.value, '');
          appendDescribeChip(row, `${prop.key}=${value}`, `${prop.key}${allowed}${prop.when ? `; ${prop.when}` : ''}`, { copyValue: value });
        }
      }
      container.appendChild(row);
    }
    function describeSection(title, countText = '') {
      const section = document.createElement('section');
      section.className = 'describe-section';
      const header = document.createElement('div');
      header.className = 'describe-section-title';
      const label = document.createElement('div');
      label.textContent = title;
      header.appendChild(label);
      if (countText) {
        const count = document.createElement('div');
        count.className = 'describe-section-count';
        count.textContent = countText;
        header.appendChild(count);
      }
      section.appendChild(header);
      return section;
    }
    function renderDescribeDsl(container, dsl) {
      const query = describeQuery();
      const allCommands = Array.isArray(dsl?.commands) ? dsl.commands : [];
      const commands = allCommands.filter(command => describeCommandMatches(command, query));
      const allCommon = Array.isArray(dsl?.common_properties) ? dsl.common_properties : [];
      const common = query ? allCommon.filter(property => describeMatchesText(query, property)) : allCommon;
      if (!commands.length && !common.length) return false;
      const section = describeSection('Experiment DSL', `${commands.length}/${allCommands.length} commands`);
      const grid = document.createElement('div');
      grid.className = 'describe-grid';
      const card = document.createElement('div');
      card.className = 'describe-card';
      const list = document.createElement('div');
      list.className = 'describe-command-list';
      for (const command of commands) {
        const row = document.createElement('div');
        row.className = 'describe-command';
        const name = document.createElement('div');
        name.className = 'describe-name';
        name.textContent = command.name || '';
        const usage = document.createElement('div');
        usage.className = 'describe-code';
        usage.textContent = command.usage || '';
        const description = document.createElement('div');
        description.className = 'describe-muted';
        description.textContent = command.description || '';
        row.appendChild(name);
        row.appendChild(usage);
        row.appendChild(description);
        list.appendChild(row);
      }
      card.appendChild(list);
      if (common.length) {
        const title = document.createElement('div');
        title.className = 'describe-muted';
        title.textContent = 'Common properties';
        card.appendChild(title);
        const row = document.createElement('div');
        row.className = 'describe-chip-row';
        for (const property of common) appendDescribeChip(row, property);
        card.appendChild(row);
      }
      grid.appendChild(card);
      section.appendChild(grid);
      container.appendChild(section);
      return true;
    }
    function renderDescribeSystems(container, systems) {
      const query = describeQuery();
      const allItems = Array.isArray(systems) ? systems : [];
      const items = allItems.filter(system => describeMatchesText(
        query,
        system?.name,
        (system?.hooks || []).join(' '),
        describePropertiesText(system?.defaults),
        system?.notes
      ));
      if (!items.length) return false;
      const section = describeSection('Systems', `${items.length}/${allItems.length} available`);
      const grid = document.createElement('div');
      grid.className = 'describe-grid';
      for (const system of items) {
        const card = document.createElement('div');
        card.className = 'describe-card';
        const header = document.createElement('div');
        header.className = 'describe-card-header';
        const title = document.createElement('div');
        title.className = 'describe-card-title';
        title.textContent = system.name || '';
        const meta = document.createElement('div');
        meta.className = 'describe-card-meta';
        meta.textContent = (system.hooks || []).join(', ');
        header.appendChild(title);
        header.appendChild(meta);
        card.appendChild(header);
        appendDescribeProperties(card, system.defaults || []);
        grid.appendChild(card);
      }
      section.appendChild(grid);
      container.appendChild(section);
      return true;
    }
    function renderDescribePartitioners(container, partitioners) {
      const query = describeQuery();
      const items = Array.isArray(partitioners) ? partitioners : [];
      const section = describeSection('Partitioners and Algorithms');
      const grid = document.createElement('div');
      grid.className = 'describe-grid';
      let visibleCount = 0;
      for (const partitioner of items) {
        const aliases = Array.isArray(partitioner.aliases) ? partitioner.aliases : [];
        const baseMatches = describePartitionerBaseMatches(partitioner, query);
        const matchingAliases = query ? aliases.filter(alias => describeAliasMatches(alias, query)) : aliases;
        if (query && !baseMatches && !matchingAliases.length) continue;
        const shownAliases = query && !baseMatches ? matchingAliases : aliases;
        visibleCount += 1;
        const card = document.createElement('div');
        card.className = 'describe-card';
        const header = document.createElement('div');
        header.className = 'describe-card-header';
        const title = document.createElement('div');
        title.className = 'describe-card-title';
        title.textContent = partitioner.name || '';
        const meta = document.createElement('div');
        meta.className = 'describe-card-meta';
        meta.textContent = shownAliases.length ? `${shownAliases.length} aliases` : 'base algorithm';
        header.appendChild(title);
        header.appendChild(meta);
        card.appendChild(header);
        appendDescribeProperties(card, partitioner.defaults || []);
        const list = document.createElement('div');
        list.className = 'describe-alias-list';
        for (const alias of shownAliases) {
          const row = document.createElement('div');
          row.className = 'describe-alias';
          const name = document.createElement('div');
          name.className = 'describe-name';
          name.textContent = alias.name || '';
          const args = document.createElement('div');
          args.className = 'describe-code';
          args.textContent = describeValue(alias.args, 'no CLI args');
          row.appendChild(name);
          row.appendChild(args);
          if (alias.parent && alias.parent !== alias.base) {
            const metaLine = document.createElement('div');
            metaLine.className = 'describe-muted';
            metaLine.textContent = `${alias.parent} -> ${alias.base}`;
            row.appendChild(metaLine);
          }
          const properties = Array.isArray(alias.properties) ? alias.properties : [];
          if (properties.length) appendDescribeProperties(row, properties, '');
          list.appendChild(row);
        }
        card.appendChild(list);
        grid.appendChild(card);
      }
      if (!visibleCount) return false;
      const count = section.querySelector('.describe-section-title');
      const meta = document.createElement('div');
      meta.className = 'describe-section-count';
      meta.textContent = `${visibleCount}/${items.length} partitioners`;
      count.appendChild(meta);
      section.appendChild(grid);
      container.appendChild(section);
      return true;
    }
    function renderDescribeSimpleLists(container, title, items) {
      const query = describeQuery();
      const allItems = Array.isArray(items) ? items : [];
      const list = allItems.filter(item => describeMatchesText(query, item?.name || String(item), item?.path || ''));
      if (!list.length) return false;
      const section = describeSection(title, `${list.length}/${allItems.length} available`);
      const row = document.createElement('div');
      row.className = 'describe-chip-row';
      for (const item of list) appendDescribeChip(row, item.name || String(item));
      section.appendChild(row);
      container.appendChild(section);
      return true;
    }
    function renderDescribeFilters() {
      document.querySelectorAll('[data-describe-filter]').forEach(button => {
        button.classList.toggle('active', button.dataset.describeFilter === state.describeFilter);
      });
      const input = document.getElementById('describe-search');
      if (input && input.value !== state.describeQuery) input.value = state.describeQuery;
    }
    function renderDescribeCatalog() {
      const body = document.getElementById('describe-body');
      const button = document.getElementById('describe-toggle');
      body.classList.toggle('hidden', !state.describeOpen);
      button.textContent = state.describeOpen ? 'Hide Reference' : 'Show Reference';
      renderDescribeFilters();
      const box = document.getElementById('describe-output');
      if (!state.describeOpen || !state.describeLoaded || !state.describeCatalog) return;
      box.className = 'describe-output';
      box.innerHTML = '';
      const filter = state.describeFilter || 'algorithms';
      let rendered = 0;
      if (filter === 'all' || filter === 'algorithms') rendered += renderDescribePartitioners(box, state.describeCatalog.partitioners || []) ? 1 : 0;
      if (filter === 'all' || filter === 'dsl') rendered += renderDescribeDsl(box, state.describeCatalog.dsl || {}) ? 1 : 0;
      if (filter === 'all' || filter === 'systems') rendered += renderDescribeSystems(box, state.describeCatalog.systems || []) ? 1 : 0;
      if (filter === 'all') rendered += renderDescribeSimpleLists(box, 'Parsers', state.describeCatalog.parsers || []) ? 1 : 0;
      if (filter === 'all' || filter === 'presets') rendered += renderDescribeSimpleLists(box, 'Presets', state.describeCatalog.presets || []) ? 1 : 0;
      if (!rendered) {
        box.className = 'csv-empty';
        box.textContent = state.describeQuery
          ? `No reference entries match "${state.describeQuery}".`
          : 'No reference entries in this category.';
      }
    }
    async function loadDescribeCatalog() {
      const box = document.getElementById('describe-output');
      box.className = 'describe-output';
      box.innerHTML = '';
      const loading = document.createElement('div');
      loading.className = 'progress-loading';
      const spinner = document.createElement('span');
      spinner.className = 'loading-spinner';
      const text = document.createElement('span');
      text.textContent = 'Loading reference...';
      loading.appendChild(spinner);
      loading.appendChild(text);
      box.appendChild(loading);
      try {
        state.describeCatalog = await api('/api/describe');
        state.describeLoaded = true;
        renderDescribeCatalog();
      } catch (err) {
        state.describeLoaded = false;
        box.className = 'csv-empty';
        box.textContent = `Reference failed: ${firstLines(err?.message || String(err), 3)}`;
        throw err;
      }
    }
    async function toggleDescribePanel() {
      state.describeOpen = !state.describeOpen;
      renderDescribeCatalog();
      if (state.describeOpen && !state.describeLoaded) {
        await withBusyButton('describe-toggle', 'Loading...', loadDescribeCatalog);
        renderDescribeCatalog();
      }
    }
    async function openCreateDialog() {
      document.getElementById('create-modal').classList.remove('hidden');
      document.getElementById('create-name').focus();
      await Promise.all([
        refreshConfig().catch(err => out(String(err))),
        refreshPresets().catch(err => out(String(err)))
      ]);
      updateCreatePreview();
    }
    function closeCreateDialog() {
      document.getElementById('create-modal').classList.add('hidden');
    }
    function suggestedCopyName() {
      const leaf = String(state.selected || 'experiment').split('/').filter(Boolean).pop() || 'experiment';
      return `${leaf.replace(/^\d{4}\.\d{2}\.\d{2}-/, '')}-copy`;
    }
    async function openCopyDialog() {
      if (!state.selected || state.shared) return;
      document.getElementById('copy-modal').classList.remove('hidden');
      document.getElementById('copy-summary').textContent = `Copying ${state.selected}.`;
      document.getElementById('copy-name').value = suggestedCopyName();
      await refreshConfig().catch(err => out(String(err)));
      document.getElementById('copy-template').value = state.config.name_template || '%Y.%m.%d-<name>';
      document.getElementById('copy-template-override').checked = false;
      updateCopyPreview();
      const name = document.getElementById('copy-name');
      name.focus();
      name.select();
    }
    function closeCopyDialog() {
      document.getElementById('copy-modal').classList.add('hidden');
    }
    function renderSubmitLock(lock) {
      state.submitLock = lock || { locked: false };
      const clearButton = document.getElementById('clear-submit-lock');
      const submitButton = document.getElementById('submit');
      const locked = Boolean(state.submitLock.locked);
      clearButton.disabled = !locked || !state.selected;
      updateSelectedExperimentLock(state.submitLock);
      renderSubmitButton();
    }
    function submitLockText(lock) {
      if (!lock?.locked) return '';
      const fields = lock.fields || {};
      const started = fields.started_at ? ` since ${fields.started_at}` : '';
      const algorithms = fields.algorithms ? ` (${fields.algorithms})` : '';
      return `Submit locked${started}${algorithms}`;
    }
    function submitLockMessage() {
      return submitLockText(state.submitLock);
    }
    function renderSubmitButton() {
      const submitButton = document.getElementById('submit');
      if (!submitButton) return;
      const locked = Boolean(state.submitLock?.locked);
      const loadingAlgorithms = Boolean(state.algorithmLoading && state.algorithmLoadingFor === state.selected);
      const cancelButton = document.getElementById('cancel-submit-nav');
      if (cancelButton) {
        const showCancel = locked && Boolean(state.selected) && !state.selectedArchived && !state.shared;
        cancelButton.classList.toggle('hidden', !showCancel);
        cancelButton.disabled = !showCancel || cancelButton.dataset.busy === '1';
        cancelButton.title = showCancel ? submitLockMessage() || 'Cancel submitted jobs for this experiment' : '';
      }
      const unarchiveButton = document.getElementById('unarchive-nav');
      if (unarchiveButton) {
        const showUnarchive = Boolean(state.selected) && state.selectedArchived && !state.shared;
        unarchiveButton.classList.toggle('hidden', !showUnarchive);
        unarchiveButton.disabled = !showUnarchive || unarchiveButton.dataset.busy === '1';
        unarchiveButton.title = showUnarchive ? `Unarchive ${state.selected}` : '';
      }
      submitButton.disabled = state.submitBusy || loadingAlgorithms || locked || !state.selected || state.selectedArchived;
      submitButton.classList.toggle('is-busy', state.submitBusy || loadingAlgorithms);
      if (state.submitBusy) {
        submitButton.textContent = 'Submitting...';
        submitButton.title = 'Submitting experiment...';
        submitButton.setAttribute('aria-label', 'Submitting experiment');
      } else if (loadingAlgorithms) {
        submitButton.textContent = 'Loading...';
        submitButton.title = 'Loading submit choices...';
        submitButton.setAttribute('aria-label', 'Loading submit choices');
      } else {
        submitButton.textContent = 'Submit';
        const submitTitle = state.selectedArchived ? 'Unarchive before submitting.' : (locked ? submitLockMessage() : 'Submit selected algorithms');
        submitButton.title = submitTitle;
        submitButton.setAttribute('aria-label', submitTitle);
      }
      const previewButton = document.getElementById('submit-preview-open');
      if (previewButton && previewButton.dataset.busy !== '1') {
        previewButton.disabled = loadingAlgorithms || !state.selected || state.selectedArchived;
        previewButton.title = loadingAlgorithms ? 'Loading submit choices...' : 'Show generated partitioner invocations';
      }
      const clearButton = document.getElementById('clear-submit-lock');
      if (clearButton) clearButton.disabled = !locked || !state.selected || state.selectedArchived;
      const renameButton = document.getElementById('rename-experiment');
      if (renameButton) {
        renameButton.disabled = locked || !state.selected || state.selectedArchived;
        renameButton.title = state.selectedArchived ? 'Unarchive before renaming.' : (locked ? 'Cannot rename while submit is locked.' : '');
      }
      const archiveButton = document.getElementById('archive-experiment');
      if (archiveButton) {
        archiveButton.disabled = locked || !state.selected || state.selectedArchived;
        archiveButton.title = state.selectedArchived ? 'Already archived.' : (locked ? 'Cannot archive while submit is locked.' : '');
      }
      const deleteButton = document.getElementById('delete-experiment');
      if (deleteButton) {
        deleteButton.disabled = locked || !state.selected || state.selectedArchived;
        deleteButton.title = state.selectedArchived ? 'Unarchive before deleting.' : (locked ? 'Cannot delete while submit is locked.' : '');
      }
      const checkButton = document.getElementById('check');
      if (checkButton && checkButton.dataset.busy !== '1') {
        checkButton.disabled = !state.selected || state.selectedArchived || state.shared;
        checkButton.title = state.selectedArchived
          ? 'Unarchive before saving.'
          : (state.editorMode === 'guided'
            ? 'Generate and save the Experiment file from the guided form'
            : 'Save and validate the Experiment file');
      }
      const guidedButton = document.getElementById('editor-mode-guided');
      if (guidedButton) guidedButton.disabled = !state.selected || state.selectedArchived || state.shared;
      const textButton = document.getElementById('editor-mode-text');
      if (textButton) textButton.disabled = !state.selected;
      const progressButton = document.getElementById('refresh-progress');
      if (progressButton && progressButton.dataset.busy !== '1') {
        progressButton.disabled = !state.selected || state.selectedArchived;
        progressButton.title = state.selectedArchived ? 'Unarchive before checking progress.' : 'Reload progress';
      }
    }
    function renderAlgorithmLoading(experimentId) {
      state.algorithms = [];
      state.algorithmGroups = [];
      state.algorithmLoading = true;
      state.algorithmLoadingFor = experimentId || '';
      const list = document.getElementById('algorithm-list');
      list.className = 'chips';
      list.innerHTML = '';
      const row = document.createElement('div');
      row.className = 'chip algorithm-loading';
      const spinner = document.createElement('span');
      spinner.className = 'loading-spinner';
      const text = document.createElement('span');
      text.textContent = 'Loading algorithms...';
      row.appendChild(spinner);
      row.appendChild(text);
      list.appendChild(row);
      renderSubmitButton();
    }
    function normalizeAlgorithmGroups(experiments) {
      return (experiments || []).map(item => {
        const experiment = item.experiment || {};
        const algorithms = (item.resolved?.algorithms || [])
          .map(algorithm => algorithm?.name || '')
          .filter(Boolean)
          .sort((left, right) => left.localeCompare(right));
        return {
          function: experiment.function || item.function || item.name || experiment.name || 'Experiment',
          name: experiment.name || item.name || experiment.function || 'Experiment',
          algorithms,
        };
      }).filter(group => group.algorithms.length > 0);
    }
    function selectionSetForGroup(selectedSelections, group) {
      if (!Array.isArray(selectedSelections)) return null;
      const values = selectedSelections
        .filter(item => item && item.experiment === group.function)
        .map(item => item.algorithm);
      return new Set(values);
    }
    function setSubmitGroupChecked(groupFunction, checked) {
      document.querySelectorAll('#algorithm-list input[data-experiment]').forEach(input => {
        if (input.dataset.experiment === groupFunction) input.checked = checked;
      });
    }
    function renderAlgorithmChoices(groups, selectedSelections = null) {
      state.algorithmGroups = Array.isArray(groups) ? groups : [];
      state.algorithms = Array.from(new Set(state.algorithmGroups.flatMap(group => group.algorithms))).sort();
      state.algorithmLoading = false;
      state.algorithmLoadingFor = '';
      const list = document.getElementById('algorithm-list');
      list.className = 'submit-choice-list';
      list.innerHTML = '';
      for (const group of state.algorithmGroups) {
        const groupSelected = selectionSetForGroup(selectedSelections, group);
        const section = document.createElement('section');
        section.className = 'submit-algorithm-group';
        const header = document.createElement('div');
        header.className = 'submit-algorithm-header';
        const title = document.createElement('div');
        title.className = 'submit-algorithm-title';
        title.textContent = group.name;
        title.title = group.function;
        const actions = document.createElement('div');
        actions.className = 'submit-algorithm-actions';
        const allButton = document.createElement('button');
        allButton.type = 'button';
        allButton.textContent = 'All';
        allButton.title = `Select all algorithms in ${group.name}`;
        allButton.onclick = () => setSubmitGroupChecked(group.function, true);
        const noneButton = document.createElement('button');
        noneButton.type = 'button';
        noneButton.textContent = 'None';
        noneButton.title = `Deselect all algorithms in ${group.name}`;
        noneButton.onclick = () => setSubmitGroupChecked(group.function, false);
        actions.appendChild(allButton);
        actions.appendChild(noneButton);
        header.appendChild(title);
        header.appendChild(actions);
        section.appendChild(header);

        const chips = document.createElement('div');
        chips.className = 'chips';
        for (const name of group.algorithms) {
          const label = document.createElement('label');
          label.className = 'chip';
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.checked = groupSelected ? groupSelected.has(name) : true;
          checkbox.value = name;
          checkbox.dataset.experiment = group.function;
          checkbox.dataset.algorithm = name;
          const text = document.createElement('span');
          text.className = 'chip-label';
          text.textContent = name;
          text.title = name;
          label.appendChild(checkbox);
          label.appendChild(text);
          chips.appendChild(label);
        }
        section.appendChild(chips);
        list.appendChild(section);
      }
      if (!state.algorithmGroups.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty';
        empty.textContent = 'No algorithms found.';
        list.appendChild(empty);
      }
      renderSubmitButton();
    }
    function clearAlgorithmChoices() {
      state.algorithms = [];
      state.algorithmGroups = [];
      state.algorithmLoading = false;
      state.algorithmLoadingFor = '';
      state.algorithmLoadSeq += 1;
      document.getElementById('algorithm-list').innerHTML = '';
      renderSubmitButton();
    }
    function collectSubmitSelections() {
      const groups = state.algorithmGroups || [];
      const total = groups.reduce((sum, group) => sum + group.algorithms.length, 0);
      const selections = [];
      document.querySelectorAll('#algorithm-list input[data-experiment]').forEach(input => {
        if (!input.checked) return;
        selections.push({
          experiment: input.dataset.experiment || '',
          algorithm: input.dataset.algorithm || input.value || '',
        });
      });
      return {
        total,
        selected: selections.length,
        allSelected: total === 0 || selections.length === total,
        selections,
      };
    }
    async function refreshSubmitLock() {
      if (!state.selected) {
        renderSubmitLock({ locked: false });
        return;
      }
      if (state.selectedArchived) {
        renderSubmitLock({ locked: false });
        return;
      }
      const lock = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit-lock`);
      renderSubmitLock(lock);
    }
    async function clearSubmitLock() {
      if (!state.selected || state.selectedArchived) return;
      await withBusyButton('clear-submit-lock', 'Unlocking...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit-lock`, { method: 'DELETE' });
        renderSubmitLock(result.submit_lock);
      });
    }
    async function cancelSubmittedExperiment() {
      if (!state.selected || state.selectedArchived || !state.submitLock?.locked) return;
      const id = state.selected;
      const message = `Cancel submitted jobs for "${id}"?\n\nThis cancels associated Slurm jobs or the local submit process and then removes the submit lock.`;
      if (!confirm(message)) return;
      await withBusyButton('cancel-submit-nav', 'Cancelling...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(id)}/cancel-submit`, {
          method: 'POST',
          body: JSON.stringify({ confirm_id: id })
        });
        renderSubmitLock(result.submit_lock);
        await Promise.all([
          refreshExperiments({ force: true }),
          loadProgress({ quiet: true }).catch(() => {})
        ]);
      });
    }
    function clearSelectedExperiment() {
      state.selected = null;
      state.selectedArchived = false;
      state.editorDirty = false;
      state.submitLock = null;
      clearCheckIndicator();
      clearPlotIndicator();
      clearAlgorithmChoices();
      resetGuidedState();
      state.results = [];
      state.resultsFor = null;
      state.stats = null;
      state.statsFor = null;
      state.selectedResults = [];
      state.compareColumnModes = {};
      state.columnVisibility = {};
      state.columnVisibilityFor = null;
      state.description = null;
      state.descriptionFor = null;
      state.descriptionEditing = false;
      state.downloadOptions = null;
      state.downloadOptionsFor = null;
      state.logsDir = '';
      state.logsListing = null;
      state.logsFor = null;
      state.selectedLog = '';
      state.logContent = null;
      state.logParseResult = null;
      state.logParseFor = '';
      state.plotSources = null;
      state.plotSourcesFor = null;
      state.plotSourcesInitializedFor = null;
      state.selectedPlotSources = new Set();
      state.externalPlotSources = [];
      state.plotArtifacts = null;
      state.plotArtifactsFor = null;
      state.selectedPlotArtifact = '';
      state.plotLabelTouched = false;
      state.plotGenerationRunning = false;
      state.progressLoadSeq += 1;
      clearPlotPdfUrl();
      setView('experiment-view').catch(err => out(String(err)));
      setSelectedExperimentMetadata('Experiment');
      editor.readOnly = Boolean(state.shared);
      setEditorValue('');
      renderResultsWorkspace();
      renderStatsWorkspace();
      renderDescriptionWorkspace();
      renderLogsWorkspace();
      renderSubmitLock({ locked: false });
      renderProgress(null);
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>';
      renderTagSelect();
      renderExperimentsList();
    }
    async function archiveExperiment() {
      if (!state.selected || state.selectedArchived) return;
      if (state.submitLock?.locked) {
        alert('Cannot archive while submit is locked.');
        renderSubmitButton();
        return;
      }
      const id = state.selected;
      if (!confirm(`Archive experiment "${id}"? It will be renamed to "${id}.archived" and hidden from the sidebar.`)) return;
      const button = document.getElementById('archive-experiment');
      await withBusyButton(button, 'Archiving...', async () => {
        await api(`/api/experiments/${encodeURIComponent(id)}/archive`, { method: 'POST' });
        clearSelectedExperiment();
        await Promise.all([
          refreshExperiments({ force: true }),
          loadArchivedExperiments({ force: true }).catch(err => out(String(err)))
        ]);
      });
    }
    async function renameExperiment() {
      if (!state.selected || state.selectedArchived) return;
      if (state.submitLock?.locked) {
        alert('Cannot rename while submit is locked.');
        renderSubmitButton();
        return;
      }
      const id = state.selected;
      const newId = prompt('New experiment path:', id);
      if (newId === null) return;
      const trimmed = newId.trim().replace(/^\/+|\/+$/g, '');
      if (!trimmed || trimmed === id) return;
      const button = document.getElementById('rename-experiment');
      await withBusyButton(button, 'Renaming...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(id)}/rename`, {
          method: 'POST',
          body: JSON.stringify({ new_id: trimmed })
        });
        await refreshExperiments({ force: true });
        await selectExperiment(result.new_id);
      });
    }
    function closeShareDialog() {
      document.getElementById('share-modal').classList.add('hidden');
    }
    function validSshUsername(value) {
      const text = String(value || '').trim();
      return /^[A-Za-z0-9._-]+$/.test(text) ? text : '';
    }
    function renderShareCommand() {
      const template = state.shareCommandTemplate || '';
      const usernameInput = document.getElementById('share-username');
      const command = document.getElementById('share-command');
      const copyButton = document.getElementById('share-copy-command');
      const username = validSshUsername(usernameInput.value);
      command.value = template ? template.split('<user>').join(username || '<user>') : '';
      command.title = username || !usernameInput.value.trim()
        ? ''
        : 'SSH usernames may contain only letters, digits, dots, underscores, and hyphens.';
      copyButton.disabled = !template || !username;
      copyButton.title = copyButton.disabled ? 'Enter a valid SSH username first.' : 'Copy command';
    }
    async function copyShareCommand() {
      const command = document.getElementById('share-command');
      const text = command.value;
      if (!text || text.includes('<user>')) return;
      await copyTextToClipboard(text, command);
    }
    async function copyTextToClipboard(text, fallbackElement = null) {
      const value = String(text ?? '');
      if (!value) return false;
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        const target = fallbackElement || document.createElement('textarea');
        if (!fallbackElement) {
          target.value = value;
          target.style.position = 'fixed';
          target.style.left = '-9999px';
          document.body.appendChild(target);
        }
        target.focus();
        target.select();
        document.execCommand('copy');
        if (!fallbackElement) target.remove();
      }
      return true;
    }
    async function shareExperiment() {
      if (!state.selected || state.shared) return;
      await withBusyButton('share-experiment', '', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/share`, { method: 'POST' });
        document.getElementById('share-modal').classList.remove('hidden');
        document.getElementById('share-summary').textContent = `Shared ${result.share?.experiment_id || state.selected}.`;
        document.getElementById('share-ssh').value = result.ssh_tunnel || '';
        document.getElementById('share-link').value = result.share_url || '';
        state.shareCommandTemplate = result.colleague_command_template || '';
        document.getElementById('share-username').value = '';
        renderShareCommand();
      });
    }
    function renderDownloadOptions(options) {
      const root = document.getElementById('download-root-files');
      const list = document.getElementById('download-directories');
      const rootFiles = options?.root_files || [];
      const directories = options?.directories || [];
      root.textContent = rootFiles.length
        ? `Root files are always included: ${rootFiles.join(', ')}.`
        : 'Root files are always included.';
      list.innerHTML = '';
      if (!directories.length) {
        list.className = 'csv-empty';
        list.textContent = 'No top-level directories found.';
        return;
      }
      list.className = 'chips';
      for (const directory of directories) {
        const name = directory.name || '';
        const label = document.createElement('label');
        label.className = 'chip';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = true;
        checkbox.value = name;
        checkbox.dataset.directory = name;
        const text = document.createElement('span');
        text.className = 'chip-label';
        text.textContent = name;
        text.title = name;
        label.appendChild(checkbox);
        label.appendChild(text);
        list.appendChild(label);
      }
    }
    function setDownloadDirectoriesChecked(checked) {
      document.querySelectorAll('#download-directories input[data-directory]').forEach(input => {
        input.checked = checked;
      });
    }
    function selectedDownloadDirectories() {
      return Array.from(document.querySelectorAll('#download-directories input[data-directory]:checked'))
        .map(input => input.dataset.directory || input.value || '')
        .filter(Boolean);
    }
    async function openDownloadDialog() {
      if (!state.selected) return;
      const experimentId = state.selected;
      document.getElementById('download-modal').classList.remove('hidden');
      document.getElementById('download-summary').textContent = `Download ${experimentId}.`;
      const list = document.getElementById('download-directories');
      list.className = 'csv-empty';
      list.textContent = 'Loading directories...';
      const options = await api(`/api/experiments/${encodeURIComponent(experimentId)}/download-options`);
      if (state.selected !== experimentId) return;
      state.downloadOptions = options;
      state.downloadOptionsFor = experimentId;
      renderDownloadOptions(options);
    }
    function closeDownloadDialog() {
      document.getElementById('download-modal').classList.add('hidden');
    }
    async function performDownload() {
      if (!state.selected) return;
      const query = new URLSearchParams();
      query.set('select', '1');
      for (const directory of selectedDownloadDirectories()) query.append('dir', directory);
      await withBusyButton('download-submit', 'Preparing...', async () => {
        const result = await fetchDownload(`/api/experiments/${encodeURIComponent(state.selected)}/download?${query.toString()}`);
        const url = URL.createObjectURL(result.blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = result.filename || `${slugifyName(state.selected)}.zip`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        closeDownloadDialog();
      });
    }
    async function downloadExperiment() {
      await withBusyButton('download-experiment', '', openDownloadDialog);
    }
    function closeSubmitPreviewDialog() {
      document.getElementById('submit-preview-modal').classList.add('hidden');
    }
    function appendSubmitPreviewCode(container, title, code) {
      const item = document.createElement('div');
      item.className = 'submit-preview-step';
      const label = document.createElement('div');
      label.className = 'submit-preview-title';
      label.textContent = title;
      const block = document.createElement('pre');
      block.className = 'submit-preview-code';
      block.textContent = code;
      item.appendChild(label);
      item.appendChild(block);
      container.appendChild(item);
    }
    function groupedSubmitInvocations(invocations) {
      const groups = [];
      const byKey = new Map();
      for (const invocation of invocations) {
        const experiment = invocation.experiment || 'Experiment';
        const algorithm = invocation.algorithm || 'Algorithm';
        const key = `${experiment}\u0000${algorithm}`;
        let group = byKey.get(key);
        if (!group) {
          group = { experiment, algorithm, commands: [] };
          byKey.set(key, group);
          groups.push(group);
        }
        if (invocation.command) group.commands.push(invocation.command);
      }
      return groups;
    }
    function renderSubmitPreview(data) {
      const summary = document.getElementById('submit-preview-summary');
      const output = document.getElementById('submit-preview-output');
      const invocations = Array.isArray(data?.invocations) ? data.invocations : [];
      const generate = data?.generate || {};
      const groups = groupedSubmitInvocations(invocations);
      summary.textContent = `${invocations.length} generated partitioner invocation(s) across ${groups.length} experiment/partitioner group(s) in ${data?.cwd || 'the experiment directory'}.`;
      output.className = 'submit-preview-list';
      output.innerHTML = '';
      if (generate.returncode && generate.returncode !== 0) {
        const note = document.createElement('div');
        note.className = 'status-message error';
        note.textContent = `mkexp2 generate failed with return code ${generate.returncode}.`;
        output.appendChild(note);
        const details = [generate.stdout, generate.stderr].filter(Boolean).join('\n').trim();
        if (details) appendSubmitPreviewCode(output, 'Generate output', details);
        return;
      }
      if (!invocations.length) {
        output.className = 'csv-empty';
        output.textContent = 'No generated invocations matched the selected algorithms.';
        return;
      }
      for (const group of groups) {
        const title = `${group.experiment} / ${group.algorithm} (${group.commands.length} command${group.commands.length === 1 ? '' : 's'})`;
        appendSubmitPreviewCode(
          output,
          title,
          group.commands.join('\n')
        );
      }
    }
    async function openSubmitPreviewDialog() {
      if (!state.selected || state.selectedArchived) return;
      const experimentId = state.selected;
      const modal = document.getElementById('submit-preview-modal');
      const summary = document.getElementById('submit-preview-summary');
      const output = document.getElementById('submit-preview-output');
      modal.classList.remove('hidden');
      if (state.algorithmLoading) {
        summary.textContent = 'Submit choices are still loading.';
        output.className = 'csv-empty';
        output.textContent = 'Wait for algorithm loading to finish before previewing generated invocations.';
        return;
      }
      if (state.editorDirty && !state.shared) {
        summary.textContent = 'Saving experiment before generating invocation preview...';
        output.className = 'csv-empty';
        output.textContent = 'Saving...';
        const priorSelection = collectSubmitSelections();
        const allSelectedBeforeSave = priorSelection.allSelected;
        await persistExperiment();
        state.editorDirty = false;
        if (state.selected !== experimentId) return;
        await loadAlgorithms(experimentId, {
          selectedSelections: allSelectedBeforeSave ? null : priorSelection.selections,
        });
        if (state.selected !== experimentId) return;
      }
      const submitSelection = collectSubmitSelections();
      if (submitSelection.total > 0 && submitSelection.selected === 0) {
        summary.textContent = 'No algorithms selected.';
        output.className = 'csv-empty';
        output.textContent = 'Select at least one algorithm before previewing generated invocations.';
        return;
      }
      summary.textContent = 'Generating invocation preview...';
      output.className = 'csv-empty';
      output.textContent = 'Loading...';
      const selections = submitSelection.allSelected ? [] : submitSelection.selections;
      const data = await api(`/api/experiments/${encodeURIComponent(experimentId)}/submit-preview`, {
        method: 'POST',
        body: JSON.stringify({ selections })
      });
      if (state.selected !== experimentId) return;
      renderSubmitPreview(data);
    }
    async function deleteExperiment() {
      if (!state.selected || state.selectedArchived) return;
      if (state.submitLock?.locked) {
        alert('Cannot delete while submit is locked.');
        renderSubmitButton();
        return;
      }
      const id = state.selected;
      const typed = prompt(`Type the full experiment name to delete it:\n${id}`);
      if (typed !== id) return;
      if (!confirm(`Delete experiment "${id}" and all files in its directory?`)) return;
      const button = document.getElementById('delete-experiment');
      await withBusyButton(button, 'Deleting...', async () => {
        await api(`/api/experiments/${encodeURIComponent(id)}`, { method: 'DELETE' });
        clearSelectedExperiment();
        await refreshExperiments({ force: true });
      });
    }
    async function unarchiveExperiment(id, button, options = {}) {
      const keepArchivePane = Boolean(options.keepArchivePane);
      await withBusyButton(button, 'Unarchiving...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(id)}/unarchive`, { method: 'POST' });
        await Promise.all([
          refreshExperiments({ force: true }),
          loadArchivedExperiments({ force: true })
        ]);
        if (state.selected === id && result.active_id) {
          await selectExperiment(result.active_id, { keepArchivePane });
        }
      });
    }
    async function unarchiveSelectedExperiment() {
      if (!state.selected || !state.selectedArchived || state.shared) return;
      await unarchiveExperiment(state.selected, document.getElementById('unarchive-nav'), { keepArchivePane: true });
    }
    function setProgressLoading(active) {
      if (active) {
        if (!state.progressBusyRestore) state.progressBusyRestore = setIconButtonSpinning('refresh-progress');
        return;
      }
      if (state.progressBusyRestore) {
        state.progressBusyRestore();
        state.progressBusyRestore = null;
      }
    }
    function startProgressPolling() {
      if (state.progressTimer) return;
      state.progressTimer = setTimeout(() => {
        state.progressTimer = null;
        if (state.selected) loadProgress({ quiet: true, auto: true }).catch(err => out(String(err)));
      }, AUTO_RELOAD_INTERVAL_MS);
    }
    function stopProgressPolling() {
      if (state.progressTimer) {
        clearTimeout(state.progressTimer);
        state.progressTimer = null;
      }
    }
    function renderProgressLoading(experimentId = state.selected) {
      if (!state.selected || (experimentId && state.selected !== experimentId)) return;
      stopProgressPolling();
      setProgressLoading(true);
    }
    async function openProgressLog(path) {
      const logPath = String(path || '');
      if (!state.selected || !logPath) return;
      const experimentId = state.selected;
      await setView('logs-view');
      if (state.selected !== experimentId) return;
      const directory = logPath.split('/').slice(0, -1).join('/');
      await loadLogs(directory);
      await loadLogFile(logPath);
    }
    function makeProgressLogClickable(element, path, label) {
      if (!path) return;
      element.classList.add('progress-clickable');
      element.tabIndex = 0;
      element.title = `Open newest log for ${label}`;
      element.onclick = () => openProgressLog(path).catch(err => out(String(err)));
      element.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openProgressLog(path).catch(err => out(String(err)));
        }
      };
    }
    function renderProgress(result) {
      const box = document.getElementById('progress-output');
      const command = result?.progress || result;
      const progress = result?.progress_json || null;
      if (!state.selected) {
        stopProgressPolling();
        setProgressLoading(false);
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        return;
      }
      if (!result) {
        stopProgressPolling();
        setProgressLoading(false);
        box.className = 'csv-empty';
        box.textContent = 'Run progress to count finished log files against expected runs.';
        return;
      }
      if (progress) {
        box.className = 'progress-output';
        box.innerHTML = '';
        for (const experiment of progress.experiments || []) {
          const card = document.createElement('section');
          card.className = 'progress-experiment';
          const header = document.createElement('div');
          header.className = 'progress-experiment-header';
          if (!experiment.complete) {
            makeProgressLogClickable(header, experiment.latest_log, experiment.name || experiment.function || 'experiment');
          }
          const name = document.createElement('div');
          name.className = 'progress-experiment-name';
          name.textContent = experiment.name || experiment.function || 'Experiment';
          const bar = document.createElement('div');
          bar.className = 'progress-bar';
          const fill = document.createElement('div');
          fill.className = 'progress-bar-fill';
          fill.style.width = `${Math.max(0, Math.min(100, Number(experiment.percent || 0)))}%`;
          bar.appendChild(fill);
          const count = document.createElement('div');
          count.className = 'progress-count';
          count.textContent = `${experiment.done || 0} / ${experiment.total || 0}`;
          header.appendChild(name);
          header.appendChild(bar);
          header.appendChild(count);
          card.appendChild(header);

          for (const algorithm of experiment.algorithms || []) {
            const row = document.createElement('div');
            row.className = 'progress-row';
            if (!algorithm.complete) {
              makeProgressLogClickable(row, algorithm.latest_log, algorithm.name || 'algorithm');
            }
            const rowName = document.createElement('div');
            rowName.className = 'progress-row-name';
            rowName.textContent = algorithm.name || '';
            const rowBar = document.createElement('div');
            rowBar.className = 'progress-bar';
            const rowFill = document.createElement('div');
            rowFill.className = 'progress-bar-fill';
            rowFill.style.width = `${Math.max(0, Math.min(100, Number(algorithm.percent || 0)))}%`;
            rowBar.appendChild(rowFill);
            const rowCount = document.createElement('div');
            rowCount.className = 'progress-count';
            rowCount.textContent = `${algorithm.done || 0} / ${algorithm.total || 0}`;
            row.appendChild(rowName);
            row.appendChild(rowBar);
            row.appendChild(rowCount);
            card.appendChild(row);
          }
          box.appendChild(card);
        }
        if (progress.complete) stopProgressPolling();
        else startProgressPolling();
        setProgressLoading(false);
        return;
      }
      stopProgressPolling();
      setProgressLoading(false);
      const text = stripAnsi(`${command?.stdout || ''}${command?.stderr ? `\n${command.stderr}` : ''}`).trim();
      box.className = text ? 'progress-output' : 'csv-empty';
      box.textContent = text || 'No progress output.';
    }
    async function loadProgress(options = {}) {
      const experimentId = options.experimentId || state.selected;
      if (!experimentId) return;
      if (state.selectedArchived && experimentId === state.selected) return;
      const loadId = ++state.progressLoadSeq;
      renderProgressLoading(experimentId);
      let result = null;
      try {
        result = await api(`/api/experiments/${encodeURIComponent(experimentId)}/progress`);
      } catch (err) {
        if (state.selected === experimentId && state.progressLoadSeq === loadId) setProgressLoading(false);
        if (state.selected === experimentId && state.progressLoadSeq === loadId && options.auto) startProgressPolling();
        if (state.selected === experimentId && state.progressLoadSeq === loadId && !options.quiet) {
          stopProgressPolling();
          const box = document.getElementById('progress-output');
          box.className = 'csv-empty';
          box.textContent = `Progress failed: ${firstLines(err?.message || String(err), 3)}`;
        }
        throw err;
      }
      if (state.selected !== experimentId || state.progressLoadSeq !== loadId) return;
      renderSubmitLock(result.submit_lock);
      renderProgress(result);
    }
    async function selectExperiment(id, options = {}) {
      const selectionId = ++state.selectionSeq;
      state.selected = id;
      state.selectedArchived = false;
      state.archivePaneOpen = Boolean(options.keepArchivePane);
      state.sidebarFocus = 'experiments';
      state.algorithmLoadSeq += 1;
      state.editorDirty = false;
      clearCheckIndicator();
      clearPlotIndicator();
      resetGuidedState();
      state.results = [];
      state.resultsFor = null;
      state.stats = null;
      state.statsFor = null;
      state.selectedResults = [];
      state.compareColumnModes = {};
      state.columnVisibility = {};
      state.columnVisibilityFor = null;
      state.description = null;
      state.descriptionFor = null;
      state.descriptionEditing = false;
      state.downloadOptions = null;
      state.downloadOptionsFor = null;
      state.logsDir = '';
      state.logsListing = null;
      state.logsFor = null;
      state.selectedLog = '';
      state.logContent = null;
      state.logParseResult = null;
      state.logParseFor = '';
      state.submitLock = null;
      state.plotSources = null;
      state.plotSourcesFor = null;
      state.plotSourcesInitializedFor = null;
      state.selectedPlotSources = new Set();
      state.externalPlotSources = [];
      state.plotArtifacts = null;
      state.plotArtifactsFor = null;
      state.selectedPlotArtifact = '';
      state.plotLabelTouched = false;
      state.plotGenerationRunning = false;
      clearPlotPdfUrl();
      editor.readOnly = Boolean(state.shared);
      setView('experiment-view').catch(err => out(String(err)));
      renderResultsWorkspace();
      renderStatsWorkspace();
      renderDescriptionWorkspace();
      renderLogsWorkspace();
      renderSubmitLock({ locked: false });
      renderProgress(null);
      loadProgress({ experimentId: id }).catch(err => {
        if (state.selected === id) out(String(err));
      });
      renderAlgorithmLoading(id);
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>';
      openExperimentAncestors(id);
      renderTagSelect();
      renderExperimentsList();
      const data = await api(`/api/experiments/${encodeURIComponent(id)}/experiment`);
      if (state.selected !== id || state.selectionSeq !== selectionId) return;
      clearTransientOutput();
      setSelectedExperimentMetadata(id, data.experiment_file || data.path, data);
      setEditorValue(data.experiment);
      state.editorDirty = false;
      setExperimentTagInState(id, data.tag || null);
      renderSubmitLock(data.submit_lock);
      renderTagSelect();
      loadDescription().catch(err => out(String(err)));
      await loadAlgorithms(id);
    }
    async function selectArchivedExperiment(id) {
      const selectionId = ++state.selectionSeq;
      state.selected = id;
      state.selectedArchived = true;
      state.sidebarFocus = 'archive';
      state.algorithmLoadSeq += 1;
      state.editorDirty = false;
      clearCheckIndicator();
      clearPlotIndicator();
      clearAlgorithmChoices();
      resetGuidedState();
      state.results = [];
      state.resultsFor = null;
      state.stats = null;
      state.statsFor = null;
      state.selectedResults = [];
      state.compareColumnModes = {};
      state.columnVisibility = {};
      state.columnVisibilityFor = null;
      state.description = null;
      state.descriptionFor = null;
      state.descriptionEditing = false;
      state.downloadOptions = null;
      state.downloadOptionsFor = null;
      state.logsDir = '';
      state.logsListing = null;
      state.logsFor = null;
      state.selectedLog = '';
      state.logContent = null;
      state.logParseResult = null;
      state.logParseFor = '';
      state.submitLock = { locked: false, fields: {} };
      state.plotSources = null;
      state.plotSourcesFor = null;
      state.plotSourcesInitializedFor = null;
      state.selectedPlotSources = new Set();
      state.externalPlotSources = [];
      state.plotArtifacts = null;
      state.plotArtifactsFor = null;
      state.selectedPlotArtifact = '';
      state.plotLabelTouched = false;
      state.plotGenerationRunning = false;
      state.progressLoadSeq += 1;
      clearPlotPdfUrl();
      editor.readOnly = true;
      setView('experiment-view').catch(err => out(String(err)));
      renderResultsWorkspace();
      renderStatsWorkspace();
      renderDescriptionWorkspace();
      renderLogsWorkspace();
      renderSubmitLock({ locked: false });
      const progressBox = document.getElementById('progress-output');
      progressBox.className = 'csv-empty';
      progressBox.textContent = 'Archived experiment.';
      const list = document.getElementById('algorithm-list');
      list.className = 'csv-empty';
      list.textContent = 'Archived experiment; unarchive before submitting.';
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>';
      renderTagSelect();
      renderSubmitButton();
      renderExperimentsList();
      renderArchivedExperiments();
      const data = await api(`/api/experiments/${encodeURIComponent(id)}/experiment`);
      if (state.selected !== id || state.selectionSeq !== selectionId) return;
      clearTransientOutput();
      setSelectedExperimentMetadata(id, data.experiment_file || data.path, data);
      setEditorValue(data.experiment);
      state.editorDirty = false;
      setExperimentTagInState(id, data.tag || null);
      renderSubmitLock(data.submit_lock || { locked: false });
      renderTagSelect();
      renderSubmitButton();
      loadDescription().catch(err => out(String(err)));
    }
    async function selectSharedExperiment(shareId) {
      state.shared = true;
      state.shareId = shareId;
      document.querySelector('.app').classList.add('share-mode');
      editor.readOnly = true;
      clearCheckIndicator();
      clearPlotIndicator();
      const data = await api(`/api/share/${encodeURIComponent(shareId)}/experiment`);
      const id = data.id;
      state.selected = id;
      state.selectedArchived = false;
      state.selectionSeq += 1;
      state.algorithmLoadSeq += 1;
      clearAlgorithmChoices();
      resetGuidedState();
      setView('experiment-view').catch(err => out(String(err)));
      setSelectedExperimentMetadata(id, data.experiment_file || data.path, data);
      setEditorValue(data.experiment);
      state.editorDirty = false;
      renderSubmitLock(data.submit_lock);
      renderProgress(null);
      loadProgress({ experimentId: id }).catch(err => {
        if (state.selected === id) out(String(err));
      });
      loadDescription().catch(err => out(String(err)));
    }
    async function persistExperiment() {
      if (!state.selected) return;
      if (state.selectedArchived) throw new Error('Archived experiments cannot be edited.');
      if (state.shared) throw new Error('Shared experiments cannot be edited.');
      const experiment = document.getElementById('experiment-editor').value;
      return await api(`/api/experiments/${encodeURIComponent(state.selected)}/experiment`, {
        method: 'PUT',
        body: JSON.stringify({ experiment })
      });
    }
    async function createExperiment() {
      const name = document.getElementById('create-name').value || 'experiment';
      const preset = document.getElementById('create-preset').value;
      const nameTemplate = activeCreateTemplate();
      const button = document.getElementById('create-submit');
      await withBusyButton(button, 'Creating...', async () => {
        const data = await api('/api/experiments', {
          method: 'POST',
          body: JSON.stringify({ name, preset, name_template: nameTemplate })
        });
        closeCreateDialog();
        await refreshExperiments({ force: true });
        await selectExperiment(data.id);
      });
    }
    async function copyExperiment() {
      if (!state.selected || state.shared || state.selectedArchived) return;
      const sourceId = state.selected;
      const name = document.getElementById('copy-name').value || suggestedCopyName();
      const nameTemplate = activeCopyTemplate();
      const button = document.getElementById('copy-submit');
      await withBusyButton(button, 'Copying...', async () => {
        if (state.editorDirty) {
          await persistExperiment();
          state.editorDirty = false;
          if (state.selected !== sourceId) return;
        }
        const data = await api(`/api/experiments/${encodeURIComponent(sourceId)}/copy`, {
          method: 'POST',
          body: JSON.stringify({ name, name_template: nameTemplate })
        });
        closeCopyDialog();
        await refreshExperiments({ force: true });
        await selectExperiment(data.id);
      });
    }
    async function checkExperiment() {
      if (!state.selected || state.selectedArchived || state.shared) return;
      const experimentId = state.selected;
      const button = document.getElementById('check');
      clearCheckIndicator();
      try {
        await withBusyButton(button, 'Saving...', async () => {
          out(state.editorMode === 'guided' ? 'Saving guided experiment and checking...' : 'Saving and checking...');
          const saved = state.editorMode === 'guided' ? await saveGuidedExperiment() : await persistExperiment();
          state.editorDirty = false;
          if (state.selected !== experimentId) return;
          const result = await api(`/api/experiments/${encodeURIComponent(experimentId)}/check`, { method: 'POST' });
          if (state.selected !== experimentId) return;
          renderCheckResult(result, saved);
          try {
            await loadAlgorithms(experimentId);
          } catch (err) {
            out(`Algorithm refresh failed after check: ${String(err)}`);
          }
        });
      } catch (err) {
        if (state.selected !== experimentId) return;
        const message = firstLines(err?.message || String(err), 8) || 'mkexp2 check failed.';
        setCheckIndicator(false, message);
        appendConsoleLog('Check failed', {
          message: 'Saved the Experiment file, but mkexp2 check could not complete.',
          error: message
        });
      }
    }
    async function probeExperiment() {
      if (!state.selected) return;
      const experimentId = state.selected;
      await withBusyButton('probe-run', 'Running...', async () => {
        document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Running mkexp2 probe...</div>';
        const listing = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        if (state.selected !== experimentId) return;
        const results = [];
        for (const item of listing.experiments || []) {
          const detail = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
            method: 'POST',
            body: JSON.stringify({ selector: item.name })
          });
          if (state.selected !== experimentId) return;
          results.push(detail);
        }
        renderProbeResult(results, null);
        if (!state.selectedArchived) await loadAlgorithms(experimentId);
      });
    }
    async function loadAlgorithms(experimentId = state.selected, options = {}) {
      if (!experimentId) return;
      if (state.selectedArchived && experimentId === state.selected) return;
      const loadId = ++state.algorithmLoadSeq;
      const isCurrent = () => state.selected === experimentId && state.algorithmLoadSeq === loadId;
      const selectedSelections = Array.isArray(options.selectedSelections) ? options.selectedSelections : null;
      renderAlgorithmLoading(experimentId);
      try {
        const probe = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
          method: 'POST',
          body: JSON.stringify({ flags: ['--all', '--algorithms'] })
        });
        if (!isCurrent()) return;
        const experiments = probe.experiments || [];
        const groups = normalizeAlgorithmGroups(experiments);
        if (!isCurrent()) return;
        renderAlgorithmChoices(groups, selectedSelections);
      } catch (err) {
        if (!isCurrent()) return;
        state.algorithmLoading = false;
        state.algorithmLoadingFor = '';
        const list = document.getElementById('algorithm-list');
        list.innerHTML = '<div class="csv-empty status-bad">Algorithm loading failed.</div>';
        renderSubmitButton();
        throw err;
      }
    }
    async function submitExperiment(force = false) {
      if (!state.selected || state.selectedArchived) return;
      const experimentId = state.selected;
      if (state.algorithmLoading) {
        out('Wait for algorithm loading to finish before submitting.');
        renderSubmitButton();
        return;
      }
      if (state.submitLock?.locked) {
        renderSubmitButton();
        return;
      }
      state.submitBusy = true;
      renderSubmitButton();
      try {
        if (state.editorDirty && !state.shared) {
          const priorSelection = collectSubmitSelections();
          const allSelectedBeforeSave = priorSelection.allSelected;
          await persistExperiment();
          state.editorDirty = false;
          if (state.selected !== experimentId) return;
          await loadAlgorithms(experimentId, {
            selectedSelections: allSelectedBeforeSave ? null : priorSelection.selections,
          });
          if (state.selected !== experimentId) return;
        }
        const submitSelection = collectSubmitSelections();
        if (submitSelection.total > 0 && submitSelection.selected === 0) {
          out('Select at least one algorithm.');
          return;
        }
        const selections = submitSelection.allSelected ? [] : submitSelection.selections;
        const action = await api(`/api/experiments/${encodeURIComponent(experimentId)}/submit`, {
          method: 'POST',
          body: JSON.stringify({ selections, force })
        });
        const completed = await watchAction(action.id);
        if (!force && completed?.status === 'completed' && completed.result?.blocked === 'check failed') {
          state.submitBusy = false;
          renderSubmitButton();
          if (confirm('mkexp2 check failed. Submit anyway?')) {
            await submitExperiment(true);
          }
          return;
        }
      } finally {
        state.submitBusy = false;
        renderSubmitButton();
      }
      await refreshSubmitLock();
      await loadProgress({ quiet: true }).catch(() => {});
    }
    async function parseExperiment() {
      if (!state.selected || state.selectedArchived) return;
      await withBusyButton('parse-results', 'Parsing...', async () => {
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/parse`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        const completed = await watchAction(action.id);
        if (completed?.status === 'completed' && completed.result?.parsed) {
          await loadResults();
          state.stats = null;
          state.statsFor = null;
          renderStatsWorkspace();
        }
      });
    }
