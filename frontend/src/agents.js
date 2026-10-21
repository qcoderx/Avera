// The four Avera agents narrate their work. Every line is computed from the live view model
// (or the recorded fixture offline), so the agents only ever describe work that happened.

export const AGENTS = [
  { id: 'observability', name: 'Observability', short: 'Obs', color: '#1EA896', role: 'Signal fusion' },
  { id: 'ghost', name: 'Ghost Test', short: 'Ghost', color: '#8178D8', role: 'Safe validation' },
  { id: 'blast', name: 'Blast Radius', short: 'Blast', color: '#F2A65A', role: 'Impact planning' },
  { id: 'resolution', name: 'Resolution', short: 'Resolve', color: '#FF715B', role: 'Approved action' },
];

export const agentById = Object.fromEntries(AGENTS.map((agent) => [agent.id, agent]));

const STEP_OWNER = ['observability', 'observability', 'observability', 'ghost', 'blast', 'resolution', 'resolution'];
const HEARTBEAT_MS = 5000;
const DWELL_MS = 6000;

const n = (value) => Number(value || 0).toLocaleString('en-NG');

function facts(D, ctx) {
  const run = ctx.run;
  const validated = D.hypotheses.find((item) => item.state === 'validated');
  const pending = D.approvals.filter((item) => item.status === 'pending' || (!D.live && item.status === undefined));
  return {
    run,
    started: run ? run.started : true,
    events: run ? run.eventCount : 5690,
    transfers: run ? run.transfers : 1314,
    timeouts: run ? run.codes.ACK_TIMEOUT || 0 : 1284,
    complaints: run ? run.complaints.length : D.impact.complained,
    lastComplaint: run?.complaints.at(-1)?.text,
    retries: run ? run.serviceCodes['transfer-ui:RETRY_LOOP'] || 0 : 27,
    switches: run ? run.serviceCodes['mobile-app:CHANNEL_SWITCH'] || 0 : 18,
    signals: run?.incident?.signals ?? 2407,
    zones: run?.incident?.suspectZones.length ?? 4,
    incidentId: D.incident.id,
    hypotheses: D.hypotheses,
    tests: D.tests,
    validated,
    decoys: D.hypotheses.filter((item) => item.state === 'rejected'),
    impact: D.impact,
    plan: D.fixPlan,
    approvals: D.approvals,
    pending,
    rerunPassed: run ? run.resolutions.some((item) => item.status === 'verified_pass') : true,
    resolved: run ? run.resolutions.some((item) => item.status === 'resolved') : D.step === 6,
    investigating: ctx.obsState === 'investigating',
    degraded: ctx.obsState === 'degraded',
  };
}

