import './styles.css';
import { fixture } from './fixtures.js';
import { ServiceMap, webglAvailable } from './three/serviceMap.js';
import { bindTilt, leaveView, revealView } from './motion.js';
import { api, apiBase, auth, openStream } from './api.js';
import { LiveStore, viewModel, pretty, naira } from './live.js';

const app = document.querySelector('#app');
const params = new URLSearchParams(window.location.search);

const state = {
  view: params.get('view') || 'overview',
  demoStep: Math.max(0, Math.min(Number(params.get('step') ?? 4), fixture.demoSteps.length - 1)),
  playing: false,
  demoOpen: false,
  approvals: Object.fromEntries(fixture.approvals.map((item) => [item.id, item.status])),
  activeApproval: fixture.approvals[0].id,
  loading: true,
  theme: 'light',
  memoryQuery: '',
  selectedNode: null,
  mapFullscreen: false,
  busy: null,
  tokenFormRole: null,
  consoleResult: null,
  remediation: null,
};

// Live connection: the API is the source of truth; the recorded fixture is the offline fallback.
const store = new LiveStore();
const live = { mode: params.get('mode') === 'fixture' ? 'fixture' : 'connecting', stream: 'closed', health: null, routes: null, error: null, closeStream: null, retry: null };
let D = fixture;

function isLive() {
  return live.mode === 'live';
}

function currentStep() {
  return isLive() ? D.step : state.demoStep;
}

const has3d = webglAvailable();
let serviceMap = null;
let lastView = null;

const statusLabels = {
  healthy: 'Healthy',
  watch: 'Watch',
  critical: 'Suspect zone',
  suspect: 'Hypothesis under test',
  cleared: 'Ruled out',
  proposed: 'Reroute target',
  patching: 'Patch in review',
};

function mapState() {
  return D.mapStates[currentStep()];
}

function nodeStatus(id) {
  return mapState().status[id] || 'healthy';
}

const navItems = [
  ['overview', 'System health', 'pulse', 'Live health, active incidents and agent activity.'],
  ['map', 'Service map', 'map', 'Trace services, dependencies and affected routes.'],
  ['hypotheses', 'Hypotheses', 'branch', 'Review ranked causes and supporting evidence.'],
  ['tests', 'Ghost tests', 'flask', 'Inspect safe experiments and validation results.'],
  ['impact', 'Customer impact', 'people', 'See affected customers, journeys and exposure.'],
  ['plan', 'Fix plan', 'plan', 'Review containment and recovery steps.'],
  ['approvals', 'Approvals', 'check', 'Approve guarded production actions.'],
  ['timeline', 'Incident timeline', 'clock', 'Follow every signal, decision and action.'],
  ['memory', 'Memory', 'archive', 'Search previous incidents and learned patterns.'],
  ['states', 'Agent states', 'spark', 'Preview agent modes, motion and loading states.'],
];

const iconPaths = {
  pulse: '<path d="M3 12h4l2.2-6 4.2 12 2.2-6H21"/>',
  map: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 11 10-4M7 13l10 4"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 5h3a3 3 0 0 1 3 3v7a3 3 0 0 0 3 3M14 9a3 3 0 0 1 3-3"/>',
  flask: '<path d="M9 3h6M10 3v6l-5 8.5A2.2 2.2 0 0 0 6.9 21h10.2a2.2 2.2 0 0 0 1.9-3.5L14 9V3M7.5 15h9"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0M16 4.5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4 5"/>',
  plan: '<path d="M6 3h12v18H6zM9 8h6M9 12h6M9 16h4"/>',
  check: '<path d="M4 12.5 9 17l11-11"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  archive: '<path d="M4 8h16v12H4zM3 4h18v4H3zM9 12h6"/>',
  spark: '<path d="m12 3 1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6L12 3ZM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z"/>',
  sun: '<circle cx="12" cy="12" r="3.5"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  reset: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5M4 4v4.5h4.5"/>',
  arrow: '<path d="m9 18 6-6-6-6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',
  focus: '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  shrink: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
  orbit: '<ellipse cx="12" cy="12" rx="9" ry="4"/><path d="M12 3a9 4 90 0 1 0 18"/><circle cx="12" cy="12" r="1.5"/>',
};

function icon(name, size = 20) {
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name]}</svg>`;
}

function formatNumber(value) {
  return new Intl.NumberFormat('en-NG').format(value);
}

function sparkline(values, failureAt = 0.68, width = 430, height = 96) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / (max - min || 1)) * (height - 10) - 5;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const marker = failureAt == null ? '' : `<line x1="${(failureAt * 100).toFixed(1)}%" y1="0" x2="${(failureAt * 100).toFixed(1)}%" y2="${height}" stroke="var(--coral)" stroke-dasharray="4 5"/><circle cx="${(failureAt * 100).toFixed(1)}%" cy="67" r="4" fill="var(--coral)" stroke="var(--paper)" stroke-width="3"/>`;
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Transaction throughput trend"><polygon points="0,${height} ${points} ${width},${height}" fill="var(--teal-pale)"/><polyline points="${points}" fill="none" stroke="var(--teal)" stroke-width="2" vector-effect="non-scaling-stroke"/>${marker}</svg>`;
}

function logo() {
  return `<div class="brand-mark"><img src="/assets/avera-logo.png" alt=""/><span>Avera</span></div>`;
}

function agentImage(mode = 'listening', agent = 'observability', alt = '') {
  const frameMap = {
    listening: ['listen-a', 'listen-b'],
    working: ['work-a', 'work-b'],
    ambient: ['idle', 'wave-a', 'wave-b', 'scratch', 'think', 'listen-a'],
    result: ['result'],
    bug: ['bug'],
  };
  const frames = frameMap[mode] || [mode];
  return `<div class="agent-image agent-${agent} mode-${mode} frames-${frames.length}">${frames.map((frame, index) => `<img class="agent-frame frame-${index}" src="/assets/agents/${agent}/agent-${frame}.png" alt="${index === 0 ? alt : ''}" ${index > 0 ? 'aria-hidden="true"' : ''}/>`).join('')}</div>`;
}

function shell(content) {
  const current = navItems.find(([id]) => id === state.view) || navItems[0];
  const step = D.demoSteps[currentStep()];
  return `
    <div class="app-shell" data-theme="${state.theme}">
      <aside class="sidebar">
        ${logo()}
        <nav class="primary-nav" aria-label="Primary navigation">
          ${navItems.map(([id, label, glyph, description]) => `<button class="nav-item ${state.view === id ? 'active' : ''}" data-view="${id}" aria-label="${label}" aria-describedby="nav-tip-${id}" aria-current="${state.view === id ? 'page' : 'false'}">${icon(glyph)}<span class="nav-tooltip" id="nav-tip-${id}" role="tooltip"><strong>${label}</strong><small>${description}</small></span>${id === 'approvals' && pendingCount() ? `<em>${pendingCount()}</em>` : ''}</button>`).join('')}
        </nav>
        <div class="sidebar-foot">
          <button class="nav-item" data-action="theme" aria-label="Toggle color theme" aria-describedby="nav-tip-display">${icon('sun')}<span class="nav-tooltip" id="nav-tip-display" role="tooltip"><strong>Display</strong><small>Switch between light and dark mode.</small></span></button>
          <button class="operator"><span>PO</span><i><strong>Platinum</strong><small>Demo operator</small></i></button>
        </div>
      </aside>
      <div class="workspace">
        <header class="topbar">
          <div class="breadcrumb"><span>Incident command centre</span>${icon('arrow', 14)}<strong>${current[1]}</strong></div>
          <div class="top-actions">
            ${connectionPill()}
            <button class="demo-trigger" data-action="demo-panel"><span class="demo-dot"></span><span class="demo-step-no">${String(currentStep() + 1).padStart(2, '0')}</span>${step.label}<kbd>D</kbd></button>
          </div>
        </header>
        <main class="main-content view-${state.view} ${state.loading ? 'is-loading' : ''}" id="main-content">${content}</main>
      </div>
      ${demoPanel()}
    </div>`;
}

function connectionPill() {
  if (isLive()) {
    const ok = live.stream === 'open';
    return `<div class="stream-status ${ok ? '' : 'warn'}" title="${apiBase}"><i></i> Live API <span>${ok ? 'streaming' : 'reconnecting'}</span></div>`;
  }
  if (live.mode === 'connecting') return '<div class="stream-status warn"><i></i> Connecting <span>API</span></div>';
  return `<button class="stream-status offline" data-action="reconnect" title="${live.error || ''}"><i></i> Recorded fixture <span>retry live</span></button>`;
}

function pendingCount() {
  if (isLive()) return D.approvals.filter((item) => item.status === 'pending').length;
  return Object.values(state.approvals).filter((status) => status === 'pending').length;
}

function pageHeader(title, description, extra = '') {
  return `<div class="page-header"><div><h1>${title}</h1><p>${description}</p></div>${extra}</div>`;
}

