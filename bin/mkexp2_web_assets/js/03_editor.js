    function appendInlineMarkdown(parent, text) {
      const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
      let cursor = 0;
      for (const match of text.matchAll(pattern)) {
        if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
        const value = match[0];
        const node = value.startsWith('`') ? document.createElement('code') : document.createElement('strong');
        node.textContent = value.startsWith('`') ? value.slice(1, -1) : value.slice(2, -2);
        parent.appendChild(node);
        cursor = match.index + value.length;
      }
      if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
    }
    function renderMarkdown(markdown, target) {
      target.innerHTML = '';
      target.className = 'markdown-doc';
      const lines = String(markdown || '').split(/\r?\n/);
      let paragraph = [];
      let list = null;
      let code = null;

      const flushParagraph = () => {
        if (!paragraph.length) return;
        const p = document.createElement('p');
        appendInlineMarkdown(p, paragraph.join(' '));
        target.appendChild(p);
        paragraph = [];
      };
      const flushList = () => {
        if (!list) return;
        target.appendChild(list);
        list = null;
      };
      const flushCode = () => {
        if (!code) return;
        const pre = document.createElement('pre');
        const codeNode = document.createElement('code');
        codeNode.textContent = code.join('\n');
        pre.appendChild(codeNode);
        target.appendChild(pre);
        code = null;
      };

      for (const line of lines) {
        if (code) {
          if (/^```/.test(line)) flushCode();
          else code.push(line);
          continue;
        }
        if (/^```/.test(line)) {
          flushParagraph();
          flushList();
          code = [];
          continue;
        }
        const heading = line.match(/^(#{1,4})\s+(.+)$/);
        if (heading) {
          flushParagraph();
          flushList();
          const level = Math.min(heading[1].length, 4);
          const h = document.createElement(`h${level}`);
          appendInlineMarkdown(h, heading[2]);
          target.appendChild(h);
          continue;
        }
        const bullet = line.match(/^\s*[-*]\s+(.+)$/);
        if (bullet) {
          flushParagraph();
          if (!list) list = document.createElement('ul');
          const item = document.createElement('li');
          appendInlineMarkdown(item, bullet[1]);
          list.appendChild(item);
          continue;
        }
        if (!line.trim()) {
          flushParagraph();
          flushList();
          continue;
        }
        flushList();
        paragraph.push(line.trim());
      }
      flushCode();
      flushParagraph();
      flushList();
    }
    function renderDescriptionWorkspace() {
      const rendered = document.getElementById('description-rendered');
      const editorNode = document.getElementById('description-editor');
      const actions = document.getElementById('description-actions');
      const editButton = document.getElementById('description-edit');
      if (!state.selected) {
        state.descriptionEditing = false;
        rendered.className = 'csv-empty';
        rendered.textContent = 'Select an experiment first.';
        editorNode.classList.add('hidden');
        actions.classList.add('hidden');
        editButton.disabled = true;
        return;
      }
      editButton.disabled = state.descriptionFor !== state.selected || state.shared || state.selectedArchived;
      if (state.descriptionFor !== state.selected || !state.description) {
        state.descriptionEditing = false;
        rendered.className = 'csv-empty';
        rendered.textContent = 'Loading description...';
        editorNode.classList.add('hidden');
        actions.classList.add('hidden');
        return;
      }
      const content = state.description.content || '';
      if (state.descriptionEditing && !state.shared && !state.selectedArchived) {
        rendered.classList.add('hidden');
        editorNode.classList.remove('hidden');
        actions.classList.remove('hidden');
        editButton.disabled = true;
        return;
      }
      editorNode.classList.add('hidden');
      actions.classList.add('hidden');
      rendered.classList.remove('hidden');
      if (content.trim()) {
        renderMarkdown(content, rendered);
      } else {
        rendered.className = 'csv-empty';
        rendered.textContent = 'No description yet.';
      }
    }
    async function loadDescription() {
      if (!state.selected) return;
      const experimentId = state.selected;
      state.descriptionEditing = false;
      renderDescriptionWorkspace();
      const data = await api(`/api/experiments/${encodeURIComponent(experimentId)}/description`);
      if (state.selected !== experimentId) return;
      state.description = data;
      state.descriptionFor = experimentId;
      renderDescriptionWorkspace();
    }
    function editDescription() {
      if (!state.selected || state.shared || state.selectedArchived || state.descriptionFor !== state.selected) return;
      const editorNode = document.getElementById('description-editor');
      editorNode.value = state.description?.content || '';
      state.descriptionEditing = true;
      renderDescriptionWorkspace();
      editorNode.focus();
    }
    function cancelDescriptionEdit() {
      state.descriptionEditing = false;
      renderDescriptionWorkspace();
    }
    async function saveDescription() {
      if (!state.selected || state.shared || state.selectedArchived) return;
      const experimentId = state.selected;
      const description = document.getElementById('description-editor').value;
      await withBusyButton('description-save', 'Saving...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(experimentId)}/description`, {
          method: 'PUT',
          body: JSON.stringify({ description })
        });
        if (state.selected !== experimentId) return;
        state.description = result;
        state.descriptionFor = experimentId;
        state.descriptionEditing = false;
        renderDescriptionWorkspace();
      });
    }
    function span(className, value) {
      return `<span class="${className}">${esc(value)}</span>`;
    }
    const experimentKeywords = new Set([
      'System', 'Wrapper', 'Property', 'SystemProperty', 'AlgorithmProperty',
      'DefineAlgorithm', 'Algorithms', 'Threads', 'Seeds', 'Ks', 'Epsilons',
      'Timelimit', 'TimelimitPerInstance', 'Graphs', 'Graph'
    ]);
    const shellKeywords = new Set([
      'if', 'then', 'elif', 'else', 'fi', 'for', 'while', 'do', 'done', 'case',
      'esac', 'in', 'function', 'local', 'typeset', 'return', 'true', 'false'
    ]);
    function commentIndex(line) {
      let quote = '';
      for (let index = 0; index < line.length; index += 1) {
        const char = line[index];
        const previous = line[index - 1];
        if (quote) {
          if (char === quote && previous !== '\\') quote = '';
          continue;
        }
        if (char === '"' || char === "'") {
          quote = char;
          continue;
        }
        if (char === '#') return index;
      }
      return -1;
    }
    function readVariable(code, start) {
      if (code[start + 1] === '{') {
        const end = code.indexOf('}', start + 2);
        return end === -1 ? code.length : end + 1;
      }
      if (code[start + 1] === '(') {
        let depth = 1;
        for (let index = start + 2; index < code.length; index += 1) {
          if (code[index] === '(') depth += 1;
          if (code[index] === ')') depth -= 1;
          if (depth === 0) return index + 1;
        }
        return code.length;
      }
      const match = code.slice(start).match(/^\$[A-Za-z_][A-Za-z0-9_]*/);
      return match ? start + match[0].length : start + 1;
    }
    function highlightCode(code) {
      let html = '';
      for (let index = 0; index < code.length;) {
        const char = code[index];
        if (char === '"' || char === "'") {
          let end = index + 1;
          while (end < code.length) {
            const current = code[end];
            const previous = code[end - 1];
            end += 1;
            if (current === char && previous !== '\\') break;
          }
          html += span('tok-string', code.slice(index, end));
          index = end;
          continue;
        }
        if (char === '$') {
          const end = readVariable(code, index);
          html += span('tok-variable', code.slice(index, end));
          index = end;
          continue;
        }
        const number = code.slice(index).match(/^[0-9]+(?:\.[0-9]+)?(?:x[0-9]+)*/);
        if (number) {
          html += span('tok-number', number[0]);
          index += number[0].length;
          continue;
        }
        const word = code.slice(index).match(/^[A-Za-z_][A-Za-z0-9_.-]*/);
        if (word) {
          const value = word[0];
          const rest = code.slice(index + value.length);
          if (experimentKeywords.has(value)) {
            html += span('tok-keyword', value);
          } else if (shellKeywords.has(value)) {
            html += span('tok-shell', value);
          } else if (/^Experiment[A-Za-z0-9_]*$/.test(value) && /^\s*\(\)/.test(rest)) {
            html += span('tok-function', value);
          } else {
            html += esc(value);
          }
          index += value.length;
          continue;
        }
        html += esc(char);
        index += 1;
      }
      return html;
    }
    function highlightExperiment(text) {
      return text.split('\n').map(line => {
        if (line.startsWith('#!')) return span('tok-comment', line);
        const hash = commentIndex(line);
        if (hash === -1) return highlightCode(line);
        return highlightCode(line.slice(0, hash)) + span('tok-comment', line.slice(hash));
      }).join('\n') + '\n';
    }
    function syncEditorHighlight() {
      editorHighlight.scrollTop = editor.scrollTop;
      editorHighlight.scrollLeft = editor.scrollLeft;
    }
    function updateEditorHighlight() {
      editorHighlight.innerHTML = highlightExperiment(editor.value);
      syncEditorHighlight();
    }
    function setEditorValue(value) {
      editor.value = value;
      updateEditorHighlight();
    }
    editor.addEventListener('input', () => {
      state.editorDirty = true;
      clearCheckIndicator();
      updateEditorHighlight();
    });
    editor.addEventListener('scroll', syncEditorHighlight);
    updateEditorHighlight();
    function guidedTextList(value) {
      if (Array.isArray(value)) return value.map(item => String(item ?? '').trim()).filter(Boolean);
      return String(value || '').split(/\s+/).map(item => item.trim()).filter(Boolean);
    }
    function guidedLineList(value) {
      if (Array.isArray(value)) return value.map(item => String(item ?? '').trim()).filter(Boolean);
      return String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    }
    function guidedPropertyRows(properties) {
      if (!properties) return [];
      if (Array.isArray(properties)) {
        return properties.map(prop => ({ key: String(prop.key || '').trim(), value: String(prop.value ?? '').trim() })).filter(prop => prop.key);
      }
      return Object.entries(properties).map(([key, value]) => ({ key, value: String(value ?? '') })).filter(prop => prop.key);
    }
    function describePartitionerMap(describe = state.guidedModel?.describe) {
      const map = new Map();
      for (const partitioner of describe?.partitioners || []) map.set(partitioner.name, partitioner);
      return map;
    }
    function describeAliasNames(describe = state.guidedModel?.describe) {
      const names = new Set();
      for (const partitioner of describe?.partitioners || []) {
        for (const alias of partitioner.aliases || []) if (alias.name) names.add(alias.name);
      }
      return names;
    }
    function describeAlgorithmOptionNames(describe = state.guidedModel?.describe) {
      const names = new Set();
      for (const partitioner of describe?.partitioners || []) {
        if (partitioner.name) names.add(partitioner.name);
        for (const alias of partitioner.aliases || []) if (alias.name) names.add(alias.name);
      }
      return names;
    }
    function guidedBaseOptions(describe = state.guidedModel?.describe) {
      const names = new Set();
      for (const partitioner of describe?.partitioners || []) {
        names.add(partitioner.name);
        for (const alias of partitioner.aliases || []) names.add(alias.name);
      }
      for (const algorithm of state.guidedForm?.algorithm_definitions || []) {
        if (algorithm.name) names.add(algorithm.name);
      }
      return Array.from(names).sort((left, right) => left.localeCompare(right));
    }
    function updateGuidedBaseSuggestions() {
      const datalist = document.getElementById('guided-algorithm-base-suggestions');
      if (!datalist) return;
      datalist.innerHTML = '';
      for (const name of guidedBaseOptions()) {
        const option = document.createElement('option');
        option.value = name;
        datalist.appendChild(option);
      }
    }
    function describeSystemMap(describe = state.guidedModel?.describe) {
      const map = new Map();
      for (const system of describe?.systems || []) map.set(system.name, system);
      return map;
    }
    function normalizePropertyMetadata(prop) {
      return {
        key: String(prop?.key || '').trim(),
        value: String(prop?.value ?? ''),
        allowed: String(prop?.allowed || ''),
        closed: Boolean(prop?.closed),
        values: Array.isArray(prop?.values) ? prop.values.map(value => String(value)) : [],
        when: String(prop?.when || ''),
      };
    }
    function mergePropertyCatalog(catalog, properties) {
      for (const raw of properties || []) {
        const prop = normalizePropertyMetadata(raw);
        if (prop.key) catalog.set(prop.key, prop);
      }
      return catalog;
    }
    function guidedAlgorithmDefinition(name) {
      return (state.guidedForm?.algorithm_definitions || []).find(algorithm => algorithm.name === name) || null;
    }
    function guidedCustomAlgorithmNames() {
      return new Set((state.guidedForm?.algorithm_definitions || []).map(algorithm => algorithm.name).filter(Boolean));
    }
    function guidedDefinitionChain(base, seen = new Set()) {
      const value = String(base || '').trim();
      if (!value || seen.has(value)) return [];
      seen.add(value);
      const definition = guidedAlgorithmDefinition(value);
      if (!definition) return [];
      return [...guidedDefinitionChain(definition.base, seen), definition];
    }
    function describePartitionerForBase(base, describe = state.guidedModel?.describe, seen = new Set()) {
      const value = String(base || '').trim();
      if (!value || seen.has(value)) return null;
      seen.add(value);
      const partitioners = describePartitionerMap(describe);
      const direct = partitioners.get(value);
      if (direct) return { partitioner: direct, alias: null };
      for (const partitioner of partitioners.values()) {
        const alias = (partitioner.aliases || []).find(item => item.name === value);
        if (alias) return { partitioner, alias };
      }
      const definition = guidedAlgorithmDefinition(value);
      if (definition?.base) return describePartitionerForBase(definition.base, describe, seen);
      return null;
    }
    function systemPropertyCatalog(systemName) {
      const catalog = new Map();
      const systems = describeSystemMap();
      const selected = systems.get(systemName);
      if (selected) {
        mergePropertyCatalog(catalog, selected.defaults || []);
      } else {
        for (const system of systems.values()) mergePropertyCatalog(catalog, system.defaults || []);
      }
      return catalog;
    }
    function algorithmPropertyCatalog(base) {
      const catalog = new Map();
      const resolved = describePartitionerForBase(base);
      mergePropertyCatalog(catalog, resolved?.partitioner?.defaults || []);
      mergePropertyCatalog(catalog, resolved?.alias?.properties || []);
      for (const definition of guidedDefinitionChain(base)) {
        mergePropertyCatalog(catalog, guidedPropertyRows(definition.properties || []));
      }
      return catalog;
    }
    function guidedPropertyCatalog(context) {
      if (context?.kind === 'algorithm') return algorithmPropertyCatalog(context.base || '');
      return systemPropertyCatalog(context?.system || state.guidedForm?.system || '');
    }
    function defaultRepoUrlForBase(base) {
      return algorithmPropertyCatalog(base).get('repo_url')?.value || '';
    }
    function guidedPropertyKeyOptions(catalog, rows) {
      const keys = new Set(catalog.keys());
      for (const row of rows || []) {
        if (row.key) keys.add(row.key);
      }
      const priority = ['repo_url', 'repo_ref', 'parser', 'binary', 'build_target', 'cmake_flags', 'build_opts', 'build_options'];
      return Array.from(keys).sort((left, right) => {
        const leftPriority = priority.includes(left) ? priority.indexOf(left) : 99;
        const rightPriority = priority.includes(right) ? priority.indexOf(right) : 99;
        return leftPriority - rightPriority || left.localeCompare(right);
      });
    }
    function suggestedGuidedProperty(context, rows) {
      const catalog = guidedPropertyCatalog(context);
      const used = new Set((rows || []).map(row => row.key).filter(Boolean));
      const keys = guidedPropertyKeyOptions(catalog, rows);
      const key = keys.find(item => !used.has(item)) || keys[0] || '';
      const meta = catalog.get(key);
      return { key, value: meta?.value || '' };
    }
    function guidedPropertiesForAlgorithm(algorithm, describe) {
      const resolved = describePartitionerForBase(algorithm.base, describe);
      const defaults = new Map();
      mergePropertyCatalog(defaults, resolved?.partitioner?.defaults || []);
      mergePropertyCatalog(defaults, resolved?.alias?.properties || []);
      const properties = algorithm.declaredProperties || {};
      return Object.entries(properties)
        .filter(([, value]) => value !== '' && value !== null && value !== undefined)
        .filter(([key, value]) => String(value) !== String(defaults.get(key)?.value ?? ''))
        .sort((left, right) => {
          const priority = ['repo_url', 'repo_ref', 'parser', 'cmake_flags', 'build_opts', 'build_options'];
          const leftKey = left[0];
          const rightKey = right[0];
          return (priority.indexOf(leftKey) < 0 ? 99 : priority.indexOf(leftKey))
            - (priority.indexOf(rightKey) < 0 ? 99 : priority.indexOf(rightKey))
            || leftKey.localeCompare(rightKey);
        })
        .map(([key, value]) => ({ key, value: String(value ?? '') }));
    }
    function guidedFormFromModel(model) {
      const experiments = model?.probe?.experiments || [];
      const describe = model?.describe || {};
      const first = experiments[0] || {};
      const declaredDefinitions = new Map();
      const declaredProperties = {};
      for (const experiment of experiments) {
        for (const definition of experiment.declared?.algorithm_definitions || []) {
          if (definition.name && !declaredDefinitions.has(definition.name)) declaredDefinitions.set(definition.name, definition);
        }
        for (const [name, properties] of Object.entries(experiment.declared?.algorithm_properties || {})) {
          declaredProperties[name] = { ...(declaredProperties[name] || {}), ...(properties || {}) };
        }
      }
      const algorithmOptions = describeAlgorithmOptionNames(describe);
      for (const experiment of experiments) {
        const declared = experiment.declared || {};
        const selectedNames = guidedTextList(declared.algorithms?.length ? declared.algorithms : (experiment.resolved?.algorithms || []).map(algorithm => algorithm.name));
        for (const name of selectedNames) algorithmOptions.add(name);
      }
      const builtinAliasNames = describeAliasNames(describe);
      for (const name of declaredDefinitions.keys()) algorithmOptions.add(name);
      const editableDefinitions = new Map(Array.from(declaredDefinitions.entries()).filter(([name, definition]) =>
        !definition.builtin && !builtinAliasNames.has(name)
      ));
      const algorithmMap = new Map();
      for (const [name, definition] of editableDefinitions.entries()) {
        algorithmMap.set(name, {
          name,
          base: definition.base || '',
          args: definition.args ?? '',
          properties: guidedPropertiesForAlgorithm({
            name,
            base: definition.base || '',
            declaredProperties: declaredProperties[name] || {},
          }, describe),
        });
      }
      for (const experiment of experiments) {
        for (const algorithm of experiment.resolved?.algorithms || []) {
          if (editableDefinitions.has(algorithm.name) && algorithmMap.has(algorithm.name)) {
            const definition = editableDefinitions.get(algorithm.name) || {};
            algorithmMap.set(algorithm.name, Object.assign({}, algorithmMap.get(algorithm.name), {
              name: algorithm.name,
              base: definition.base || algorithm.base || '',
              args: definition.args ?? algorithm.args ?? '',
              properties: guidedPropertiesForAlgorithm({
                ...algorithm,
                base: definition.base || algorithm.base || '',
                declaredProperties: declaredProperties[algorithm.name] || {},
              }, describe),
            }));
          }
        }
      }
      const basePath = model?.settings?.benchmark_base_path || state.settings?.benchmark_base_path || '';
      const formExperiments = experiments.map((experiment, index) => {
        const declared = experiment.declared || {};
        const graphDirectives = Array.isArray(declared.graph_directives) && declared.graph_directives.length
          ? declared.graph_directives.map(graph => ({
              kind: graph.command === 'Graphs' ? 'Graphs' : 'Graph',
              path: graph.path || '',
              extension: graph.extension || '',
            })).filter(graph => graph.path)
          : guidedLineList(declared.graphs?.length ? declared.graphs : (experiment.resolved?.graphs || []).map(graph => graph.spec))
              .map(graph => ({ kind: 'Graph', path: graph, extension: '' }));
        return {
          function: experiment.experiment?.function || `Experiment${index + 1}`,
          algorithms: guidedTextList(declared.algorithms?.length ? declared.algorithms : (experiment.resolved?.algorithms || []).map(algorithm => algorithm.name)),
          graphs: graphDirectives.length ? graphDirectives : (basePath ? [{ kind: 'Graphs', path: basePath, extension: '' }] : []),
          ks: guidedTextList(declared.ks || []),
          seeds: guidedTextList(declared.seeds || []),
          epsilons: guidedTextList(declared.epsilons || []),
          topologies: guidedTextList(declared.topologies || []),
          timelimit: declared.timelimit || '',
          timelimit_per_instance: declared.timelimit_per_instance || '',
        };
      });
      return {
        system: first.experiment?.system || describe.systems?.[0]?.name || 'slurm',
        properties: guidedPropertyRows(first.declared?.global_properties || {}),
        algorithm_options: Array.from(algorithmOptions).sort((left, right) => left.localeCompare(right)),
        algorithm_definitions: Array.from(algorithmMap.values()),
        experiments: formExperiments.length ? formExperiments : [{
          function: 'ExperimentWeb',
          algorithms: Array.from(algorithmOptions.size ? algorithmOptions : algorithmMap.keys()),
          graphs: basePath ? [{ kind: 'Graphs', path: basePath, extension: '' }] : [],
          ks: ['2'],
          seeds: ['1'],
          epsilons: ['0.03'],
          topologies: ['1x1x1'],
          timelimit: '',
          timelimit_per_instance: '',
        }],
      };
    }
    function guidedInput(className, value = '', placeholder = '') {
      const input = document.createElement('input');
      input.className = className;
      input.value = value ?? '';
      input.placeholder = placeholder;
      return input;
    }
    function guidedOptionValuesFromDatalist(id) {
      return Array.from(document.getElementById(id)?.querySelectorAll('option') || [])
        .map(option => option.value || option.textContent || '')
        .filter(Boolean);
    }
    function guidedComboInput(className, value = '', placeholder = '', optionsProvider = () => []) {
      const wrapper = document.createElement('div');
      wrapper.className = 'guided-combo';
      const input = guidedInput(className, value, placeholder);
      input.autocomplete = 'off';
      const options = document.createElement('div');
      options.className = 'guided-combo-options hidden';
      const closeOptions = () => {
        options.classList.add('hidden');
        options.innerHTML = '';
      };
      const optionValues = () => {
        const raw = typeof optionsProvider === 'function' ? optionsProvider() : optionsProvider;
        return Array.from(new Set((raw || []).map(item => String(item || '').trim()).filter(Boolean)))
          .sort((left, right) => left.localeCompare(right));
      };
      const renderOptions = () => {
        const query = input.value.trim().toLowerCase();
        const values = optionValues()
          .filter(item => !query || item.toLowerCase().includes(query))
          .slice(0, 60);
        options.innerHTML = '';
        if (!values.length) {
          closeOptions();
          return;
        }
        for (const item of values) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'guided-combo-option';
          button.textContent = item;
          button.title = item;
          button.onmousedown = event => event.preventDefault();
          button.onclick = () => {
            input.value = item;
            closeOptions();
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.focus();
          };
          options.appendChild(button);
        }
        options.classList.remove('hidden');
      };
      input.addEventListener('focus', renderOptions);
      input.addEventListener('input', renderOptions);
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeOptions();
        if (event.key === 'ArrowDown' && !options.classList.contains('hidden')) {
          event.preventDefault();
          options.querySelector('.guided-combo-option')?.focus();
        }
      });
      wrapper.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        closeOptions();
        input.focus();
      });
      wrapper.addEventListener('focusout', () => {
        setTimeout(() => {
          if (!wrapper.contains(document.activeElement)) closeOptions();
        }, 80);
      });
      wrapper.appendChild(input);
      wrapper.appendChild(options);
      wrapper.input = input;
      return { control: wrapper, input };
    }
    function guidedArgumentList(className, values = [], placeholder = 'value') {
      const wrapper = document.createElement('div');
      wrapper.className = `guided-arg-list ${className}`;
      let input = null;
      const updatePlaceholder = () => {
        if (input) input.placeholder = wrapper.querySelector('.guided-arg-token') ? '' : placeholder;
      };
      const renderToken = value => {
        const text = String(value || '').trim();
        if (!text) return;
        const token = document.createElement('span');
        token.className = 'guided-arg-token';
        token.dataset.value = text;
        const label = document.createElement('span');
        label.textContent = text;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = 'x';
        remove.title = 'Remove argument';
        remove.onclick = () => {
          token.remove();
          updatePlaceholder();
          markGuidedDirty();
        };
        token.appendChild(label);
        token.appendChild(remove);
        wrapper.insertBefore(token, input);
        updatePlaceholder();
      };
      input = guidedInput('guided-arg-value', '', placeholder);
      const commitInput = () => {
        const values = guidedTextList(input.value);
        if (!values.length) return;
        for (const value of values) renderToken(value);
        input.value = '';
        updatePlaceholder();
        markGuidedDirty();
      };
      input.addEventListener('keydown', event => {
        if (event.key !== ' ' && event.key !== 'Enter') return;
        if (!input.value.trim()) return;
        event.preventDefault();
        commitInput();
      });
      input.addEventListener('blur', commitInput);
      const initial = guidedTextList(values);
      wrapper.appendChild(input);
      for (const value of initial) renderToken(value);
      updatePlaceholder();
      return wrapper;
    }
    function collectGuidedArgumentList(card, className) {
      const root = card.querySelector(`.${className}`);
      const tokens = Array.from(root?.querySelectorAll('.guided-arg-token') || []).map(token => token.dataset.value || token.textContent || '');
      const pending = guidedTextList(root?.querySelector('.guided-arg-value')?.value || '');
      return [...tokens, ...pending].map(value => String(value).trim()).filter(Boolean);
    }
    function guidedSelect(className, options, value = '') {
      const select = document.createElement('select');
      select.className = className;
      for (const optionValue of options) {
        const option = document.createElement('option');
        option.value = optionValue;
        option.textContent = optionValue;
        select.appendChild(option);
      }
      if (value && !options.includes(value)) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.prepend(option);
      }
      select.value = value || options[0] || '';
      return select;
    }
    function guidedPropertyValueControl(meta, value = '') {
      const values = meta?.values || [];
      if (values.length) {
        return guidedSelect('guided-property-value', values, value || meta?.value || values[0] || '');
      }
      if (meta?.key === 'repo_ref') {
        return guidedComboInput(
          'guided-property-value',
          value || '',
          meta?.value || 'origin/main',
          () => guidedOptionValuesFromDatalist('guided-repo-ref-suggestions')
        ).control;
      }
      const input = guidedInput('guided-property-value', value || '', meta?.value || 'value');
      return input;
    }
    function guidedField(label, control) {
      const wrapper = document.createElement('label');
      wrapper.className = 'guided-field';
      const title = document.createElement('span');
      title.className = 'guided-field-label';
      title.textContent = label;
      wrapper.appendChild(title);
      wrapper.appendChild(control);
      return wrapper;
    }
    function markGuidedDirty() {
      state.guidedDirty = true;
      clearCheckIndicator();
    }
    function removeGuidedCard(button) {
      button.closest('.guided-card, .guided-row')?.remove();
      markGuidedDirty();
    }
    function renderGuidedPropertyRows(container, properties, ownerClass, context = {}) {
      container.innerHTML = '';
      const rows = guidedPropertyRows(properties);
      const catalog = guidedPropertyCatalog(context);
      const options = guidedPropertyKeyOptions(catalog, rows);
      rows.forEach((prop, index) => {
        const key = prop.key || options[0] || '';
        const meta = catalog.get(key) || { key, value: '', values: [], closed: false };
        const row = document.createElement('div');
        row.className = `guided-row guided-property-row ${ownerClass}`;
        row.dataset.propertyIndex = String(index);
        const keySelect = guidedSelect('guided-property-key', options, key);
        keySelect.title = meta.when || meta.allowed || key;
        keySelect.onchange = () => {
          const nextRows = collectGuidedPropertyRows(container);
          const nextMeta = catalog.get(keySelect.value);
          nextRows[index] = { key: keySelect.value, value: nextMeta?.value || '' };
          renderGuidedPropertyRows(container, nextRows, ownerClass, context);
          markGuidedDirty();
        };
        row.appendChild(keySelect);
        const value = guidedPropertyValueControl(meta, prop.value || '');
        row.appendChild(value);
        if (key === 'repo_ref' && context.kind === 'algorithm') {
          const fetch = document.createElement('button');
          fetch.type = 'button';
          fetch.textContent = 'Fetch refs';
          fetch.className = 'guided-fetch-refs';
          fetch.onclick = () => fetchGuidedRepoRefs(fetch).catch(err => out(String(err)));
          row.appendChild(fetch);
        } else {
          row.classList.add('no-fetch');
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = 'x';
        remove.title = 'Remove property';
        remove.onclick = () => removeGuidedCard(remove);
        row.appendChild(remove);
        container.appendChild(row);
      });
    }
    function collectGuidedPropertyRows(container) {
      return Array.from(container.querySelectorAll('.guided-property-row')).map(row => ({
        key: row.querySelector('.guided-property-key')?.value.trim() || '',
        value: row.querySelector('.guided-property-value')?.value.trim() || '',
      })).filter(prop => prop.key);
    }
    async function fetchGuidedRepoRefs(button) {
      const card = button.closest('[data-guided-algorithm]');
      const explicitRepoUrl = Array.from(card.querySelectorAll('.guided-algorithm-property')).find(row =>
        row.querySelector('.guided-property-key')?.value.trim() === 'repo_url'
      )?.querySelector('.guided-property-value')?.value.trim();
      const base = card.querySelector('.guided-algorithm-base')?.value.trim() || '';
      const repoUrl = explicitRepoUrl || defaultRepoUrlForBase(base);
      if (!repoUrl) {
        alert('No repo_url set and no default repo_url is known for this base.');
        return;
      }
      await withBusyButton(button, 'Fetching...', async () => {
        const data = await api('/api/repo-refs', {
          method: 'POST',
          body: JSON.stringify({ repo_url: repoUrl })
        });
        const datalist = document.getElementById('guided-repo-ref-suggestions');
        datalist.innerHTML = '';
        for (const ref of data.refs || []) {
          const option = document.createElement('option');
          option.value = ref.name;
          option.label = `${ref.kind} ${ref.sha?.slice(0, 10) || ''}`;
          datalist.appendChild(option);
        }
        const repoRef = Array.from(card.querySelectorAll('.guided-algorithm-property')).find(row =>
          row.querySelector('.guided-property-key')?.value.trim() === 'repo_ref'
        )?.querySelector('.guided-property-value');
        if (repoRef) repoRef.focus();
      });
    }
    function renderGuidedAlgorithm(container, algorithm) {
      const card = document.createElement('article');
      card.className = 'guided-card guided-custom-algorithm-card';
      card.dataset.guidedAlgorithm = '1';
      const header = document.createElement('div');
      header.className = 'guided-card-header';
      const title = document.createElement('div');
      title.className = 'guided-card-title';
      title.textContent = algorithm.name || 'Algorithm';
      const badge = document.createElement('span');
      badge.className = 'guided-algorithm-badge';
      badge.textContent = 'Custom';
      title.appendChild(badge);
      const actions = document.createElement('div');
      actions.className = 'guided-inline-actions';
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Remove';
      remove.onclick = () => removeGuidedCard(remove);
      actions.appendChild(remove);
      header.appendChild(title);
      header.appendChild(actions);
      const grid = document.createElement('div');
      grid.className = 'guided-grid';
      const baseCombo = guidedComboInput('guided-algorithm-base', algorithm.base || '', 'KaMinPar or existing algorithm', guidedBaseOptions);
      const baseInput = baseCombo.input;
      grid.appendChild(guidedField('Name', guidedInput('guided-algorithm-name', algorithm.name || '', 'MyVariant')));
      grid.appendChild(guidedField('Base / alias', baseCombo.control));
      grid.appendChild(guidedField('CLI arguments', guidedInput('guided-algorithm-args', algorithm.args || '', '-P strong')));
      const propList = document.createElement('div');
      propList.className = 'guided-row-list guided-algorithm-properties';
      const renderAlgorithmProperties = rows => renderGuidedPropertyRows(
        propList,
        rows,
        'guided-algorithm-property',
        { kind: 'algorithm', base: baseInput.value.trim() }
      );
      baseInput.addEventListener('change', () => {
        renderAlgorithmProperties(collectGuidedPropertyRows(propList));
        markGuidedDirty();
      });
      renderAlgorithmProperties(algorithm.properties || []);
      const addProp = document.createElement('button');
      addProp.type = 'button';
      addProp.textContent = 'Add property';
      addProp.onclick = () => {
        const rows = collectGuidedPropertyRows(propList);
        rows.push(suggestedGuidedProperty({ kind: 'algorithm', base: baseInput.value.trim() }, rows));
        renderAlgorithmProperties(rows);
        markGuidedDirty();
      };
      card.appendChild(header);
      card.appendChild(grid);
      card.appendChild(propList);
      card.appendChild(addProp);
      container.appendChild(card);
    }
    function normalizeGuidedGraphRows(graphs) {
      const rows = Array.isArray(graphs) ? graphs : [];
      return rows.map(graph => {
        if (typeof graph === 'object' && graph !== null) {
          return {
            kind: graph.kind === 'Graphs' || graph.command === 'Graphs' ? 'Graphs' : 'Graph',
            path: String(graph.path || graph.value || '').trim(),
            extension: String(graph.extension || graph.ext || '').trim().replace(/^\./, ''),
          };
        }
        return { kind: 'Graph', path: String(graph || '').trim(), extension: '' };
      }).filter(graph => graph.path || graph.kind === 'Graphs');
    }
    function collectGuidedGraphRows(container) {
      return Array.from(container.querySelectorAll('.guided-graph-row')).map(row => ({
        kind: row.querySelector('.guided-graph-kind')?.value || 'Graph',
        path: row.querySelector('.guided-graph')?.value.trim() || '',
        extension: row.querySelector('.guided-graph-extension')?.value.trim().replace(/^\./, '') || '',
      })).filter(graph => graph.path);
    }
    async function expandGuidedGraphDirectory(button) {
      if (!state.selected) return;
      const row = button.closest('.guided-graph-row');
      const container = row?.parentElement;
      if (!row || !container) return;
      const path = row.querySelector('.guided-graph')?.value.trim() || '';
      const extension = row.querySelector('.guided-graph-extension')?.value.trim().replace(/^\./, '') || '';
      if (!path) {
        alert('Set a graph directory first.');
        return;
      }
      await withBusyButton(button, 'Expanding...', async () => {
        const data = await api(`/api/experiments/${encodeURIComponent(state.selected)}/graph-directory`, {
          method: 'POST',
          body: JSON.stringify({ path, extension })
        });
        const entries = data.entries || [];
        if (!entries.length) {
          alert('No graph files found in that directory.');
          return;
        }
        const rows = collectGuidedGraphRows(container);
        const index = Array.from(container.querySelectorAll('.guided-graph-row')).indexOf(row);
        rows.splice(index, 1, ...entries.map(entry => ({ kind: 'Graph', path: entry.path, extension: '' })));
        renderGuidedGraphRows(container, rows);
        markGuidedDirty();
      });
    }
    function renderGuidedGraphRows(container, graphs) {
      container.innerHTML = '';
      const rows = normalizeGuidedGraphRows(graphs);
      for (const graph of (rows.length ? rows : [{ kind: 'Graphs', path: '', extension: '' }])) {
        const row = document.createElement('div');
        row.className = 'guided-row guided-graph-row';
        const kind = guidedSelect('guided-graph-kind', ['Graphs', 'Graph'], graph.kind || 'Graphs');
        row.appendChild(kind);
        const graphCombo = guidedComboInput(
          'guided-graph',
          graph.path || '',
          'graph file or benchmark set',
          () => guidedOptionValuesFromDatalist('guided-graph-suggestions')
        );
        row.appendChild(graphCombo.control);
        const extension = guidedInput('guided-graph-extension', graph.extension || '', 'ext');
        row.appendChild(extension);
        const expand = document.createElement('button');
        expand.type = 'button';
        expand.textContent = 'Expand';
        expand.title = 'Replace this directory with one Graph entry per file';
        expand.disabled = kind.value !== 'Graphs';
        kind.onchange = () => {
          expand.disabled = kind.value !== 'Graphs';
          markGuidedDirty();
        };
        expand.onclick = () => expandGuidedGraphDirectory(expand).catch(err => out(String(err)));
        row.appendChild(expand);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = 'x';
        remove.title = 'Remove graph entry';
        remove.onclick = () => removeGuidedCard(remove);
        row.appendChild(remove);
        container.appendChild(row);
      }
    }
    function renderGuidedExperiment(container, experiment) {
      const card = document.createElement('article');
      card.className = 'guided-card';
      card.dataset.guidedExperiment = '1';
      const header = document.createElement('div');
      header.className = 'guided-card-header';
      const title = document.createElement('div');
      title.className = 'guided-card-title';
      title.textContent = experiment.function || 'Experiment';
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Remove';
      remove.onclick = () => removeGuidedCard(remove);
      header.appendChild(title);
      header.appendChild(remove);
      const grid = document.createElement('div');
      grid.className = 'guided-grid';
      grid.appendChild(guidedField('Function', guidedInput('guided-experiment-function', experiment.function || '', 'ExperimentName')));
      grid.appendChild(guidedField('Ks', guidedArgumentList('guided-experiment-ks', experiment.ks || [], '2')));
      grid.appendChild(guidedField('Seeds', guidedArgumentList('guided-experiment-seeds', experiment.seeds || [], '1')));
      grid.appendChild(guidedField('Epsilons', guidedArgumentList('guided-experiment-epsilons', experiment.epsilons || [], '0.03')));
      grid.appendChild(guidedField('Threads', guidedArgumentList('guided-experiment-topologies', experiment.topologies || [], '1x1x64')));
      grid.appendChild(guidedField('Timelimit', guidedInput('guided-experiment-timelimit', experiment.timelimit || '', 'empty = unlimited time')));
      const algorithmChecks = document.createElement('div');
      algorithmChecks.className = 'guided-check-grid';
      const algorithmNames = new Set(state.guidedForm.algorithm_options || []);
      const customAlgorithms = guidedCustomAlgorithmNames();
      for (const algorithm of state.guidedForm.algorithm_definitions || []) if (algorithm.name) algorithmNames.add(algorithm.name);
      for (const algorithm of experiment.algorithms || []) algorithmNames.add(algorithm);
      for (const algorithmName of Array.from(algorithmNames).sort((left, right) => left.localeCompare(right))) {
        const label = document.createElement('label');
        const isCustom = customAlgorithms.has(algorithmName);
        label.className = `guided-check${isCustom ? ' custom' : ''}`;
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = algorithmName;
        checkbox.checked = (experiment.algorithms || []).includes(algorithmName);
        const text = document.createElement('span');
        text.textContent = algorithmName;
        text.title = algorithmName;
        label.appendChild(checkbox);
        label.appendChild(text);
        if (isCustom) {
          const badge = document.createElement('span');
          badge.className = 'guided-algorithm-badge';
          badge.textContent = 'Custom';
          label.appendChild(badge);
        }
        algorithmChecks.appendChild(label);
      }
      const graphList = document.createElement('div');
      graphList.className = 'guided-row-list guided-graphs';
      renderGuidedGraphRows(graphList, experiment.graphs || []);
      const addGraph = document.createElement('button');
      addGraph.type = 'button';
      addGraph.textContent = 'Add graph';
      addGraph.onclick = () => {
        const rows = collectGuidedGraphRows(graphList);
        rows.push({ kind: 'Graphs', path: state.settings?.benchmark_base_path || '', extension: '' });
        renderGuidedGraphRows(graphList, rows);
        markGuidedDirty();
      };
      card.appendChild(header);
      card.appendChild(grid);
      card.appendChild(guidedField('Algorithms', algorithmChecks));
      card.appendChild(guidedField('Graphs', graphList));
      card.appendChild(addGraph);
      container.appendChild(card);
    }
    function renderGuidedEditor() {
      const box = document.getElementById('guided-output');
      if (!box) return;
      const form = state.guidedForm;
      if (!form) {
        box.className = 'csv-empty';
        box.textContent = 'Guided model not loaded.';
        return;
      }
      box.className = 'guided-editor';
      box.innerHTML = '';
      updateGuidedBaseSuggestions();
      const systems = (state.guidedModel?.describe?.systems || []).map(system => system.name).sort();
      const basics = document.createElement('section');
      basics.className = 'guided-section';
      const basicsHeader = document.createElement('div');
      basicsHeader.className = 'guided-section-header';
      const basicsTitle = document.createElement('div');
      basicsTitle.className = 'guided-section-title';
      basicsTitle.textContent = 'System and properties';
      const addGlobal = document.createElement('button');
      addGlobal.type = 'button';
      addGlobal.textContent = 'Add property';
      basicsHeader.appendChild(basicsTitle);
      basicsHeader.appendChild(addGlobal);
      const basicsGrid = document.createElement('div');
      basicsGrid.className = 'guided-grid';
      const systemSelect = guidedSelect('guided-system', systems, form.system || '');
      basicsGrid.appendChild(guidedField('System', systemSelect));
      const globalRows = document.createElement('div');
      globalRows.className = 'guided-row-list guided-global-properties';
      const renderGlobalProperties = rows => renderGuidedPropertyRows(
        globalRows,
        rows,
        'guided-global-property',
        { kind: 'system', system: systemSelect.value }
      );
      systemSelect.addEventListener('change', () => {
        renderGlobalProperties(collectGuidedPropertyRows(globalRows));
        markGuidedDirty();
      });
      renderGlobalProperties(form.properties || []);
      addGlobal.onclick = () => {
        const rows = collectGuidedPropertyRows(globalRows);
        rows.push(suggestedGuidedProperty({ kind: 'system', system: systemSelect.value }, rows));
        renderGlobalProperties(rows);
        markGuidedDirty();
      };
      basics.appendChild(basicsHeader);
      basics.appendChild(basicsGrid);
      basics.appendChild(globalRows);
      const algorithms = document.createElement('section');
      algorithms.className = 'guided-section';
      const algorithmsHeader = document.createElement('div');
      algorithmsHeader.className = 'guided-section-header';
      const algorithmsTitle = document.createElement('div');
      algorithmsTitle.className = 'guided-section-title';
      algorithmsTitle.textContent = 'Algorithms';
      const addAlgorithm = document.createElement('button');
      addAlgorithm.type = 'button';
      addAlgorithm.textContent = 'Add algorithm';
      addAlgorithm.onclick = () => {
        state.guidedForm = collectGuidedForm();
        state.guidedForm.algorithm_definitions.push({ name: 'NewAlgorithm', base: guidedBaseOptions()[0] || '', args: '', properties: [] });
        renderGuidedEditor();
        markGuidedDirty();
      };
      algorithmsHeader.appendChild(algorithmsTitle);
      algorithmsHeader.appendChild(addAlgorithm);
      const algorithmList = document.createElement('div');
      algorithmList.className = 'guided-row-list';
      for (const algorithm of form.algorithm_definitions || []) renderGuidedAlgorithm(algorithmList, algorithm);
      algorithms.appendChild(algorithmsHeader);
      algorithms.appendChild(algorithmList);
      const experiments = document.createElement('section');
      experiments.className = 'guided-section';
      const experimentsHeader = document.createElement('div');
      experimentsHeader.className = 'guided-section-header';
      const experimentsTitle = document.createElement('div');
      experimentsTitle.className = 'guided-section-title';
      experimentsTitle.textContent = 'Experiments';
      const addExperiment = document.createElement('button');
      addExperiment.type = 'button';
      addExperiment.textContent = 'Add experiment';
      addExperiment.onclick = () => {
        state.guidedForm = collectGuidedForm();
        state.guidedForm.experiments.push({
          function: `Experiment${state.guidedForm.experiments.length + 1}`,
          algorithms: (state.guidedForm.algorithm_definitions || []).map(algorithm => algorithm.name),
          graphs: state.settings?.benchmark_base_path ? [{ kind: 'Graphs', path: state.settings.benchmark_base_path, extension: '' }] : [],
          ks: ['2'],
          seeds: ['1'],
          epsilons: ['0.03'],
          topologies: ['1x1x1'],
          timelimit: '',
          timelimit_per_instance: '',
        });
        renderGuidedEditor();
        markGuidedDirty();
      };
      experimentsHeader.appendChild(experimentsTitle);
      experimentsHeader.appendChild(addExperiment);
      const experimentList = document.createElement('div');
      experimentList.className = 'guided-row-list';
      for (const experiment of form.experiments || []) renderGuidedExperiment(experimentList, experiment);
      experiments.appendChild(experimentsHeader);
      experiments.appendChild(experimentList);
      box.appendChild(basics);
      box.appendChild(algorithms);
      box.appendChild(experiments);
    }
    function collectGuidedForm() {
      const box = document.getElementById('guided-output');
      return {
        system: box.querySelector('.guided-system')?.value || 'slurm',
        properties: collectGuidedPropertyRows(box.querySelector('.guided-global-properties') || document.createElement('div')),
        algorithm_options: Array.from(new Set([
          ...(state.guidedForm?.algorithm_options || []),
          ...Array.from(box.querySelectorAll('[data-guided-algorithm]')).map(card => card.querySelector('.guided-algorithm-name')?.value.trim() || '').filter(Boolean),
        ])).sort((left, right) => left.localeCompare(right)),
        algorithm_definitions: Array.from(box.querySelectorAll('[data-guided-algorithm]')).map(card => ({
          name: card.querySelector('.guided-algorithm-name')?.value.trim() || '',
          base: card.querySelector('.guided-algorithm-base')?.value.trim() || '',
          args: card.querySelector('.guided-algorithm-args')?.value.trim() || '',
          properties: collectGuidedPropertyRows(card.querySelector('.guided-algorithm-properties') || document.createElement('div')),
        })).filter(algorithm => algorithm.name && algorithm.base),
        experiments: Array.from(box.querySelectorAll('[data-guided-experiment]')).map(card => ({
          function: card.querySelector('.guided-experiment-function')?.value.trim() || '',
          algorithms: Array.from(card.querySelectorAll('.guided-check input:checked')).map(input => input.value),
          graphs: collectGuidedGraphRows(card.querySelector('.guided-graphs') || document.createElement('div')),
          ks: collectGuidedArgumentList(card, 'guided-experiment-ks'),
          seeds: collectGuidedArgumentList(card, 'guided-experiment-seeds'),
          epsilons: collectGuidedArgumentList(card, 'guided-experiment-epsilons'),
          topologies: collectGuidedArgumentList(card, 'guided-experiment-topologies'),
          timelimit: card.querySelector('.guided-experiment-timelimit')?.value.trim() || '',
          timelimit_per_instance: '',
        })).filter(experiment => experiment.function),
      };
    }
    async function loadBenchmarkSuggestions() {
      const base = state.settings?.benchmark_base_path || '';
      if (!base || state.benchmarkSetsFor === base) return;
      const data = await api('/api/benchmark-sets');
      state.benchmarkSets = data.sets || [];
      state.benchmarkSetsFor = base;
      const datalist = document.getElementById('guided-graph-suggestions');
      datalist.innerHTML = '';
      for (const item of state.benchmarkSets) {
        const option = document.createElement('option');
        option.value = item.path;
        option.label = `${item.kind} ${item.relative}`;
        datalist.appendChild(option);
      }
    }
    async function loadGuidedEditor(force = false) {
      if (!state.selected || state.selectedArchived || state.shared) return;
      const selected = state.selected;
      if (!force && state.guidedFor === selected && state.guidedForm) {
        renderGuidedEditor();
        return;
      }
      const box = document.getElementById('guided-output');
      box.className = 'csv-empty';
      box.textContent = 'Loading guided model from mkexp2 probe and describe...';
      const [model] = await Promise.all([
        api(`/api/experiments/${encodeURIComponent(selected)}/guided`),
        loadUiSettings().catch(() => state.settings),
      ]);
      if (state.selected !== selected) return;
      state.guidedModel = model;
      state.guidedFor = selected;
      state.guidedForm = guidedFormFromModel(model);
      state.guidedDirty = false;
      await loadBenchmarkSuggestions().catch(err => out(String(err)));
      renderGuidedEditor();
    }
    function renderEditorMode() {
      const textMode = state.editorMode !== 'guided';
      document.querySelector('.editor-shell')?.classList.toggle('hidden', !textMode);
      document.getElementById('experiment-editor-tools')?.classList.toggle('hidden', !textMode);
      document.getElementById('guided-editor')?.classList.toggle('hidden', textMode);
      document.getElementById('editor-mode-text')?.classList.toggle('active', textMode);
      document.getElementById('editor-mode-guided')?.classList.toggle('active', !textMode);
      renderEditorInsertButtons();
      const check = document.getElementById('check');
      if (check) {
        check.textContent = textMode ? 'Save' : 'Save';
        check.title = textMode ? 'Save and validate the Experiment file' : 'Generate and save the Experiment file from the guided form';
      }
    }
    function resetGuidedState() {
      state.editorMode = 'text';
      state.guidedModel = null;
      state.guidedFor = null;
      state.guidedForm = null;
      state.guidedDirty = false;
      renderEditorMode();
    }
    async function switchEditorMode(mode) {
      if (mode === 'guided') {
        state.editorMode = 'guided';
        renderEditorMode();
        await loadGuidedEditor(true);
        return;
      }
      state.editorMode = 'text';
      renderEditorMode();
    }
    async function saveGuidedExperiment() {
      if (!state.selected || state.selectedArchived || state.shared) return;
      state.guidedForm = collectGuidedForm();
      const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/guided`, {
        method: 'PUT',
        body: JSON.stringify({ form: state.guidedForm })
      });
      setEditorValue(result.experiment || '');
      state.editorDirty = false;
      state.guidedDirty = false;
      setSelectedExperimentMetadata(state.selected, result.experiment_file || result.path, result);
      await loadAlgorithms(state.selected, { force: true }).catch(err => out(String(err)));
      clearCheckIndicator();
      return result;
    }
    document.getElementById('guided-editor').addEventListener('input', markGuidedDirty);
    document.getElementById('guided-editor').addEventListener('change', markGuidedDirty);
