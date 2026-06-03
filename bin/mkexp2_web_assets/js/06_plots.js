    function sourceKey(source) {
      if (source.kind === 'algorithm') return `algorithm:${source.name}`;
      return `csv:${source.experiment_id}:${source.file}:${source.alias || ''}`;
    }
    function selectedPlotSourceObjects() {
      const current = state.plotSources?.current || [];
      const all = [...current, ...state.externalPlotSources];
      return all.filter(source => state.selectedPlotSources.has(sourceKey(source)));
    }
    function removeExternalPlotSource(source) {
      const key = sourceKey(source);
      state.externalPlotSources = state.externalPlotSources.filter(item => item !== source && sourceKey(item) !== key);
      state.selectedPlotSources.delete(key);
      syncPlotLabelSuggestion();
      renderPlotPanel();
    }
    function plotById(id) {
      return (state.plotCatalog?.plots || []).find(plot => plot.id === id) || null;
    }
    function suggestedPlotLabel() {
      const selectedPlots = Array.from(state.selectedPlotTypes).map(plotById).filter(Boolean);
      const sources = selectedPlotSourceObjects();
      if (!selectedPlots.length || !sources.length) return '';
      const sourceText = sources.map(source => source.alias || source.name || source.file).join(', ');
      if (selectedPlots.length === 1) return `${selectedPlots[0].name} - ${sourceText}`;
      return `Plot set - ${sourceText}`;
    }
    function syncPlotLabelSuggestion() {
      const input = document.getElementById('plot-label');
      if (!input) return;
      if (!state.plotLabelTouched) input.value = suggestedPlotLabel();
    }
    function validatePlotSelection() {
      const plots = Array.from(state.selectedPlotTypes).map(plotById).filter(Boolean);
      const sourceCount = selectedPlotSourceObjects().length;
      if (!plots.length) return 'Select at least one plot type.';
      if (!sourceCount) return 'Select at least one CSV source.';
      for (const plot of plots) {
        if (sourceCount < Number(plot.min_sources || 0)) return `${plot.name} requires at least ${plot.min_sources} source(s).`;
        if (plot.max_sources !== null && plot.max_sources !== undefined && sourceCount > Number(plot.max_sources)) {
          return `${plot.name} accepts at most ${plot.max_sources} source(s).`;
        }
      }
      return '';
    }
    function renderPlotCatalog() {
      const box = document.getElementById('plot-catalog');
      if (!box) return;
      if (!state.plotCatalog) {
        box.className = 'csv-empty';
        box.textContent = 'Loading plot types...';
        return;
      }
      const plots = state.plotCatalog?.plots || [];
      if (!plots.length) {
        box.className = 'csv-empty';
        box.textContent = 'No plot types loaded.';
        return;
      }
      if (!state.plotCatalogInitialized) {
        state.selectedPlotTypes = new Set(plots.filter(plot => plot.default_selected).map(plot => plot.id));
        state.plotCatalogInitialized = true;
      }
      box.className = 'plot-artifact-list';
      box.innerHTML = '';
      for (const plot of plots) {
        const label = document.createElement('label');
        label.className = [
          'plot-choice',
          state.selectedPlotTypes.has(plot.id) ? 'selected' : '',
          plot.expensive ? 'expensive' : ''
        ].filter(Boolean).join(' ');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selectedPlotTypes.has(plot.id);
        checkbox.onchange = () => {
          if (checkbox.checked) state.selectedPlotTypes.add(plot.id);
          else state.selectedPlotTypes.delete(plot.id);
          syncPlotLabelSuggestion();
          renderPlotPanel();
        };
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'plot-choice-title';
        title.textContent = plot.name;
        const desc = document.createElement('div');
        desc.className = 'plot-choice-desc';
        const maxText = plot.max_sources === null || plot.max_sources === undefined ? 'any' : plot.max_sources;
        desc.textContent = `${plot.description} Sources: ${plot.min_sources}-${maxText}.${plot.expensive ? ' Expensive.' : ''}`;
        body.appendChild(title);
        body.appendChild(desc);
        label.appendChild(checkbox);
        label.appendChild(body);
        box.appendChild(label);
      }
    }
    function renderPlotSources() {
      const box = document.getElementById('plot-sources');
      if (!box) return;
      if (state.plotSourcesFor !== state.selected || !state.plotSources) {
        box.className = 'csv-empty';
        box.textContent = 'Loading sources...';
        return;
      }
      const current = state.plotSources?.current || [];
      const sources = [...current, ...state.externalPlotSources];
      if (!sources.length) {
        box.className = 'csv-empty';
        box.textContent = 'No CSV results found. Run Parse Logs first or add a CSV from another experiment.';
        return;
      }
      box.className = 'plot-artifact-list';
      box.innerHTML = '';
      for (const source of sources) {
        const key = sourceKey(source);
        const row = document.createElement('div');
        row.className = [
          'plot-source-row',
          state.selectedPlotSources.has(key) ? 'selected' : '',
          source.kind === 'csv' ? 'external' : ''
        ].filter(Boolean).join(' ');
        const checkLabel = document.createElement('label');
        checkLabel.className = 'plot-source-check';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selectedPlotSources.has(key);
        checkbox.onchange = () => {
          if (checkbox.checked) state.selectedPlotSources.add(key);
          else state.selectedPlotSources.delete(key);
          syncPlotLabelSuggestion();
          renderPlotPanel();
        };
        const body = document.createElement('div');
        body.className = 'plot-source-body';
        const title = document.createElement('div');
        title.className = 'plot-source-title';
        title.textContent = source.alias || source.name || source.file;
        const meta = document.createElement('div');
        meta.className = 'plot-source-meta';
        const sourceFile = source.file || `${source.name}.csv`;
        meta.textContent = source.kind === 'algorithm'
          ? sourceFile
          : `${source.experiment_id}/${source.file}`;
        body.appendChild(title);
        body.appendChild(meta);
        if (source.kind === 'csv') {
          const alias = document.createElement('input');
          alias.className = 'plot-source-alias';
          alias.value = source.alias || '';
          alias.onchange = () => {
            const wasSelected = state.selectedPlotSources.has(key);
            source.alias = alias.value.trim() || `${source.experiment_id}/${source.name || csvLabel(source.file)}`;
            state.selectedPlotSources.delete(key);
            if (wasSelected) state.selectedPlotSources.add(sourceKey(source));
            syncPlotLabelSuggestion();
            renderPlotPanel();
          };
          body.appendChild(alias);
        }
        checkLabel.appendChild(checkbox);
        checkLabel.appendChild(body);
        row.appendChild(checkLabel);
        if (source.kind === 'csv') {
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'icon-button plot-source-remove';
          remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
          remove.title = 'Remove external CSV source';
          remove.setAttribute('aria-label', `Remove ${source.alias || source.file}`);
          remove.onclick = event => {
            event.preventDefault();
            removeExternalPlotSource(source);
          };
          row.appendChild(remove);
        } else {
          row.appendChild(document.createElement('span'));
        }
        box.appendChild(row);
      }
    }
    function plotArtifactSetId(artifact) {
      return artifact.plot_set_id || `legacy-${slugifyName(artifact.plot_set_label || artifact.label || artifact.id)}`;
    }
    function plotArtifactSetLabel(artifact) {
      return artifact.plot_set_label || artifact.label || 'Plot set';
    }
    function plotArtifactTypeId(artifact) {
      return artifact.plot_id || 'unknown';
    }
    function plotArtifactSourceLabels(artifact) {
      return (artifact.sources || []).map(source => source.alias || source.name || source.file).filter(Boolean);
    }
    function plotArtifactSourcesText(artifact) {
      return plotArtifactSourceLabels(artifact).join(', ');
    }
    function plotArtifactDateText(value) {
      if (!value) return '';
      const date = new Date(value);
      if (!Number.isFinite(date.getTime())) return String(value);
      return date.toLocaleString([], {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    }
    function plotArtifactPreviewDetails(artifact) {
      const details = [];
      const sources = plotArtifactSourcesText(artifact);
      if (artifact.plot_set_label) details.push(artifact.plot_set_label);
      if (sources) details.push(sources);
      if (artifact.created_at) details.push(plotArtifactDateText(artifact.created_at));
      details.push(formatBytes(artifact.size));
      return details.filter(Boolean).join(' - ');
    }
    function groupPlotArtifacts(artifacts, mode) {
      const groups = new Map();
      for (const artifact of artifacts) {
        const key = mode === 'types' ? plotArtifactTypeId(artifact) : plotArtifactSetId(artifact);
        if (!groups.has(key)) {
          groups.set(key, {
            id: key,
            label: mode === 'types' ? (artifact.plot_name || artifact.plot_id || 'Unknown plot') : plotArtifactSetLabel(artifact),
            mode,
            artifacts: [],
            created_at: artifact.plot_set_created_at || artifact.created_at || '',
          });
        }
        groups.get(key).artifacts.push(artifact);
      }
      const items = Array.from(groups.values());
      for (const group of items) {
        group.artifacts.sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')));
        group.sources = Array.from(new Set(group.artifacts.flatMap(artifact =>
          plotArtifactSourceLabels(artifact)
        )));
        group.size = group.artifacts.reduce((total, artifact) => total + Number(artifact.size || 0), 0);
      }
      items.sort((left, right) => {
        const latestLeft = left.artifacts[0]?.created_at || left.created_at || '';
        const latestRight = right.artifacts[0]?.created_at || right.created_at || '';
        return String(latestRight).localeCompare(String(latestLeft));
      });
      return items;
    }
    function renderPlotArtifactItem(container, artifact) {
      const item = document.createElement('div');
      item.className = 'plot-artifact-item';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'plot-artifact-select' + (state.selectedPlotArtifact === artifact.id ? ' active' : '');
      const body = document.createElement('div');
      const title = document.createElement('div');
      title.className = 'plot-artifact-title';
      title.textContent = artifact.plot_name || artifact.label || artifact.id;
      const meta = document.createElement('div');
      meta.className = 'plot-artifact-meta';
      const sources = plotArtifactSourcesText(artifact);
      meta.textContent = `${sources || 'no sources'}; ${formatBytes(artifact.size)}`;
      body.appendChild(title);
      body.appendChild(meta);
      const open = document.createElement('span');
      open.className = 'plot-artifact-open';
      open.textContent = 'Open';
      button.appendChild(body);
      button.appendChild(open);
      button.onclick = () => {
        state.selectedPlotArtifact = artifact.id;
        clearPlotPdfUrl();
        renderPlotPanel();
      };
      item.appendChild(button);
      container.appendChild(item);
    }
    function renderPlotArtifactGroup(container, group) {
      const section = document.createElement('section');
      section.className = 'plot-artifact-group';
      const header = document.createElement('div');
      header.className = 'plot-artifact-group-header';
      const body = document.createElement('div');
      const title = document.createElement('div');
      title.className = 'plot-artifact-title';
      title.textContent = group.label;
      const meta = document.createElement('div');
      meta.className = 'plot-artifact-meta';
      const nouns = group.mode === 'types' ? 'artifact' : 'plot';
      meta.textContent = `${group.artifacts.length} ${nouns}${group.artifacts.length === 1 ? '' : 's'}; ${group.sources.join(', ') || 'no sources'}; ${formatBytes(group.size)}`;
      body.appendChild(title);
      body.appendChild(meta);
      header.appendChild(body);
      const actions = document.createElement('div');
      actions.className = 'plot-artifact-group-actions';
      if (!state.shared && group.mode === 'sets') {
        const rename = document.createElement('button');
        rename.type = 'button';
        rename.className = 'small-button';
        rename.textContent = 'Rename';
        rename.onclick = () => renamePlotArtifactSet(group.id, group.label, rename).catch(err => out(String(err)));
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'small-button danger';
        del.textContent = 'Delete set';
        del.onclick = () => deletePlotArtifactSet(group.id, group.label, del).catch(err => out(String(err)));
        actions.appendChild(rename);
        actions.appendChild(del);
      }
      header.appendChild(actions);
      section.appendChild(header);
      const items = document.createElement('div');
      items.className = 'plot-artifact-items';
      for (const artifact of group.artifacts) renderPlotArtifactItem(items, artifact);
      section.appendChild(items);
      container.appendChild(section);
    }
    function renderPlotArtifacts() {
      const box = document.getElementById('plot-artifacts');
      if (!box) return;
      const artifacts = state.plotArtifacts?.artifacts || [];
      if (!artifacts.length) {
        box.className = 'csv-empty';
        box.textContent = state.plotArtifacts?.legacy?.exists
          ? 'No managed artifacts yet. Legacy plots.pdf is still available below.'
          : 'No managed artifacts yet.';
        return;
      }
      if (!state.selectedPlotArtifact || !artifacts.find(item => item.id === state.selectedPlotArtifact)) {
        state.selectedPlotArtifact = artifacts[0].id;
      }
      const setToggle = document.getElementById('plot-view-sets');
      const typeToggle = document.getElementById('plot-view-types');
      if (setToggle) setToggle.classList.toggle('active', state.plotArtifactView !== 'types');
      if (typeToggle) typeToggle.classList.toggle('active', state.plotArtifactView === 'types');
      box.className = 'plot-artifact-list';
      box.innerHTML = '';
      for (const group of groupPlotArtifacts(artifacts, state.plotArtifactView)) {
        renderPlotArtifactGroup(box, group);
      }
    }
    function renderSelectedPlotArtifact() {
      const file = document.getElementById('plot-file');
      if (!file) return;
      const artifacts = state.plotArtifacts?.artifacts || [];
      const artifact = artifacts.find(item => item.id === state.selectedPlotArtifact);
      if (!artifact) {
        if (state.plotArtifacts?.legacy?.exists) {
          renderLegacyPlotPdf();
          return;
        }
        file.className = 'csv-empty';
        file.textContent = 'Generate a plot artifact to preview it here.';
        return;
      }
      const pdfUrl = `/api/experiments/${encodeURIComponent(state.selected)}/plot-artifacts/${encodeURIComponent(artifact.id)}.pdf`;
      const version = encodeURIComponent(`${artifact.modified_at || ''}-${artifact.size || ''}`);
      if (state.plotPdfUrlFor === artifact.id && state.plotPdfVersion === version && state.plotPdfUrl) {
        file.className = 'plot-preview';
        const title = artifact.label || artifact.plot_name || artifact.id;
        file.innerHTML = `
          <div class="plot-preview-header">
            <div class="plot-preview-heading">
              <div class="plot-preview-title">${esc(title)}</div>
              <div class="plot-preview-meta">${esc(plotArtifactPreviewDetails(artifact))}</div>
            </div>
            <a class="small-button plot-preview-open" href="${esc(state.plotPdfUrl)}" target="_blank" rel="noreferrer">Open PDF</a>
          </div>
          <iframe class="plot-pdf" src="${esc(state.plotPdfUrl)}" title="${esc(title)}"></iframe>
        `;
      } else {
        file.className = 'csv-empty';
        file.textContent = 'Loading plot artifact...';
        loadPlotPdf(pdfUrl, version, artifact.id).catch(err => {
          file.className = 'csv-empty status-bad';
          file.textContent = `Could not load plot artifact: ${err.message || err}`;
        });
      }
    }
    function renderLegacyPlotPdf() {
      const file = document.getElementById('plot-file');
      const legacy = state.plotArtifacts?.legacy;
      if (!file || !legacy?.exists) return;
      const pdfUrl = `/api/experiments/${encodeURIComponent(state.selected)}/plots.pdf`;
      const version = encodeURIComponent(`${legacy.modified_at || ''}-${legacy.size || ''}`);
      if (state.plotPdfUrlFor === 'legacy' && state.plotPdfVersion === version && state.plotPdfUrl) {
        file.className = 'plot-preview';
        file.innerHTML = `
          <div class="plot-preview-header">
            <div class="plot-preview-heading">
              <div class="plot-preview-title">Legacy plots.pdf</div>
              <div class="plot-preview-meta">${esc([plotArtifactDateText(legacy.modified_at), formatBytes(legacy.size)].filter(Boolean).join(' - '))}</div>
            </div>
            <a class="small-button plot-preview-open" href="${esc(state.plotPdfUrl)}" target="_blank" rel="noreferrer">Open PDF</a>
          </div>
          <iframe class="plot-pdf" src="${esc(state.plotPdfUrl)}" title="plots.pdf"></iframe>
        `;
      } else {
        file.className = 'csv-empty';
        file.textContent = 'Loading legacy plots.pdf...';
        loadPlotPdf(pdfUrl, version, 'legacy').catch(err => {
          file.className = 'csv-empty status-bad';
          file.textContent = `Could not load legacy plots.pdf: ${err.message || err}`;
        });
      }
    }
    function renderPlotPanel(action = null) {
      applyPlotBackendStatus();
      renderPlotCatalog();
      renderPlotSources();
      renderPlotArtifacts();
      syncPlotLabelSuggestion();
      const error = validatePlotSelection();
      const addButton = document.getElementById('plot-add-open');
      if (addButton && addButton.dataset.busy !== '1') {
        addButton.disabled = !state.selected || state.plotGenerationRunning || state.selectedArchived;
        addButton.title = state.selectedArchived
          ? 'Unarchive before generating plots.'
          : (state.plotGenerationRunning ? 'Plot generation is running' : 'Create plot artifacts');
        addButton.setAttribute('aria-label', state.plotGenerationRunning ? 'Generating plot artifacts' : 'Create plot artifacts');
        setPlotCreateButtonContent(addButton, state.plotGenerationRunning || action?.status === 'running');
      }
      const sourceButton = document.getElementById('add-plot-source');
      if (sourceButton) {
        sourceButton.hidden = Boolean(state.shared || state.selectedArchived);
        sourceButton.disabled = Boolean(state.shared || state.selectedArchived);
      }
      const button = document.getElementById('plot-results');
      if (button && button.dataset.busy !== '1') {
        button.disabled = Boolean(error) || !state.selected || state.selectedArchived;
        button.title = state.selectedArchived ? 'Unarchive before generating plots.' : (error || 'Generate selected plot artifacts');
      }
      if (!state.selected) {
        clearPlotIndicator();
        return;
      }
      if (action?.status === 'running') {
        clearPlotIndicator();
      } else if (action) {
        setPlotIndicator(actionSucceeded(action, 'plot-artifacts'), plotActionTooltip(action));
      }
      renderSelectedPlotArtifact();
    }
    function applyPlotBackendStatus() {
      const checkbox = document.getElementById('plot-no-docker');
      const label = document.getElementById('plot-no-docker-label');
      if (!checkbox || !label) return;
      const backend = state.plotBackend;
      if (!backend) {
        checkbox.disabled = false;
        label.classList.remove('disabled');
        label.title = 'Use host R instead of Docker';
        return;
      }
      if (!backend.docker_available) {
        checkbox.checked = true;
        checkbox.disabled = true;
        label.classList.add('disabled');
        label.title = backend.native_r_available
          ? 'Docker is not available; native R will be used.'
          : 'Docker is not available, and Rscript was not found.';
        return;
      }
      checkbox.disabled = false;
      label.classList.remove('disabled');
      if (!state.plotNoDockerTouched) checkbox.checked = false;
      label.title = 'Use host R instead of Docker';
    }
    async function loadPlotBackendStatus() {
      state.plotBackend = await api('/api/plot/backend');
      applyPlotBackendStatus();
      return state.plotBackend;
    }
    async function loadPlotInfo() {
      if (!state.selected) return null;
      const experimentId = state.selected;
      const artifacts = await api(`/api/experiments/${encodeURIComponent(experimentId)}/plot-artifacts`);
      if (state.selected !== experimentId) return null;
      state.plotArtifacts = artifacts;
      state.plotArtifactsFor = experimentId;
      renderPlotPanel();
      return state.plotArtifacts;
    }
    async function openPlotGenerateDialog() {
      if (!state.selected || state.selectedArchived) return;
      document.getElementById('plot-generate-modal').classList.remove('hidden');
      renderPlotPanel();
      await Promise.all([
        loadPlotBackendStatus(),
        state.plotCatalog ? Promise.resolve(state.plotCatalog) : loadPlotCatalog(),
        state.plotSourcesFor === state.selected ? Promise.resolve(state.plotSources) : loadPlotSources(false)
      ]);
      renderPlotPanel();
    }
    function closePlotGenerateDialog() {
      document.getElementById('plot-generate-modal').classList.add('hidden');
    }
    async function loadPlotPdf(pdfUrl, version, owner = state.selectedPlotArtifact || 'legacy') {
      if (!state.selected) return null;
      const selected = state.selected;
      const blob = await fetchBlob(`${pdfUrl}?v=${version}`);
      if (state.selected !== selected) return null;
      clearPlotPdfUrl();
      state.plotPdfUrl = URL.createObjectURL(blob);
      state.plotPdfUrlFor = owner;
      state.plotPdfVersion = version;
      renderPlotPanel();
      return state.plotPdfUrl;
    }
    async function deletePlotArtifactSet(setId, label, button = null) {
      if (!state.selected || state.shared) return;
      if (!confirm(`Delete the plot set "${label}" and all PDFs in it?`)) return;
      await withBusyButton(button, 'Deleting...', async () => {
        await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot-artifact-sets/${encodeURIComponent(setId)}`, {
          method: 'DELETE'
        });
        state.selectedPlotArtifact = '';
        clearPlotPdfUrl();
        await loadPlotInfo();
      });
    }
    async function renamePlotArtifactSet(setId, currentLabel, button = null) {
      if (!state.selected || state.shared) return;
      const label = prompt('Plot set name', currentLabel || '');
      if (label === null) return;
      const trimmed = label.trim();
      if (!trimmed) return;
      await withBusyButton(button, 'Renaming...', async () => {
        await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot-artifact-sets/${encodeURIComponent(setId)}`, {
          method: 'PUT',
          body: JSON.stringify({ label: trimmed })
        });
        await loadPlotInfo();
      });
    }
    async function loadPlotCatalog() {
      state.plotCatalog = await api('/api/plots/catalog');
      renderPlotPanel();
      return state.plotCatalog;
    }
    async function loadPlotSources(includeAll = false) {
      if (!state.selected) return null;
      const experimentId = state.selected;
      const query = includeAll ? '?all=1' : '';
      const data = await api(`/api/experiments/${encodeURIComponent(experimentId)}/plot-sources${query}`);
      if (state.selected !== experimentId) return null;
      if (includeAll) return data;
      state.plotSources = data;
      state.plotSourcesFor = experimentId;
      if (state.plotSourcesInitializedFor !== experimentId) {
        state.selectedPlotSources = new Set((data.current || []).map(sourceKey));
        state.plotSourcesInitializedFor = experimentId;
      }
      renderPlotPanel();
      return data;
    }
    function addExternalPlotSource(experiment, file) {
      const source = Object.assign({}, file, {
        kind: 'csv',
        alias: file.alias || `${experiment.id}/${csvLabel(file.file)}`
      });
      const key = sourceKey(source);
      if (!state.externalPlotSources.find(item => sourceKey(item) === key)) {
        state.externalPlotSources.push(source);
      }
      state.selectedPlotSources.add(key);
      syncPlotLabelSuggestion();
      renderPlotPanel();
    }
    function renderPlotSourceExperiment(container, experiment) {
      const section = document.createElement('section');
      section.className = 'plot-source-modal-exp';
      const title = document.createElement('div');
      title.className = 'plot-artifact-title';
      title.textContent = experiment.label || experiment.name || experiment.id;
      title.title = experiment.id;
      section.appendChild(title);
      if (experiment.id !== title.textContent) {
        const meta = document.createElement('div');
        meta.className = 'plot-artifact-meta';
        meta.textContent = experiment.id;
        section.appendChild(meta);
      }
      const files = document.createElement('div');
      files.className = 'plot-source-modal-files';
      for (const file of experiment.files || []) {
        const button = document.createElement('button');
        button.className = 'small-button';
        button.textContent = csvLabel(file.file);
        button.title = `${experiment.id}/${file.file}`;
        button.onclick = () => {
          addExternalPlotSource(experiment, file);
          closePlotSourceDialog();
        };
        files.appendChild(button);
      }
      section.appendChild(files);
      container.appendChild(section);
    }
    function renderPlotSourceTree(container, node, prefix = '') {
      const folders = Array.from(node.folders.entries()).sort((left, right) => left[0].localeCompare(right[0]));
      for (const [name, child] of folders) {
        const id = prefix ? `${prefix}/${name}` : name;
        const details = document.createElement('details');
        details.className = 'experiment-folder plot-source-folder';
        details.open = state.plotSourceOpenDirs.has(id);
        details.addEventListener('toggle', () => {
          if (details.open) state.plotSourceOpenDirs.add(id);
          else state.plotSourceOpenDirs.delete(id);
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
        renderPlotSourceTree(children, child, id);
        details.appendChild(summary);
        details.appendChild(children);
        container.appendChild(details);
      }
      const experiments = Array.from(node.experiments).sort((left, right) => left.label.localeCompare(right.label));
      for (const experiment of experiments) {
        renderPlotSourceExperiment(container, experiment);
      }
    }
    async function openPlotSourceDialog() {
      if (!state.selected || state.selectedArchived) return;
      const experimentId = state.selected;
      const modal = document.getElementById('plot-source-modal');
      const list = document.getElementById('plot-source-modal-list');
      const summary = document.getElementById('plot-source-modal-summary');
      modal.classList.remove('hidden');
      list.className = 'plot-source-modal-list csv-empty';
      list.textContent = 'Loading CSV files...';
      const data = await loadPlotSources(true);
      if (!data || state.selected !== experimentId) return;
      const experiments = data.experiments || [];
      summary.textContent = `${experiments.length} experiment(s) with CSV results.`;
      list.className = 'plot-source-modal-list';
      list.innerHTML = '';
      if (!experiments.length) {
        list.className = 'plot-source-modal-list csv-empty';
        list.textContent = 'No CSV files found in other experiments.';
        return;
      }
      renderPlotSourceTree(list, experimentTree(experiments));
    }
    function closePlotSourceDialog() {
      document.getElementById('plot-source-modal').classList.add('hidden');
    }
    function closeVisibleModal() {
      const modalIds = [
        'plot-source-modal',
        'plot-generate-modal',
        'job-details-modal',
        'submit-preview-modal',
        'settings-modal',
        'queue-modal',
        'share-modal',
        'git-modal',
        'download-modal',
        'copy-modal',
        'create-modal',
      ];
      for (const id of modalIds) {
        const modal = document.getElementById(id);
        if (modal && !modal.classList.contains('hidden')) {
          modal.classList.add('hidden');
          return true;
        }
      }
      return false;
    }
    async function plotExperiment() {
      if (!state.selected || state.selectedArchived) return;
      setView('plots-view').catch(err => out(String(err)));
      applyPlotBackendStatus();
      clearPlotIndicator();
      const error = validatePlotSelection();
      if (error) {
        out(error);
        renderPlotPanel();
        return;
      }
      state.plotGenerationRunning = true;
      closePlotGenerateDialog();
      renderPlotPanel({ status: 'running' });
      try {
        await withBusyButton('plot-results', 'Generating...', async () => {
          const noDocker = document.getElementById('plot-no-docker')?.checked || false;
          const label = document.getElementById('plot-label')?.value || '';
          const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot-artifacts`, {
            method: 'POST',
            body: JSON.stringify({
              no_docker: noDocker,
              plots: Array.from(state.selectedPlotTypes),
              sources: selectedPlotSourceObjects(),
              label
            })
          });
          renderPlotPanel({ status: 'running', id: action.id });
          const completed = await watchAction(action.id, current => renderPlotPanel(current));
          if (completed?.status === 'completed' && completed.result?.plotted) {
            await new Promise(resolve => setTimeout(resolve, PLOT_RELOAD_DELAY_MS));
            await loadPlotInfo();
          }
        });
      } catch (err) {
        setPlotIndicator(false, `Plot generation failed.\n${firstLines(err?.message || String(err), 6)}`);
        out(String(err));
      } finally {
        state.plotGenerationRunning = false;
        renderPlotPanel();
      }
    }
    async function watchAction(id, onUpdate = null) {
      let action = null;
      for (;;) {
        action = await api(`/api/actions/${encodeURIComponent(id)}`);
        if (onUpdate) onUpdate(action);
        else out(action);
        if (action.status !== 'running') break;
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
      return action;
    }
    async function loadResults() {
      if (!state.selected) return;
      const experimentId = state.selected;
      const previousSelection = new Set(state.selectedResults || []);
      const [data, columns] = await Promise.all([
        api(`/api/experiments/${encodeURIComponent(experimentId)}/results`),
        api(`/api/experiments/${encodeURIComponent(experimentId)}/columns`).catch(err => {
          out(`Column visibility load failed: ${String(err)}`);
          return { visibility: {} };
        }),
      ]);
      if (state.selected !== experimentId) return;
      clearTransientOutput();
      state.results = (data.files || []).map(prepareCsvFile);
      state.resultsFor = experimentId;
      state.columnVisibility = columns.visibility || {};
      state.columnVisibilityFor = 'global';
      const availableNames = state.results.map(file => file.name);
      const preservedSelection = availableNames.filter(name => previousSelection.has(name));
      state.selectedResults = preservedSelection.length
        ? preservedSelection
        : (state.results[0] ? [state.results[0].name] : []);
      if (!preservedSelection.length || preservedSelection.length !== previousSelection.size) {
        state.compareColumnModes = {};
      }
      state.stats = null;
      state.statsFor = null;
      renderResultsWorkspace();
      renderStatsWorkspace();
    }
    async function loadStats() {
      if (!state.selected || state.selectedArchived) return;
      const experimentId = state.selected;
      const data = await api(`/api/experiments/${encodeURIComponent(experimentId)}/stats`);
      if (state.selected !== experimentId) return;
      clearTransientOutput();
      state.stats = data;
      state.statsFor = experimentId;
      renderStatsWorkspace();
    }