function demoPanel() {
  const step = D.demoSteps[currentStep()];
  if (isLive()) {
    const h = live.health;
    const busy = (key) => (state.busy === key ? 'disabled aria-busy="true"' : state.busy ? 'disabled' : '');
    return `<aside class="demo-panel ${state.demoOpen ? 'open' : ''}" aria-label="Demo controller">
      <div class="demo-panel-head"><div><span>Live demo · ${apiBase}</span><strong>${step.title}</strong></div><button data-action="demo-panel" aria-label="Close demo controller">${icon('close')}</button></div>
      <div class="demo-track live">${D.demoSteps.map((item, index) => `<div class="demo-stage ${index <= currentStep() ? 'complete' : ''} ${index === currentStep() ? 'current' : ''}"><i>${index + 1}</i><span>${item.label}</span></div>`).join('')}</div>
      <div class="demo-transport live">
        <button data-live="start" ${busy('start')}>${icon('play')} Start baseline</button>
        <button class="transport-primary" data-live="inject" ${busy('inject')}>${icon('spark')} Inject failure</button>
        <button data-live="reset" ${busy('reset')}>${icon('reset')} Reset</button>
      </div>
      <div class="demo-transport live secondary">
        <button data-live="investigate" ${busy('investigate')}>${icon('search')} Investigate now</button>
        <button data-live="replay" ${busy('replay')}>${icon('clock')} Replay recorded stream</button>
      </div>
      <dl class="health-grid">
        <div><dt>Observability</dt><dd class="${h?.observability?.state === 'degraded' ? 'bad' : ''}">${h?.observability?.state || '—'}${h?.observability?.model_configured === false ? ' · no model' : ''}</dd></div>
        <div><dt>Storage · bus</dt><dd>${h ? `${h.storage} · ${h.event_bus}` : '—'}</dd></div>
        <div><dt>Records streamed</dt><dd>${store.lastSequence.toLocaleString('en-NG')}</dd></div>
        <div><dt>Outbox pending</dt><dd>${h?.outbox_pending ?? '—'}</dd></div>
      </dl>
      ${h?.observability?.errors?.length ? `<p class="health-error">${h.observability.errors.join(', ')}</p>` : ''}
    </aside>`;
  }
  return `<aside class="demo-panel ${state.demoOpen ? 'open' : ''}" aria-label="Demo controller">
    <div class="demo-panel-head"><div><span>Fixture replay</span><strong>${step.title}</strong></div><button data-action="demo-panel" aria-label="Close demo controller">${icon('close')}</button></div>
    <div class="demo-track">${fixture.demoSteps.map((item, index) => `<button class="demo-stage ${index <= state.demoStep ? 'complete' : ''} ${index === state.demoStep ? 'current' : ''}" data-step="${index}"><i>${index + 1}</i><span>${item.label}</span></button>`).join('')}</div>
    <div class="demo-transport"><button data-action="reset">${icon('reset')} Reset</button><button class="transport-primary" data-action="play">${icon(state.playing ? 'pause' : 'play')} ${state.playing ? 'Pause' : 'Play sequence'}</button><button data-action="next">Next ${icon('arrow')}</button></div>
    <p>${live.mode === 'connecting' ? 'Connecting to the live API…' : `The live API at <code>${apiBase}</code> is unreachable${live.error ? ` (${live.error})` : ''}, so this is the recorded incident. <button class="text-button inline" data-action="reconnect">Retry live</button>`}</p>
  </aside>`;
}

function render() {
  D = isLive() ? viewModel(store, live.routes) : fixture;
  const views = {
    overview: overviewView,
    map: mapView,
    hypotheses: hypothesesView,
    tests: testsView,
    impact: impactView,
    plan: planView,
    approvals: approvalsView,
    timeline: timelineView,
    memory: memoryView,
    states: statesView,
  };
  const content = state.loading ? loadingView() : views[state.view]();
  app.innerHTML = shell(content);
  bindEvents();
  const entering = !state.loading && lastView !== state.view;
  mountMap(entering);
  if (!state.loading) {
    lastView = state.view;
    if (entering) revealView(document.querySelector('#main-content'));
    bindTilt(app);
  }
}

let mapSignature = '';

function mountMap(intro) {
  const slot = document.querySelector('[data-map-slot]');
  if (!slot) {
    serviceMap?.sleep();
    return;
  }
  if (!serviceMap) {
    serviceMap = new ServiceMap(fixture, {
      onSelect: (id) => {
        if (state.view !== 'map' || state.selectedNode === id) return;
        state.selectedNode = id;
        updateNodeCard();
      },
    });
  }
  serviceMap.setTheme(state.theme);
  serviceMap.setData(D);
  const signature = JSON.stringify(mapState());
  serviceMap.setStep(currentStep(), signature !== mapSignature);
  mapSignature = signature;
  serviceMap.mount(slot, { mode: slot.dataset.mapSlot, intro });
}

function overviewView() {
  const step = D.demoSteps[currentStep()];
  const healthy = currentStep() < 1 || (!isLive() && currentStep() < 2);
  const top = D.hypotheses.find((item) => item.state === 'validated') || D.hypotheses[0];
  const orbitValue = healthy ? (isLive() ? Math.min(...D.health.services.map((item) => item.value)) : 99.98) : top?.confidence ?? 0;
  const firstSignal = isLive() ? D.incident.firstSignal : '10:42:08';
  const detected = isLive() ? D.incident.detected : '9m 12s';
  const validated = top?.state === 'validated' && currentStep() >= 3 ? `${top.confidence}%` : currentStep() >= 2 ? 'Testing' : '—';
  const containment = currentStep() >= 4 && D.fixPlan ? D.fixPlan.containmentEta : currentStep() >= 2 ? 'Calculating' : '—';
  const transfers = isLive() ? `${formatNumber(D.health.transferCount || 0)} <small>transfers</small>` : '2,841 <small>txn/min</small>';
  const signals = isLive() ? D.signals : D.signals.slice(0, currentStep() < 2 ? 2 : 7);
  return `
    ${pageHeader('System health', 'Live operational state across channels, services and active incidents.', `<div class="incident-chip ${healthy ? 'nominal' : ''}"><i></i>${healthy ? D.health.nominalLabel : `${D.incident.severity} · ${D.incident.id}`}</div>`)}
    <section class="overview-theatre" data-reveal>
      <div class="health-stage">
        <div class="stage-topline"><span>${healthy ? 'Bank-wide availability' : 'Active incident'}</span><time>${healthy ? 'Updated now' : `Open ${D.incident.elapsed}`}</time></div>
        <div class="incident-title">
          <div class="health-orbit ${healthy ? 'healthy' : ''}"><em class="ring r1"><b></b></em><em class="ring r2"><b></b></em><em class="ring r3"><b></b></em><div class="orbit-core"><span><span data-count="${orbitValue}" data-decimals="${healthy ? 2 : 0}">${orbitValue}</span><small>%</small></span></div></div>
          <div><h2>${healthy ? D.health.nominalLabel : D.incident.title}</h2><p>${healthy ? (isLive() && !store.run.started ? 'The simulated bank is idle. Open the demo controls (D) to start a healthy baseline.' : 'Synthetic traffic is flowing normally across every monitored route.') : 'Avera connected customer reports, retries, channel switching and route-level timeouts into one incident.'}</p></div>
        </div>
        <div class="stage-metrics">
          <div><span>First signal</span><strong>${firstSignal}</strong></div>
          <div><span>Detected in</span><strong>${detected}</strong></div>
          <div><span>Validated cause</span><strong>${validated}</strong></div>
          <div><span>Est. containment</span><strong>${containment}</strong></div>
        </div>
        <div class="throughput">
          <div class="chart-label"><span>Transaction throughput</span><strong>${transfers}</strong></div>
          ${sparkline(D.health.throughput, isLive() ? D.health.failureAt : 0.68)}
          ${!isLive() || D.health.failureAt != null ? `<span class="failure-marker" style="left:${((isLive() ? D.health.failureAt : 0.64) * 100).toFixed(1)}%">Failure begins</span>` : ''}
        </div>
      </div>
      <aside class="signal-rail">
        <div class="rail-head"><span>Live incident feed</span><i>${isLive() ? (live.stream === 'open' ? 'Streaming' : 'Reconnecting') : 'Recorded'}</i></div>
        <div class="signal-list" data-stagger>${signals.slice().reverse().map((signal, index) => `<article class="signal ${index === 0 ? 'latest' : ''}"><time>${signal.time}</time><i class="tone-${signal.tone}"></i><div><span>${signal.channel}</span><p>${signal.text}</p></div></article>`).join('')}</div>
        <button class="text-button" data-view="timeline">Open incident timeline ${icon('arrow', 16)}</button>
      </aside>
    </section>
    <section class="overview-map" data-reveal>
      <div class="overview-map-head">
        <div><span>Live topology</span><strong>${step.title}</strong></div>
        ${mapLegend()}
        <button class="text-button" data-view="map">Open service map ${icon('arrow', 16)}</button>
      </div>
      ${has3d ? '<div class="map3d-slot" data-map-slot="compact"></div>' : `<div class="topology compact">${flatTopology()}</div>`}
    </section>
    <section class="service-strip" aria-label="Service health" data-reveal data-stagger>
      ${D.health.services.map((service) => `<div class="service-reading"><i class="${service.state}"></i><span>${service.name}</span><strong>${service.value}%</strong><small>${service.delta}</small></div>`).join('')}
    </section>
    <section class="agent-dock" data-reveal>
      <div class="dock-copy"><span>Agent activity</span><p>Four accountable agents. Every handoff carries evidence.</p></div>
      ${D.agents.map((agent) => `<button class="agent-mini" data-view="states" data-tilt="7" style="--agent:${agent.color}">${agentImage(agent.status, agent.id, `${agent.name} agent`)}<div><span>${agent.name}</span><strong>${agent.state}</strong><small>${agent.detail}</small></div></button>`).join('')}
    </section>`;
}