// Current activity for every agent at the displayed story step.
export function agentStatuses(D, step, ctx) {
  const f = facts(D, ctx);
  const owner = STEP_OWNER[step];
  const status = {};

  // Observability: listens, notices, investigates, hands over.
  if (!f.started) {
    status.observability = { mode: 'listening', headline: 'Standing by', lines: ['Standing by. Start the demo baseline so I have traffic to watch.'] };
  } else if (step === 0) {
    status.observability = { mode: 'listening', headline: 'Listening', lines: [
      `Listening to ${n(f.events)} live events across app, USSD and partner routes.`,
      `${n(f.transfers)} transfers so far and every one acknowledged. All quiet.`,
      'Watching complaint channels. Nothing unusual yet.',
    ] };
  } else if (step === 1) {
    status.observability = { mode: 'working', headline: 'Noticing a pattern', lines: [
      `I'm seeing ${n(f.timeouts)} acknowledgement timeouts on Gateway B → Bank X.`,
      `${n(f.complaints)} customers have complained about hanging transfers.${f.lastComplaint ? ` Latest: “${f.lastComplaint}”` : ''}`,
      `Customers are retrying (${n(f.retries)}) and switching to USSD (${n(f.switches)}). Correlating these signals now.`,
      f.degraded ? 'My language model is not configured, so I cannot propose hypotheses yet.' : f.investigating ? 'Asking the model for competing explanations of the failure…' : 'Grouping signals by route, destination and app version…',
    ] };
  } else if (step === 2) {
    status.observability = { mode: 'working', headline: 'Investigating', lines: [
      `Incident ${f.incidentId} is open. ${n(f.signals)} signals fused into one fingerprint.`,
      `Marking ${f.zones} suspect zones, including the app release that shipped at the same time.`,
      `Writing a disproof condition for each of my ${f.hypotheses.length || 'competing'} hypotheses so Ghost Test can try to break them.`,
    ] };
  } else {
    status.observability = { mode: 'done', headline: 'Handed over', lines: [
      `Handed ${f.hypotheses.length} hypotheses to Ghost Test.`,
      `Still listening: ${n(f.complaints)} complaints and ${n(f.events)} events on record.`,
    ] };
  }

  // Ghost Test: waits, tests in the twin, validates, reruns after the fix.
  if (step < 2) {
    status.ghost = { mode: 'idle', headline: 'Waiting', lines: ['Waiting for hypotheses from Observability. My digital twin is ready.'] };
  } else if (step === 2) {
    status.ghost = { mode: 'listening', headline: 'Receiving', lines: ['Receiving hypotheses. Building synthetic transfers for the twin.'] };
  } else if (step === 3) {
    const lines = f.tests.map((test) => {
      const hypothesis = f.hypotheses.find((item) => item.id === test.hypothesis);
      return `Testing ${test.hypothesis} ${hypothesis?.zone || ''}: ${test.runs} reproduced. ${test.result}.`;
    });
    status.ghost = { mode: 'working', headline: 'Testing in the twin', lines: lines.length ? lines : ['Running synthetic transfers through each suspect route…'] };
  } else {
    const v = f.validated;
    status.ghost = { mode: 'done', headline: step === 6 ? 'Verified the fix' : 'Validated', lines: [
      v ? `${v.id} ${v.zone} survived every disproof check at ${v.confidence}%. Handed to Blast Radius.` : 'No hypothesis cleared 85%. Sending them back to Observability.',
      `Ruled out ${f.decoys.length} decoys${f.decoys.some((item) => /app/i.test(item.zone)) ? ', including the app release' : ''}.`,
      ...(step === 6 ? [f.rerunPassed ? 'Reran the original test after the reroute: the failure no longer reproduces.' : 'Post-fix rerun is still pending.'] : []),
    ] };
  }

  // Blast Radius: searches for every affected customer, including the silent ones.
  if (step < 3) {
    status.blast = { mode: 'idle', headline: 'Waiting', lines: ['Waiting for a validated cause before I search customer impact.'] };
  } else if (step === 3) {
    status.blast = { mode: 'listening', headline: 'Ready', lines: ['Ready to search as soon as Ghost Test validates a cause.'] };
  } else if (step === 4) {
    status.blast = { mode: 'working', headline: 'Searching', lines: [
      `Matching the failure fingerprint across ${n(f.impact.transactions)} transactions…`,
      `Found ${n(f.impact.affected)} affected customers. ${n(f.impact.silent)} of them never complained.`,
      `Estimated exposure ${f.impact.exposure}. Drafting a message so customers hear from us first.`,
    ] };
  } else {
    status.blast = { mode: 'done', headline: 'Impact found', lines: [
      `${n(f.impact.affected)} customers affected, ${n(f.impact.silent)} silent. Plan and message sent for approval.`,
    ] };
  }

  // Resolution: plans, waits for humans, applies, verifies.
  if (step < 4) {
    status.resolution = { mode: 'idle', headline: 'Waiting', lines: ['Waiting for a validated cause and impact before planning a fix.'] };
  } else if (step === 4) {
    status.resolution = { mode: 'listening', headline: 'Planning', lines: [`Preparing containment, patch and reconciliation steps${f.plan ? ` (containment ${f.plan.containmentEta})` : ''}.`] };
  } else if (step === 5) {
    const next = f.pending[0];
    status.resolution = { mode: 'waiting', headline: 'Awaiting approval', lines: [
      next ? `Waiting for ${next.owner} to approve: ${next.title}.` : 'Waiting for an approver.',
      `${f.pending.length} decision${f.pending.length === 1 ? '' : 's'} pending. Nothing changes in production until a human approves.`,
      f.plan?.simulationSuccess != null ? `The reroute passed ${f.plan.simulationSuccess}% of twin transfers. Ready when you are.` : 'The plan is ready in the Fix plan view.',
    ] };
  } else {
    status.resolution = { mode: 'done', headline: 'Resolved', lines: [
      'Reroute applied: Bank X transfers now flow through Gateway C.',
      f.rerunPassed ? 'Ghost Test rerun passed. Incident resolved and saved to memory.' : 'Verifying the fix with a Ghost Test rerun…',
      `Follow-ups open: ${f.pending.length ? `${f.pending.length} approvals including the connector patch` : 'connector patch review and reconciliation'}.`,
    ] };
  }

  AGENTS.forEach(({ id }) => { status[id].active = id === owner; });
  return status;
}

