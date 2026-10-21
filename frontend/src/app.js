import './styles.css';
import { fixture } from './fixtures.js';
import { ServiceMap, webglAvailable } from './three/serviceMap.js';
import { bindTilt, leaveView, revealView } from './motion.js';

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
};

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
  return fixture.mapStates[state.demoStep];
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

function sparkline(values, width = 430, height = 96) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / (max - min || 1)) * (height - 10) - 5;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Transaction throughput trend"><polygon points="0,${height} ${points} ${width},${height}" fill="var(--teal-pale)"/><polyline points="${points}" fill="none" stroke="var(--teal)" stroke-width="2" vector-effect="non-scaling-stroke"/><line x1="68%" y1="0" x2="68%" y2="${height}" stroke="var(--coral)" stroke-dasharray="4 5"/><circle cx="68%" cy="67" r="4" fill="var(--coral)" stroke="var(--paper)" stroke-width="3"/></svg>`;
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
  const step = fixture.demoSteps[state.demoStep];
  return `
    <div class="app-shell" data-theme="${state.theme}">
      <aside class="sidebar">
        ${logo()}
        <nav class="primary-nav" aria-label="Primary navigation">
          ${navItems.map(([id, label, glyph, description]) => `<button class="nav-item ${state.view === id ? 'active' : ''}" data-view="${id}" aria-label="${label}" aria-describedby="nav-tip-${id}" aria-current="${state.view === id ? 'page' : 'false'}">${icon(glyph)}<span class="nav-tooltip" id="nav-tip-${id}" role="tooltip"><strong>${label}</strong><small>${description}</small></span>${id === 'approvals' ? `<em>${pendingCount()}</em>` : ''}</button>`).join('')}
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
            <div class="stream-status"><i></i> Fixture stream <span>live</span></div>
            <button class="demo-trigger" data-action="demo-panel"><span class="demo-dot"></span><span class="demo-step-no">${String(state.demoStep + 1).padStart(2, '0')}</span>${step.label}<kbd>D</kbd></button>
          </div>
        </header>
        <main class="main-content view-${state.view} ${state.loading ? 'is-loading' : ''}" id="main-content">${content}</main>
      </div>
      ${demoPanel()}
      <div class="toast-region" aria-live="polite"></div>
    </div>`;
}

function pendingCount() {
  return Object.values(state.approvals).filter((status) => status === 'pending').length;
}

function pageHeader(title, description, extra = '') {
  return `<div class="page-header"><div><h1>${title}</h1><p>${description}</p></div>${extra}</div>`;
}

function demoPanel() {
  const step = fixture.demoSteps[state.demoStep];
  return `<aside class="demo-panel ${state.demoOpen ? 'open' : ''}" aria-label="Demo controller">
    <div class="demo-panel-head"><div><span>Fixture replay</span><strong>${step.title}</strong></div><button data-action="demo-panel" aria-label="Close demo controller">${icon('close')}</button></div>
    <div class="demo-track">${fixture.demoSteps.map((item, index) => `<button class="demo-stage ${index <= state.demoStep ? 'complete' : ''} ${index === state.demoStep ? 'current' : ''}" data-step="${index}"><i>${index + 1}</i><span>${item.label}</span></button>`).join('')}</div>
    <div class="demo-transport"><button data-action="reset">${icon('reset')} Reset</button><button class="transport-primary" data-action="play">${icon(state.playing ? 'pause' : 'play')} ${state.playing ? 'Pause' : 'Play sequence'}</button><button data-action="next">Next ${icon('arrow')}</button></div>
    <p>Uses the recorded incident stream. Switch to <code>live</code> with one environment setting when the backend is ready.</p>
  </aside>`;
}

function render() {
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
  serviceMap.setStep(state.demoStep);
  serviceMap.mount(slot, { mode: slot.dataset.mapSlot, intro });
}