function mapLegend() {
  return `<div class="legend"><span><i class="healthy"></i>Healthy</span><span><i class="watch"></i>Watch</span><span><i class="critical"></i>Suspect</span><span><i class="hypothesis"></i>Hypothesis</span><span><i class="probe"></i>Ghost probe</span></div>`;
}

function flatTopology() {
  const byId = Object.fromEntries(fixture.nodes.map((node) => [node.id, node]));
  const map = mapState();
  return `
    <div class="map-labels">${fixture.lanes.filter((lane) => lane.nodes.length).map((lane) => `<span>${lane.label}</span>`).join('')}</div>
    <svg class="map-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      ${fixture.edges.filter(([from, to, kind]) => byId[from] && byId[to] && (kind !== 'reroute' || map.reroute !== 'hidden')).map(([from, to, kind]) => { const a = byId[from], b = byId[to]; const suspect = from === 'gb' && to === 'bankx' && map.bug === 'alert'; return `<path class="${suspect ? 'suspect-edge' : ''} ${kind === 'reroute' ? `reroute-edge ${map.reroute}` : ''}" d="M ${a.x} ${a.y} C ${(a.x+b.x)/2} ${a.y}, ${(a.x+b.x)/2} ${b.y}, ${b.x} ${b.y}"/>`; }).join('')}
    </svg>
    ${fixture.nodes.map((node) => { const tag = map.hypotheses[node.id]?.id || map.tags[node.id]; return `<div class="map-node ${nodeStatus(node.id)}" style="--x:${node.x}%;--y:${node.y}%"><i></i><span>${node.name}</span><small>${map.metrics[node.id] || node.metric}</small>${tag ? `<b>${tag}</b>` : ''}</div>`; }).join('')}`;
}

function agentForLabel(label) {
  return { Observability: 'observability', 'Ghost Test': 'ghost', 'Blast Radius': 'blast', Resolution: 'resolution' }[label] || 'observability';
}

function crowdCard() {
  const map = mapState();
  const { affected, complained, transactions, exposure, silent } = D.impact;
  const found = map.customers.affected >= 1 || map.customers.notified;
  const facts = [
    ['Customers affected', found ? formatNumber(affected) : 'Not yet known'],
    ['Complained', isLive() ? formatNumber(complained) : found ? formatNumber(complained) : map.customers.complainers ? 'Arriving' : 'None yet'],
    ['Never complained', found ? formatNumber(isLive() ? silent : affected - complained) : 'Not yet known'],
    ['Affected transactions', found ? formatNumber(transactions) : 'Counting'],
    ['Estimated exposure', found ? exposure : 'Counting'],
  ];
  return `<div class="node-card-head"><div class="node-glyph status-${found ? 'critical' : 'watch'}"><i></i></div><div><span>Customers</span><strong>Blast radius</strong></div><button data-map-action="reset" aria-label="Close details">${icon('close', 16)}</button></div>
    <h3>${map.customers.caption}</h3>
    <p>Each figure is one customer. The failure spreads outward from the epicentre; raised figures are the ones who complained.</p>
    <dl>${facts.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>`;
}

function nodeCard() {
  if (state.selectedNode === 'crowd') return crowdCard();
  const node = D.nodes.find((item) => item.id === state.selectedNode);
  if (!node) return '';
  const map = mapState();
  const status = nodeStatus(node.id);
  const lane = D.lanes.find((item) => item.nodes.includes(node.id));
  const hyp = map.hypotheses[node.id];
  const hypothesis = hyp && D.hypotheses.find((item) => item.id === hyp.id);
  const hypState = hyp && { open: 'Open', testing: 'Testing in twin', proven: `Proven · ${hyp.score}%`, ruled: `Ruled out · ${hyp.score}%` }[hyp.state];
  const facts = [['Current reading', map.metrics[node.id] || node.metric]];
  if (hypothesis) facts.push([hyp.id, hypState], ['Routed to', hypothesis.owner], ['Disproof condition', hypothesis.disproof]);
  if (map.tags[node.id]) facts.push(['Status', map.tags[node.id]]);
  return `<div class="node-card-head"><div class="node-glyph status-${status}"><i></i></div><div><span>${lane.label}</span><strong>${node.name}</strong></div><button data-map-action="reset" aria-label="Close component details">${icon('close', 16)}</button></div>
    <h3>${statusLabels[status]}</h3>
    <p>${hypothesis ? hypothesis.claim : status === 'healthy' ? 'No abnormal errors, latency or volume changes on this component.' : map.insight.body}</p>
    <dl>${facts.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>`;
}

function updateNodeCard() {
  const card = document.querySelector('.stage-node');
  if (!card) return;
  card.innerHTML = nodeCard();
  card.classList.toggle('open', Boolean(state.selectedNode));
  card.querySelector('[data-map-action="reset"]')?.addEventListener('click', () => serviceMap?.resetView());
}

function mapView() {
  const step = D.demoSteps[currentStep()];
  const map = mapState();
  const insight = map.insight;
  const last = D.demoSteps.length - 1;
  const playLabel = isLive() ? 'Run the live incident' : state.playing ? 'Pause story' : 'Play story';
  return `
    <h1 class="visually-hidden">Service map</h1>
    <section class="map-stage ${has3d ? '' : 'flat'}" data-reveal>
      <div class="stage-top">
        <article class="stage-story">
          <div class="story-agent">${agentImage(insight.agent, agentForLabel(insight.label), `${insight.label} agent`)}</div>
          <div class="story-copy">
            <span class="story-kicker"><i>${String(currentStep() + 1).padStart(2, '0')}</i>${step.time} · ${insight.label}${isLive() ? '<b class="live-tag">Live</b>' : ''}</span>
            <h2>${insight.title}</h2>
            <p>${insight.body}</p>
          </div>
        </article>
        <ol class="stage-pipeline" aria-label="Avera agent pipeline">${D.pipeline.map((stage, index) => `<li class="${index < map.stage ? 'done' : index === map.stage ? 'active' : ''}"><i>${index < map.stage ? '✓' : index + 1}</i><div><strong>${stage.label}</strong><span>${stage.agent}</span></div></li>`).join('')}</ol>
      </div>
      <div class="stage-canvas">
        ${has3d ? '<div class="map3d-slot" data-map-slot="full"></div>' : `<div class="topology">${flatTopology()}</div>`}
        <aside class="stage-node ${state.selectedNode ? 'open' : ''}" aria-live="polite">${nodeCard()}</aside>
      </div>
      <div class="stage-bottom">
        <button class="stage-play" ${isLive() ? 'data-live="run"' : 'data-action="play"'} aria-label="${playLabel}" title="${playLabel}" ${state.busy ? 'disabled' : ''}>${icon(!isLive() && state.playing ? 'pause' : 'play', 18)}</button>
        <ol class="stage-timeline ${isLive() ? 'live' : ''}" style="--progress:${currentStep() / last}">${D.demoSteps.map((item, index) => `<li><button class="${index < currentStep() ? 'done' : ''} ${index === currentStep() ? 'current' : ''}" ${isLive() ? 'disabled' : `data-step="${index}"`}><time>${item.time}</time><span>${item.label}</span></button></li>`).join('')}</ol>
        ${has3d ? `<div class="map-controls"><button data-map-action="focus">${icon('focus', 16)} Zoom to suspect</button><button data-map-action="reset">${icon('reset', 16)} Reset view</button>${fullscreenButton()}</div>` : ''}
        ${mapLegend()}
      </div>
    </section>`;
}

function emptyState(title, body, action = '') {
  return `<section class="empty-state" data-reveal>${agentImage('listening', 'observability', '')}<div><h2>${title}</h2><p>${body}</p>${action}</div></section>`;
}

function liveStartAction() {
  return isLive() ? `<button class="primary-button" data-live="run" ${state.busy ? 'disabled' : ''}>${icon('play', 17)} Run the live incident</button>` : '';
}