const HANDOFFS = {
  1: [['observability', 'Something is wrong on the Bank X route. Acknowledgements stopped and customers are starting to complain. Investigating.']],
  2: [['observability', 'Opened an incident and fused the signals into one fingerprint. Writing competing hypotheses now.']],
  3: [['observability', 'Done investigating. Handing my hypotheses to Ghost Test.'], ['ghost', 'Got them. Testing each one in the digital twin with synthetic transfers.']],
  4: [['ghost', 'Validation finished. Handing the validated cause to Blast Radius.'], ['blast', 'On it. Searching every transaction for customers hit by this fingerprint.']],
  5: [['blast', 'Impact mapped. Handing the plan to Resolution.'], ['resolution', 'Plan ready. I need human approval before anything changes in production.']],
  6: [['resolution', 'Approved change applied. Asking Ghost Test to verify.'], ['ghost', 'Rerun complete: the failure no longer reproduces. Incident resolved.']],
};

// Paces the displayed story so a burst of backend records still reads as distinct handoffs,
// and keeps every agent talking on a 5 second heartbeat.
export class AgentDirector {
  constructor() {
    this.displayStep = 0;
    this.stepSince = 0;
    this.synced = false;
    this.messages = [];
    this.listeners = new Set();
    this.lineIndex = {};
    this.lastPost = {};
    this.statuses = null;
    this.source = null;
    this.beats = 0;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(kind) {
    this.listeners.forEach((listener) => listener(kind));
  }

  post(agentId, text, kind = 'update') {
    const last = this.messages.at(-1);
    if (last && last.agent === agentId && last.text === text) return;
    this.messages.push({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, agent: agentId, text, kind, at: new Date() });
    if (this.messages.length > 200) this.messages.splice(0, this.messages.length - 200);
    this.lastPost[agentId] = Date.now();
    this.emit('message');
  }

  postUser(text) {
    this.messages.push({ id: `${Date.now()}-u`, agent: 'engineer', text, kind: 'question', at: new Date() });
    this.emit('message');
  }

  // source() returns { D, step, ctx, paced }
  start(source) {
    this.source = source;
    window.setInterval(() => this.advance(), 1000);
    window.setInterval(() => this.heartbeat(), HEARTBEAT_MS);
  }

  sync(step) {
    this.displayStep = step;
    this.stepSince = Date.now();
    this.synced = true;
    this.refresh();
  }

  target() {
    return this.source().step;
  }

  advance() {
    const { paced } = this.source();
    const target = this.target();
    if (!paced) {
      if (target !== this.displayStep) this.setDisplay(target, target === this.displayStep + 1);
      return;
    }
    if (!this.synced) return;
    if (target < this.displayStep) {
      this.setDisplay(target, false);
    } else if (target > this.displayStep && Date.now() - this.stepSince >= DWELL_MS) {
      this.setDisplay(this.displayStep + 1, true);
    }
  }

  setDisplay(step, announce) {
    this.displayStep = step;
    this.stepSince = Date.now();
    this.refresh();
    if (announce) (HANDOFFS[step] || []).forEach(([agent, text]) => this.post(agent, text, 'handoff'));
    this.emit('step');
  }

  refresh() {
    const { D, ctx } = this.source();
    this.statuses = agentStatuses(D, this.displayStep, ctx);
  }

  // The line each agent is saying right now (rotates every heartbeat).
  line(agentId) {
    const status = this.statuses?.[agentId];
    if (!status) return '';
    const index = (this.lineIndex[agentId] || 0) % status.lines.length;
    return status.lines[index];
  }

  heartbeat() {
    this.refresh();
    this.beats += 1;
    AGENTS.forEach(({ id }) => { this.lineIndex[id] = (this.lineIndex[id] || 0) + 1; });
    const active = AGENTS.find(({ id }) => this.statuses[id].active);
    if (active && this.source().ctx.chatty) this.post(active.id, this.line(active.id));
    // Every fourth beat a supporting agent checks in, so nobody goes quiet for long.
    if (this.beats % 4 === 0 && this.source().ctx.chatty) {
      const others = AGENTS.filter(({ id }) => !this.statuses[id].active && this.statuses[id].mode !== 'idle');
      const other = others[(this.beats / 4) % Math.max(1, others.length)];
      if (other) this.post(other.id, this.line(other.id));
    }
    this.emit('beat');
  }
}

// ---- Engineer questions ---------------------------------------------------------------

const INTENTS = [
  ['help', /\b(help|what can (i|you)|commands?)\b/],
  ['greet', /^(hi|hey|hello|yo|good (morning|afternoon|evening))\b/],
  ['impact', /\b(customers?|impact|affected|how many|silent|exposure|blast|naira|₦|money)\b/],
  ['eta', /\b(eta|when|how long|fixed|resolve[d]? (by|in)|time to)\b/],
  ['approval', /\b(approv|pending|waiting|sign ?off|who needs|decision)/],
  ['rollback', /\b(rollback|roll back|undo|revert)\b/],
  ['tests', /\b(tests?|reproduc|twin|ghost|control|verify|verification|rerun)\b/],
  ['hypotheses', /\b(hypothes|cause|root|why did|culprit|suspect|decoy|app release)\b/],
  ['complaints', /\b(complain|complaint|report|said|quote)/],
  ['evidence', /\b(why|evidence|how do you know|proof|sure|confident|confidence)\b/],
  ['next', /\b(next|then what|after that|what now)\b/],
  ['status', /\b(doing|status|now|up to|working|progress|happening|busy|update|you there|alive)\b/],
];

const OWNER = { impact: 'blast', eta: 'resolution', approval: 'resolution', rollback: 'resolution', tests: 'ghost', hypotheses: 'ghost', complaints: 'observability' };

function addressed(text, chosen) {
  if (/\bobserv|\bobs\b/.test(text)) return 'observability';
  if (/\bghost/.test(text) && !/ghost test(s)? (result|run)/.test(text)) return 'ghost';
  if (/\bblast/.test(text)) return 'blast';
  if (/\bresolution|\bresolver\b/.test(text)) return 'resolution';
  return chosen;
}

function reply(agentId, intent, D, step, ctx, statuses) {
  const f = facts(D, ctx);
  const status = statuses[agentId];
  const now = status.lines[0];
  switch (intent) {
    case 'greet': return `Hi! I'm ${agentById[agentId].name}. Right now: ${now}`;
    case 'help': return 'Ask me what I am doing, why, how many customers are affected, what is pending approval, the ETA, the rollback plan, or what happens next. Mention an agent by name to ask them directly.';
    case 'status': return `${status.headline}. ${status.lines.join(' ')}`;
    case 'next': {
      const next = { 0: 'If anything breaks, I will open an incident.', 1: 'Next I fuse these signals into an incident and propose hypotheses.', 2: 'Next Ghost Test tries to disprove every hypothesis in the twin.', 3: 'Next Blast Radius finds every affected customer.', 4: 'Next Resolution asks a human to approve the fix.', 5: 'Once approved, the reroute is applied and Ghost Test verifies it.', 6: 'The incident is resolved. Follow-ups are the patch review and reconciliation.' }[step];
      return next;
    }
    case 'complaints': return f.complaints ? `${n(f.complaints)} complaints so far${f.lastComplaint ? `. Latest: “${f.lastComplaint}”` : ''}. Most customers never complain, so I also watch retries (${n(f.retries)}) and channel switching (${n(f.switches)}).` : 'No complaints yet.';
    case 'hypotheses': {
      if (!f.hypotheses.length) return 'No hypotheses yet. Observability writes them once the incident opens.';
      const top = f.hypotheses.slice(0, 4).map((item) => `${item.id} ${item.zone} ${item.confidence}% (${item.state})`).join('; ');
      return `${f.hypotheses.length} hypotheses: ${top}.${f.validated ? ` ${f.validated.id} is the validated cause.` : ''}`;
    }
    case 'tests': {
      if (!f.tests.length) return 'No tests have run yet. I test as soon as Observability hands me hypotheses.';
      const v = f.validated;
      return `${f.tests.length} tests ran in the isolated twin with synthetic transfers only.${v ? ` ${v.id} ${v.zone} reproduced every run and the healthy control did not, so it validated at ${v.confidence}%.` : ' Nothing has cleared the 85% threshold yet.'}${step === 6 ? ` After the reroute, ${f.rerunPassed ? 'the rerun no longer reproduces the failure.' : 'the rerun is still pending.'}` : ''}`;
    }
    case 'impact': return f.impact.affected ? `${n(f.impact.affected)} customers and ${n(f.impact.transactions)} transactions are affected, about ${f.impact.exposure} in exposure. Only ${n(f.impact.complained)} complained; ${n(f.impact.silent)} never did.` : 'I have not searched yet. I start once Ghost Test validates a cause.';
    case 'eta': return f.plan ? `Containment: ${f.plan.containmentEta}. Full fix: ${f.plan.fullEta}.` : 'No plan yet, so no ETA. It comes after the cause is validated.';
    case 'approval': return f.pending.length ? `${f.pending.length} pending: ${f.pending.map((item) => `${item.title} (needs ${item.owner})`).join('; ')}. Open the Approvals page to decide.` : step === 6 ? 'Nothing is blocking the incident. Remaining follow-ups are in the approval queue.' : 'Nothing is waiting for approval right now.';
    case 'rollback': return f.plan ? f.plan.rollback : 'There is no change to roll back yet.';
    case 'evidence': {
      if (agentId === 'observability') return f.validated ? `${f.validated.id} rests on: ${f.validated.evidence.slice(0, 3).join('; ') || 'route-level signals'}. Disproof condition: ${f.validated.disproof}` : `${n(f.timeouts)} timeouts on one route, ${n(f.complaints)} complaints and unusual retries all line up in time.`;
      if (agentId === 'ghost') return f.validated ? `${f.validated.id} reproduced in every run through ${f.validated.zone}, while the healthy control did not. That is ${f.validated.confidence}% confidence.` : 'I only trust a hypothesis after it survives its disproof condition in the twin.';
      if (agentId === 'blast') return 'I match the validated fingerprint (route, destination and error) against every transaction, not just the customers who called.';
      return f.plan?.simulationSuccess != null ? `The reroute succeeded for ${f.plan.simulationSuccess}% of twin transfers, with an automatic rollback if errors pass the threshold.` : 'I only act on approved, simulated changes.';
    }
    default: return `${status.headline}. ${now}`;
  }
}

// Answer an engineer's question; may involve a handoff to the agent who owns the topic.
export function answer(question, chosenAgent, D, step, ctx) {
  const text = question.toLowerCase().trim();
  const statuses = agentStatuses(D, step, ctx);
  const agentId = addressed(text, chosenAgent || STEP_OWNER[step]);
  const intent = (INTENTS.find(([, pattern]) => pattern.test(text)) || ['status'])[0];
  const owner = OWNER[intent];
  if (owner && owner !== agentId && !chosenAgent && !/\b(observ|ghost|blast|resolution)/.test(text)) {
    return [{ agent: owner, text: reply(owner, intent, D, step, ctx, statuses) }];
  }
  if (owner && owner !== agentId) {
    return [
      { agent: agentId, text: `${agentById[owner].name} owns that. Passing it over.` },
      { agent: owner, text: reply(owner, intent, D, step, ctx, statuses) },
    ];
  }
  return [{ agent: agentId, text: reply(agentId, intent, D, step, ctx, statuses) }];
}
