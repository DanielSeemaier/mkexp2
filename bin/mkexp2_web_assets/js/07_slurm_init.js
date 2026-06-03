    function cpuCount(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : NaN;
    }
    function formatCpuCount(value) {
      const parsed = cpuCount(value);
      if (!Number.isFinite(parsed)) return 'n/a';
      const cores = parsed / 2;
      const text = Number.isInteger(cores) ? String(cores) : cores.toFixed(1);
      return `${text} cores`;
    }
    function nodeStateClass(state) {
      const bucket = slurmStateBucket(state);
      if (bucket === 'down') return 'node-state-down';
      if (bucket === 'idle') return 'node-state-idle';
      if (bucket === 'allocated') return 'node-state-allocated';
      return '';
    }
    function setNodeStatusLoading(active) {
      if (active) {
        if (!state.nodeStatusBusyRestore) state.nodeStatusBusyRestore = setIconButtonSpinning('refresh-status');
        return;
      }
      if (state.nodeStatusBusyRestore) {
        state.nodeStatusBusyRestore();
        state.nodeStatusBusyRestore = null;
      }
    }
    function startNodeStatusPolling() {
      if (state.nodeStatusTimer || state.shared || !(token() || allowEmptyToken)) return;
      state.nodeStatusTimer = setTimeout(() => {
        state.nodeStatusTimer = null;
        refreshStatus({ auto: true, quiet: true }).catch(() => {});
      }, AUTO_RELOAD_INTERVAL_MS);
    }
    function stopNodeStatusPolling() {
      if (state.nodeStatusTimer) {
        clearTimeout(state.nodeStatusTimer);
        state.nodeStatusTimer = null;
      }
    }
    async function refreshStatus(_options = {}) {
      stopNodeStatusPolling();
      setNodeStatusLoading(true);
      try {
        const data = await api('/api/status/slurm');
        state.nodeStatusPayload = data;
        renderDashboard();
        clearTransientOutput();
        const box = document.getElementById('slurm-status');
        if (!data.nodes.length) {
          box.className = 'node-list muted';
          box.textContent = 'No Slurm nodes found.';
          return;
        }
        box.className = 'node-list';
        const nodes = Array.from(data.nodes).sort((left, right) => {
          const rightCpus = cpuCount(right.cpus || right.cpu_info);
          const leftCpus = cpuCount(left.cpus || left.cpu_info);
          return (Number.isFinite(rightCpus) ? rightCpus : -1) - (Number.isFinite(leftCpus) ? leftCpus : -1)
            || String(left.name ?? '').localeCompare(String(right.name ?? ''));
        });
        const rows = nodes.map(node => {
          const state = node.state || node.availability || '';
          const stateClass = nodeStateClass(state);
          return `<div class="node-row ${esc(stateClass)}" title="${esc(state)}"><span class="node-name">${esc(node.name)}</span><span class="node-spec">${esc(formatCpuCount(node.cpus || node.cpu_info))}</span></div>`;
        }).join('');
        box.innerHTML = rows;
      } finally {
        setNodeStatusLoading(false);
        startNodeStatusPolling();
      }
    }
    document.getElementById('refresh-status').onclick = () => refreshStatus().catch(err => out(String(err)));
    document.getElementById('auth-submit').onclick = () => withBusyButton('auth-submit', 'Connecting...', submitAuthToken).catch(err => out(String(err)));
    document.getElementById('auth-token').addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        withBusyButton('auth-submit', 'Connecting...', submitAuthToken).catch(err => out(String(err)));
      }
    });
    document.getElementById('queue-open').onclick = () => withBusyButton('queue-open', '', openQueueDialog).catch(err => out(String(err)));
    document.getElementById('queue-close').onclick = closeQueueDialog;
    document.getElementById('queue-refresh').onclick = () => withBusyButton('queue-refresh', '', loadQueue).catch(err => out(String(err)));
    document.getElementById('queue-cancel-all').onclick = () => cancelAllQueueJobs(document.getElementById('queue-cancel-all')).catch(err => out(String(err)));
    document.getElementById('dashboard-open').onclick = () => withBusyButton('dashboard-open', '', () => setView('dashboard-view')).catch(err => out(String(err)));
    document.getElementById('create-open').onclick = () => withBusyButton('create-open', '', openCreateDialog).catch(err => out(String(err)));
    document.getElementById('dashboard-queue-refresh').onclick = () => withBusyButton('dashboard-queue-refresh', '', loadQueue).catch(err => out(String(err)));
    document.getElementById('create-close').onclick = closeCreateDialog;
    document.getElementById('create-cancel').onclick = closeCreateDialog;
    document.getElementById('create-submit').onclick = createExperiment;
    document.getElementById('create-name').oninput = updateCreatePreview;
    document.getElementById('create-template').oninput = updateCreatePreview;
    document.getElementById('create-template-override').onchange = updateCreatePreview;
    document.getElementById('copy-experiment').onclick = () => withBusyButton('copy-experiment', '', openCopyDialog).catch(err => out(String(err)));
    document.getElementById('copy-close').onclick = closeCopyDialog;
    document.getElementById('copy-cancel').onclick = closeCopyDialog;
    document.getElementById('copy-submit').onclick = () => copyExperiment().catch(err => out(String(err)));
    document.getElementById('copy-name').oninput = updateCopyPreview;
    document.getElementById('copy-template').oninput = updateCopyPreview;
    document.getElementById('copy-template-override').onchange = updateCopyPreview;
    document.getElementById('archive-open').onclick = () => withBusyButton('archive-open', 'Loading...', openArchivePane).catch(err => out(String(err)));
    document.getElementById('archive-search').oninput = event => {
      state.archiveQuery = event.target.value || '';
      renderArchivedExperiments();
    };
    document.getElementById('git-open').onclick = () => withBusyButton('git-open', '', openGitDialog).catch(err => out(String(err)));
    document.getElementById('git-close').onclick = closeGitDialog;
    document.getElementById('git-refresh').onclick = () => withBusyButton('git-refresh', '', loadGitStatus).catch(err => out(String(err)));
    document.getElementById('git-push').onclick = pushGitChanges;
    document.getElementById('share-experiment').onclick = shareExperiment;
    document.getElementById('download-experiment').onclick = downloadExperiment;
    document.getElementById('download-close').onclick = closeDownloadDialog;
    document.getElementById('download-cancel').onclick = closeDownloadDialog;
    document.getElementById('download-select-all').onclick = () => setDownloadDirectoriesChecked(true);
    document.getElementById('download-select-none').onclick = () => setDownloadDirectoriesChecked(false);
    document.getElementById('download-submit').onclick = () => performDownload().catch(err => out(String(err)));
    document.getElementById('share-close').onclick = closeShareDialog;
    document.getElementById('share-username').oninput = renderShareCommand;
    document.getElementById('share-copy-command').onclick = () => copyShareCommand().catch(err => out(String(err)));
    document.getElementById('experiment-tag-select').onchange = () => assignSelectedTag().catch(err => out(String(err)));
    document.getElementById('tag-save').onclick = () => saveTag().catch(err => out(String(err)));
    document.getElementById('settings-open').onclick = openSettingsDialog;
    document.getElementById('settings-close').onclick = closeSettingsDialog;
    initializeSettingsNav();
    document.getElementById('token-toggle').onclick = toggleTokenVisibility;
    document.getElementById('token-clear').onclick = clearSessionToken;
    document.getElementById('download-archive-format').onchange = () => saveDownloadArchiveFormat().catch(err => out(String(err)));
    document.getElementById('workspace-create').onclick = () => createWorkspace().catch(err => {
      setWorkspaceOutput(String(err), true);
      out(String(err));
    });
    document.getElementById('workspace-path').addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      createWorkspace().catch(err => {
        setWorkspaceOutput(String(err), true);
        out(String(err));
      });
    });
    document.getElementById('theme-select').onchange = event => saveTheme(event.target.value);
    document.getElementById('benchmark-base-path').addEventListener('change', () => saveBenchmarkBasePath().catch(err => out(String(err))));
    document.getElementById('insert-template-add').onclick = () => addInsertTemplate();
    document.getElementById('insert-templates-reset').onclick = () => resetInsertTemplates();
    document.getElementById('insert-templates-save').onclick = () => saveInsertTemplates().catch(err => out(String(err)));
    document.getElementById('archive-codex-experiments').onclick = () => archiveCodexExperiments().catch(err => out(String(err)));
    document.getElementById('archive-subdir-experiments').onclick = () => archiveSubdirectoryExperiments().catch(err => out(String(err)));
    document.getElementById('spack-cache-refresh').onclick = () => refreshSpackCache().catch(err => out(String(err)));
    document.getElementById('editor-mode-text').onclick = () => switchEditorMode('text').catch(err => out(String(err)));
    document.getElementById('editor-mode-guided').onclick = () => withBusyButton('editor-mode-guided', 'Loading...', () => switchEditorMode('guided')).catch(err => out(String(err)));
    document.getElementById('check').onclick = checkExperiment;
    document.getElementById('describe-toggle').onclick = () => toggleDescribePanel().catch(err => out(String(err)));
    document.getElementById('describe-refresh').onclick = () => refreshDescribePanel().catch(err => out(String(err)));
    document.getElementById('describe-search').oninput = event => {
      state.describeQuery = event.target.value || '';
      renderDescribeCatalog();
    };
    document.querySelectorAll('[data-describe-filter]').forEach(button => {
      button.onclick = () => {
        state.describeFilter = button.dataset.describeFilter || 'algorithms';
        renderDescribeCatalog();
      };
    });
    document.getElementById('probe-toggle').onclick = () => toggleProbePanel().catch(err => out(String(err)));
    document.getElementById('probe-refresh').onclick = () => refreshProbePanel().catch(err => out(String(err)));
    document.getElementById('description-edit').onclick = editDescription;
    document.getElementById('description-cancel').onclick = cancelDescriptionEdit;
    document.getElementById('description-save').onclick = saveDescription;
    document.getElementById('submit-preview-open').onclick = () => withBusyButton('submit-preview-open', '', openSubmitPreviewDialog).catch(err => out(String(err)));
    document.getElementById('submit-preview-close').onclick = closeSubmitPreviewDialog;
    document.getElementById('submit').onclick = submitExperiment;
    document.getElementById('job-details-nav').onclick = () => withBusyButton('job-details-nav', 'Loading...', openJobDetailsDialog).catch(err => alert(String(err)));
    document.getElementById('job-details-refresh').onclick = () => {
      const restore = setIconButtonSpinning('job-details-refresh');
      loadJobDetails().catch(err => alert(String(err))).finally(restore);
    };
    document.getElementById('job-details-close').onclick = closeJobDetailsDialog;
    document.getElementById('job-details-cancel').onclick = () => cancelSubmittedExperiment().catch(err => alert(String(err)));
    document.getElementById('unarchive-nav').onclick = () => unarchiveSelectedExperiment().catch(err => alert(String(err)));
    document.getElementById('clear-submit-lock').onclick = clearSubmitLock;
    document.getElementById('rename-experiment').onclick = renameExperiment;
    document.getElementById('purge-experiment').onclick = purgeExperiment;
    document.getElementById('archive-experiment').onclick = archiveExperiment;
    document.getElementById('delete-experiment').onclick = deleteExperiment;
    document.getElementById('refresh-progress').onclick = () => loadProgress().catch(err => out(String(err)));
    document.getElementById('parse-results').onclick = parseExperiment;
    document.getElementById('plot-add-open').onclick = () => withBusyButton('plot-add-open', 'Loading...', openPlotGenerateDialog).catch(err => out(String(err)));
    document.getElementById('plot-generate-close').onclick = closePlotGenerateDialog;
    document.getElementById('plot-generate-cancel').onclick = closePlotGenerateDialog;
    document.getElementById('plot-results').onclick = () => plotExperiment().catch(err => out(String(err)));
    document.getElementById('add-plot-source').onclick = () => withBusyButton('add-plot-source', 'Loading...', openPlotSourceDialog).catch(err => out(String(err)));
    document.getElementById('plot-source-close').onclick = closePlotSourceDialog;
    document.getElementById('plot-source-close-footer').onclick = closePlotSourceDialog;
    document.getElementById('plot-label').oninput = () => {
      state.plotLabelTouched = true;
    };
    document.getElementById('plot-view-sets').onclick = () => {
      state.plotArtifactView = 'sets';
      renderPlotPanel();
    };
    document.getElementById('plot-view-types').onclick = () => {
      state.plotArtifactView = 'types';
      renderPlotPanel();
    };
    document.getElementById('plot-no-docker').onchange = () => {
      state.plotNoDockerTouched = true;
    };
    document.getElementById('load-results').onclick = () => withBusyButton('load-results', '', loadResults).catch(err => out(String(err)));
    document.getElementById('load-stats').onclick = () => withBusyButton('load-stats', 'Generating...', loadStats).catch(err => out(String(err)));
    document.getElementById('reload-logs').onclick = () => withBusyButton('reload-logs', '', () => loadLogs(state.logsDir || '')).catch(err => out(String(err)));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && closeVisibleModal()) {
        event.preventDefault();
      }
    });
    document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
      backdrop.addEventListener('click', event => {
        if (event.target === backdrop && closeVisibleModal()) {
          event.preventDefault();
        }
      });
    });
    document.querySelectorAll('.view-tab').forEach(button => {
      button.onclick = () => withBusyButton(button, 'Loading...', () => setView(button.dataset.view)).catch(err => out(String(err)));
    });
    initSidebarResize();
    if (initialShareId) {
      selectSharedExperiment(initialShareId).catch(err => out(String(err)));
    } else {
      bootAuthenticatedUi({ selectMostRecent: false }).catch(err => out(String(err)));
    }