function hypothesesView() {
  const header = pageHeader('Hypotheses', 'Competing explanations ranked by evidence and Ghost Test results.', `<div class="threshold"><span>Handoff threshold</span><strong>85%</strong></div>`);
  if (!D.hypotheses.length) return `${header}${emptyState('No hypotheses yet', 'The Observability Agent proposes competing explanations once an incident opens.', liveStartAction())}`;
  const validated = D.hypotheses.find((item) => item.state === 'validated');
  const lead = validated || D.hypotheses[0];
  const leadTest = isLive() ? store.run.tests.get(lead.realId) : null;
  const decision = isLive()
    ? (validated
      ? { title: `${validated.id} survives every disproof check`, body: leadTest ? `Failure reproduced ${leadTest.reproduced} of ${leadTest.runs} times through ${validated.zone}${leadTest.control ? ` and ${leadTest.control.reproduced} of ${leadTest.control.runs} through the ${pretty(leadTest.control.route)} control` : ''}.` : '' }
      : { title: 'No hypothesis has cleared 85% yet', body: 'Ghost Test keeps sending weak candidates back to the Observability Agent with their disproof results.' })
    : { title: 'H1 survives every disproof check', body: 'Failure reproduced 5 of 5 times through Gateway B and 0 of 5 through the healthy control.' };
  return `
    ${header}
    <section class="hypothesis-layout">
      <div class="hypothesis-board">
        ${D.hypotheses.map((hypothesis) => `<article class="hypothesis-row ${hypothesis.state}" data-reveal>
          <div class="hypothesis-id"><span>${hypothesis.id}</span><i>${hypothesis.rank}</i></div>
          <div class="hypothesis-claim"><div class="row-meta"><span>${hypothesis.zone}</span><em>${hypothesis.state}</em></div><h2>${hypothesis.claim}</h2><p>Routed to ${hypothesis.owner}</p></div>
          <div class="confidence"><strong><span data-count="${hypothesis.confidence}">${hypothesis.confidence}</span><small>%</small></strong><div><i data-bar="${hypothesis.confidence}" style="width:${hypothesis.confidence}%"></i><b style="left:85%"></b></div><span>${hypothesis.confidence >= 85 ? 'Threshold cleared' : 'Below threshold'}</span></div>
          <details><summary>Evidence and disproof ${icon('arrow', 16)}</summary><div class="hypothesis-detail"><div><span>Evidence for</span>${hypothesis.evidence.map((item) => `<p>${item}</p>`).join('') || '<p>No supporting references.</p>'}</div><div><span>Evidence against</span><p>${hypothesis.against}</p></div><div><span>Disproof condition</span><p>${hypothesis.disproof}</p></div></div>${isLive() ? `<div class="hypothesis-actions"><button class="secondary-button compact" data-live="retest" data-hypothesis="${hypothesis.realId}" ${state.busy ? 'disabled' : ''}>${icon('flask', 16)} Re-test with Ghost Test</button></div>` : ''}</details>
        </article>`).join('')}
      </div>
      <aside class="decision-column" data-reveal data-tilt="4">
        ${agentImage(validated || !isLive() ? 'result' : 'working', 'ghost', 'Ghost Test agent')}
        <span>Ghost Test decision</span>
        <h2>${decision.title}</h2>
        <p>${decision.body}</p>
        <div class="decision-score"><span>Confidence</span><strong class="num-3d"><span data-count="${lead.confidence}">${lead.confidence}</span>%</strong></div>
        <button class="primary-button" data-view="tests">Open test evidence ${icon('arrow', 17)}</button>
      </aside>
    </section>
    ${consoleResult('hypotheses')}`;
}

function traceBars(values) {
  const max = Math.max(1, ...values);
  return `<div class="trace-bars">${values.map((value, index) => `<i style="height:${Math.max(8, value / max * 100)}%" class="${index === values.length - 1 && value > 500 ? 'fail' : value >= 1000 ? 'fail' : ''}" title="${value} ms"></i>`).join('')}</div>`;
}

function consoleResult(scope) {
  const result = state.consoleResult;
  if (!result || result.scope !== scope) return '';
  return `<section class="console-card ${result.ok ? 'ok' : 'bad'}" data-reveal><div class="console-head"><i>${icon(result.ok ? 'check' : 'close', 16)}</i><strong>${result.title}</strong><button data-action="dismiss-console" aria-label="Dismiss">${icon('close', 14)}</button></div>${result.body}</section>`;
}

function testsView() {
  const header = pageHeader('Ghost tests', 'Synthetic-only validation in the isolated digital twin.', `<div class="sandbox-chip"><i></i>Sandbox isolated</div>`);
  if (!D.tests.length) return `${header}${emptyState('No Ghost Tests have run yet', 'Each hypothesis is tested against the isolated digital twin once the Observability Agent routes it.', liveStartAction())}`;
  const summary = isLive() ? D.testSummary : { runs: 15, reproduced: 5, plans: 5, controls: 3, elapsed: '3:32' };
  const validated = isLive() ? D.hypotheses.find((item) => item.state === 'validated') : null;
  const busy = (key) => (state.busy === key ? 'disabled aria-busy="true"' : state.busy ? 'disabled' : '');
  return `
    ${header}
    <section class="test-summary" data-reveal>
      <div class="test-agent">${agentImage(validated || !isLive() ? 'working' : 'listening', 'ghost', 'Ghost Test agent')}<div><span>${validated || !isLive() ? 'Validation complete' : 'Validation running'}</span><h2>${summary.runs} test runs separated the cause from the decoys.</h2><p>No customer data or funds entered the test environment.</p></div></div>
      <div class="test-count"><span>Test runs</span><strong data-count="${summary.runs}">${summary.runs}</strong><small>${summary.plans} plans · ${summary.controls} controls</small></div>
      <div class="test-count"><span>Reproduced</span><strong data-count="${summary.reproduced}">${summary.reproduced}</strong><small>${validated ? `${validated.zone} only` : isLive() ? 'No validated route yet' : 'Gateway B route only'}</small></div>
      <div class="test-count"><span>Elapsed</span><strong>${summary.elapsed}</strong><small>Digital twin time</small></div>
    </section>
    <section class="test-table-wrap" data-reveal data-stagger>
      <div class="table-head"><span>Test</span><span>Plan</span><span>Trace latency</span><span>Runs</span><span>Result</span></div>
      ${D.tests.map((test) => `<article class="test-row"><div><strong>${test.id}</strong><span>${test.hypothesis}</span></div><div><strong>${test.name}</strong><span>${test.duration}</span></div><div>${traceBars(test.trace)}<small>${test.peak ?? Math.max(...test.trace)} ms peak</small></div><div><strong>${test.runs}</strong><span>reproduced</span></div><div><em class="verdict-${test.verdict}">${test.result}</em></div></article>`).join('')}
    </section>
    ${isLive() ? `<section class="twin-actions" data-reveal>
      <div><strong>Digital twin console</strong><span>Every action runs on detached copies of the simulated bank. The live route never changes here.</span></div>
      <button class="secondary-button" data-live="twin" ${busy('twin')}>${icon('branch', 17)} Simulate Bank X via Gateway C</button>
      <button class="secondary-button" data-live="rerun" ${validated ? '' : 'disabled'} ${busy('rerun')}>${icon('reset', 17)} Rerun ${validated ? validated.id : 'validated'} test</button>
    </section>${consoleResult('tests')}` : ''}
    <div class="safety-note"><i>${icon('check')}</i><div><strong>Verification boundary held</strong><span>Tests ran with synthetic identities and zero-value transactions in a copy of the service graph.</span></div></div>`;
}

function impactView() {
  const i = D.impact;
  const header = pageHeader('Customer impact', 'Affected customers, transactions and silent behavioural signals.', `<div class="export-link">${isLive() ? (i.snapshot && i.snapshot !== '—' ? `Snapshot at ${i.snapshot}` : 'Waiting for Blast Radius') : 'Snapshot at 10:57:06'}</div>`);
  if (isLive() && !i.affected) return `${header}${emptyState('Blast Radius has not run yet', 'Avera searches every transaction for the validated failure fingerprint, including customers who never complained.', liveStartAction())}`;
  return `
    ${header}
    <section class="impact-reveal" data-reveal>
      <div class="impact-copy"><span>Blast Radius result</span><h2>${formatNumber(i.silent)} customers were found before they had to complain.</h2><p>The validated fingerprint was matched across live and historical transaction records.</p></div>
      <div class="impact-numbers"><div class="complained" data-tilt="8"><strong class="num-3d" data-count="${i.complained}">${formatNumber(i.complained)}</strong><span>complained</span></div><div class="impact-connector"><i></i><b></b><small>Avera found</small></div><div class="affected" data-tilt="8"><strong class="num-3d" data-count="${i.affected}">${formatNumber(i.affected)}</strong><span>affected</span></div></div>
      <div class="silent-number"><span>Silent customers</span><strong data-count="${i.silent}">${formatNumber(i.silent)}</strong><small>${i.prevention}% complaint prevention opportunity</small></div>
    </section>
    <section class="impact-detail" data-reveal>
      <div class="impact-stats"><div><span>Affected transactions</span><strong>${formatNumber(i.transactions)}</strong></div><div><span>Estimated exposure</span><strong>${i.exposure}</strong></div><div><span>First affected</span><strong>${isLive() ? i.firstAffected : '10:42 AM'}</strong></div></div>
      <div class="segment-list"><div class="section-title"><span>${i.segmentsTitle || 'Customer segments'}</span><small>${formatNumber(i.affected)} total</small></div>${i.segments.map((segment) => `<div class="segment"><span>${segment.label}</span><div><i data-bar="${Math.min(100, segment.share)}" style="width:${Math.min(100, segment.share)}%"></i></div><strong>${formatNumber(segment.value)}</strong><small>${segment.share}%</small></div>`).join('')}</div>
      <div class="impact-agent">${agentImage('result', 'blast', 'Blast Radius agent')}<div><span>Blast Radius</span><strong>Search complete</strong><p>Every matching customer has been tokenised and added to the notification audience.</p><button class="text-button" data-view="approvals">Review customer message ${icon('arrow', 16)}</button></div></div>
    </section>`;
}

