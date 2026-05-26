    function parseCsv(text) {
      const rows = [];
      let row = [];
      let field = '';
      let quoted = false;
      for (let index = 0; index < text.length; index += 1) {
        const char = text[index];
        if (quoted) {
          if (char === '"') {
            if (text[index + 1] === '"') {
              field += '"';
              index += 1;
            } else {
              quoted = false;
            }
          } else {
            field += char;
          }
          continue;
        }
        if (char === '"') {
          quoted = true;
          continue;
        }
        if (char === ',') {
          row.push(field);
          field = '';
          continue;
        }
        if (char === '\n') {
          row.push(field);
          rows.push(row);
          row = [];
          field = '';
          continue;
        }
        if (char === '\r') continue;
        field += char;
      }
      row.push(field);
      rows.push(row);
      if (rows.length && rows[rows.length - 1].every(cell => cell === '') && /[\r\n]$/.test(text)) {
        rows.pop();
      }
      return rows;
    }
    function prepareCsvFile(file) {
      const parsed = parseCsv(file.content || '');
      return Object.assign({}, file, {
        headers: parsed[0] || [],
        rows: parsed.slice(1)
      });
    }
    function uniqueHeaders(headers) {
      const seen = new Set();
      const out = [];
      for (const header of headers) {
        const text = String(header ?? '');
        if (!seen.has(text)) {
          seen.add(text);
          out.push(text);
        }
      }
      return out;
    }
    function headersForFiles(files) {
      return uniqueHeaders(files.flatMap(file => file?.headers || []));
    }
    function columnSignature(headers) {
      return uniqueHeaders(headers).join('\u001f');
    }
    function visibleColumns(headers) {
      const all = uniqueHeaders(headers);
      const saved = state.columnVisibility?.[columnSignature(all)];
      if (!Array.isArray(saved)) return all;
      const allowed = new Set(all);
      return saved.filter(name => allowed.has(name));
    }
    function saveVisibleColumns(headers, columns) {
      if (!state.selected) return;
      const signature = columnSignature(headers);
      state.columnVisibility = Object.assign({}, state.columnVisibility || {}, {
        [signature]: uniqueHeaders(columns),
      });
      state.columnVisibilityFor = 'global';
      renderSettingsColumnVisibility();
      if (state.shared) return;
      api(`/api/experiments/${encodeURIComponent(state.selected)}/columns`, {
        method: 'PUT',
        body: JSON.stringify({ visibility: state.columnVisibility })
      }).catch(err => out(`Column visibility save failed: ${String(err)}`));
    }
    function findResult(name) {
      return state.results.find(file => file.name === name) || null;
    }
    function escapeRegExp(value) {
      return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
    function isParseableLogPath(path) {
      const parts = String(path || '').split('/').filter(Boolean);
      return parts.length >= 2 && /\.log$/i.test(parts[parts.length - 1] || '');
    }
    function logParseMatchesSelected() {
      return Boolean(
        state.logParseResult
        && state.logParseFor
        && state.selectedLog
        && state.logParseFor === state.selectedLog
      );
    }
    function logParseHighlightDefs(result, logText) {
      if (!result?.parsed || !result?.headers?.length || !Array.isArray(result.rows)) return [];
      const skipColumns = new Set(['Graph', 'Seed', 'Epsilon', 'Cores', 'Timeout', 'Failed', 'NumNodes', 'NumMPIsPerNode', 'NumThreadsPerMPI']);
      const seen = new Set();
      const defs = [];
      for (const row of result.rows) {
        result.headers.forEach((header, index) => {
          const column = String(header || '');
          if (skipColumns.has(column)) return;
          const value = String(row[index] ?? '').trim();
          if (!value || value === '-1') return;
          if (value.length < 2 && !['N', 'M', 'K'].includes(column)) return;
          if (!String(logText || '').includes(value)) return;
          const key = `${column}\u001f${value}`;
          if (seen.has(key)) return;
          seen.add(key);
          defs.push({ column, value, className: `parse-color-${defs.length % 8}` });
        });
      }
      return defs;
    }
    function logParseClassFor(defs, column, value) {
      const text = String(value ?? '').trim();
      const def = defs.find(item => item.column === column && item.value === text);
      return def ? def.className : '';
    }
    function highlightedLogHtml(text, defs) {
      const values = Array.from(new Map(
        defs
          .filter(item => item.value)
          .sort((left, right) => right.value.length - left.value.length)
          .map(item => [item.value, item])
      ).values());
      if (!values.length) return esc(text || '');
      const pattern = new RegExp(`(^|[^A-Za-z0-9_.-])(${values.map(item => escapeRegExp(item.value)).join('|')})(?=$|[^A-Za-z0-9_.-])`, 'g');
      let html = '';
      let lastIndex = 0;
      String(text || '').replace(pattern, (match, prefix, value, offset) => {
        const valueOffset = offset + prefix.length;
        const def = values.find(item => item.value === value);
        html += esc(String(text || '').slice(lastIndex, valueOffset));
        html += `<span class="log-match ${def.className}" title="${esc(def.column)}">${esc(value)}</span>`;
        lastIndex = valueOffset + value.length;
        return match;
      });
      html += esc(String(text || '').slice(lastIndex));
      return html;
    }
    function renderLogParseResult(container, result, highlights) {
      if (!result) return;
      const panel = document.createElement('section');
      panel.className = 'log-parse-result';
      const summary = document.createElement('div');
      summary.className = 'log-parse-summary';
      if (!result.parsed) {
        panel.classList.add('status-bad');
        summary.textContent = firstLines(result.command?.stderr || result.command?.stdout || 'Parser failed.', 4);
        panel.appendChild(summary);
        container.appendChild(panel);
        return;
      }
      if (!result.headers?.length || !result.rows?.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty';
        empty.textContent = 'Parser produced no CSV rows.';
        panel.appendChild(empty);
        container.appendChild(panel);
        return;
      }
      const wrap = document.createElement('div');
      wrap.className = 'log-parse-table-wrap';
      const table = document.createElement('table');
      table.className = 'log-parse-table';
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const header of result.headers) {
        const th = document.createElement('th');
        th.textContent = header;
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const row of result.rows) {
        const tr = document.createElement('tr');
        result.headers.forEach((header, index) => {
          const td = document.createElement('td');
          const colorClass = logParseClassFor(highlights, header, row[index]);
          if (colorClass) td.className = `log-parse-cell ${colorClass}`;
          td.textContent = row[index] ?? '';
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      wrap.appendChild(table);
      panel.appendChild(wrap);
      container.appendChild(panel);
    }
    function csvLabel(name) {
      return String(name || '').replace(/\.csv$/i, '');
    }
    function numericCsvValue(value) {
      const text = String(value ?? '').trim();
      if (!text) return null;
      const parsed = Number(text);
      return Number.isFinite(parsed) ? parsed : null;
    }
    function csvHeaderIndex(file) {
      const headerIndex = new Map();
      (file?.headers || []).forEach((header, index) => {
        if (!headerIndex.has(header)) headerIndex.set(header, index);
      });
      return headerIndex;
    }
    function csvRowValue(row, headerIndex, names) {
      for (const name of names) {
        if (!headerIndex.has(name)) continue;
        const value = row[headerIndex.get(name)];
        if (value !== undefined && value !== null && String(value).trim() !== '') return String(value).trim();
      }
      return '';
    }
    function csvFlagValue(value) {
      const text = String(value ?? '').trim().toLowerCase();
      return text === '1' || text === 'true' || text === 'yes';
    }
    function csvRowStateClass(row, headerIndex) {
      if (csvFlagValue(csvRowValue(row, headerIndex, ['Timeout', 'TimedOut']))) return 'csv-row-timeout';
      if (csvFlagValue(csvRowValue(row, headerIndex, ['Failed', 'Failure']))) return 'csv-row-failed';
      const imbalance = numericCsvValue(csvRowValue(row, headerIndex, ['Imbalance']));
      const epsilon = numericCsvValue(csvRowValue(row, headerIndex, ['Epsilon', 'epsilon']));
      if (imbalance !== null && epsilon !== null && imbalance > epsilon) return 'csv-row-imbalanced';
      return '';
    }
    function csvRowStateTitle(row, headerIndex) {
      if (csvFlagValue(csvRowValue(row, headerIndex, ['Timeout', 'TimedOut']))) return 'Timeout';
      if (csvFlagValue(csvRowValue(row, headerIndex, ['Failed', 'Failure']))) return 'Failed';
      const imbalance = numericCsvValue(csvRowValue(row, headerIndex, ['Imbalance']));
      const epsilon = numericCsvValue(csvRowValue(row, headerIndex, ['Epsilon', 'epsilon']));
      if (imbalance !== null && epsilon !== null && imbalance > epsilon) return `Imbalanced: ${imbalance} > ${epsilon}`;
      return '';
    }
    function csvRowLogPayload(file, row, headerIndex) {
      const algorithm = csvLabel(file?.name || '');
      const graph = csvRowValue(row, headerIndex, ['Graph', 'graph']);
      const k = csvRowValue(row, headerIndex, ['K', 'k']);
      const seed = csvRowValue(row, headerIndex, ['Seed', 'seed']);
      const epsilon = csvRowValue(row, headerIndex, ['Epsilon', 'epsilon']);
      if (!algorithm || !graph || !k || !seed || !epsilon) return null;
      const numNodes = csvRowValue(row, headerIndex, ['NumNodes', 'Nodes']);
      const numMpis = csvRowValue(row, headerIndex, ['NumMPIsPerNode', 'MPIsPerNode']);
      const numThreads = csvRowValue(row, headerIndex, ['NumThreadsPerMPI', 'Threads', 'ThreadsPerMPI']);
      const payload = {
        algorithm,
        graph,
        k,
        seed,
        epsilon,
        num_nodes: numNodes,
        num_mpis: numMpis,
        num_threads: numThreads,
        experiment_label: csvRowValue(row, headerIndex, ['Experiment', 'ExperimentLabel', 'ExperimentFunction', 'Function']),
      };
      if (numNodes && numMpis && numThreads) {
        payload.filename = `${graph}___k${k}_seed${seed}_eps${epsilon}_P${numNodes}x${numMpis}x${numThreads}.log`;
      }
      return payload;
    }
    async function openCsvRowLog(file, row, headerIndex) {
      if (!state.selected) return;
      const payload = csvRowLogPayload(file, row, headerIndex);
      if (!payload) {
        out('Cannot open log: CSV row does not contain Graph, K, Seed, and Epsilon columns.');
        return;
      }
      const experimentId = state.selected;
      const resolved = await api(`/api/experiments/${encodeURIComponent(experimentId)}/log-resolve`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (state.selected !== experimentId) return;
      if (!resolved.path) {
        out(`No matching log found for ${payload.algorithm}/${payload.graph}.`);
        return;
      }
      await setView('logs-view');
      const directory = String(resolved.path).split('/').slice(0, -1).join('/');
      await loadLogs(directory);
      await loadLogFile(resolved.path);
      if (resolved.ambiguous) {
        out(`Opened the first of ${resolved.candidates.length} matching logs.`);
      }
    }
    function compareCellClass(value, peerValues, mode) {
      if (!mode) return '';
      const current = numericCsvValue(value);
      if (current === null) return '';
      const peers = (Array.isArray(peerValues) ? peerValues : [peerValues])
        .map(numericCsvValue)
        .filter(item => item !== null);
      if (!peers.length) return '';
      const values = [current, ...peers];
      const min = Math.min(...values);
      const max = Math.max(...values);
      if (min === max) return 'compare-equal';
      if (mode === 1) {
        if (current === min) return 'compare-good';
        if (current === max) return 'compare-bad';
      } else if (mode === 2) {
        if (current === max) return 'compare-good';
        if (current === min) return 'compare-bad';
      }
      return values.length > 2 ? 'compare-mid' : '';
    }
    function cycleCompareColumn(header) {
      const current = state.compareColumnModes[header] || 0;
      const next = (current + 1) % 3;
      if (next === 0) {
        delete state.compareColumnModes[header];
      } else {
        state.compareColumnModes[header] = next;
      }
      setTimeout(renderResultsWorkspace, 0);
    }
    function syncCompareScroll(...boxes) {
      let syncing = false;
      const sync = (source, target) => {
        if (syncing) return;
        syncing = true;
        for (const box of boxes) {
          if (box === source) continue;
          box.scrollTop = source.scrollTop;
          box.scrollLeft = source.scrollLeft;
        }
        requestAnimationFrame(() => {
          syncing = false;
        });
      };
      for (const box of boxes) {
        box.onscroll = () => sync(box);
      }
    }
    function renderColumnSelector(container, headers, onChange) {
      const all = uniqueHeaders(headers);
      const visible = new Set(visibleColumns(all));
      container.innerHTML = '';
      if (!all.length) {
        container.className = 'csv-empty';
        container.textContent = 'No CSV columns.';
        return;
      }
      container.className = 'column-selector';
      for (const header of all) {
        const label = document.createElement('label');
        label.className = 'chip';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = header;
        checkbox.checked = visible.has(header);
        checkbox.onchange = () => {
          const selected = Array.from(container.querySelectorAll('input:checked')).map(item => item.value);
          saveVisibleColumns(all, selected);
          onChange();
        };
        const text = document.createElement('span');
        text.className = 'chip-label';
        text.textContent = header || '(empty)';
        text.title = header;
        label.appendChild(checkbox);
        label.appendChild(text);
        container.appendChild(label);
      }
    }
    function setAllColumns(headers, selected, onChange) {
      const all = uniqueHeaders(headers);
      saveVisibleColumns(all, selected ? all : []);
      onChange();
    }
    function renderCsvTable(file, container, headers, options = {}) {
      container.innerHTML = '';
      container.onscroll = null;
      if (!file) {
        container.className = 'csv-empty';
        container.textContent = 'No CSV selected.';
        return;
      }
      const allHeaders = uniqueHeaders(headers);
      const shown = visibleColumns(allHeaders);
      if (!shown.length) {
        container.className = 'csv-empty';
        container.textContent = 'No columns selected.';
        return;
      }
      container.className = 'csv-table-wrap';
      const headerIndex = csvHeaderIndex(file);
      const peers = options.peers || (options.peer ? [options.peer] : []);
      const peerHeaderIndexes = peers.map(peer => {
        const indexMap = new Map();
        peer.headers.forEach((header, index) => {
          if (!indexMap.has(header)) indexMap.set(header, index);
        });
        return indexMap;
      });
      const table = document.createElement('table');
      table.className = 'csv-table';
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const header of shown) {
        const th = document.createElement('th');
        th.textContent = header || '(empty)';
        th.title = header;
        if (options.compare) {
          const mode = state.compareColumnModes[header] || 0;
          th.classList.add('compare-clickable');
          if (mode === 1) th.classList.add('compare-lower-good');
          if (mode === 2) th.classList.add('compare-higher-good');
          th.tabIndex = 0;
          th.title = mode === 1
            ? `${header} - lower values are green; click to prefer higher values`
            : mode === 2
              ? `${header} - higher values are green; click to clear`
              : `${header} - click to color lower values green`;
          th.onclick = () => cycleCompareColumn(header);
          th.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              cycleCompareColumn(header);
            }
          };
        }
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      file.rows.forEach((row, rowIndex) => {
        const tr = document.createElement('tr');
        const rowState = csvRowStateClass(row, headerIndex);
        const rowStateTitle = csvRowStateTitle(row, headerIndex);
        const logPayload = csvRowLogPayload(file, row, headerIndex);
        if (rowState) tr.classList.add(rowState);
        if (logPayload) {
          tr.classList.add('csv-row-clickable');
          tr.tabIndex = 0;
          tr.title = [rowStateTitle, `Open log for ${logPayload.algorithm}/${logPayload.graph}`].filter(Boolean).join(' - ');
          tr.onclick = () => {
            openCsvRowLog(file, row, headerIndex).catch(err => out(String(err)));
          };
          tr.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              openCsvRowLog(file, row, headerIndex).catch(err => out(String(err)));
            }
          };
        } else if (rowStateTitle) {
          tr.title = rowStateTitle;
        }
        for (const header of shown) {
          const td = document.createElement('td');
          const index = headerIndex.has(header) ? headerIndex.get(header) : -1;
          const value = index >= 0 ? (row[index] ?? '') : '';
          if (options.compare && peers.length) {
            const peerValues = peers.map((peer, peerNumber) => {
              const peerHeaderIndex = peerHeaderIndexes[peerNumber];
              const peerIndex = peerHeaderIndex.has(header) ? peerHeaderIndex.get(header) : -1;
              const peerRow = peer.rows[rowIndex] || [];
              return peerIndex >= 0 ? (peerRow[peerIndex] ?? '') : '';
            });
            const cellClass = compareCellClass(value, peerValues, state.compareColumnModes[header] || 0);
            if (cellClass) td.classList.add(cellClass);
          }
          td.textContent = value;
          td.title = value;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      container.appendChild(table);
      if (file.truncated) {
        const note = document.createElement('div');
        note.className = 'csv-summary';
        note.textContent = 'File preview is truncated by the backend response limit.';
        container.appendChild(note);
      }
    }
    function renderResultFileTabs() {
      const tabs = document.getElementById('result-file-tabs');
      tabs.innerHTML = '';
      const selected = new Set(state.selectedResults || []);
      for (const file of state.results) {
        const button = document.createElement('button');
        button.className = 'csv-file-tab' + (selected.has(file.name) ? ' active' : '');
        button.setAttribute('aria-pressed', selected.has(file.name) ? 'true' : 'false');
        button.textContent = csvLabel(file.name);
        button.title = csvLabel(file.name);
        button.onclick = () => {
          const current = new Set(state.selectedResults || []);
          if (current.has(file.name)) {
            current.delete(file.name);
          } else {
            current.add(file.name);
          }
          state.selectedResults = state.results
            .map(item => item.name)
            .filter(name => current.has(name));
          renderResultsWorkspace();
        };
        tabs.appendChild(button);
      }
    }
    function renderResultsWorkspace() {
      const box = document.getElementById('results');
      const selector = document.getElementById('column-selector');
      const allButton = document.getElementById('columns-all');
      const noneButton = document.getElementById('columns-none');
      const parseButton = document.getElementById('parse-results');
      const statsButton = document.getElementById('load-stats');
      if (parseButton && parseButton.dataset.busy !== '1') {
        parseButton.disabled = !state.selected || state.selectedArchived;
        parseButton.title = state.selectedArchived ? 'Unarchive before parsing logs.' : 'Parse logs';
      }
      if (statsButton && statsButton.dataset.busy !== '1') {
        statsButton.disabled = !state.selected || state.selectedArchived;
        statsButton.title = state.selectedArchived ? 'Unarchive before generating stats.' : 'Generate stats';
      }
      state.selectedResults = (state.selectedResults || []).filter(name => findResult(name));
      renderResultFileTabs();
      allButton.disabled = true;
      noneButton.disabled = true;
      if (!state.selected) {
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      if (!state.results.length) {
        box.className = 'csv-empty';
        box.textContent = 'No CSV files loaded.';
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      const selectedFiles = state.selectedResults.map(findResult).filter(Boolean);
      if (!selectedFiles.length) {
        state.compareColumnModes = {};
        box.onscroll = null;
        box.className = 'csv-empty';
        box.textContent = 'Select one or more algorithms above.';
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }

      if (selectedFiles.length === 1) {
        state.compareColumnModes = {};
        const file = selectedFiles[0];
        renderColumnSelector(selector, file.headers, renderResultsWorkspace);
        allButton.disabled = false;
        noneButton.disabled = false;
        allButton.onclick = () => setAllColumns(file.headers, true, renderResultsWorkspace);
        noneButton.onclick = () => setAllColumns(file.headers, false, renderResultsWorkspace);
        renderCsvTable(file, box, file.headers);
        return;
      }

      const headers = headersForFiles(selectedFiles);
      const rowCount = selectedFiles[0].rows.length;
      const mismatch = selectedFiles.find(file => file.rows.length !== rowCount);
      if (mismatch) {
        const details = selectedFiles.map(file => `${csvLabel(file.name)} has ${file.rows.length}`).join(', ');
        const message = `Cannot compare: row counts differ (${details}).`;
        selector.className = 'csv-empty status-bad';
        selector.textContent = 'Row-wise comparison is disabled until all selected CSV files have the same number of rows.';
        box.onscroll = null;
        box.className = 'csv-empty status-bad';
        box.textContent = message;
        return;
      }
      renderColumnSelector(selector, headers, renderResultsWorkspace);
      allButton.disabled = false;
      noneButton.disabled = false;
      allButton.onclick = () => setAllColumns(headers, true, renderResultsWorkspace);
      noneButton.onclick = () => setAllColumns(headers, false, renderResultsWorkspace);

      box.onscroll = null;
      box.className = 'compare-grid';
      box.innerHTML = '';
      const scrollBoxes = [];
      for (const file of selectedFiles) {
        const pane = document.createElement('div');
        pane.className = 'compare-pane';
        const title = document.createElement('div');
        title.className = 'compare-pane-title';
        title.textContent = csvLabel(file.name);
        title.title = csvLabel(file.name);
        const tableBox = document.createElement('div');
        tableBox.className = 'csv-empty';
        pane.appendChild(title);
        pane.appendChild(tableBox);
        box.appendChild(pane);
        renderCsvTable(file, tableBox, headers, {
          compare: true,
          peers: selectedFiles.filter(peer => peer !== file),
        });
        scrollBoxes.push(tableBox);
      }
      syncCompareScroll(...scrollBoxes);
    }
    function formatStatNumber(value) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return 'n/a';
      if (Math.abs(parsed) >= 1000) return parsed.toLocaleString(undefined, { maximumFractionDigits: 2 });
      return parsed.toLocaleString(undefined, { maximumSignificantDigits: 6 });
    }
    function formatStatCount(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed.toLocaleString() : '0';
    }
    function formatStatRatio(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? `${formatStatNumber(parsed)}x` : 'n/a';
    }
    function statsComparisonCell(matrix, rowAlgorithm, columnAlgorithm) {
      const cells = Array.isArray(matrix?.cells) ? matrix.cells : [];
      return cells.find(cell => cell.row_algorithm === rowAlgorithm && cell.column_algorithm === columnAlgorithm) || null;
    }
    function ratioCellClass(ratio) {
      const parsed = Number(ratio);
      if (!Number.isFinite(parsed)) return 'stats-matrix-empty';
      if (Math.abs(parsed - 1) < 1e-9) return 'stats-matrix-equal';
      return parsed < 1 ? 'stats-matrix-better' : 'stats-matrix-worse';
    }
    function comparisonMatrixById(matrices, id) {
      if (!Array.isArray(matrices)) return null;
      return matrices.find(matrix => matrix?.id === id) || null;
    }
    function appendStatsComparisonMatrixTable(panel, matrix) {
      const algorithms = Array.isArray(matrix?.algorithms) ? matrix.algorithms : [];
      if (!matrix || !algorithms.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty stats-matrix-empty-message';
        empty.textContent = 'No matrix data.';
        panel.appendChild(empty);
        return;
      }
      const tableWrap = document.createElement('div');
      tableWrap.className = 'stats-matrix-wrap';
      const table = document.createElement('table');
      table.className = 'stats-table stats-matrix';
      const thead = document.createElement('thead');
      const head = document.createElement('tr');
      head.appendChild(document.createElement('th'));
      for (const algorithm of algorithms) {
        const th = document.createElement('th');
        th.textContent = algorithm;
        th.title = algorithm;
        head.appendChild(th);
      }
      thead.appendChild(head);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      for (const rowAlgorithm of algorithms) {
        const tr = document.createElement('tr');
        const label = document.createElement('th');
        label.textContent = rowAlgorithm;
        label.title = rowAlgorithm;
        tr.appendChild(label);
        for (const columnAlgorithm of algorithms) {
          const cell = statsComparisonCell(matrix, rowAlgorithm, columnAlgorithm);
          const td = document.createElement('td');
          td.textContent = formatStatRatio(cell?.ratio);
          td.className = ratioCellClass(cell?.ratio);
          const count = formatStatCount(cell?.count);
          td.title = `${rowAlgorithm} / ${columnAlgorithm}; ${count} shared row(s)`;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      tableWrap.appendChild(table);
      panel.appendChild(tableWrap);
    }
    function appendStatsComparisonMatrices(box, matrices) {
      const section = document.createElement('section');
      section.className = 'stats-section stats-comparison-section';
      const heading = document.createElement('div');
      heading.className = 'stats-section-title';
      heading.textContent = 'Stats Matrices';
      section.appendChild(heading);

      const row = document.createElement('div');
      row.className = 'stats-matrix-row';
      const specs = [
        { metric: 'time', title: 'Time', allId: 'time_all', balancedId: 'time_balanced' },
        { metric: 'cut', title: 'Cut', allId: 'cut_all', balancedId: 'cut_balanced' },
      ];
      for (const spec of specs) {
        const includeImbalanced = Boolean(state.statsMatrixIncludeImbalanced?.[spec.metric]);
        const matrix = comparisonMatrixById(matrices, includeImbalanced ? spec.allId : spec.balancedId);
        const panel = document.createElement('div');
        panel.className = 'stats-matrix-panel';
        const header = document.createElement('div');
        header.className = 'stats-matrix-panel-header';
        const title = document.createElement('div');
        title.className = 'stats-matrix-title';
        title.textContent = spec.title;
        header.appendChild(title);
        const toggle = document.createElement('label');
        toggle.className = 'stats-matrix-toggle';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = includeImbalanced;
        checkbox.onchange = () => {
          state.statsMatrixIncludeImbalanced[spec.metric] = checkbox.checked;
          renderStatsWorkspace();
        };
        const label = document.createElement('span');
        label.textContent = 'Include imbalanced';
        toggle.appendChild(checkbox);
        toggle.appendChild(label);
        header.appendChild(toggle);
        panel.appendChild(header);
        appendStatsComparisonMatrixTable(panel, matrix);
        row.appendChild(panel);
      }
      section.appendChild(row);
      box.appendChild(section);
    }
    function renderStatsWorkspace() {
      const summary = document.getElementById('stats-summary');
      const box = document.getElementById('stats-output');
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        return;
      }
      if (state.statsFor !== state.selected || !state.stats) {
        summary.textContent = 'No stats loaded.';
        box.className = 'csv-empty';
        box.textContent = 'Generate stats to summarize parsed CSV results.';
        return;
      }
      const stats = state.stats.stats_json || null;
      const algorithms = stats?.algorithms || [];
      if (!stats || !algorithms.length) {
        summary.textContent = 'No stats available.';
        box.className = 'csv-empty';
        box.textContent = 'No parsed CSV results found. Run Parse Logs first.';
        return;
      }
      const totals = stats.summary || {};
      summary.textContent = `${algorithms.length} algorithm(s), ${formatStatCount(totals.rows ?? algorithms.reduce((sum, item) => sum + Number(item.rows || 0), 0))} row(s)`;
      box.className = 'stats-workspace';
      box.innerHTML = '';
      appendStatsComparisonMatrices(box, stats.comparisons || []);

    }
    function formatBytes(value) {
      const size = Number(value);
      if (!Number.isFinite(size)) return '';
      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
      if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MiB`;
      return `${(size / 1024 / 1024 / 1024).toFixed(1)} GiB`;
    }
    function parentLogDir(dir) {
      const parts = String(dir || '').split('/').filter(Boolean);
      if (parts.length <= 2) return '';
      parts.pop();
      return parts.join('/');
    }
    function scrollSelectedLogIntoView() {
      const list = document.getElementById('logs-list');
      const active = list?.querySelector('.log-entry.active');
      if (!list || !active) return;
      const scroll = () => {
        if (!active.isConnected) return;
        const listRect = list.getBoundingClientRect();
        const activeRect = active.getBoundingClientRect();
        const centeredDelta = activeRect.top - listRect.top - ((list.clientHeight - activeRect.height) / 2);
        list.scrollTo({ top: Math.max(0, list.scrollTop + centeredDelta), behavior: 'auto' });
      };
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(scroll);
      else setTimeout(scroll, 0);
    }
    function renderLogsWorkspace() {
      const pathLabel = document.getElementById('logs-path');
      const list = document.getElementById('logs-list');
      const content = document.getElementById('log-content');
      if (!state.selected) {
        pathLabel.textContent = '';
        content.className = 'log-content';
        list.innerHTML = '<div class="csv-empty">Select an experiment first.</div>';
        content.innerHTML = '<div class="csv-empty">Select an experiment first.</div>';
        return;
      }
      if (state.logsFor !== state.selected || !state.logsListing) {
        pathLabel.textContent = '';
        content.className = 'log-content';
        list.innerHTML = '<div class="csv-empty">Open the Logs tab to load the log directory.</div>';
        content.innerHTML = '<div class="csv-empty">Select a log file to load its content.</div>';
        return;
      }
      const listing = state.logsListing;
      const dir = listing.dir || '';
      pathLabel.textContent = dir ? `logs/${dir}/` : 'logs/';
      if (!listing.exists) {
        content.className = 'log-content';
        list.innerHTML = '<div class="csv-empty">No logs directory exists for this experiment yet.</div>';
        content.innerHTML = '<div class="csv-empty">Run experiments first, then reload logs.</div>';
        return;
      }
      list.innerHTML = '';
      if (dir) {
        const up = document.createElement('button');
        up.className = 'log-entry up';
        up.title = 'Open parent directory';
        up.innerHTML = '<span class="log-entry-icon" aria-hidden="true"></span><span class="log-entry-name">Parent directory</span><span class="log-entry-meta"></span>';
        up.onclick = () => withBusyButton(up, 'Loading...', () => loadLogs(parentLogDir(dir))).catch(err => out(String(err)));
        list.appendChild(up);
      }
      if (!listing.entries.length && !dir) {
        list.innerHTML = '<div class="csv-empty">No log files found.</div>';
      } else if (!listing.entries.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty';
        empty.textContent = 'This log directory is empty.';
        list.appendChild(empty);
      }
      for (const entry of listing.entries) {
        const button = document.createElement('button');
        button.className = `log-entry ${entry.type === 'dir' ? 'dir' : 'file'}`
          + (entry.type === 'file' && state.selectedLog === entry.path ? ' active' : '');
        button.title = entry.path;
        button.setAttribute('aria-label', `${entry.type === 'dir' ? 'Open directory' : 'Open file'} ${entry.path}`);
        const icon = document.createElement('span');
        icon.className = 'log-entry-icon';
        icon.setAttribute('aria-hidden', 'true');
        const name = document.createElement('span');
        name.className = 'log-entry-name';
        name.textContent = entry.name;
        name.title = entry.path;
        const meta = document.createElement('span');
        meta.className = 'log-entry-meta';
        meta.textContent = entry.type === 'dir' ? 'dir' : formatBytes(entry.size);
        button.appendChild(icon);
        button.appendChild(name);
        button.appendChild(meta);
        button.onclick = () => withBusyButton(button, 'Loading...', () => (
          entry.type === 'dir' ? loadLogs(entry.path) : loadLogFile(entry.path)
        )).catch(err => out(String(err)));
        list.appendChild(button);
      }
      if (listing.has_more) {
        const more = document.createElement('div');
        more.className = 'csv-empty';
        more.textContent = `Showing the first ${listing.entries.length} entries. Open a subdirectory to narrow the list.`;
        list.appendChild(more);
      }
      const selectedLogInListing = Boolean(
        state.selectedLog
        && (listing.entries || []).some(entry => entry.type === 'file' && entry.path === state.selectedLog)
      );
      if (selectedLogInListing) scrollSelectedLogIntoView();
      if (selectedLogInListing && state.logContent && state.logContent.relative_path === state.selectedLog) {
        content.className = 'log-content';
        content.innerHTML = '';
        const toolbar = document.createElement('div');
        toolbar.className = 'log-file-toolbar';
        const title = document.createElement('div');
        title.className = 'log-file-title';
        title.textContent = state.logContent.relative_path || state.selectedLog;
        toolbar.appendChild(title);
        const parseButton = document.createElement('button');
        parseButton.textContent = 'Parse File';
        parseButton.disabled = !isParseableLogPath(state.selectedLog);
        parseButton.title = parseButton.disabled
          ? 'Select a run .log file under logs/<algorithm>/ to parse one file.'
          : 'Parse this log file with the configured parser.';
        parseButton.onclick = () => withBusyButton(parseButton, 'Parsing...', () => parseSelectedLogFile()).catch(err => out(String(err)));
        toolbar.appendChild(parseButton);
        content.appendChild(toolbar);
        const parseResult = logParseMatchesSelected() ? state.logParseResult : null;
        const highlights = logParseHighlightDefs(parseResult, state.logContent.content || '');
        renderLogParseResult(content, parseResult, highlights);
        const isMarkdown = /\.md$/i.test(state.logContent.relative_path || '');
        if (isMarkdown) {
          const markdown = document.createElement('div');
          renderMarkdown(state.logContent.content || '', markdown);
          content.appendChild(markdown);
          if (state.logContent.truncated) {
            const note = document.createElement('div');
            note.className = 'csv-summary';
            note.textContent = 'File preview is truncated by the backend response limit.';
            content.appendChild(note);
          }
        } else {
          const pre = document.createElement('pre');
          pre.innerHTML = highlightedLogHtml(state.logContent.content || '', highlights);
          content.appendChild(pre);
        }
      } else {
        content.className = 'log-content';
        content.innerHTML = '<div class="csv-empty">Select a log file to load its content.</div>';
      }
    }
    async function loadLogs(dir = state.logsDir || '') {
      if (!state.selected) return;
      const nextDir = dir || '';
      const directoryChanged = nextDir !== state.logsDir;
      state.logsDir = nextDir;
      if (directoryChanged) {
        state.selectedLog = '';
        state.logContent = null;
        state.logParseResult = null;
        state.logParseFor = '';
        renderLogsWorkspace();
      }
      const query = new URLSearchParams({ dir: state.logsDir, limit: '500' });
      state.logsListing = await api(`/api/experiments/${encodeURIComponent(state.selected)}/logs?${query.toString()}`);
      state.logsFor = state.selected;
      renderLogsWorkspace();
    }
    async function loadLogFile(path) {
      if (!state.selected) return;
      state.selectedLog = path;
      state.logParseResult = null;
      state.logParseFor = '';
      const query = new URLSearchParams({ path });
      state.logContent = await api(`/api/experiments/${encodeURIComponent(state.selected)}/log?${query.toString()}`);
      renderLogsWorkspace();
    }
    async function parseSelectedLogFile() {
      if (!state.selected || !state.selectedLog || !isParseableLogPath(state.selectedLog)) return;
      const experimentId = state.selected;
      const logPath = state.selectedLog;
      const result = await api(`/api/experiments/${encodeURIComponent(experimentId)}/log-parse`, {
        method: 'POST',
        body: JSON.stringify({ path: logPath })
      });
      if (state.selected !== experimentId || state.selectedLog !== logPath) return;
      state.logParseResult = result;
      state.logParseFor = logPath;
      renderLogsWorkspace();
    }
    async function ensureLogsLoaded() {
      if (!state.selected) return;
      if (state.logsFor !== state.selected || !state.logsListing) await loadLogs('');
    }
    async function ensureResultsLoaded() {
      if (!state.selected) return;
      if (state.resultsFor !== state.selected) await loadResults();
    }
    async function activateCsvView(viewId) {
      await ensureResultsLoaded();
      if (viewId === 'results-view') {
        renderResultsWorkspace();
        renderStatsWorkspace();
      }
    }
