    const DEFAULT_TAG_COLOR_PALETTE = [
      { name: 'Blue', color: '#2563eb' },
      { name: 'Teal', color: '#0f766e' },
      { name: 'Green', color: '#16a34a' },
      { name: 'Amber', color: '#d97706' },
      { name: 'Red', color: '#dc2626' },
      { name: 'Purple', color: '#7c3aed' },
      { name: 'Pink', color: '#db2777' },
      { name: 'Slate', color: '#64748b' },
    ];
    const state = {
      experiments: [],
      archivedExperiments: [],
      selected: null,
      selectedArchived: false,
      pinnedExperiments: new Set(),
      tags: [],
      tagPalette: DEFAULT_TAG_COLOR_PALETTE,
      defaultTagNames: [],
      tagAssignments: {},
      algorithms: [],
      algorithmGroups: [],
      algorithmLoading: false,
      algorithmLoadingFor: '',
      algorithmLoadSeq: 0,
      selectionSeq: 0,
      editorDirty: false,
      editorMode: 'text',
      guidedModel: null,
      guidedFor: null,
      guidedForm: null,
      guidedDirty: false,
      benchmarkSets: [],
      benchmarkSetsFor: '',
      presets: [],
      describeCatalog: null,
      describeLoaded: false,
      describeOpen: false,
      describeFilter: 'algorithms',
      describeQuery: '',
      probeOpen: false,
      probeLoaded: false,
      probeFor: null,
      config: { name_template: '%Y.%m.%d-<name>' },
      openDirs: new Set(),
      archivedOpenDirs: new Set(),
      archiveQuery: '',
      archivePaneOpen: false,
      sidebarFocus: 'experiments',
      experimentSubdirectories: [],
      results: [],
      resultsFor: null,
      stats: null,
      statsFor: null,
      selectedResults: [],
      compareColumnModes: {},
      columnVisibility: {},
      columnVisibilityFor: null,
      logsDir: '',
      logsListing: null,
      logsFor: null,
      selectedLog: '',
      logContent: null,
      logParseResult: null,
      logParseFor: '',
      submitLock: null,
      submitBusy: false,
      plotBackend: null,
      plotCatalog: null,
      plotCatalogInitialized: false,
      plotSources: null,
      plotSourcesFor: null,
      plotSourcesInitializedFor: null,
      plotSourceOpenDirs: new Set(),
      selectedPlotTypes: new Set(),
      selectedPlotSources: new Set(),
      externalPlotSources: [],
      plotArtifacts: null,
      plotArtifactsFor: null,
      plotArtifactView: 'sets',
      selectedPlotArtifact: '',
      plotPdfUrl: '',
      plotPdfUrlFor: null,
      plotPdfVersion: '',
      plotLabelTouched: false,
      plotNoDockerTouched: false,
      plotGenerationRunning: false,
      spackCache: null,
      progressTimer: null,
      progressBusyRestore: null,
      progressLoadSeq: 0,
      nodeStatusTimer: null,
      nodeStatusBusyRestore: null,
      description: null,
      descriptionFor: null,
      descriptionEditing: false,
      downloadOptions: null,
      downloadOptionsFor: null,
      queueServerUser: '',
      activeView: 'experiment-view',
      authRequired: false,
      shared: false,
      shareId: '',
      shareCommandTemplate: '',
      settings: { theme: 'light', benchmark_base_path: '', download_archive_format: 'tar.zstd', download_archive_formats: ['tar.zstd', 'tar.gz', 'zip'], postprocess_defaults: { email_to: '', plots: 'default', email_subject: 'mkexp2 {status}: {experiment_id}', email_body: '' }, insert_templates: [] },
      settingsLoaded: false,
      workspaces: [],
      workspacesLoaded: false
    };
    const PLOT_RELOAD_DELAY_MS = 5000;
    const AUTO_RELOAD_INTERVAL_MS = 15000;
    const THEME_STORAGE_KEY = 'mkexp2-theme';
    const SIDEBAR_WIDTH_KEY = 'mkexp2-sidebar-width';
    const DEFAULT_SIDEBAR_WIDTH = 320;
    if (window.matchMedia) {
      const systemThemeQuery = window.matchMedia('(prefers-color-scheme: dark)');
      const onSystemThemeChange = () => {
        if (normalizeTheme(state.settings?.theme || localStorage.getItem(THEME_STORAGE_KEY)) === 'system') {
          applyTheme('system', false);
        }
      };
      if (systemThemeQuery.addEventListener) systemThemeQuery.addEventListener('change', onSystemThemeChange);
      else if (systemThemeQuery.addListener) systemThemeQuery.addListener(onSystemThemeChange);
    }
    const MIN_SIDEBAR_WIDTH = 260;
    const MAX_SIDEBAR_WIDTH = 560;
    const allowEmptyToken = __ALLOW_EMPTY_TOKEN__;
    const initialShareId = __SHARE_ID__;
    const tokenInput = document.getElementById('token');
    const authTokenInput = document.getElementById('auth-token');
    const authMessage = document.getElementById('auth-message');
    const editor = document.getElementById('experiment-editor');
    const editorHighlight = document.getElementById('experiment-highlight');
    tokenInput.value = localStorage.getItem('mkexp2-token') || '';
    if (authTokenInput) authTokenInput.value = tokenInput.value;
    tokenInput.addEventListener('change', () => {
      setStoredToken(tokenInput.value);
      bootAuthenticatedUi({ selectMostRecent: true }).catch(err => out(String(err)));
    });

    function token() { return tokenInput.value; }
    function setStoredToken(value) {
      const next = String(value || '').trim();
      tokenInput.value = next;
      if (authTokenInput) authTokenInput.value = next;
      if (next) localStorage.setItem('mkexp2-token', next);
      else localStorage.removeItem('mkexp2-token');
    }
    function setAuthRequired(message = '') {
      if (state.shared) return;
      state.authRequired = true;
      document.querySelector('.app')?.classList.add('auth-required');
      if (authMessage) authMessage.textContent = message;
      const experiments = document.getElementById('experiments');
      if (experiments) {
        experiments.className = 'experiment-list csv-empty';
        experiments.textContent = 'Enter a session token to load experiments.';
      }
      const nodes = document.getElementById('slurm-status');
      if (nodes) {
        nodes.className = 'node-list muted';
        nodes.textContent = 'Enter a session token to load node status.';
      }
      if (typeof stopNodeStatusPolling === 'function') stopNodeStatusPolling();
      if (authTokenInput) {
        authTokenInput.value = token();
        window.setTimeout(() => authTokenInput.focus(), 0);
      }
    }
    function clearAuthRequired() {
      state.authRequired = false;
      document.querySelector('.app')?.classList.remove('auth-required');
      if (authMessage) authMessage.textContent = '';
      const experiments = document.getElementById('experiments');
      if (experiments) experiments.className = 'experiment-list';
    }
    async function submitAuthToken() {
      const value = authTokenInput ? authTokenInput.value : tokenInput.value;
      if (!String(value || '').trim() && !allowEmptyToken) {
        setAuthRequired('Paste the session token printed by mkexp2 web.');
        return;
      }
      setStoredToken(value);
      clearAuthRequired();
      await bootAuthenticatedUi({ selectMostRecent: true });
    }
    async function bootAuthenticatedUi(options = {}) {
      if (state.shared) return;
      if (!(token() || allowEmptyToken)) {
        setAuthRequired();
        return;
      }
      clearAuthRequired();
      out('');
      loadUiSettings().catch(err => out(String(err)));
      refreshConfig().catch(err => out(String(err)));
      refreshPresets().catch(err => out(String(err)));
      refreshExperiments({ selectMostRecent: options.selectMostRecent !== false }).catch(err => out(String(err)));
      refreshStatus().catch(err => out(String(err)));
    }
    function apiPath(path) {
      if (!state.shared) return path;
      if (path.startsWith('/api/actions/')) {
        return `/api/share/${encodeURIComponent(state.shareId)}/actions/${path.split('/').pop()}`;
      }
      if (path === '/api/plot/backend') return `/api/share/${encodeURIComponent(state.shareId)}/plot/backend`;
      if (path === '/api/plots/catalog') return `/api/share/${encodeURIComponent(state.shareId)}/plots/catalog`;
      if (path === '/api/describe') return `/api/share/${encodeURIComponent(state.shareId)}/describe`;
      const match = path.match(/^\/api\/experiments\/[^/]+(?:\/([^?]+))?(\?.*)?$/);
      if (!match) return path;
      const tail = match[1] || 'metadata';
      const query = match[2] || '';
      return `/api/share/${encodeURIComponent(state.shareId)}/${tail}${query}`;
    }
    function clampSidebarWidth(width) {
      const viewportLimit = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, Math.round(window.innerWidth * 0.48)));
      return Math.max(MIN_SIDEBAR_WIDTH, Math.min(viewportLimit, Math.round(width)));
    }
    function setSidebarWidth(width, persist = true) {
      const clamped = clampSidebarWidth(width);
      document.documentElement.style.setProperty('--sidebar-width', `${clamped}px`);
      if (persist) localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clamped));
      return clamped;
    }
    function initSidebarResize() {
      const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
      setSidebarWidth(Number.isFinite(saved) && saved > 0 ? saved : DEFAULT_SIDEBAR_WIDTH, false);
      const app = document.querySelector('.app');
      const resizer = document.getElementById('sidebar-resizer');
      let resizing = false;
      function stopResize(event) {
        if (!resizing) return;
        resizing = false;
        app.classList.remove('resizing');
        if (event?.pointerId !== undefined && resizer.hasPointerCapture(event.pointerId)) {
          resizer.releasePointerCapture(event.pointerId);
        }
      }
      resizer.addEventListener('pointerdown', event => {
        if (window.matchMedia('(max-width: 980px)').matches) return;
        resizing = true;
        app.classList.add('resizing');
        resizer.setPointerCapture(event.pointerId);
        setSidebarWidth(event.clientX);
        event.preventDefault();
      });
      resizer.addEventListener('pointermove', event => {
        if (!resizing) return;
        setSidebarWidth(event.clientX);
      });
      resizer.addEventListener('pointerup', stopResize);
      resizer.addEventListener('pointercancel', stopResize);
      resizer.addEventListener('keydown', event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        const current = Number(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').replace('px', '')) || DEFAULT_SIDEBAR_WIDTH;
        setSidebarWidth(current + (event.key === 'ArrowRight' ? 24 : -24));
        event.preventDefault();
      });
      window.addEventListener('resize', () => {
        const current = Number(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').replace('px', '')) || DEFAULT_SIDEBAR_WIDTH;
        setSidebarWidth(current, false);
      });
    }
    function setButtonBusy(buttonOrId, label = undefined) {
      const button = typeof buttonOrId === 'string' ? document.getElementById(buttonOrId) : buttonOrId;
      if (!button) return () => {};
      if (button.dataset.busy === '1') return () => {};
      const previous = {
        disabled: button.disabled,
        html: button.innerHTML,
        title: button.title || '',
      };
      button.dataset.busy = '1';
      button.disabled = true;
      button.classList.add('is-busy');
      button.setAttribute('aria-busy', 'true');
      const nextLabel = label === undefined
        ? (button.classList.contains('icon-button') ? '' : 'Working...')
        : label;
      button.textContent = nextLabel;
      return () => {
        button.disabled = previous.disabled;
        button.innerHTML = previous.html;
        button.title = previous.title;
        button.classList.remove('is-busy');
        button.removeAttribute('aria-busy');
        delete button.dataset.busy;
      };
    }
    function setIconButtonSpinning(buttonOrId) {
      const button = typeof buttonOrId === 'string' ? document.getElementById(buttonOrId) : buttonOrId;
      if (!button) return () => {};
      if (button.dataset.busy === '1') return () => {};
      const previous = {
        disabled: button.disabled,
        title: button.title || '',
      };
      button.dataset.busy = '1';
      button.disabled = true;
      button.classList.add('is-spinning');
      button.setAttribute('aria-busy', 'true');
      return () => {
        button.disabled = previous.disabled;
        button.title = previous.title;
        button.classList.remove('is-spinning');
        button.removeAttribute('aria-busy');
        delete button.dataset.busy;
      };
    }
    async function withBusyButton(buttonOrId, label, task) {
      const restore = setButtonBusy(buttonOrId, label);
      try {
        return await task();
      } finally {
        restore();
      }
    }
    function appendConsoleLog(title, value) {
      if (window.console && console.debug) console.debug(title, value);
    }
    function out(value) {
      appendConsoleLog('Message', value);
    }
    function clearTransientOutput() {
    }
    function stripAnsi(text) {
      return String(text || '').replace(/\x1b\[[0-9;]*m/g, '');
    }
    function checkCount(text, label) {
      const matches = Array.from(stripAnsi(text).matchAll(new RegExp(`${label}:\\s*(\\d+)`, 'gi')));
      if (!matches.length) return null;
      return matches[matches.length - 1][1];
    }
    function appendCheckFact(container, label, value) {
      const item = document.createElement('div');
      item.className = 'check-fact';
      const itemLabel = document.createElement('div');
      itemLabel.className = 'check-fact-label';
      itemLabel.textContent = label;
      const itemValue = document.createElement('div');
      itemValue.className = 'check-fact-value';
      itemValue.textContent = value;
      item.appendChild(itemLabel);
      item.appendChild(itemValue);
      container.appendChild(item);
    }
    function parseCheckJson(result) {
      const text = stripAnsi(result?.stdout || '').trim();
      if (!text) return null;
      try {
        const parsed = JSON.parse(text);
        return parsed && typeof parsed === 'object' ? parsed : null;
      } catch {
        return null;
      }
    }
    function setCheckIndicator(ok, tooltip) {
      const indicator = document.getElementById('check-indicator');
      if (!indicator) return;
      indicator.className = `check-indicator ${ok ? 'ok' : 'bad'}`;
      indicator.textContent = ok ? '✓' : '!';
      indicator.title = tooltip || (ok ? 'mkexp2 check passed.' : 'mkexp2 check failed.');
      indicator.setAttribute('aria-label', indicator.title);
    }
    function clearCheckIndicator() {
      const indicator = document.getElementById('check-indicator');
      if (!indicator) return;
      indicator.className = 'check-indicator hidden';
      indicator.textContent = '';
      indicator.title = '';
      indicator.removeAttribute('aria-label');
    }
    function setPlotIndicator(ok, tooltip) {
      const indicator = document.getElementById('plot-indicator');
      if (!indicator) return;
      indicator.className = `check-indicator ${ok ? 'ok' : 'bad'}`;
      indicator.textContent = ok ? '✓' : '!';
      indicator.title = tooltip || (ok ? 'Plot generation completed.' : 'Plot generation failed.');
      indicator.setAttribute('aria-label', indicator.title);
    }
    function clearPlotIndicator() {
      const indicator = document.getElementById('plot-indicator');
      if (!indicator) return;
      indicator.className = 'check-indicator hidden';
      indicator.textContent = '';
      indicator.title = '';
      indicator.removeAttribute('aria-label');
    }
    function setPlotCreateButtonContent(button, running = false) {
      if (!button) return;
      button.innerHTML = '';
      if (running) {
        const spinner = document.createElement('span');
        spinner.className = 'loading-spinner';
        spinner.setAttribute('aria-hidden', 'true');
        const label = document.createElement('span');
        label.textContent = 'Generating...';
        button.appendChild(spinner);
        button.appendChild(label);
        return;
      }
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.setAttribute('viewBox', '0 0 24 24');
      icon.setAttribute('aria-hidden', 'true');
      for (const d of ['M12 5v14', 'M5 12h14']) {
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', d);
        icon.appendChild(path);
      }
      const label = document.createElement('span');
      label.textContent = 'Create';
      button.appendChild(icon);
      button.appendChild(label);
    }
    function firstLines(text, limit = 6) {
      return stripAnsi(String(text || ''))
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(Boolean)
        .slice(0, limit)
        .join('\n');
    }
    function checkErrorTooltip(result, payload, importantLines = []) {
      const payloadErrors = Array.isArray(payload?.errors)
        ? payload.errors.map(item => typeof item === 'string' ? item : JSON.stringify(item))
        : [];
      const lines = [
        result?.timed_out ? `mkexp2 check timed out after ${result.elapsed_seconds ?? '?'}s.` : '',
        ...payloadErrors,
        ...importantLines,
        firstLines(result?.stderr || '', 4),
        firstLines(result?.stdout || '', 4)
      ].filter(Boolean);
      return lines.length ? lines.slice(0, 8).join('\n') : 'mkexp2 check failed.';
    }
    function actionSucceeded(action, kind) {
      if (action?.status !== 'completed') return false;
      if (kind === 'parse') return Boolean(action.result?.parsed);
      if (kind === 'plot') return Boolean(action.result?.plotted);
      if (kind === 'plot-artifacts') return Boolean(action.result?.plotted);
      return true;
    }
    function actionCommand(action, kind) {
      if (kind === 'parse') return action?.result?.parse;
      if (kind === 'plot') return action?.result?.plot;
      if (kind === 'plot-artifacts') return action?.result?.commands?.[0]?.command || null;
      return null;
    }
    function plotActionTooltip(action) {
      const command = actionCommand(action, 'plot-artifacts');
      const prefix = actionSucceeded(action, 'plot-artifacts')
        ? 'Plot generation completed.'
        : 'Plot generation failed.';
      const details = [];
      if (command) {
        details.push(`Return code ${command.returncode}; elapsed ${command.elapsed_seconds ?? '?'}s.`);
        const stderr = firstLines(command.stderr || '', 4);
        const stdout = firstLines(command.stdout || '', 4);
        if (stderr) details.push(stderr);
        else if (stdout && !actionSucceeded(action, 'plot-artifacts')) details.push(stdout);
      } else if (action?.error) {
        details.push(String(action.error));
      }
      return [prefix, ...details].filter(Boolean).join('\n');
    }
    function renderCheckResult(result, saveResult) {
      const payload = parseCheckJson(result);
      const ok = result?.timed_out ? false : (payload ? Boolean(payload.ok) && Number(result.returncode) === 0 : Number(result.returncode) === 0);
      const warningOnly = ok && Number(payload?.warnings || 0) > 0;
      const combined = `${result.stdout || ''}\n${result.stderr || ''}`;
      const cleanOutput = stripAnsi(combined);
      const importantLines = cleanOutput
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => /\[(fail|warn)\]/i.test(line));
      setCheckIndicator(ok, ok
        ? (warningOnly ? 'mkexp2 check passed with warnings.' : 'mkexp2 check passed.')
        : checkErrorTooltip(result, payload, importantLines));
      appendConsoleLog(ok ? (warningOnly ? 'Check passed with warnings' : 'Check passed') : 'Check failed', {
        message: ok
          ? 'Saved the Experiment file and validated the experiment configuration.'
          : 'Saved the Experiment file, but mkexp2 check reported problems.',
        saved: saveResult?.path || null,
        returncode: result.returncode,
        elapsed_seconds: result.elapsed_seconds,
        errors: payload?.errors ?? checkCount(cleanOutput, 'errors') ?? (ok ? '0' : 'unknown'),
        warnings: payload?.warnings ?? checkCount(cleanOutput, 'warnings') ?? '0',
        important_lines: importantLines,
        check: result,
        parsed: payload
      });
    }
    function algorithmDefinitionMap(declared) {
      const map = new Map();
      for (const definition of declared?.algorithm_definitions || []) {
        map.set(definition.name, definition);
      }
      return map;
    }
    function algorithmChain(name, definitionMap) {
      const chain = [];
      const seen = new Set();
      let current = name;
      while (current && !seen.has(current)) {
        seen.add(current);
        const definition = definitionMap.get(current);
        if (!definition) {
          chain.push({ name: current, base: '', args: '', plugin: true });
          break;
        }
        chain.push(definition);
        current = definition.base;
      }
      return chain;
    }
    function probeDisplayValue(value) {
      if (value === '' || value === null || value === undefined) return '(none)';
      return String(value);
    }
    function probeChainText(algorithm, declared) {
      const definitions = algorithmDefinitionMap(declared);
      return algorithmChain(algorithm.name, definitions).map(node => node.name).join(' -> ');
    }
    function renderProbeIdentity(algorithm, declared) {
      const identity = document.createElement('div');
      identity.className = 'probe-identity';
      const title = document.createElement('div');
      title.className = 'probe-algorithm-title';
      const name = document.createElement('div');
      name.textContent = algorithm.name;
      const base = document.createElement('div');
      base.className = 'probe-algorithm-base';
      base.textContent = `base ${algorithm.base || '(none)'}`;
      title.appendChild(name);
      title.appendChild(base);
      const chain = document.createElement('div');
      chain.className = 'probe-chain';
      chain.textContent = probeChainText(algorithm, declared);
      identity.appendChild(title);
      identity.appendChild(chain);
      return identity;
    }
    function renderProbePrimaryField(label, value) {
      const field = document.createElement('div');
      field.className = 'probe-primary-field';
      const fieldLabel = document.createElement('div');
      fieldLabel.className = 'probe-primary-label';
      fieldLabel.textContent = label;
      const fieldValue = document.createElement('div');
      fieldValue.className = 'probe-primary-value';
      fieldValue.textContent = probeDisplayValue(value);
      if (fieldValue.textContent === '(none)') fieldValue.classList.add('probe-empty');
      field.appendChild(fieldLabel);
      field.appendChild(fieldValue);
      return field;
    }
    function probeSettingPairs(algorithm) {
      const pairs = [];
      const add = (key, value) => {
        if (value === '' || value === null || value === undefined) return;
        pairs.push([key, String(value)]);
      };
      add('parser', algorithm.parser?.spec);
      const properties = algorithm.properties || {};
      for (const key of Object.keys(properties).sort()) {
        if (key === 'repo_ref') continue;
        if (key === 'build_key' || key === 'binary_path') continue;
        add(key, properties[key]);
      }
      return pairs;
    }
    function renderProbeSettings(algorithm) {
      const wrapper = document.createElement('div');
      wrapper.className = 'probe-settings';
      const settingsTitle = document.createElement('div');
      settingsTitle.className = 'probe-settings-title';
      settingsTitle.textContent = 'Resolved settings';
      wrapper.appendChild(settingsTitle);
      const chips = document.createElement('div');
      chips.className = 'probe-setting-chips';
      const pairs = probeSettingPairs(algorithm);
      if (!pairs.length) {
        const empty = document.createElement('span');
        empty.className = 'probe-empty';
        empty.textContent = '(none)';
        chips.appendChild(empty);
      }
      for (const [key, value] of pairs) {
        const chip = document.createElement('span');
        chip.className = 'probe-setting-chip';
        const keyNode = document.createElement('span');
        keyNode.className = 'probe-setting-key';
        keyNode.textContent = `${key}=`;
        const valueNode = document.createElement('span');
        valueNode.className = 'probe-setting-value';
        valueNode.title = value;
        valueNode.textContent = value;
        chip.appendChild(keyNode);
        chip.appendChild(valueNode);
        chips.appendChild(chip);
      }
      wrapper.appendChild(chips);
      return wrapper;
    }
    function probeArrayValues(values) {
      return Array.isArray(values)
        ? values.filter(value => value !== null && value !== undefined && value !== '').map(value => String(value))
        : [];
    }
    function probeGraphs(result) {
      const declared = probeArrayValues(result.declared?.graphs);
      if (declared.length) return declared;
      return probeArrayValues((result.resolved?.graphs || []).map(graph => graph.spec || graph.basename || graph.resolved_path));
    }
    function renderProbeInputCard(label, values, options = {}) {
      const card = document.createElement('div');
      card.className = 'probe-input-card';
      const title = document.createElement('div');
      title.className = 'probe-input-title';
      title.textContent = `${label} (${values.length})`;
      card.appendChild(title);
      const list = document.createElement('div');
      list.className = `probe-input-values${options.graphs ? ' graphs' : ''}`;
      if (!values.length) {
        list.textContent = '(none)';
        list.classList.add('probe-empty');
      } else if (options.graphs) {
        list.textContent = values.join('\n');
      } else {
        for (const value of values) {
          const chip = document.createElement('span');
          chip.className = 'probe-input-chip';
          chip.title = value;
          chip.textContent = value;
          list.appendChild(chip);
        }
      }
      card.appendChild(list);
      return card;
    }
    function renderProbeInputs(result) {
      const grid = document.createElement('div');
      grid.className = 'probe-input-grid';
      grid.appendChild(renderProbeInputCard('Graphs', probeGraphs(result), { graphs: true }));
      grid.appendChild(renderProbeInputCard('K', probeArrayValues(result.declared?.ks)));
      grid.appendChild(renderProbeInputCard('Eps', probeArrayValues(result.declared?.epsilons)));
      grid.appendChild(renderProbeInputCard('Seeds', probeArrayValues(result.declared?.seeds)));
      return grid;
    }
    function renderProbeResult(results, saveResult) {
      const box = document.getElementById('probe-output');
      box.innerHTML = '';
      box.className = 'probe-output';
      const root = document.createElement('div');
      root.className = 'probe-output';

      for (const result of results) {
        const section = document.createElement('section');
        section.className = 'probe-section';
        const sectionHeader = document.createElement('div');
        sectionHeader.className = 'probe-section-header';
        const title = document.createElement('h3');
        title.textContent = `${result.experiment?.name || 'Experiment'} (${result.experiment?.function || 'unknown'})`;
        sectionHeader.appendChild(title);

        const details = document.createElement('div');
        details.className = 'probe-section-meta';
        details.textContent = `System ${result.experiment?.system || 'unknown'}; ${result.resolved?.algorithms?.length || 0} enabled algorithm(s).`;
        sectionHeader.appendChild(details);
        section.appendChild(sectionHeader);
        section.appendChild(renderProbeInputs(result));

        const list = document.createElement('div');
        list.className = 'probe-algorithm-list';

        for (const algorithm of result.resolved?.algorithms || []) {
          const row = document.createElement('article');
          row.className = 'probe-algorithm-row';
          const main = document.createElement('div');
          main.className = 'probe-algorithm-main';
          main.appendChild(renderProbeIdentity(algorithm, result.declared || {}));
          main.appendChild(renderProbePrimaryField('Branch', algorithm.properties?.repo_ref || ''));
          main.appendChild(renderProbePrimaryField('CLI arguments', algorithm.args || ''));
          row.appendChild(main);
          const detailRow = document.createElement('div');
          detailRow.className = 'probe-detail-row';
          detailRow.appendChild(renderProbeSettings(algorithm));
          row.appendChild(detailRow);
          list.appendChild(row);
        }
        section.appendChild(list);
        root.appendChild(section);
      }
      box.appendChild(root);
    }
    function isCommandResult(value) {
      return value && typeof value === 'object'
        && (Array.isArray(value.argv) || typeof value.cmd === 'string')
        && ('returncode' in value || 'stdout' in value || 'stderr' in value);
    }
    function collectCommandResults(value, prefix = '', out = []) {
      if (!value || typeof value !== 'object') return out;
      if (isCommandResult(value)) {
        out.push({ label: prefix || 'command', command: value });
        return out;
      }
      if (Array.isArray(value)) {
        value.forEach((item, index) => collectCommandResults(item, `${prefix}[${index}]`, out));
        return out;
      }
      for (const [key, child] of Object.entries(value)) {
        const label = prefix ? `${prefix}.${key}` : key;
        collectCommandResults(child, label, out);
      }
      return out;
    }
    function logApiCommands(method, path, payload) {
      const commands = collectCommandResults(payload);
      for (const item of commands) {
        appendConsoleLog(`${method} ${path} :: ${item.label}`, item.command);
      }
    }
    async function api(path, options = {}) {
      const method = options.method || 'GET';
      const headers = Object.assign({ 'X-MKEXP2-Token': token() }, options.headers || {});
      if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
      const requestPath = apiPath(path);
      const response = await fetch(requestPath, Object.assign({}, options, { headers }));
      if (!response.ok) {
        const text = await response.text();
        if (response.status === 401) {
          setAuthRequired('Missing or invalid token. Paste the session token printed by mkexp2 web.');
          throw new Error('Missing or invalid token.');
        }
        appendConsoleLog(`${method} ${requestPath} failed`, text);
        throw new Error(text);
      }
      const payload = response.headers.get('content-type')?.includes('application/json')
        ? response.json()
        : response.text();
      const data = await payload;
      logApiCommands(method, requestPath, data);
      return data;
    }
    async function fetchBlob(path) {