function planView() {
  const plan = D.fixPlan;
  const header = (extra) => pageHeader('Fix plan', 'Containment, permanent resolution, rollback and verification.', extra);
  if (!plan) return `${header('')}${emptyState('No fix plan yet', 'The Resolution Agent drafts containment, permanent fix and reconciliation steps once Ghost Test validates a cause.', liveStartAction())}`;
  const remediation = state.remediation && state.remediation.incident_id === D.incident.id ? state.remediation : null;
  const anyToken = auth.get('engineer') || auth.get('customer_dispute');
  return `
    ${header(plan.simulationSuccess != null ? `<div class="plan-confidence"><span>Simulation success</span><strong>${plan.simulationSuccess}%</strong></div>` : '')}
    <section class="plan-hero" data-reveal>
      <div><span>Validated cause</span><h2>${isLive() ? plan.cause : 'Gateway B acknowledgement handling fails for Bank X transfers.'}</h2><p>${isLive() ? `Owners: ${plan.owners.join(', ')}.` : 'Contain the route first, then repair the connector and reconcile uncertain transaction states.'}</p></div>
      <div class="eta-band"><div><span>Containment</span><strong>${plan.containmentEta}</strong></div><i></i><div><span>Permanent fix</span><strong>${plan.fullEta}</strong></div><i></i><div><span>${plan.reconciliationLabel || 'Reconciliation'}</span><strong>${plan.reconciliationEta}</strong></div></div>
    </section>
    <section class="plan-body" data-reveal>
      <div class="steps" data-stagger><div class="section-title"><span>Execution plan</span><small>Human approval required before routing, messaging and reconciliation</small></div>${plan.steps.map((step, index) => `<article class="plan-step"><span>${step.n}</span><div><small>${step.owner}</small><h3>${step.title}</h3><p>${step.detail}</p></div><em class="step-${step.status}">${step.status}</em>${index < plan.steps.length - 1 ? '<i></i>' : ''}</article>`).join('')}</div>
      <aside class="rollback" data-tilt="3"><div class="rollback-head">${icon('reset')}<span>Rollback condition</span></div><p>${plan.rollback}</p><div class="guardrail"><strong>No money movement</strong><span>Avera may prepare reconciliation batches. A human executes every financial action.</span></div><button class="primary-button" data-view="approvals">Review approvals ${icon('arrow', 17)}</button></aside>
    </section>
    ${isLive() ? `<section class="remediation-panel" data-reveal>
      <div class="section-title"><span>Signed remediation record</span><small>GET /remediation/${D.incident.id}</small></div>
      ${remediation ? `<div class="remediation-grid">
        <div><span>Case status</span><strong>${pretty(remediation.status)}</strong></div>
        <div><span>Playbook</span><strong>${pretty(remediation.plan.playbook)}</strong></div>
        <div><span>Cause confidence</span><strong>${Math.round(remediation.plan.cause_confidence * 100)}%</strong></div>
        <div><span>Containment estimate</span><strong>${remediation.plan.eta_containment.minutes} min · ${remediation.plan.eta_containment.confidence}</strong></div>
        <div><span>Twin simulation</span><strong>${remediation.plan.simulation ? `${(remediation.plan.simulation.success_rate * 100).toFixed(1)}% of ${remediation.plan.simulation.samples}` : '—'}</strong></div>
        <div><span>Projected if uncontained</span><strong>${formatNumber(remediation.blast_radius.projected_count || 0)} customers</strong></div>
      </div>
      <ol class="remediation-tracks">${remediation.plan.steps.map((step) => `<li><b>${pretty(step.track)}</b><span>${step.title}</span><em>${step.eta_minutes} min${step.approval_kind ? ` · needs ${step.approval_kind} approval` : ''}</em></li>`).join('')}</ol>
      <p class="remediation-note">${remediation.events.length} resolution event${remediation.events.length === 1 ? '' : 's'} recorded · ${remediation.blast_radius.projection_basis}</p>`
      : `<p class="remediation-note">${anyToken ? 'Load the full case record, including track estimates and the projected blast radius.' : 'Sign in as an approver on the Approvals page to read the full case record.'}</p>
      <button class="secondary-button compact" data-live="remediation" ${anyToken && !state.busy ? '' : 'disabled'}>${icon('plan', 16)} Load case record</button>`}
    </section>` : ''}`;
}

function approvalsView() {
  if (!isLive()) return fixtureApprovalsView();
  const header = pageHeader('Approvals', 'Customer communication and production changes requiring human authority.', `<div class="approval-summary"><strong>${pendingCount()}</strong><span>pending decisions</span></div>`);
  if (!D.approvals.length) return `${header}${emptyState('Nothing awaits approval', 'Routing changes, customer messages, reconciliation batches and code patches appear here after the Blast Radius Agent plans the fix.', liveStartAction())}`;
  const active = D.approvals.find((item) => item.id === state.activeApproval) || D.approvals.find((item) => item.status === 'pending') || D.approvals[0];
  state.activeApproval = active.id;
  const session = auth.get(active.requiredRole);
  const statusLabel = (item) => (item.closedReason === 'superseded' ? 'superseded' : item.status);
  const preview = { sms: phonePreview, config: routeChange, code: codeDiff, reconciliation: reconPreview }[active.rawKind] || (() => '');
  return `
    ${header}
    <section class="approval-layout" data-reveal>
      <div class="approval-queue" data-stagger>
        <div class="section-title"><span>Approval queue</span><small>Hash-chained audit log</small></div>
        ${D.approvals.map((approval) => `<button class="approval-item ${approval.id === active.id ? 'active' : ''}" data-approval="${approval.id}"><i class="level-${approval.level}">L${approval.level}</i><div><span>${approval.kind}</span><strong>${approval.title}</strong><small>${approval.owner} · ${approval.created}</small></div><em class="status-${statusLabel(approval)}">${statusLabel(approval)}</em>${icon('arrow', 16)}</button>`).join('')}
      </div>
      <div class="approval-detail">
        <div class="approval-detail-head"><div><span>${active.id} · Level ${active.level} · needs ${active.owner}</span><h2>${active.title}</h2></div><em>${active.risk} risk</em></div>
        ${preview(active)}
        <div class="approval-why"><strong>Why this action is recommended</strong><p>${approvalReason(active)}</p></div>
        ${approverBar(active, session)}
        <div class="decision-actions"><button class="secondary-button" data-decision="reject" ${active.status !== 'pending' || !session || state.busy ? 'disabled' : ''}>Reject</button><button class="primary-button" data-decision="approve" ${active.status !== 'pending' || !session || state.busy ? 'disabled' : ''}>${active.status === 'pending' ? (state.busy === 'decide' ? 'Recording decision…' : 'Approve action') : `Decision: ${statusLabel(active)}${active.approver ? ` by ${active.approver}` : ''}`}</button></div>
      </div>
    </section>`;
}

function approverBar(active, session) {
  if (active.status !== 'pending') return '';
  if (session) return `<div class="approver-bar signed"><i>${icon('check', 15)}</i><span>Signed in as <strong>${session.sub}</strong> · ${pretty(session.role)}</span><button class="text-button inline" data-auth="signout" data-role="${session.role}">Sign out</button></div>`;
  const role = active.requiredRole;
  const pasting = state.tokenFormRole === role;
  return `<div class="approver-bar">
    <span>Only a <strong>${pretty(role)}</strong> approver can decide this. Decisions are verified with a signed JWT.</span>
    <div class="approver-actions">
      <button class="secondary-button compact" data-auth="signin" data-role="${role}" ${state.busy ? 'disabled' : ''}>Sign in as ${pretty(role)}</button>
      <button class="text-button inline" data-auth="paste" data-role="${role}">Use an identity-provider token</button>
    </div>
    ${pasting ? `<form class="token-form" data-token-form="${role}"><input name="token" type="password" autocomplete="off" placeholder="Paste a ${pretty(role)} JWT" required/><button class="primary-button compact" type="submit">Use token</button></form>` : ''}
  </div>`;
}

function approvalReason(item) {
  const p = item.payload;
  if (item.rawKind === 'config') return `The digital twin shows ${p.simulated_success_rate != null ? `${(p.simulated_success_rate * 100).toFixed(1)}%` : 'a passing'} success across ${formatNumber(p.simulation_samples || 0)} synthetic transfers. Rollback to ${pretty(p.rollback?.action?.to_route)} triggers automatically if errors exceed ${p.rollback ? `${p.rollback.threshold * 100}%` : 'the threshold'} within ${p.rollback?.window_minutes || 10} minutes.`;
  if (item.rawKind === 'sms') return `The message goes to ${formatNumber(p.recipient_count || 0)} tokenised affected customers, uses the plan estimate (“${p.eta_phrase || 'resolution estimate'}”) and includes only their own transfer context.`;
  if (item.rawKind === 'reconciliation') return `Avera prepares ${formatNumber(p.item_count || 0)} uncertain transfers worth ${naira(p.total_ngn)} for human review. ${p.executed_by_avera ? '' : 'Avera never executes reversals or moves funds.'}`;
  if (item.rawKind === 'code') return `${p.summary || 'Connector patch prepared.'} ${p.sandbox ? `Sandbox: ${p.sandbox.tests_run} tests, ${p.sandbox.tests_failed} failed.` : ''}`;
  return 'Recommended by the Resolution workflow.';
}

function fixtureApprovalsView() {
  const active = fixture.approvals.find((item) => item.id === state.activeApproval) || fixture.approvals[0];
  const status = state.approvals[active.id];
  return `
    ${pageHeader('Approvals', 'Customer communication and production changes requiring human authority.', `<div class="approval-summary"><strong>${pendingCount()}</strong><span>pending decisions</span></div>`)}
    <section class="approval-layout" data-reveal>
      <div class="approval-queue" data-stagger>
        <div class="section-title"><span>Approval queue</span><small>Immutable audit log enabled</small></div>
        ${fixture.approvals.map((approval) => `<button class="approval-item ${approval.id === active.id ? 'active' : ''}" data-approval="${approval.id}"><i class="level-${approval.level}">L${approval.level}</i><div><span>${approval.kind}</span><strong>${approval.title}</strong><small>${approval.owner} · ${approval.created}</small></div><em class="status-${state.approvals[approval.id]}">${state.approvals[approval.id]}</em>${icon('arrow', 16)}</button>`).join('')}
      </div>
      <div class="approval-detail">
        <div class="approval-detail-head"><div><span>${active.id} · Level ${active.level}</span><h2>${active.title}</h2></div><em>${active.risk} risk</em></div>
        ${active.kind === 'Customer message' ? phonePreview() : active.kind === 'Routing change' ? routeChange() : codeDiff()}
        <div class="approval-why"><strong>Why this action is recommended</strong><p>${active.kind === 'Customer message' ? 'The message uses the plan estimate, includes only necessary transaction context, and targets tokenised affected customers.' : 'The digital twin shows 96.7% success after this change. Rollback triggers automatically if Gateway C error rate exceeds 2%.'}</p></div>
        <div class="decision-actions"><button class="secondary-button" data-decision="rejected" ${status !== 'pending' ? 'disabled' : ''}>Reject</button><button class="primary-button" data-decision="approved" ${status !== 'pending' ? 'disabled' : ''}>${status === 'pending' ? 'Approve action' : `Decision: ${status}`}</button></div>
      </div>
    </section>`;
}

