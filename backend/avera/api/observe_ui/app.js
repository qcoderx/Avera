const state = {
  records: [],
  lastId: 0,
  level: 'ALL',
  query: '',
  paused: false,
  source: null,
};

const elements = {
  connection: document.querySelector('#connection-state'),
  list: document.querySelector('#log-list'),
  viewport: document.querySelector('#log-viewport'),
  empty: document.querySelector('#empty-state'),
  search: document.querySelector('#search-input'),
  pause: document.querySelector('#pause-button'),
  clear: document.querySelector('#clear-button'),
  updated: document.querySelector('#last-updated'),
};

function setText(id, value) {
  const node = document.querySelector(`#${id}`);
  if (node) node.textContent = value;
}

function markHealth(name, healthy) {
  const node = document.querySelector(`[data-health="${name}"]`);
  if (!node) return;
  node.classList.toggle('healthy', healthy);
  node.classList.toggle('unhealthy', !healthy);
}

function duration(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

async function refreshStatus() {
  try {
    const response = await fetch('/observe/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error(`status ${response.status}`);
    const payload = await response.json();
    const health = payload.health;
    const kafka = payload.kafka;
    const healthy = health.status === 'ok';

    setText('api-state', healthy ? 'Operational' : 'Degraded');
    setText('api-detail', `${duration(payload.logs.uptime_seconds)} uptime`);
    markHealth('api', healthy);

    const storageHealthy = health.storage === 'postgresql' || health.storage === 'memory';
    setText('storage-state', storageHealthy ? 'Connected' : 'Unavailable');
    markHealth('storage', storageHealthy);

    const kafkaHealthy = kafka.state === 'connected' || kafka.state === 'ready';
    setText('kafka-state', kafkaHealthy ? 'Connected' : 'Disconnected');
    setText('kafka-detail', kafka.provider === 'aiven' ? `${kafka.topic} · SASL/TLS` : kafka.provider);
    markHealth('kafka', kafkaHealthy);

    const pending = health.outbox_pending ?? 0;
    setText('outbox-state', pending === 0 ? 'Drained' : `${pending} pending`);
    setText('outbox-detail', `${health.metrics.publish_failures ?? 0} publish failures`);
    markHealth('outbox', pending === 0 && (health.metrics.unpublished ?? 0) === 0);

    setText('started-at', new Date(payload.logs.started_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
    setText('retained-count', `${payload.logs.retained} / ${payload.logs.capacity}`);
  } catch (_) {
    setText('api-state', 'Unavailable');
    markHealth('api', false);
  }
}

function logTime(timestamp) {
  const date = new Date(timestamp);
  return date.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function sourceName(record) {
  if (record.kind === 'request') return 'http';
  return record.logger.replace(/^avera\.?/, '') || 'runtime';
}

function matches(record) {
  if (state.level !== 'ALL' && record.level !== state.level) return false;
  if (!state.query) return true;
  const haystack = `${record.logger} ${record.message} ${record.context?.path || ''}`.toLowerCase();
  return haystack.includes(state.query);
}

function cell(className, text) {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = text;
  return node;
}

function render() {
  const visible = state.records.filter(matches);
  const fragment = document.createDocumentFragment();
  for (const record of visible) {
    const row = document.createElement('li');
    row.className = 'log-row';
    row.dataset.level = record.level;
    row.append(
      cell('log-time', logTime(record.timestamp)),
      cell('log-level', record.level === 'WARNING' ? 'WARN' : record.level),
      cell('log-origin', sourceName(record)),
    );
    const message = cell('log-message', record.message);
    if (record.context?.path) message.append(cell('log-path', record.context.path));
    row.append(message, cell('log-duration', record.context?.duration_ms != null ? `${record.context.duration_ms} ms` : '—'));
    fragment.append(row);
  }
  elements.list.replaceChildren(fragment);
  elements.empty.hidden = visible.length !== 0;

  const counts = state.records.reduce((result, record) => {
    result[record.level] = (result[record.level] || 0) + 1;
    return result;
  }, {});
  setText('count-all', state.records.length);
  setText('count-info', counts.INFO || 0);
  setText('count-warning', counts.WARNING || 0);
  setText('count-error', (counts.ERROR || 0) + (counts.CRITICAL || 0));
}

function addRecord(record, { follow = true } = {}) {
  if (state.records.some((item) => item.id === record.id)) return;
  state.records.push(record);
  state.records.sort((a, b) => a.id - b.id);
  if (state.records.length > 500) state.records.splice(0, state.records.length - 500);
  state.lastId = Math.max(state.lastId, record.id);
  render();
  elements.updated.textContent = `Last signal ${logTime(record.timestamp)}`;
  if (follow) elements.viewport.scrollTop = elements.viewport.scrollHeight;
}

async function loadHistory() {
  const response = await fetch('/observe/api/logs?limit=200', { cache: 'no-store' });
  if (!response.ok) throw new Error(`logs ${response.status}`);
  const payload = await response.json();
  payload.records.forEach((record) => addRecord(record, { follow: false }));
  elements.viewport.scrollTop = elements.viewport.scrollHeight;
}

function setConnection(mode, label) {
  elements.connection.dataset.state = mode;
  elements.connection.querySelector('span').textContent = label;
}

function connect() {
  if (state.paused) return;
  state.source?.close();
  setConnection('connecting', 'Connecting to server');
  state.source = new EventSource(`/observe/api/stream?after=${state.lastId}`);
  state.source.addEventListener('open', () => setConnection('live', 'Live from API server'));
  state.source.addEventListener('log', (event) => {
    try { addRecord(JSON.parse(event.data)); } catch (_) { /* malformed records are ignored */ }
  });
  state.source.addEventListener('error', () => setConnection('offline', 'Reconnecting'));
}

document.querySelectorAll('[data-level]').forEach((button) => {
  button.addEventListener('click', () => {
    state.level = button.dataset.level;
    document.querySelectorAll('[data-level]').forEach((item) => item.classList.toggle('active', item === button));
    render();
  });
});

elements.search.addEventListener('input', () => {
  state.query = elements.search.value.trim().toLowerCase();
  render();
});

elements.pause.addEventListener('click', () => {
  state.paused = !state.paused;
  elements.pause.setAttribute('aria-pressed', String(state.paused));
  elements.pause.querySelector('span').textContent = state.paused ? 'Resume' : 'Pause';
  if (state.paused) {
    state.source?.close();
    setConnection('offline', 'Stream paused');
  } else {
    connect();
  }
});

elements.clear.addEventListener('click', () => {
  state.records = [];
  render();
  elements.updated.textContent = 'View cleared; waiting for the next signal';
});

document.addEventListener('keydown', (event) => {
  if (event.key === '/' && document.activeElement !== elements.search) {
    event.preventDefault();
    elements.search.focus();
  }
});

async function start() {
  await Promise.allSettled([refreshStatus(), loadHistory()]);
  connect();
  window.setInterval(refreshStatus, 5000);
}

start();