function overviewView() {
  const step = fixture.demoSteps[state.demoStep];
  const healthy = state.demoStep < 2;
  return `
    ${pageHeader('System health', 'Live operational state across channels, services and active incidents.', `<div class="incident-chip ${healthy ? 'nominal' : ''}"><i></i>${healthy ? fixture.health.nominalLabel : `${fixture.incident.severity} · ${fixture.incident.id}`}</div>`)}
    <section class="overview-theatre" data-reveal>
      <div class="health-stage">
        <div class="stage-topline"><span>${healthy ? 'Bank-wide availability' : 'Active incident'}</span><time>${healthy ? 'Updated now' : `Open ${fixture.incident.elapsed}`}</time></div>
        <div class="incident-title">
          <div class="health-orbit ${healthy ? 'healthy' : ''}"><em class="ring r1"><b></b></em><em class="ring r2"><b></b></em><em class="ring r3"><b></b></em><div class="orbit-core"><span>${healthy ? '<span data-count="99.98" data-decimals="2">99.98</span>' : `<span data-count="${fixture.hypotheses[0].confidence}">${fixture.hypotheses[0].confidence}</span>`}<small>%</small></span></div></div>
          <div><h2>${healthy ? fixture.health.nominalLabel : fixture.incident.title}</h2><p>${healthy ? 'Synthetic traffic is flowing normally across every monitored route.' : 'Avera connected customer reports, retries, channel switching and route-level timeouts into one incident.'}</p></div>
        </div>
        <div class="stage-metrics">
          <div><span>First signal</span><strong>10:42:08</strong></div>
          <div><span>Detected</span><strong>9m 12s</strong></div>
          <div><span>Validated cause</span><strong>${state.demoStep >= 4 ? '96%' : 'Testing'}</strong></div>
          <div><span>Est. containment</span><strong>${state.demoStep >= 5 ? fixture.fixPlan.containmentEta : 'Calculating'}</strong></div>
        </div>
        <div class="throughput">
          <div class="chart-label"><span>Transaction throughput</span><strong>2,841 <small>txn/min</small></strong></div>
          ${sparkline(fixture.health.throughput)}
          <span class="failure-marker">Failure begins</span>
        </div>
      </div>
      <aside class="signal-rail">
        <div class="rail-head"><span>Live incident feed</span><i>Streaming</i></div>
        <div class="signal-list" data-stagger>${fixture.signals.slice(0, state.demoStep < 2 ? 2 : 7).reverse().map((signal, index) => `<article class="signal ${index === 0 ? 'latest' : ''}"><time>${signal.time}</time><i class="tone-${signal.tone}"></i><div><span>${signal.channel}</span><p>${signal.text}</p></div></article>`).join('')}</div>
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
      ${fixture.health.services.map((service) => `<div class="service-reading"><i class="${service.state}"></i><span>${service.name}</span><strong>${service.value}%</strong><small>${service.delta}</small></div>`).join('')}
    </section>
    <section class="agent-dock" data-reveal>
      <div class="dock-copy"><span>Agent activity</span><p>Four accountable agents. Every handoff carries evidence.</p></div>
      ${fixture.agents.map((agent) => `<button class="agent-mini" data-view="states" data-tilt="7" style="--agent:${agent.color}">${agentImage(agent.status, agent.id, `${agent.name} agent`)}<div><span>${agent.name}</span><strong>${agent.state}</strong><small>${agent.detail}</small></div></button>`).join('')}
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
  const { affected, complained, transactions, exposure } = fixture.impact;
  const found = map.customers.affected >= 1 || map.customers.notified;
  const facts = [
    ['Customers sending to Bank X', formatNumber(affected)],
    ['Complained', found ? formatNumber(complained) : map.customers.complainers ? 'Arriving' : 'None yet'],
    ['Never complained', found ? formatNumber(affected - complained) : 'Not yet known'],
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
  const node = fixture.nodes.find((item) => item.id === state.selectedNode);
  if (!node) return '';
  const map = mapState();
  const status = nodeStatus(node.id);
  const lane = fixture.lanes.find((item) => item.nodes.includes(node.id));
  const hyp = map.hypotheses[node.id];
  const hypothesis = hyp && fixture.hypotheses.find((item) => item.id === hyp.id);
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
  const step = fixture.demoSteps[state.demoStep];
  const map = mapState();
  const insight = map.insight;
  const last = fixture.demoSteps.length - 1;
  return `
    <h1 class="visually-hidden">Service map</h1>
    <section class="map-stage ${has3d ? '' : 'flat'}" data-reveal>
      <div class="stage-top">
        <article class="stage-story">
          <div class="story-agent">${agentImage(insight.agent, agentForLabel(insight.label), `${insight.label} agent`)}</div>
          <div class="story-copy">
            <span class="story-kicker"><i>${String(state.demoStep + 1).padStart(2, '0')}</i>${step.time} · ${insight.label}</span>
            <h2>${insight.title}</h2>
            <p>${insight.body}</p>
          </div>
        </article>
        <ol class="stage-pipeline" aria-label="Avera agent pipeline">${fixture.pipeline.map((stage, index) => `<li class="${index < map.stage ? 'done' : index === map.stage ? 'active' : ''}"><i>${index < map.stage ? '✓' : index + 1}</i><div><strong>${stage.label}</strong><span>${stage.agent}</span></div></li>`).join('')}</ol>
      </div>
      <div class="stage-canvas">
        ${has3d ? '<div class="map3d-slot" data-map-slot="full"></div>' : `<div class="topology">${flatTopology()}</div>`}
        <aside class="stage-node ${state.selectedNode ? 'open' : ''}" aria-live="polite">${nodeCard()}</aside>
      </div>
      <div class="stage-bottom">
        <button class="stage-play" data-action="play" aria-label="${state.playing ? 'Pause story' : 'Play story'}">${icon(state.playing ? 'pause' : 'play', 18)}</button>
        <ol class="stage-timeline" style="--progress:${state.demoStep / last}">${fixture.demoSteps.map((item, index) => `<li><button class="${index < state.demoStep ? 'done' : ''} ${index === state.demoStep ? 'current' : ''}" data-step="${index}"><time>${item.time}</time><span>${item.label}</span></button></li>`).join('')}</ol>
        ${has3d ? `<div class="map-controls"><button data-map-action="focus">${icon('focus', 16)} Zoom to suspect</button><button data-map-action="reset">${icon('reset', 16)} Reset view</button>${fullscreenButton()}</div>` : ''}
        ${mapLegend()}
      </div>
    </section>`;
}

function hypothesesView() {
  return `
    ${pageHeader('Hypotheses', 'Competing explanations ranked by evidence and Ghost Test results.', `<div class="threshold"><span>Handoff threshold</span><strong>85%</strong></div>`)}
    <section class="hypothesis-layout">
      <div class="hypothesis-board">
        ${fixture.hypotheses.map((hypothesis) => `<article class="hypothesis-row ${hypothesis.state}" data-reveal>
          <div class="hypothesis-id"><span>${hypothesis.id}</span><i>${hypothesis.rank}</i></div>
          <div class="hypothesis-claim"><div class="row-meta"><span>${hypothesis.zone}</span><em>${hypothesis.state}</em></div><h2>${hypothesis.claim}</h2><p>Routed to ${hypothesis.owner}</p></div>
          <div class="confidence"><strong><span data-count="${hypothesis.confidence}">${hypothesis.confidence}</span><small>%</small></strong><div><i data-bar="${hypothesis.confidence}" style="width:${hypothesis.confidence}%"></i><b style="left:85%"></b></div><span>${hypothesis.confidence >= 85 ? 'Threshold cleared' : 'Below threshold'}</span></div>
          <details><summary>Evidence and disproof ${icon('arrow', 16)}</summary><div class="hypothesis-detail"><div><span>Evidence for</span>${hypothesis.evidence.map((item) => `<p>${item}</p>`).join('')}</div><div><span>Evidence against</span><p>${hypothesis.against}</p></div><div><span>Disproof condition</span><p>${hypothesis.disproof}</p></div></div></details>
        </article>`).join('')}
      </div>
      <aside class="decision-column" data-reveal data-tilt="4">
        ${agentImage('result', 'ghost', 'Ghost Test agent')}
        <span>Ghost Test decision</span>
        <h2>H1 survives every disproof check</h2>
        <p>Failure reproduced 5 of 5 times through Gateway B and 0 of 5 through the healthy control.</p>
        <div class="decision-score"><span>Confidence</span><strong class="num-3d"><span data-count="${fixture.hypotheses[0].confidence}">${fixture.hypotheses[0].confidence}</span>%</strong></div>
        <button class="primary-button" data-view="tests">Open test evidence ${icon('arrow', 17)}</button>
      </aside>
    </section>`;
}

function traceBars(values) {
  const max = Math.max(...values);
  return `<div class="trace-bars">${values.map((value, index) => `<i style="height:${Math.max(8, value / max * 100)}%" class="${index === values.length - 1 && value > 500 ? 'fail' : ''}" title="${value} ms"></i>`).join('')}</div>`;
}

function testsView() {
  return `
    ${pageHeader('Ghost tests', 'Synthetic-only validation in the isolated digital twin.', `<div class="sandbox-chip"><i></i>Sandbox isolated</div>`)}
    <section class="test-summary" data-reveal>
      <div class="test-agent">${agentImage('working', 'ghost', 'Ghost Test agent working')}<div><span>Validation complete</span><h2>15 tests separated the cause from the decoys.</h2><p>No customer data or funds entered the test environment.</p></div></div>
      <div class="test-count"><span>Tests run</span><strong data-count="15">15</strong><small>5 plans · 3 controls</small></div>
      <div class="test-count"><span>Reproduced</span><strong data-count="5">5</strong><small>Gateway B route only</small></div>
      <div class="test-count"><span>Elapsed</span><strong>3:32</strong><small>Digital twin time</small></div>
    </section>
    <section class="test-table-wrap" data-reveal data-stagger>
      <div class="table-head"><span>Test</span><span>Plan</span><span>Trace latency</span><span>Runs</span><span>Result</span></div>
      ${fixture.tests.map((test) => `<article class="test-row"><div><strong>${test.id}</strong><span>${test.hypothesis}</span></div><div><strong>${test.name}</strong><span>${test.duration}</span></div><div>${traceBars(test.trace)}<small>${Math.max(...test.trace)} ms peak</small></div><div><strong>${test.runs}</strong><span>reproduced</span></div><div><em class="verdict-${test.verdict}">${test.result}</em></div></article>`).join('')}
    </section>
    <div class="safety-note"><i>${icon('check')}</i><div><strong>Verification boundary held</strong><span>Tests ran with synthetic identities and zero-value transactions in a copy of the service graph.</span></div></div>`;
}

function impactView() {
  const i = fixture.impact;
  return `
    ${pageHeader('Customer impact', 'Affected customers, transactions and silent behavioural signals.', `<div class="export-link">Snapshot at 10:57:06</div>`)}
    <section class="impact-reveal" data-reveal>
      <div class="impact-copy"><span>Blast Radius result</span><h2>${formatNumber(i.silent)} customers were found before they had to complain.</h2><p>The validated fingerprint was matched across live and historical transaction records.</p></div>
      <div class="impact-numbers"><div class="complained" data-tilt="8"><strong class="num-3d" data-count="${i.complained}">${formatNumber(i.complained)}</strong><span>complained</span></div><div class="impact-connector"><i></i><b></b><small>Avera found</small></div><div class="affected" data-tilt="8"><strong class="num-3d" data-count="${i.affected}">${formatNumber(i.affected)}</strong><span>affected</span></div></div>
      <div class="silent-number"><span>Silent customers</span><strong data-count="${i.silent}">${formatNumber(i.silent)}</strong><small>${i.prevention}% complaint prevention opportunity</small></div>
    </section>
    <section class="impact-detail" data-reveal>
      <div class="impact-stats"><div><span>Affected transactions</span><strong>${formatNumber(i.transactions)}</strong></div><div><span>Estimated exposure</span><strong>${i.exposure}</strong></div><div><span>First affected</span><strong>10:42 AM</strong></div></div>
      <div class="segment-list"><div class="section-title"><span>Customer segments</span><small>${formatNumber(i.affected)} total</small></div>${i.segments.map((segment) => `<div class="segment"><span>${segment.label}</span><div><i data-bar="${segment.share}" style="width:${segment.share}%"></i></div><strong>${formatNumber(segment.value)}</strong><small>${segment.share}%</small></div>`).join('')}</div>
      <div class="impact-agent">${agentImage('result', 'blast', 'Blast Radius agent')}<div><span>Blast Radius</span><strong>Search complete</strong><p>Every matching customer has been tokenised and added to the notification audience.</p><button class="text-button" data-view="approvals">Review customer message ${icon('arrow', 16)}</button></div></div>
    </section>`;
}

function planView() {
  const plan = fixture.fixPlan;
  return `
    ${pageHeader('Fix plan', 'Containment, permanent resolution, rollback and verification.', `<div class="plan-confidence"><span>Simulation success</span><strong>${plan.simulationSuccess}%</strong></div>`)}
    <section class="plan-hero" data-reveal>
      <div><span>Validated cause</span><h2>Gateway B acknowledgement handling fails for Bank X transfers.</h2><p>Contain the route first, then repair the connector and reconcile uncertain transaction states.</p></div>
      <div class="eta-band"><div><span>Containment</span><strong>${plan.containmentEta}</strong></div><i></i><div><span>Permanent fix</span><strong>${plan.fullEta}</strong></div><i></i><div><span>Reconciliation</span><strong>${plan.reconciliationEta}</strong></div></div>
    </section>
    <section class="plan-body" data-reveal>
      <div class="steps" data-stagger><div class="section-title"><span>Execution plan</span><small>Human approval required before steps 01 and 02</small></div>${plan.steps.map((step, index) => `<article class="plan-step"><span>${step.n}</span><div><small>${step.owner}</small><h3>${step.title}</h3><p>${step.detail}</p></div><em class="step-${step.status}">${step.status}</em>${index < plan.steps.length - 1 ? '<i></i>' : ''}</article>`).join('')}</div>
      <aside class="rollback" data-tilt="3"><div class="rollback-head">${icon('reset')}<span>Rollback condition</span></div><p>${plan.rollback}</p><div class="guardrail"><strong>No money movement</strong><span>Avera may prepare reconciliation batches. A human executes every financial action.</span></div><button class="primary-button" data-view="approvals">Review approvals ${icon('arrow', 17)}</button></aside>
    </section>`;
}

function approvalsView() {
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

function phonePreview() {
  return `<div class="phone-preview-wrap"><div class="phone-stage"><div class="phone"><div class="phone-top"><i></i><span>10:58</span><b>● ● ●</b></div><div class="messages-head"><button>${icon('arrow', 15)}</button><div class="message-logo"><img src="/assets/avera-logo.png" alt=""/></div><div><strong>Avera Bank</strong><span>Verified sender</span></div></div><div class="phone-body"><time>Today 10:58</time><p>${fixture.sms}</p><small>Delivered</small></div><div class="phone-home"></div></div></div><div class="message-meta"><div><span>Audience</span><strong>${formatNumber(fixture.impact.affected)} customers</strong></div><div><span>Estimated delivery</span><strong>97.8%</strong></div><div><span>Contact preferences</span><strong>Respected</strong></div></div></div>`;
}

function routeChange() {
  return `<div class="route-diff"><div><span>Current route</span><strong>Bank X</strong><i></i><em>Gateway B</em></div><b>${icon('arrow', 22)}</b><div class="proposed"><span>Proposed route</span><strong>Bank X</strong><i></i><em>Gateway C</em></div><p><span>Simulation</span><strong>96.7% successful</strong><small>5,000 synthetic transactions</small></p></div>`;
}

function codeDiff() {
  return `<div class="code-diff"><div class="code-head"><span>gateway_b/connector.py</span><em>+8 −3</em></div><pre><span class="line">  def await_ack(transaction):</span>
<span class="removed">-   return poll(transaction, timeout=15)</span>
<span class="added">+   result = poll(transaction, timeout=8)</span>
<span class="added">+   if result.timed_out:</span>
<span class="added">+       return reconcile_idempotently(transaction)</span>
<span class="line">    return result</span></pre></div>`;
}

function timelineView() {
  return `
    ${pageHeader('Incident timeline', 'Every signal, agent decision, approval and verified action in order.', `<button class="secondary-button compact">Export audit trail</button>`)}
    <section class="timeline-layout" data-reveal>
      <div class="timeline-scale"><span>10:40</span><span>10:45</span><span>10:50</span><span>10:55</span><span>11:00</span><span>11:05</span></div>
      <div class="timeline-events" data-stagger>${fixture.timeline.map((event, index) => `<article class="timeline-event type-${event.type}"><time>${event.time}</time><i><b></b></i><div><span>${event.type}</span><h2>${event.title}</h2><p>${event.detail}</p></div>${index < fixture.timeline.length - 1 ? '<em></em>' : ''}</article>`).join('')}</div>
      <aside class="timeline-now"><span>Now</span><strong>Awaiting two approvals</strong><p>The incident remains contained in the digital twin. No production change has been applied.</p><button class="primary-button" data-view="approvals">Open approval queue ${icon('arrow', 17)}</button></aside>
    </section>`;
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

function showToast(message) {
  const region = document.querySelector('.toast-region');
  if (!region) return;
  region.innerHTML = `<div class="toast">${icon('check', 18)}<span>${message}</span></div>`;
  window.setTimeout(() => { if (region) region.innerHTML = ''; }, 3200);
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

function bindEvents() {
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.view)));
  document.querySelectorAll('[data-approval]').forEach((button) => button.addEventListener('click', () => { state.activeApproval = button.dataset.approval; render(); }));
  document.querySelectorAll('[data-decision]').forEach((button) => button.addEventListener('click', () => decide(button.dataset.decision)));
  document.querySelectorAll('[data-step]').forEach((button) => button.addEventListener('click', () => setStep(Number(button.dataset.step))));
  document.querySelector('.topbar [data-action="demo-panel"]')?.addEventListener('click', () => setDemoOpen(!state.demoOpen));
  document.querySelector('.demo-panel [data-action="demo-panel"]')?.addEventListener('click', () => setDemoOpen(false));
  document.querySelectorAll('[data-action="play"]').forEach((button) => button.addEventListener('click', () => togglePlay(Boolean(button.closest('.demo-panel')))));
  document.querySelector('[data-action="next"]')?.addEventListener('click', () => setStep(state.demoStep + 1));
  document.querySelector('[data-action="reset"]')?.addEventListener('click', () => { state.playing = false; clearInterval(playTimer); setStep(0); });
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

render();
window.setTimeout(() => {
  state.loading = false;
  render();
}, 1100);