function phonePreview(item) {
  const message = item ? item.payload.message : fixture.sms;
  const audience = item ? item.payload.recipient_count : fixture.impact.affected;
  const now = item ? (item.created || '') : '10:58';
  const samples = item?.payload.samples?.length;
  return `<div class="phone-preview-wrap"><div class="phone-stage"><div class="phone"><div class="phone-top"><i></i><span>${now}</span><b>● ● ●</b></div><div class="messages-head"><button>${icon('arrow', 15)}</button><div class="message-logo"><img src="/assets/avera-logo.png" alt=""/></div><div><strong>Avera Bank</strong><span>Verified sender</span></div></div><div class="phone-body"><time>Today ${now}</time><p>${message}</p><small>${item ? 'Preview' : 'Delivered'}</small></div><div class="phone-home"></div></div></div><div class="message-meta"><div><span>Audience</span><strong>${formatNumber(audience || 0)} customers</strong></div><div><span>${item ? 'Personalised previews' : 'Estimated delivery'}</span><strong>${item ? `${samples || 0} samples` : '97.8%'}</strong></div><div><span>${item ? 'Template fallback' : 'Contact preferences'}</span><strong>${item ? (item.payload.fallback_used ? 'Used' : 'Not needed') : 'Respected'}</strong></div></div></div>`;
}

function routeChange(item) {
  if (!item) return `<div class="route-diff"><div><span>Current route</span><strong>Bank X</strong><i></i><em>Gateway B</em></div><b>${icon('arrow', 22)}</b><div class="proposed"><span>Proposed route</span><strong>Bank X</strong><i></i><em>Gateway C</em></div><p><span>Simulation</span><strong>96.7% successful</strong><small>5,000 synthetic transactions</small></p></div>`;
  const p = item.payload;
  const gateway = (route) => pretty(route?.split('.')[0]);
  return `<div class="route-diff"><div><span>Current route</span><strong>${pretty(p.destination_bank)}</strong><i></i><em>${gateway(p.from_route)}</em></div><b>${icon('arrow', 22)}</b><div class="proposed"><span>Proposed route</span><strong>${pretty(p.destination_bank)}</strong><i></i><em>${gateway(p.to_route)}</em></div><p><span>Simulation</span><strong>${p.simulated_success_rate != null ? `${(p.simulated_success_rate * 100).toFixed(1)}% successful` : 'Not simulated'}</strong><small>${formatNumber(p.simulation_samples || 0)} synthetic transactions</small></p></div>`;
}

