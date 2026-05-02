/**
 * agent-lens dashboard — app.js
 * Vanilla JS, no build step required.
 * Connects to the agent-lens server via SSE, renders runs/spans/events in real time.
 */

(function () {
  'use strict';

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  const state = {
    runs: [],
    selectedRunId: null,
    selectedSpanId: null,
    isPaused: false,
    view: 'tree',   // 'tree' | 'timeline' | 'inspector'
    sseConnected: false,
    eventCount: 0,
    search: '',
    expandedSpans: new Set(),
    csrfToken: window.__AGENT_LENS_CSRF__ || '',
    isExport: !!window.__AGENT_LENS_EXPORT__,
    exportData: window.__AGENT_LENS_DATA__ || null,
  };

  let sse = null;
  let spanData = [];   // flat span list for selected run
  let virtualStart = 0;  // for virtualized span list
  const VIRTUAL_PAGE = 200;

  // ----------------------------------------------------------------
  // DOM refs (populated after DOMContentLoaded)
  // ----------------------------------------------------------------

  let $runList, $runSelect, $tabContent,
      $statusConnDot, $statusConnText, $statusEventCount,
      $btnPause, $btnResume, $btnStep, $btnFork,
      $searchInput, $modal, $modalMessages;

  // ----------------------------------------------------------------
  // API helpers
  // ----------------------------------------------------------------

  async function apiFetch(path, method = 'GET', body = null) {
    const opts = {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-Agent-Lens-Token': state.csrfToken,
      },
    };
    if (body !== null) opts.body = JSON.stringify(body);
    try {
      const res = await fetch(path, opts);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text}`);
      }
      return res.json();
    } catch (e) {
      console.error('API error', path, e);
      throw e;
    }
  }

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------

  async function loadRuns() {
    if (state.isExport && state.exportData) {
      state.runs = [state.exportData.run];
      renderRunList();
      if (!state.selectedRunId) {
        selectRun(state.exportData.run.id);
      }
      return;
    }
    try {
      const runs = await apiFetch('/runs');
      state.runs = runs;
      renderRunList();
    } catch (e) {
      // Silently fail — dashboard may be loading before server
    }
  }

  async function selectRun(id) {
    state.selectedRunId = id;
    state.selectedSpanId = null;

    if (state.isExport && state.exportData) {
      spanData = state.exportData.spans || [];
    } else {
      try {
        const detail = await apiFetch(`/runs/${id}`);
        spanData = detail.spans || [];
      } catch (e) {
        spanData = [];
      }
    }

    // Update UI state
    const run = state.runs.find(r => r.id === id);
    state.isPaused = run && run.status === 'paused';

    renderRunList();
    renderRunSelect();
    renderContent();
    updateControlButtons();
  }

  function selectSpan(id) {
    state.selectedSpanId = id;
    renderContent();
  }

  // ----------------------------------------------------------------
  // Control actions
  // ----------------------------------------------------------------

  async function pauseRun() {
    if (!state.selectedRunId) return;
    try {
      await apiFetch(`/runs/${state.selectedRunId}/pause`, 'POST');
      state.isPaused = true;
      updateControlButtons();
    } catch (e) {
      alert('Failed to pause: ' + e.message);
    }
  }

  async function resumeRun() {
    if (!state.selectedRunId) return;
    try {
      await apiFetch(`/runs/${state.selectedRunId}/resume`, 'POST');
      state.isPaused = false;
      updateControlButtons();
    } catch (e) {
      alert('Failed to resume: ' + e.message);
    }
  }

  async function stepRun() {
    if (!state.selectedRunId) return;
    try {
      await apiFetch(`/runs/${state.selectedRunId}/step`, 'POST');
    } catch (e) {
      alert('Failed to step: ' + e.message);
    }
  }

  async function forkRun(spanId, editedMessages) {
    if (!state.selectedRunId) return;
    try {
      const result = await apiFetch(`/runs/${state.selectedRunId}/fork`, 'POST', {
        span_id: spanId || state.selectedSpanId,
        edited_messages: editedMessages || null,
      });
      await loadRuns();
      await selectRun(result.new_run_id);
    } catch (e) {
      alert('Failed to fork: ' + e.message);
    }
  }

  // ----------------------------------------------------------------
  // SSE connection
  // ----------------------------------------------------------------

  function connectSSE() {
    if (state.isExport) return;
    if (sse) { sse.close(); }

    sse = new EventSource('/events/stream');
    setConnStatus('connecting');

    sse.onopen = () => {
      setConnStatus('connected');
    };

    sse.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        handleSSEEvent(data);
      } catch (err) {
        // Ignore malformed events
      }
    };

    sse.onerror = () => {
      setConnStatus('disconnected');
      // Reconnect after 3 seconds
      setTimeout(connectSSE, 3000);
    };
  }

  function handleSSEEvent(data) {
    if (!data || !data.type) return;

    state.eventCount++;
    updateEventCount();

    switch (data.type) {
      case 'run_start':
        if (data.run) {
          const existing = state.runs.findIndex(r => r.id === data.run.id);
          if (existing >= 0) state.runs[existing] = data.run;
          else state.runs.unshift(data.run);
          renderRunList();
          renderRunSelect();
        }
        break;

      case 'run_end':
        if (data.run_id) {
          const run = state.runs.find(r => r.id === data.run_id);
          if (run) {
            run.status = data.status || 'completed';
            renderRunList();
            if (state.selectedRunId === data.run_id) {
              state.isPaused = run.status === 'paused';
              updateControlButtons();
            }
          }
        }
        break;

      case 'span_start':
      case 'span_end':
        if (data.span && data.span.run_id === state.selectedRunId) {
          const existing = spanData.findIndex(s => s.id === data.span.id);
          if (existing >= 0) spanData[existing] = data.span;
          else spanData.push(data.span);
          renderContent();
        }
        break;

      case 'event':
        // Individual event — update event count only
        break;

      case 'ping':
      case 'keepalive':
        setConnStatus('connected');
        break;
    }
  }

  function setConnStatus(status) {
    state.sseConnected = status === 'connected';
    if ($statusConnDot) {
      $statusConnDot.className = 'conn-dot ' + status;
    }
    if ($statusConnText) {
      $statusConnText.textContent = status === 'connected' ? 'connected'
        : status === 'connecting' ? 'connecting...'
        : 'disconnected';
    }
  }

  function updateEventCount() {
    if ($statusEventCount) {
      $statusEventCount.textContent = `${state.eventCount} events`;
    }
  }

  // ----------------------------------------------------------------
  // Rendering helpers
  // ----------------------------------------------------------------

  function renderRunList() {
    if (!$runList) return;
    const filtered = state.search
      ? state.runs.filter(r => r.name.toLowerCase().includes(state.search.toLowerCase()))
      : state.runs;

    // Virtualized: only render visible items
    const visible = filtered.slice(virtualStart, virtualStart + VIRTUAL_PAGE);

    $runList.innerHTML = visible.map(run => {
      const isActive = run.id === state.selectedRunId;
      const ts = new Date(run.start_time * 1000).toLocaleTimeString();
      const dur = run.end_time
        ? formatDuration((run.end_time - run.start_time) * 1000)
        : run.status === 'running' ? '…' : '?';
      return `<div class="run-item${isActive ? ' active' : ''} fade-in" data-id="${esc(run.id)}">
        <div class="run-name">${esc(run.name)}</div>
        <div class="run-meta">
          <span class="run-time">${esc(ts)}</span>
          <span class="run-time">${esc(dur)}</span>
          <span class="badge badge-${esc(run.status)}">${esc(run.status)}</span>
        </div>
      </div>`;
    }).join('');

    // Bind click handlers
    $runList.querySelectorAll('.run-item').forEach(el => {
      el.addEventListener('click', () => selectRun(el.dataset.id));
    });
  }

  function renderRunSelect() {
    if (!$runSelect) return;
    $runSelect.innerHTML = '<option value="">— select run —</option>' +
      state.runs.map(r =>
        `<option value="${esc(r.id)}"${r.id === state.selectedRunId ? ' selected' : ''}>${esc(r.name)} (${esc(r.status)})</option>`
      ).join('');
  }

  function renderContent() {
    if (!$tabContent) return;
    switch (state.view) {
      case 'tree':      renderTree(); break;
      case 'timeline':  renderTimeline(); break;
      case 'inspector': renderInspector(); break;
      default:          renderTree();
    }
  }

  // ----------------------------------------------------------------
  // Tree view
  // ----------------------------------------------------------------

  function renderTree() {
    if (!state.selectedRunId) {
      $tabContent.innerHTML = emptyState('Select a run to inspect', 'Click a run in the left panel, or run <code>agent_lens.trace</code> on your agent.');
      return;
    }
    if (!spanData.length) {
      $tabContent.innerHTML = emptyState('No spans yet', 'The agent hasn\'t started any traced calls yet.');
      return;
    }

    const tree = buildTree(spanData);
    $tabContent.innerHTML = `<div class="span-tree">${renderSpanNodes(tree)}</div>`;

    // Bind toggle and select
    $tabContent.querySelectorAll('.span-row').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = el.dataset.id;
        toggleSpan(id);
        selectSpan(id);
      });
    });
  }

  function buildTree(spans) {
    const map = {};
    spans.forEach(s => { map[s.id] = { ...s, _children: [] }; });
    const roots = [];
    spans.forEach(s => {
      if (s.parent_id && map[s.parent_id]) {
        map[s.parent_id]._children.push(map[s.id]);
      } else {
        roots.push(map[s.id]);
      }
    });
    return roots;
  }

  function renderSpanNodes(nodes, depth = 0) {
    return nodes.map(span => {
      const isExpanded = state.expandedSpans.has(span.id) || depth === 0;
      const isSelected = span.id === state.selectedSpanId;
      const hasChildren = span._children && span._children.length > 0;
      const dur = span.end_time
        ? formatDuration((span.end_time - span.start_time) * 1000)
        : '…';

      const icon = getSpanIcon(span.type);
      const statusCls = span.status === 'error' ? 'error'
        : span.status === 'paused' ? 'paused'
        : 'ok';

      return `<div class="span-node">
        <div class="span-row${isSelected ? ' selected' : ''} ${statusCls}" data-id="${esc(span.id)}">
          <span class="span-toggle${hasChildren ? '' : ' invisible'}${isExpanded ? ' expanded' : ''}">▶</span>
          <span class="status-dot ${esc(span.status || 'ok')}"></span>
          <span class="span-icon">${icon}</span>
          <span class="span-name">${esc(span.name)}</span>
          <span class="span-duration">${esc(dur)}</span>
        </div>
        ${hasChildren ? `
          <div class="span-children${isExpanded ? '' : ' collapsed'}">
            ${renderSpanNodes(span._children, depth + 1)}
          </div>` : ''}
      </div>`;
    }).join('');
  }

  function toggleSpan(id) {
    if (state.expandedSpans.has(id)) state.expandedSpans.delete(id);
    else state.expandedSpans.add(id);
    renderContent();
  }

  function getSpanIcon(type) {
    switch (type) {
      case 'llm':   return '🤖';
      case 'tool':  return '🔧';
      case 'agent': return '🧠';
      case 'chain': return '⛓';
      default:      return '◦';
    }
  }

  // ----------------------------------------------------------------
  // Timeline view (CSS flame graph)
  // ----------------------------------------------------------------

  function renderTimeline() {
    if (!spanData.length) {
      $tabContent.innerHTML = emptyState('No spans to display', 'Select a run with completed spans.');
      return;
    }

    const minTime = Math.min(...spanData.map(s => s.start_time));
    const maxTime = Math.max(...spanData.map(s => s.end_time || (s.start_time + 0.001)));
    const totalMs = (maxTime - minTime) * 1000 || 1;

    const ticks = [0, 25, 50, 75, 100];
    const tickHtml = ticks.map(t => `<div class="timeline-tick">${formatDuration(totalMs * t / 100)}</div>`).join('');

    const rowsHtml = spanData.map(span => {
      const left = ((span.start_time - minTime) / (maxTime - minTime)) * 100;
      const end = span.end_time || maxTime;
      const width = Math.max(((end - span.start_time) / (maxTime - minTime)) * 100, 0.5);
      const cls = `timeline-bar type-${esc(span.type || 'agent')}${span.status === 'error' ? ' status-error' : ''}`;

      return `<div class="timeline-row">
        <div class="timeline-label" title="${esc(span.name)}">${esc(span.name)}</div>
        <div class="timeline-track">
          <div class="${cls}"
               style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"
               title="${esc(span.name)} — ${formatDuration((end - span.start_time) * 1000)}"
               data-id="${esc(span.id)}">
          </div>
        </div>
      </div>`;
    }).join('');

    $tabContent.innerHTML = `<div class="timeline">
      <div class="timeline-header">${tickHtml}</div>
      ${rowsHtml}
    </div>`;

    $tabContent.querySelectorAll('.timeline-bar').forEach(el => {
      el.addEventListener('click', () => {
        selectSpan(el.dataset.id);
        setView('inspector');
      });
    });
  }

  // ----------------------------------------------------------------
  // Inspector view
  // ----------------------------------------------------------------

  function renderInspector() {
    if (!state.selectedSpanId) {
      $tabContent.innerHTML = emptyState('Select a span', 'Click a span in the Tree tab to inspect its details.');
      return;
    }

    const span = spanData.find(s => s.id === state.selectedSpanId);
    if (!span) {
      $tabContent.innerHTML = emptyState('Span not found', 'The selected span may have been removed.');
      return;
    }

    const dur = span.end_time ? formatDuration((span.end_time - span.start_time) * 1000) : 'running';

    // Find events for this span from state
    let events = [];
    if (state.isExport && state.exportData) {
      events = (state.exportData.events || []).filter(e => e.span_id === span.id);
    }

    const llmStart = events.find(e => e.type === 'llm_start');
    const llmEnd = events.find(e => e.type === 'llm_end');

    let messagesHtml = '';
    if (llmStart && llmStart.data && llmStart.data.messages) {
      messagesHtml = renderMessages(llmStart.data.messages);
    }

    let responseHtml = '';
    if (llmEnd && llmEnd.data) {
      responseHtml = `<div class="inspector-section">
        <div class="inspector-section-header">Response / Metrics</div>
        <div class="inspector-section-body">
          <table class="kv-table">
            ${llmEnd.data.latency_ms !== undefined ? `<tr><td class="kv-key">latency</td><td class="kv-val">${llmEnd.data.latency_ms.toFixed(1)}ms</td></tr>` : ''}
            ${llmEnd.data.prompt_tokens !== undefined ? `<tr><td class="kv-key">prompt tokens</td><td class="kv-val">${llmEnd.data.prompt_tokens}</td></tr>` : ''}
            ${llmEnd.data.completion_tokens !== undefined ? `<tr><td class="kv-key">completion tokens</td><td class="kv-val">${llmEnd.data.completion_tokens}</td></tr>` : ''}
            ${llmEnd.data.cost_usd !== undefined ? `<tr><td class="kv-key">cost</td><td class="kv-val">$${llmEnd.data.cost_usd.toFixed(6)}</td></tr>` : ''}
          </table>
        </div>
      </div>`;
    }

    $tabContent.innerHTML = `<div class="inspector fade-in">
      <div class="inspector-section">
        <div class="inspector-section-header">Span</div>
        <div class="inspector-section-body">
          <table class="kv-table">
            <tr><td class="kv-key">name</td><td class="kv-val mono">${esc(span.name)}</td></tr>
            <tr><td class="kv-key">type</td><td class="kv-val">${esc(span.type)}</td></tr>
            <tr><td class="kv-key">status</td><td class="kv-val">${esc(span.status)}</td></tr>
            <tr><td class="kv-key">duration</td><td class="kv-val mono">${esc(dur)}</td></tr>
            <tr><td class="kv-key">id</td><td class="kv-val mono muted">${esc(span.id)}</td></tr>
          </table>
        </div>
      </div>

      ${messagesHtml ? `
      <div class="inspector-section">
        <div class="inspector-section-header">Messages</div>
        <div class="inspector-section-body">
          <div class="messages-list">${messagesHtml}</div>
        </div>
      </div>` : ''}

      ${responseHtml}

      ${llmEnd && llmEnd.data && llmEnd.data.response ? `
      <div class="inspector-section">
        <div class="inspector-section-header">Raw Response</div>
        <div class="inspector-section-body">
          ${renderJSON(llmEnd.data.response)}
        </div>
      </div>` : ''}

      ${!state.isExport ? `
      <div style="padding-top:8px;display:flex;gap:8px;">
        <button class="btn btn-fork" onclick="window.__al.showForkModal('${esc(span.id)}')">Fork from here</button>
      </div>` : ''}
    </div>`;
  }

  function renderMessages(messages) {
    if (!Array.isArray(messages)) return '';
    return messages.map(m => {
      const role = m.role || 'unknown';
      const content = typeof m.content === 'string' ? m.content
        : JSON.stringify(m.content, null, 2);
      return `<div class="message-bubble role-${esc(role)}">
        <div class="message-role">${esc(role)}</div>
        <div>${esc(content)}</div>
      </div>`;
    }).join('');
  }

  // ----------------------------------------------------------------
  // JSON pretty-printer (pure JS + CSS, no lib)
  // ----------------------------------------------------------------

  function renderJSON(value) {
    const html = syntaxHighlightJSON(JSON.stringify(value, null, 2));
    return `<div class="json-viewer">${html}</div>`;
  }

  function syntaxHighlightJSON(str) {
    return str
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(
        /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
        function (match) {
          let cls = 'json-number';
          if (/^"/.test(match)) {
            cls = /:$/.test(match) ? 'json-key' : 'json-string';
          } else if (/true|false/.test(match)) {
            cls = 'json-bool';
          } else if (/null/.test(match)) {
            cls = 'json-null';
          }
          return `<span class="${cls}">${match}</span>`;
        }
      );
  }

  // ----------------------------------------------------------------
  // Fork modal
  // ----------------------------------------------------------------

  function showForkModal(spanId) {
    const span = spanData.find(s => s.id === spanId);
    if (!span) return;

    // Get messages from the LLM_START event if available
    let defaultMessages = '[]';
    if (state.isExport && state.exportData) {
      const ev = state.exportData.events.find(
        e => e.span_id === spanId && e.type === 'llm_start'
      );
      if (ev && ev.data && ev.data.messages) {
        defaultMessages = JSON.stringify(ev.data.messages, null, 2);
      }
    }

    document.getElementById('fork-span-id').value = spanId;
    document.getElementById('fork-messages').value = defaultMessages;
    document.getElementById('fork-modal').style.display = 'flex';
  }

  function hideForkModal() {
    document.getElementById('fork-modal').style.display = 'none';
  }

  async function submitFork() {
    const spanId = document.getElementById('fork-span-id').value;
    const raw = document.getElementById('fork-messages').value;
    let messages = null;
    try {
      messages = JSON.parse(raw);
    } catch (e) {
      alert('Invalid JSON in messages field.');
      return;
    }
    hideForkModal();
    await forkRun(spanId, messages);
  }

  // ----------------------------------------------------------------
  // View switching
  // ----------------------------------------------------------------

  function setView(view) {
    state.view = view;
    document.querySelectorAll('.tab').forEach(t => {
      t.classList.toggle('active', t.dataset.view === view);
    });
    renderContent();
  }

  // ----------------------------------------------------------------
  // Control buttons
  // ----------------------------------------------------------------

  function updateControlButtons() {
    if (!$btnPause) return;
    $btnPause.disabled = state.isPaused || !state.selectedRunId;
    $btnResume.disabled = !state.isPaused || !state.selectedRunId;
    $btnStep.disabled = !state.isPaused || !state.selectedRunId;
    $btnFork.disabled = !state.selectedRunId;
  }

  // ----------------------------------------------------------------
  // Keyboard shortcuts
  // ----------------------------------------------------------------

  function bindKeyboard() {
    document.addEventListener('keydown', (e) => {
      // Don't capture when typing in input / textarea
      if (e.target.matches('input, textarea, select')) return;

      switch (e.key) {
        case '/':
          e.preventDefault();
          $searchInput && $searchInput.focus();
          break;
        case ' ':
          e.preventDefault();
          if (state.isPaused) resumeRun(); else pauseRun();
          break;
        case 'f':
          if (!e.ctrlKey && !e.metaKey) {
            showForkModal(state.selectedSpanId);
          }
          break;
        case 'j': {
          const idx = spanData.findIndex(s => s.id === state.selectedSpanId);
          if (idx < spanData.length - 1) selectSpan(spanData[idx + 1].id);
          break;
        }
        case 'k': {
          const idx = spanData.findIndex(s => s.id === state.selectedSpanId);
          if (idx > 0) selectSpan(spanData[idx - 1].id);
          break;
        }
        case '1': setView('tree'); break;
        case '2': setView('timeline'); break;
        case '3': setView('inspector'); break;
        case 'Escape':
          hideForkModal();
          break;
      }
    });
  }

  // ----------------------------------------------------------------
  // Utility
  // ----------------------------------------------------------------

  function esc(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatDuration(ms) {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(2)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  }

  function emptyState(title, body) {
    return `<div class="empty-state">
      <div class="big-icon">🔍</div>
      <h3>${esc(title)}</h3>
      <p>${body}</p>
    </div>`;
  }

  // ----------------------------------------------------------------
  // Init
  // ----------------------------------------------------------------

  function init() {
    $runList = document.getElementById('run-list');
    $runSelect = document.getElementById('run-select');
    $tabContent = document.getElementById('tab-content');
    $statusConnDot = document.getElementById('conn-dot');
    $statusConnText = document.getElementById('conn-text');
    $statusEventCount = document.getElementById('event-count');
    $btnPause = document.getElementById('btn-pause');
    $btnResume = document.getElementById('btn-resume');
    $btnStep = document.getElementById('btn-step');
    $btnFork = document.getElementById('btn-fork');
    $searchInput = document.getElementById('search-input');

    // Tab click handlers
    document.querySelectorAll('.tab').forEach(t => {
      t.addEventListener('click', () => setView(t.dataset.view));
    });

    // Run select change
    if ($runSelect) {
      $runSelect.addEventListener('change', (e) => {
        if (e.target.value) selectRun(e.target.value);
      });
    }

    // Search
    if ($searchInput) {
      $searchInput.addEventListener('input', (e) => {
        state.search = e.target.value;
        renderRunList();
      });
    }

    // Control buttons
    if ($btnPause)  $btnPause.addEventListener('click', pauseRun);
    if ($btnResume) $btnResume.addEventListener('click', resumeRun);
    if ($btnStep)   $btnStep.addEventListener('click', stepRun);
    if ($btnFork)   $btnFork.addEventListener('click', () => showForkModal(state.selectedSpanId));

    bindKeyboard();
    updateControlButtons();

    // Start SSE and initial load
    if (!state.isExport) {
      connectSSE();
      loadRuns();
      // Poll for new runs every 5 seconds
      setInterval(loadRuns, 5000);
    } else {
      // Export mode: load from embedded data
      setConnStatus('disconnected');
      loadRuns();
    }

    // Expose fork modal API to inline onclick handlers
    window.__al = { showForkModal, hideForkModal, submitFork };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