function reconPreview(item) {
  const p = item.payload;
  return `<div class="route-diff recon"><div><span>Batch</span><strong>${p.batch_id}</strong></div><div><span>Uncertain transfers</span><strong>${formatNumber(p.item_count || 0)}</strong></div><div class="proposed"><span>Value under review</span><strong>${naira(p.total_ngn)}</strong></div><p><span>Execution</span><strong>${p.executed_by_avera ? 'Avera executes' : 'A human executes every reversal'}</strong><small>Avera only prepares the batch</small></p></div>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

function codeDiff(item) {
  if (!item) {
    return `<div class="code-diff"><div class="code-head"><span>gateway_b/connector.py</span><em>+8 −3</em></div><pre><span class="line">  def await_ack(transaction):</span>
<span class="removed">-   return poll(transaction, timeout=15)</span>
<span class="added">+   result = poll(transaction, timeout=8)</span>
<span class="added">+   if result.timed_out:</span>
<span class="added">+       return reconcile_idempotently(transaction)</span>
<span class="line">    return result</span></pre></div>`;
  }
  const lines = (item.payload.diff || '').split('\n').filter((line) => !line.startsWith('---') && !line.startsWith('+++'));
  const added = lines.filter((line) => line.startsWith('+')).length;
  const removed = lines.filter((line) => line.startsWith('-')).length;
  return `<div class="code-diff"><div class="code-head"><span>${escapeHtml(item.payload.target || 'patch')}</span><em>+${added} −${removed}</em></div><pre>${lines.map((line) => `<span class="${line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : 'line'}">${escapeHtml(line)}</span>`).join('\n')}</pre></div>`;
}

function timelineView() {
  const events = D.timeline;
  const header = pageHeader('Incident timeline', 'Every signal, agent decision, approval and verified action in order.', `<button class="secondary-button compact" data-action="export">Export audit trail</button>`);
  const pending = isLive() ? D.approvals.filter((item) => item.status === 'pending') : [];
  const resolved = isLive() && currentStep() === 6;
  const now = isLive()
    ? (resolved ? { title: 'Incident resolved', body: 'The reroute was approved, applied and verified by a Ghost Test rerun. Follow-up work stays open for engineers.' }
      : pending.length ? { title: `Awaiting ${pending.length} approval${pending.length === 1 ? '' : 's'}`, body: 'Nothing changes in production until an authorised human approves it.' }
        : events.length ? { title: 'Investigation in progress', body: 'Agents are fusing signals and testing explanations.' } : { title: 'No incident yet', body: 'Start the demo from the controls (D) to stream a live incident.' })
    : { title: 'Awaiting two approvals', body: 'The incident remains contained in the digital twin. No production change has been applied.' };
  const scale = timeScale(events);
  return `
    ${header}
    <section class="timeline-layout" data-reveal>
      <div class="timeline-scale">${scale.map((label) => `<span>${label}</span>`).join('')}</div>
      <div class="timeline-events" data-stagger>${events.length ? events.map((event, index) => `<article class="timeline-event type-${event.type}"><time>${event.time}</time><i><b></b></i><div><span>${event.type}</span><h2>${event.title}</h2><p>${event.detail}</p></div>${index < events.length - 1 ? '<em></em>' : ''}</article>`).join('') : '<p class="empty-result">No signals yet.</p>'}</div>
      <aside class="timeline-now"><span>Now</span><strong>${now.title}</strong><p>${now.body}</p><button class="primary-button" data-view="approvals">Open approval queue ${icon('arrow', 17)}</button>
        ${isLive() ? `<form class="complaint-form" data-complaint-form><label for="complaint-text">Log a customer report</label><textarea id="complaint-text" name="text" rows="2" maxlength="280" placeholder="e.g. My transfer to Bank X is still pending" required></textarea><button class="secondary-button compact" type="submit" ${state.busy ? 'disabled' : ''}>Send to Avera</button><small>Posted to <code>/ingest/command-centre</code>; Avera re-investigates automatically.</small></form>` : ''}
      </aside>
    </section>`;
}

function timeScale(events) {
  if (!isLive()) return ['10:40', '10:45', '10:50', '10:55', '11:00', '11:05'];
  const times = events.map((event) => Date.parse(event.at)).filter(Number.isFinite);
  if (times.length < 2) return [];
  const [min, max] = [Math.min(...times), Math.max(...times)];
  return Array.from({ length: 6 }, (_, index) => new Date(min + ((max - min) * index) / 5).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
}

function memoryView() {
  const q = state.memoryQuery.toLowerCase();
  const records = fixture.memory.filter((record) => `${record.id} ${record.cause} ${record.fix}`.toLowerCase().includes(q));
  return `
    ${pageHeader('Incident memory', 'Validated causes, rejected hypotheses and outcomes from past incidents.', `<div class="memory-stat"><strong>247</strong><span>resolved incidents</span></div>`)}
    <section class="memory-hero" data-reveal>
      <div>${icon('spark', 26)}<span>Similarity match</span><h2>Current incident is ${fixture.incident.similarity}% similar to ${fixture.incident.similarIncident}.</h2><p>The fastest verified fix then was a reroute through Gateway C in nine minutes.</p><button class="primary-button" data-view="plan">Apply insight to plan ${icon('arrow', 17)}</button></div>
      ${agentImage('result', 'observability', 'Avera memory match')}
    </section>
    <section class="memory-search"><label>${icon('search', 18)}<input id="memory-query" value="${state.memoryQuery}" placeholder="Search causes, systems or fixes"/></label><button class="secondary-button">Filters</button></section>
    <section class="memory-list" data-reveal><div class="memory-list-head"><span>Incident</span><span>Similarity</span><span>Validated cause</span><span>Verified fix</span><span>Time to contain</span></div>${records.map((record) => `<article><div><strong>${record.id}</strong><span>${record.date}</span></div><div class="similarity"><i data-bar="${record.similarity}" style="width:${record.similarity}%"></i><strong>${record.similarity}%</strong></div><p>${record.cause}</p><p>${record.fix}</p><div><strong>${record.time}</strong><span>${record.state}</span></div></article>`).join('') || '<div class="empty-result">No incidents match this search.</div>'}</section>`;
}

function statesView() {
  const modes = [
    ['listening', 'Listening', 'Cupping an ear toward live signals and leaning closer as evidence arrives.'],
    ['working', 'Working', 'Typing on a tiny laptop with calm, visible concentration.'],
    ['result', 'Result ready', 'A validated output is ready for the next handoff.'],
    ['bug', 'Needs attention', 'A recoverable issue includes a clear next action.'],
  ];
  return `
    ${pageHeader('Agent states', 'A consistent visual language for attention, work, results and recovery.', `<div class="state-note">Motion respects reduced-motion settings</div>`)}
    <section class="mascot-showcase" data-stagger>
      ${fixture.agents.map((agent) => `<article class="agent-profile" data-tilt="6" style="--agent:${agent.color}"><div class="agent-profile-top">${agentImage('ambient', agent.id, `${agent.name} agent`)}<div><span>${agent.role}</span><h2>${agent.name}</h2><p>${agent.state}</p></div></div><div class="agent-color"><i></i><code>${agent.color}</code></div></article>`).join('')}
    </section>
    <section class="mode-grid" data-stagger>
      ${modes.map(([mode, label, description]) => `<article class="mode-card" data-tilt="6"><div class="mode-canvas">${agentImage(mode, mode === 'bug' ? 'resolution' : 'observability', `${label} mode`)}</div><div><span>${label}</span><p>${description}</p></div></article>`).join('')}
    </section>
    <section class="loading-lab" data-reveal><div><span>Contextual loading state</span><h2>Testing the strongest explanation</h2><p>Run 4 of 5 · Comparing Gateway B with the healthy control</p><div class="phase-track"><i></i><i></i><i></i><i></i><b></b></div><div class="phase-labels"><span>Plan test</span><span>Build synthetic case</span><span>Run control</span><span>Score evidence</span></div></div>${agentImage('working', 'ghost', 'Ghost Test agent working')}</section>`;
}

function loadingView() {
  const labels = {
    overview: ['Connecting the incident stream', 'Synchronising service health and live signals'],
    map: ['Mapping service dependencies', 'Overlaying latency, errors and recent changes'],
    hypotheses: ['Ranking competing explanations', 'Checking evidence and disproof conditions'],
    tests: ['Opening the isolated test record', 'Verifying traces and control results'],
    impact: ['Calculating customer impact', 'Matching the validated fingerprint safely'],
    plan: ['Assembling the fix plan', 'Checking rollback and verification steps'],
    approvals: ['Preparing the approval evidence', 'Confirming scope, risk and authority'],
    timeline: ['Ordering the incident record', 'Joining signals, decisions and actions'],
    memory: ['Searching institutional memory', 'Comparing fingerprints and past outcomes'],
    states: ['Preparing agent states', 'Loading motion and accessibility preferences'],
  };
  const [title, subtitle] = labels[state.view];
  return `<section class="screen-loader"><div class="loader-field"><span class="loader-path"></span><i class="loader-signal s1"></i><i class="loader-signal s2"></i><i class="loader-signal s3"></i>${agentImage('working', 'observability', '')}</div><div><span>Avera is working</span><h1>${title}</h1><p>${subtitle}</p><div class="loader-progress"><i></i></div></div></section>`;
}

let playTimer;
let navigating = false;

async function navigate(view) {
  if (view === state.view || navigating) return;
  navigating = true;
  await leaveView(document.querySelector('#main-content'));
  state.view = view;
  if (view !== 'map') {
    state.selectedNode = null;
    setMapFullscreen(false);
  }
  render();
  window.scrollTo({ top: 0 });
  document.querySelector('#main-content')?.focus({ preventScroll: true });
  navigating = false;
}

// The toast region lives outside the app root so live re-renders never wipe a message.
const toastRegion = document.createElement('div');
toastRegion.className = 'toast-region';
toastRegion.setAttribute('aria-live', 'polite');
document.body.appendChild(toastRegion);
let toastTimer;

function showToast(message, tone = 'ok') {
  toastRegion.innerHTML = `<div class="toast ${tone}">${icon(tone === 'ok' ? 'check' : 'close', 18)}<span>${message}</span></div>`;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toastRegion.innerHTML = ''; }, tone === 'ok' ? 3600 : 6000);
}

function setStep(step) {
  state.demoStep = Math.max(0, Math.min(step, fixture.demoSteps.length - 1));
  render();
}

function setDemoOpen(open) {
  state.demoOpen = open;
  document.querySelector('.demo-panel')?.classList.toggle('open', open);
}

function togglePlay(fromPanel = false) {
  state.playing = !state.playing;
  clearInterval(playTimer);
  if (state.playing) {
    if (fromPanel) state.demoOpen = true;
    if (state.demoStep === fixture.demoSteps.length - 1) state.demoStep = 0;
    playTimer = window.setInterval(() => {
      if (state.demoStep >= fixture.demoSteps.length - 1) {
        clearInterval(playTimer);
        state.playing = false;
      } else state.demoStep += 1;
      render();
    }, 4500);
  }
  render();
}

function decide(decision) {
  if (isLive()) {
    decideLive(decision);
    return;
  }
  const approval = fixture.approvals.find((item) => item.id === state.activeApproval);
  state.approvals[approval.id] = decision;
  render();
  showToast(`${approval.id} ${decision}. The audit record has been updated.`);
  if (decision === 'approved' && approval.kind === 'Routing change' && state.demoStep === fixture.demoSteps.length - 2) {
    window.setTimeout(() => {
      setStep(fixture.demoSteps.length - 1);
      showToast('Reroute applied in the simulated bank. Ghost Tests rerun green.');
    }, 900);
  }
}

const DECISION_ERRORS = {
  401: 'Your approver session is invalid or expired. Sign in again.',
  403: 'This approver role cannot decide this action.',
  404: 'This approval is not visible to your role.',
  409: 'This approval is no longer pending.',
  412: 'The action changed since it was shown. Review the refreshed version and decide again.',
};

async function decideLive(decision) {
  const item = D.approvals.find((approval) => approval.id === state.activeApproval);
  const session = item && auth.get(item.requiredRole);
  if (!item || !session) return;
  await runBusy('decide', async () => {
    // Read the authoritative record first so the decision binds to the exact payload shown.
    const current = await api.approval(item.id, session.token);
    const decided = await api.decide(item.id, session.token, decision, current.payload_hash);
    showToast(`${item.kind} ${decided.status} by ${decided.approver}. The audit record has been updated.`);
  }, (error) => {
    if (error.status === 401) auth.clear(item.requiredRole);
    return DECISION_ERRORS[error.status] || error.message;
  });
}

async function runBusy(key, task, describeError = (error) => error.message) {
  if (state.busy) return;
  state.busy = key;
  render();
  try {
    await task();
  } catch (error) {
    showToast(describeError(error), 'bad');
  } finally {
    state.busy = null;
    render();
  }
}

const wait = (ms) => new Promise((resolve) => { window.setTimeout(resolve, ms); });

async function liveAction(action, target) {
  const actions = {
    start: async () => {
      store.reset();
      const receipt = await api.demoStart();
      showToast(`Healthy baseline started: ${formatNumber(receipt.baseline_transfers)} transfers, Bank X via ${pretty(receipt.bank_x_route)}.`);
    },
    inject: async () => {
      const receipt = await api.demoInject();
      showToast(`Failure injected: ${formatNumber(receipt.transactions)} transfers to ${pretty(receipt.destination_bank)} through ${pretty(receipt.fault_gateway)}.`);
    },
    reset: async () => {
      store.reset();
      await api.demoReset();
      state.remediation = null;
      showToast('Simulated bank reset to its baseline.');
    },
    run: async () => {
      if (store.step() === 6 || !store.run.started) {
        store.reset();
        state.remediation = null;
        await api.demoStart();
        await wait(1200);
      }
      if (!store.run.firstFailureTs) {
        const receipt = await api.demoInject();
        showToast(`Failure injected silently: ${formatNumber(receipt.transactions)} Bank X transfers. Watch Avera respond.`);
      } else {
        showToast('An incident is already in progress.', 'bad');
      }
    },
    investigate: async () => {
      const result = await api.investigate();
      showToast(`Investigation ${result.state}${result.hypotheses ? ` · ${result.hypotheses.length} hypotheses` : ''}.`);
    },
    replay: async () => {
      store.reset();
      const receipt = await api.replayFixture();
      showToast(`Replayed ${formatNumber(receipt.records)} recorded records (${formatNumber(receipt.inserted)} new).`);
    },
    retest: async () => {
      const hypothesis = store.run.hypotheses.get(target.dataset.hypothesis);
      const result = await api.submitHypotheses([hypothesis]);
      const evaluation = result.evaluations[0];
      state.consoleResult = {
        scope: 'hypotheses',
        ok: result.validated_hypothesis_ids.length > 0,
        title: `Ghost Test re-ran ${pretty(hypothesis.zone)}`,
        body: `<p>${result.summary}</p><dl><div><dt>Verdict</dt><dd>${evaluation.test_result.verdict}</dd></div><div><dt>Confidence</dt><dd>${Math.round(evaluation.test_result.confidence * 100)}%</dd></div><div><dt>Reproduced</dt><dd>${evaluation.test_result.reproduced}/${evaluation.test_result.runs}</dd></div><div><dt>Handoff</dt><dd>${result.send_back_to_observability ? 'Sent back to Observability' : 'Eligible'}</dd></div></dl>`,
      };
    },
    twin: async () => {
      const receipt = await api.simulateTwin('gateway_c');
      state.consoleResult = {
        scope: 'tests',
        ok: receipt.candidate_failures <= receipt.baseline_failures,
        title: `Twin: ${pretty(receipt.destination_bank)} via ${pretty(receipt.to_gateway)}`,
        body: `<dl><div><dt>Current route ${pretty(receipt.from_gateway)}</dt><dd>${receipt.baseline_completed}/${receipt.runs} completed · ${receipt.baseline_failures} failed</dd></div><div><dt>Candidate ${pretty(receipt.to_gateway)}</dt><dd>${receipt.candidate_completed}/${receipt.runs} completed · ${receipt.candidate_failures} failed</dd></div><div><dt>Improvement</dt><dd>${receipt.improvement} transfers</dd></div><div><dt>Live route</dt><dd>${receipt.live_route_unchanged ? 'Unchanged' : 'Changed'}</dd></div></dl>`,
      };
    },
    rerun: async () => {
      const validated = D.hypotheses.find((item) => item.state === 'validated');
      const receipt = await api.rerunGhostTest(validated.realId);
      const test = receipt.test_result;
      state.consoleResult = {
        scope: 'tests',
        ok: Boolean(test.rerun_passed),
        title: `Rerun ${validated.id} on the current route`,
        body: `<dl><div><dt>Route used</dt><dd>${pretty(receipt.route_used)}</dd></div><div><dt>Reproduced</dt><dd>${test.reproduced}/${test.runs}</dd></div><div><dt>Verification</dt><dd>${test.rerun_passed ? 'Passed — failure no longer reproduces' : 'Failed — failure still reproduces'}</dd></div></dl>`,
      };
    },
    remediation: async () => {
      const session = auth.get('engineer') || auth.get('customer_dispute');
      state.remediation = await api.remediation(D.incident.id, session.token);
    },
  };
  await runBusy(action, actions[action], (error) => (error.status === 409 ? error.message : `${pretty(action)} failed: ${error.message}`));
}

async function authAction(kind, role) {
  if (kind === 'signout') {
    auth.clear(role);
    render();
    return;
  }
  if (kind === 'paste') {
    state.tokenFormRole = state.tokenFormRole === role ? null : role;
    render();
    document.querySelector('[data-token-form] input')?.focus();
    return;
  }
  await runBusy('signin', async () => {
    try {
      const session = await auth.signIn(role);
      showToast(`Signed in as ${session.sub}.`);
    } catch (error) {
      if (error.status === 404) {
        state.tokenFormRole = role;
        throw new Error('This API does not issue development tokens. Paste a token from your identity provider.');
      }
      throw error;
    }
  });
}

function exportAuditTrail() {
  const payload = { exported_at: new Date().toISOString(), source: isLive() ? apiBase : 'recorded fixture', incident: D.incident, timeline: D.timeline, approvals: D.approvals.map(({ payload, ...item }) => item) };
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
  const link = Object.assign(document.createElement('a'), { href: url, download: `avera-audit-${D.incident.id || 'trail'}.json` });
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function bindEvents() {
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.view)));
  document.querySelectorAll('[data-approval]').forEach((button) => button.addEventListener('click', () => { state.activeApproval = button.dataset.approval; state.tokenFormRole = null; render(); }));
  document.querySelectorAll('[data-decision]').forEach((button) => button.addEventListener('click', () => decide(button.dataset.decision)));
  document.querySelectorAll('[data-step]').forEach((button) => button.addEventListener('click', () => setStep(Number(button.dataset.step))));
  document.querySelectorAll('[data-live]').forEach((button) => button.addEventListener('click', () => liveAction(button.dataset.live, button)));
  document.querySelectorAll('[data-auth]').forEach((button) => button.addEventListener('click', () => authAction(button.dataset.auth, button.dataset.role)));
  document.querySelector('[data-token-form]')?.addEventListener('submit', (event) => {
    event.preventDefault();
    try {
      const session = auth.set(new FormData(event.target).get('token'));
      state.tokenFormRole = null;
      showToast(`Token accepted for ${session.sub} (${pretty(session.role)}).`);
      render();
    } catch (error) {
      showToast(error.message, 'bad');
    }
  });
  document.querySelector('[data-complaint-form]')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = new FormData(event.target).get('text').trim();
    if (!text) return;
    runBusy('complaint', async () => {
      const now = new Date().toISOString();
      await api.ingest('command-centre', 'complaint', { id: `cmp-ui-${Date.now()}`, ts: now, channel: 'support', raw_text: text });
      showToast('Customer report sent. Avera is re-investigating.');
    });
  });
  document.querySelectorAll('[data-action="reconnect"]').forEach((button) => button.addEventListener('click', () => connect()));
  document.querySelector('[data-action="export"]')?.addEventListener('click', exportAuditTrail);
  document.querySelector('[data-action="dismiss-console"]')?.addEventListener('click', () => { state.consoleResult = null; render(); });
  document.querySelector('.topbar [data-action="demo-panel"]')?.addEventListener('click', () => setDemoOpen(!state.demoOpen));
  document.querySelector('.demo-panel [data-action="demo-panel"]')?.addEventListener('click', () => setDemoOpen(false));
  document.querySelectorAll('[data-action="play"]').forEach((button) => button.addEventListener('click', () => togglePlay(Boolean(button.closest('.demo-panel')))));
  document.querySelector('[data-action="next"]')?.addEventListener('click', () => setStep(state.demoStep + 1));
  document.querySelector('.demo-panel [data-action="reset"]')?.addEventListener('click', () => { state.playing = false; clearInterval(playTimer); setStep(0); });
  document.querySelector('[data-action="theme"]')?.addEventListener('click', () => { state.theme = state.theme === 'light' ? 'dark' : 'light'; render(); });
  document.querySelector('[data-map-action="focus"]')?.addEventListener('click', () => serviceMap?.focusSuspect());
  document.querySelector('[data-map-action="fullscreen"]')?.addEventListener('click', () => setMapFullscreen(!state.mapFullscreen));
  document.querySelectorAll('[data-map-action="reset"]').forEach((button) => button.addEventListener('click', () => serviceMap?.resetView()));
  document.querySelector('#memory-query')?.addEventListener('input', (event) => {
    state.memoryQuery = event.target.value;
    window.clearTimeout(event.target._timer);
    event.target._timer = window.setTimeout(() => {
      render();
      const input = document.querySelector('#memory-query');
      input?.focus();
      input?.setSelectionRange(input.value.length, input.value.length);
    }, 180);
  });
}

function fullscreenButton() {
  return `<button data-map-action="fullscreen" aria-pressed="${state.mapFullscreen}">${icon(state.mapFullscreen ? 'shrink' : 'expand', 16)} ${state.mapFullscreen ? 'Exit full screen' : 'Full screen'}</button>`;
}

// Full screen is a class on <html>, so it survives the re-renders that replace the map's markup.
// The browser Fullscreen API is used when available to also hide the browser chrome.
function setMapFullscreen(on) {
  if (on === state.mapFullscreen) return;
  state.mapFullscreen = on;
  document.documentElement.classList.toggle('map-fullscreen', on);
  if (on && !document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {});
  if (!on && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  const button = document.querySelector('[data-map-action="fullscreen"]');
  if (button) {
    button.outerHTML = fullscreenButton();
    document.querySelector('[data-map-action="fullscreen"]').addEventListener('click', () => setMapFullscreen(!state.mapFullscreen));
  }
}

document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement && state.mapFullscreen) setMapFullscreen(false);
});

document.addEventListener('keydown', (event) => {
  if (['INPUT', 'TEXTAREA'].includes(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === 'd') setDemoOpen(!state.demoOpen);
  if (key === 'f' && state.view === 'map' && has3d) setMapFullscreen(!state.mapFullscreen);
  if (key === 'escape' && state.mapFullscreen) setMapFullscreen(false);
});

// Live updates re-render at most every 250 ms, and wait while someone is typing.
let deferredRender = false;

function liveRender() {
  if (state.loading) return;
  const typing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
  if (typing || navigating) {
    deferredRender = true;
    return;
  }
  render();
}

document.addEventListener('focusout', () => {
  if (!deferredRender) return;
  window.setTimeout(() => {
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    deferredRender = false;
    render();
  }, 0);
});

store.subscribe(liveRender);

async function refreshHealth() {
  try {
    const [health, map] = await Promise.all([api.health(), api.serviceMap()]);
    live.health = health;
    live.routes = map.routes;
    return true;
  } catch (error) {
    live.error = error.message;
    return false;
  }
}

async function connect() {
  if (params.get('mode') === 'fixture') return;
  window.clearTimeout(live.retry);
  live.mode = 'connecting';
  liveRender();
  if (!(await refreshHealth())) {
    live.mode = 'fixture';
    liveRender();
    live.retry = window.setTimeout(connect, 20000);
    return;
  }
  live.mode = 'live';
  live.error = null;
  live.closeStream?.();
  live.closeStream = openStream({
    after: store.lastSequence,
    onRecord: (record) => store.ingest(record),
    onOpen: () => { live.stream = 'open'; liveRender(); },
    onError: () => { live.stream = 'reconnecting'; liveRender(); },
  });
  liveRender();
}

// Health and routes change on their own (reroutes, outbox, model state), so poll them gently.
window.setInterval(async () => {
  if (!isLive() || document.hidden) return;
  const before = JSON.stringify([live.health?.observability, live.health?.remediation, live.routes]);
  await refreshHealth();
  if (before !== JSON.stringify([live.health?.observability, live.health?.remediation, live.routes])) liveRender();
}, 8000);

render();
connect();
window.setTimeout(() => {
  state.loading = false;
  render();
}, 1100);
