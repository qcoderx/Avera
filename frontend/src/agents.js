// The four Avera agents narrate their work. Every line is computed from the live view model
// (or the recorded fixture offline), so the agents only ever describe work that happened.

import { claimParts } from './prose.js';
import { naira } from './live.js';

export const AGENTS = [
  { id: 'observability', name: 'Observability', short: 'Obs', color: '#1EA896', role: 'Signal fusion' },
  { id: 'ghost', name: 'Ghost Test', short: 'Ghost', color: '#8178D8', role: 'Safe validation' },
  { id: 'blast', name: 'Blast Radius', short: 'Blast', color: '#F2A65A', role: 'Impact planning' },
  { id: 'resolution', name: 'Resolution', short: 'Resolve', color: '#FF715B', role: 'Approved action' },
];

export const agentById = Object.fromEntries(AGENTS.map((agent) => [agent.id, agent]));

const STEP_OWNER = ['observability', 'observability', 'observability', 'ghost', 'blast', 'resolution', 'resolution'];
const HEARTBEAT_MS = 5000;
// How long each displayed stage holds before the next handoff. Validation is the slowest on
// purpose: Ghost Test works through every hypothesis where engineers can follow it.
const DWELL_MS = { 0: 4000, 1: 9000, 2: 11000, 3: 18000, 4: 10000, 5: 6000 };

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

  // Resolution: plans, escalates to the humans, applies, verifies.
  if (step < 4) {
    status.resolution = { mode: 'idle', headline: 'Waiting', lines: ['Waiting for a validated cause and impact before planning a fix.'] };
  } else if (step === 4) {
    status.resolution = { mode: 'listening', headline: 'Planning', lines: [`Preparing containment, patch and reconciliation steps${f.plan ? ` (containment ${f.plan.containmentEta})` : ''}.`] };
  } else if (step === 5) {
    const next = f.pending.find((item) => item.kind === 'Routing change') || f.pending[0];
    status.resolution = { mode: 'waiting', headline: 'Escalated to the humans', lines: [
      next ? `Escalated to the humans: ${next.title}. Only ${next.owner} can approve this.` : 'Escalated to the humans: an authorised engineer must approve.',
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

  if (step === 6) {
    status.observability = { mode: 'done', headline: 'Done', lines: [`Done. ${n(f.signals)} signals fused, incident ${f.incidentId} closed out. Back to listening.`] };
    status.ghost = { mode: 'done', headline: 'Done', lines: [f.rerunPassed ? 'Done. The fix is verified: the failure no longer reproduces.' : 'Done validating. The post-fix rerun is still pending.'] };
    status.blast = { mode: 'done', headline: 'Done', lines: [`Done. ${n(f.impact.affected)} customers found and told first.`] };
    status.resolution = { mode: 'done', headline: 'Done', lines: [
      'Done. Bank X transfers now flow through Gateway C and the incident is resolved.',
      `Follow-ups still open: ${f.pending.length ? `${f.pending.length} approvals, including the connector patch` : 'connector patch review and reconciliation'}.`,
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
  5: [['blast', 'Impact mapped. Handing the plan to Resolution.'], ['resolution', 'Escalated to the humans. I will not change anything in production until an engineer approves the reroute.']],
  6: [['resolution', 'Approval received. Reroute applied. Asking Ghost Test to verify.'], ['ghost', 'Rerun complete: the failure no longer reproduces.'],
    ['resolution', 'Done. Incident resolved. All four agents are standing down; ask any of us if you need something.']],
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
    const { paced, quiet } = this.source();
    const target = this.target();
    if (!paced) {
      if (target !== this.displayStep) this.setDisplay(target, !quiet && target === this.displayStep + 1);
      return;
    }
    if (!this.synced) return;
    if (target < this.displayStep) {
      this.setDisplay(target, false);
    } else if (target > this.displayStep && Date.now() - this.stepSince >= (DWELL_MS[this.displayStep] ?? 8000)) {
      this.setDisplay(this.displayStep + 1, true);
    }
  }

  setDisplay(step, announce) {
    this.displayStep = step;
    this.stepSince = Date.now();
    this.refresh();
    // Handoff messages arrive one after another, like a conversation, not as a single burst.
    // A step that moves on before its handoffs finish flushes them first, so none are lost.
    window.clearTimeout(this.handoffTimer);
    (this.handoffQueue || []).forEach(([agent, text]) => this.post(agent, text, 'handoff'));
    this.handoffQueue = announce ? [...(HANDOFFS[step] || [])] : [];
    const next = () => {
      const item = this.handoffQueue.shift();
      if (!item) return;
      this.post(item[0], item[1], 'handoff');
      if (this.handoffQueue.length) this.handoffTimer = window.setTimeout(next, 1400);
    };
    next();
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
    // Once the incident is resolved the agents stay quiet in the channel until someone asks.
    if (this.displayStep === 6) {
      this.emit('beat');
      return;
    }
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
// A question is read for what kind of answer it wants (a count, a reason, a time, a list, a
// yes or no) and what it is about, then answered from the live incident in the voice of the
// agent who was asked. Unclear questions get a clarifying reply, never a status dump.

const TOPICS = [
  ['tests', 'ghost', /\b(tests?|tested|testing|runs?|ran|reproduc\w*|twin|sandbox|synthetic|controls?|disproof|reruns?|verif\w*|validat\w*)\b/g],
  ['hypotheses', 'observability', /\b(hypothes\w*|theor\w*|suspects?|candidates?|explanations?|decoys?|ruled out|zones?|options)\b/g],
  ['cause', 'ghost', /\b(cause[ds]?|root|culprit|what broke|went wrong|what happened|problem|issue|bug|fault|failing|failure|broken)\b/g],
  ['customers', 'blast', /\b(customers?|users?|people|clients?|affected|impact\w*|blast|silent|radius)\b/g],
  ['transactions', 'blast', /\b(transactions?|transfers?|payments?)\b/g],
  ['money', 'blast', /\b(money|naira|₦|exposure|amount|value|worth|cost|funds?|lost)\b/g],
  ['complaints', 'observability', /\b(complain\w*|reports?|tickets?|quotes?|calls?)\b/g],
  ['signals', 'observability', /\b(signals?|events?|logs?|telemetry|timeouts?|errors?|retry|retries|channel switch\w*|traffic|listening|monitor\w*)\b/g],
  ['approval', 'resolution', /\b(approv\w*|pending|sign ?off|decisions?|waiting|authori[sz]\w*)\b/g],
  ['eta', 'resolution', /\b(eta|how long|when will|by when|time to|fixed by|resolved by|duration)\b/g],
  ['rollback', 'resolution', /\b(rollback|roll back|undo|revert|guardrails?|safety)\b/g],
  ['plan', 'resolution', /\b(plan|fix|fixes|fixed|remediat\w*|reroute\w*|route|gateway c|contain\w*|patch\w*|steps?|solution)\b/g],
  ['message', 'blast', /\b(sms|messages?|notify|notified|notification\w*|apolog\w*|told)\b/g],
  ['reconciliation', 'resolution', /\b(reconcil\w*|reversals?|refunds?|batch)\b/g],
  ['release', 'ghost', /\b(app release|release|v4\.2|app version|deploy\w*)\b/g],
  ['confidence', 'ghost', /\b(confiden\w*|sure|certain|probabilit\w*|score|percent|threshold)\b/g],
  ['team', null, /\b(who are you|agents|team|your job|your role|what do you do|yourself|introduce)\b/g],
];
const TOPIC_OWNER = Object.fromEntries(TOPICS.map(([topic, owner]) => [topic, owner]));
const DOMAIN = {
  observability: ['signals', 'complaints', 'hypotheses'],
  ghost: ['tests', 'cause', 'confidence', 'release'],
  blast: ['customers', 'transactions', 'money', 'message'],
  resolution: ['plan', 'approval', 'eta', 'rollback', 'reconciliation'],
};
const EXAMPLES = {
  observability: ['How many complaints came in?', 'What signals did you see?', 'Which hypotheses did you write?'],
  ghost: ['How many tests ran?', 'What did you test?', 'Why are you confident it is Gateway B?'],
  blast: ['How many customers are affected?', 'How much money is exposed?', 'What does the SMS say?'],
  resolution: ['What is pending approval?', 'What is the rollback plan?', 'When will it be fixed?'],
};
const JOBS = {
  observability: "I'm Observability. I listen to every log, metric and complaint, open the incident and write hypotheses with a disproof condition for each.",
  ghost: "I'm Ghost Test. I try to break each hypothesis in an isolated twin with synthetic transfers, and only hand over a cause that survives.",
  blast: "I'm Blast Radius. I find every customer the validated failure touched, including the ones who never complained, and draft the apology.",
  resolution: "I'm Resolution. I plan the fix, simulate it in the twin and wait for a human to approve anything that touches production.",
};
const KINDS = [
  ['correction', /\b(not what i asked|that'?s not what|not my question|wrong (answer|agent)|you didn'?t answer|answer (the|my) question|i asked)\b|^(no|nope|wrong|huh|what)[\s.!?]*$/],
  ['help', /\b(help|what can (i|you) ask|commands?)\b/],
  ['count', /\b(how many|how much|number of|count|total)\b/],
  ['why', /\b(why|how come|how do you know|evidence|proof|reason|explain)\b/],
  ['when', /\b(when|what time|since when|how long ago)\b/],
  ['next', /\b(next|then what|after that|what now|what happens)\b/],
  ['status', /\b(doing|status|up to|working on|progress|busy|update|where are we|you there|alive)\b/],
  ['yesno', /^(is|are|did|does|do|was|were|has|have|can|will|should)\b/],
  ['list', /\b(what|which|list|show|tell me|describe|details?)\b/],
];

const TAGS = [
  ['observability', /@(observability|obs)\b/i],
  ['ghost', /@(ghost[\s-]?test|ghost)\b/i],
  ['blast', /@(blast[\s-]?radius|blast)\b/i],
  ['resolution', /@(resolution|resolve)\b/i],
];

// Agents tagged with @name in the question; only they reply.
export function taggedAgents(text) {
  return TAGS.filter(([, pattern]) => pattern.test(text)).map(([id]) => id);
}

const cleanQuestion = (question) => question.replace(/@[\w-]+(\s+(test|radius))?/gi, ' ').toLowerCase()
  .replace(/^\W*(hi|hey|hello|yo|good (morning|afternoon|evening))\b\W*/, '').replace(/\s+/g, ' ').trim();

// Topics ranked by how strongly the question mentions them; the asked agent's own subjects win ties.
function rankTopics(text, agentId) {
  return TOPICS.map(([topic, , pattern]) => ({ topic, score: (text.match(pattern) || []).length + (DOMAIN[agentId]?.includes(topic) ? 0.25 : 0) }))
    .filter((item) => item.score >= 1)
    .sort((a, b) => b.score - a.score)
    .map((item) => item.topic);
}

const kindOf = (text) => (text ? (KINDS.find(([, pattern]) => pattern.test(text)) || ['unknown'])[0] : 'greet');
const listOf = (items, limit = 4) => (items.length > limit ? `${items.slice(0, limit).join('; ')}; and ${items.length - limit} more` : items.join('; '));
const isRuled = (item) => ['rejected', 'ruled', 'disproved'].includes(item.state);
const runsText = (runs) => String(runs).replace(/^(\d+)\/(\d+)$/, '$1 of $2 runs');
const firstSentence = (text) => (claimParts(text).summary || text).replace(/\.$/, '');

function knowledge(D, step, ctx, statuses) {
  const f = facts(D, ctx);
  const summary = D.testSummary || { runs: 15, reproduced: 5, plans: D.tests.length, controls: 3 };
  const v = f.validated;
  const validatedTest = v && D.tests.find((test) => test.hypothesis === v.id);
  const release = D.hypotheses.find((item) => /release|customer app|mobile app/i.test(`${item.zone} ${item.claim}`));
  const approvalOf = (pattern) => D.approvals.find((item) => pattern.test(`${item.rawKind || ''} ${item.kind}`));
  return { f, D, step, statuses, summary, v, validatedTest, release, sms: approvalOf(/sms|customer message/i), recon: approvalOf(/reconciliation/i), config: approvalOf(/config|routing/i) };
}

function countAnswer(topic, k) {
  const { f, D, summary, v } = k;
  switch (topic) {
    case 'tests': return f.tests.length ? `${n(summary.runs)} synthetic runs across ${n(summary.plans)} test plans, ${n(summary.controls)} of them against a healthy control route.${v ? ` ${v.id} ${v.zone} reproduced the failure in ${runsText(k.validatedTest?.runs || `${summary.reproduced}/${summary.reproduced}`)} and validated as the cause.` : ''}` : 'None yet. I start testing as soon as Observability hands me hypotheses.';
    case 'hypotheses': {
      if (!f.hypotheses.length) return 'None yet. Observability writes them once the incident opens.';
      const ruled = f.hypotheses.filter(isRuled).length;
      const unclear = f.hypotheses.filter((item) => item.state === 'inconclusive').length;
      const open = f.hypotheses.length - ruled - unclear - (v ? 1 : 0);
      return `${f.hypotheses.length} hypotheses: ${v ? '1 validated' : 'none validated yet'}, ${ruled} ruled out${unclear ? `, ${unclear} inconclusive` : ''}${open > 0 ? `, ${open} ${k.step >= 4 ? 'not needed once the cause was proven' : 'still under test'}` : ''}.`;
    }
    case 'customers': return f.impact.affected ? `${n(f.impact.affected)} customers are affected. Only ${n(f.impact.complained)} complained; ${n(f.impact.silent)} never did.` : 'Not counted yet. I search for affected customers once Ghost Test validates the cause.';
    case 'transactions': return f.impact.transactions ? `${n(f.impact.transactions)} transactions are affected, out of ${n(f.transfers)} transfers in this run.` : `${n(f.transfers)} transfers have gone through so far; I count the affected ones once the cause is validated.`;
    case 'money': return f.impact.affected ? `About ${f.impact.exposure} across ${n(f.impact.transactions)} affected transactions.` : 'No exposure figure yet. It comes with the blast radius search.';
    case 'complaints': return f.complaints ? `${n(f.complaints)} complaints so far${f.lastComplaint ? `. The latest: “${f.lastComplaint}”` : '.'}` : 'No complaints yet.';
    case 'signals': return `${n(f.events)} events so far, including ${n(f.timeouts)} acknowledgement timeouts, ${n(f.retries)} retry loops and ${n(f.switches)} channel switches.`;
    case 'approval': return f.pending.length ? `${f.pending.length} pending: ${f.pending.map((item) => `${item.title} (needs ${item.owner})`).join('; ')}.` : 'Nothing is waiting for approval.';
    case 'message': return `The apology goes to ${n(k.sms?.payload?.recipient_count || f.impact.affected)} affected customers.`;
    case 'reconciliation': return k.recon?.payload ? `${n(k.recon.payload.item_count)} uncertain transfers worth ${naira(k.recon.payload.total_ngn)} are prepared for human review.` : listAnswer('reconciliation', k);
    default: return listAnswer(topic, k);
  }
}

function listAnswer(topic, k) {
  const { f, D, step, v } = k;
  switch (topic) {
    case 'tests': {
      if (!f.tests.length) return 'Nothing yet. I test as soon as Observability hands me hypotheses.';
      const label = (test) => {
        const hypothesis = D.hypotheses.find((item) => item.id === test.hypothesis);
        return `${test.hypothesis}${hypothesis ? ` ${hypothesis.zone}` : ''}: ${test.result}, reproduced in ${runsText(test.runs)}`;
      };
      return `I ran ${f.tests.length} test plans in the isolated twin, synthetic transfers only. ${listOf(f.tests.map(label))}.${v ? ` Verdict: ${v.id} ${v.zone} is the cause at ${v.confidence}%.` : ''}${step === 6 ? ` After the reroute, ${f.rerunPassed ? 'the rerun no longer reproduces the failure.' : 'the rerun is still pending.'}` : ''}`;
    }
    case 'hypotheses': return f.hypotheses.length ? `${f.hypotheses.length} hypotheses: ${listOf(f.hypotheses.map((item) => `${item.id} ${item.zone}, ${item.confidence}% (${item.state})`), 5)}.` : 'None yet. Observability writes them once the incident opens.';
    case 'cause': {
      if (v) return `${v.zone}. ${firstSentence(v.claim)}. Validated at ${v.confidence}%.`;
      const top = f.hypotheses[0];
      return top ? `Not confirmed yet. The leading suspect is ${top.id} ${top.zone}; Ghost Test is still trying to disprove it.` : 'Not known yet. The incident has not opened.';
    }
    case 'customers': case 'transactions': case 'money':
      return f.impact.affected ? `${n(f.impact.affected)} customers and ${n(f.impact.transactions)} transactions are affected, about ${f.impact.exposure} in exposure. Only ${n(f.impact.complained)} complained; ${n(f.impact.silent)} never did.` : 'Not measured yet. I search once Ghost Test validates a cause.';
    case 'complaints': return countAnswer('complaints', k);
    case 'signals': return countAnswer('signals', k);
    case 'approval': return f.pending.length ? `${countAnswer('approval', k)} Open Approvals to decide.` : step === 6 ? 'Nothing is blocking the incident. Follow-ups are in the approval queue.' : 'Nothing is waiting for approval right now.';
    case 'eta': return f.plan ? `Containment: ${f.plan.containmentEta}. Full fix: ${f.plan.fullEta}.` : 'No plan yet, so no ETA. It comes after the cause is validated.';
    case 'rollback': return f.plan ? f.plan.rollback : 'There is no change to roll back yet.';
    case 'plan': return f.plan ? `${f.plan.steps.length} steps: ${f.plan.steps.map((item, index) => `${index + 1}. ${item.title} (${item.status})`).join('; ')}.` : 'No plan yet. Resolution drafts it once the cause and impact are known.';
    case 'message': return D.sms ? `The SMS reads: “${D.sms}” ${k.sms ? `It is ${k.sms.status === 'pending' || !k.sms.status ? 'waiting for Customer Dispute to approve' : k.sms.status}.` : ''}` : 'No message drafted yet.';
    case 'reconciliation': return k.recon ? `${k.recon.title}. Avera only prepares the batch; a human executes every reversal.` : 'No reconciliation batch yet.';
    case 'release': {
      const r = k.release;
      return r ? `The app release shipped in the same window, so it was a suspect. ${r.id} was ${r.state} at ${r.confidence}%: the failure follows Gateway B, not the app version.` : 'The app release is not among the hypotheses yet.';
    }
    case 'confidence': return v ? `${v.id} ${v.zone} validated at ${v.confidence}%, above the 85% handoff threshold.` : 'Nothing has cleared the 85% threshold yet.';
    case 'team': return 'Four agents: Observability listens to every signal and writes hypotheses, Ghost Test tries to disprove them in a twin, Blast Radius finds everyone affected, and Resolution plans the fix and waits for human approval.';
    default: return null;
  }
}

function whyAnswer(topic, agentId, k) {
  const { f, v, summary } = k;
  const sim = f.plan?.simulationSuccess;
  switch (topic) {
    case 'customers': case 'transactions': case 'money': case 'complaints':
      return `Most affected customers never complain: they retry (${n(f.retries)}), refresh their history or switch channels (${n(f.switches)}). So I match the validated fingerprint against every transaction instead of waiting for calls.`;
    case 'approval': return `Every production change needs a named human.${sim != null ? ` The reroute passed ${sim}% in the twin,` : ''} but Avera never changes routing, messages customers or moves money on its own.`;
    case 'plan': case 'rollback': case 'eta': return `Gateway C is healthy for Bank X${sim != null ? ` and passed ${sim}% of twin transfers` : ''}, so rerouting contains the failure in minutes while the Gateway B patch is reviewed. If errors rise, it rolls back automatically.`;
    case 'release': return listAnswer('release', k);
    case 'tests': case 'cause': case 'confidence': case 'hypotheses':
      return v ? `${v.id} reproduced the failure in ${runsText(k.validatedTest?.runs || `${summary.reproduced}/${summary.reproduced}`)} through ${v.zone}, while the same transfers through a healthy control route did not fail. ${v.evidence.length ? `Supporting evidence: ${v.evidence.slice(0, 2).join('; ')}. ` : ''}It survived its disproof check: ${v.disproof.split(/;\s*/)[0].replace(/\.$/, '').replace(/^./, (c) => c.toLowerCase())}.` : 'I only trust a hypothesis after it survives its disproof condition in the twin, and none has yet.';
    default: break;
  }
  if (agentId === 'observability') return v ? `${v.id} rests on: ${v.evidence.slice(0, 3).join('; ') || 'route-level signals'}.` : `${n(f.timeouts)} timeouts on one route, ${n(f.complaints)} complaints and unusual retries all line up in time.`;
  if (agentId === 'blast') return whyAnswer('customers', agentId, k);
  if (agentId === 'resolution') return whyAnswer('approval', agentId, k);
  return whyAnswer('cause', agentId, k);
}

function yesNoAnswer(text, topic, k) {
  const { f, step } = k;
  if (/\b(fixed|resolved|over|done|working( again)?|better|back to normal)\b/.test(text)) {
    if (f.resolved) return `Yes. Bank X transfers now go through Gateway C and ${f.rerunPassed ? 'the rerun no longer reproduces the failure' : 'verification is running'}.`;
    return step >= 5 ? 'Not yet. The reroute is ready, but it waits for an engineer to approve it.' : 'Not yet. We are still working out the cause and impact.';
  }
  if (topic === 'release') return `No. ${listAnswer('release', k)}`;
  if (topic === 'message') return k.sms ? (k.sms.status === 'approved' ? 'Yes. The apology SMS was approved and sent.' : `Not yet. ${listAnswer('message', k)}`) : 'Not yet. No message has been drafted.';
  if (/\b(moved?|reverse[sd]?|refund(ed)?)\b/.test(text) && /\b(money|funds?|reversals?|refunds?)\b/.test(text)) return 'No. Avera never moves money. It prepares reconciliation batches for a human to execute.';
  if (topic === 'approval') return f.pending.length ? `Yes. ${countAnswer('approval', k)}` : 'No. Nothing is waiting for approval.';
  return null;
}

function whenAnswer(topic, k) {
  if (topic === 'eta' || topic === 'plan') return listAnswer('eta', k);
  const { D } = k;
  const incident = D.incident || {};
  return `The first failure signal arrived at ${incident.firstSignal || incident.openedAt || 'the start of the run'}${incident.openedAt ? `, and the incident opened at ${incident.openedAt}` : ''}${incident.detected ? `, ${incident.detected} after the failure began` : ''}.`;
}

function clarify(agentId) {
  const topics = DOMAIN[agentId].join(', ').replace(/, ([^,]*)$/, ' or $1');
  return `I'm not sure what you're asking. I can tell you about ${topics}. Try “${EXAMPLES[agentId][0]}”`;
}

function compose(agentId, kind, topic, text, k) {
  const status = k.statuses[agentId];
  const now = status.lines[0];
  if (kind === 'greet') return `Hi! I'm ${agentById[agentId].name}. Right now: ${now}`;
  if (kind === 'help') return `Ask me anything about the incident. For example: ${EXAMPLES[agentId].map((example) => `“${example}”`).join(', ')}. Tag an agent with @ to ask them directly.`;
  if (topic === 'team' && /\b(who are you|your (job|role)|what do you do|yourself|introduce)\b/.test(text)) return `${JOBS[agentId]} Right now: ${now}`;
  if (kind === 'status' && (!topic || topic === 'team')) return status.lines[0]?.startsWith(status.headline) ? status.lines.join(' ') : `${status.headline}. ${status.lines.join(' ')}`;
  if (kind === 'next') return { 0: 'If anything breaks, Observability opens an incident.', 1: 'Next, Observability fuses these signals into an incident and writes hypotheses.', 2: 'Next, Ghost Test tries to disprove every hypothesis in the twin.', 3: 'Next, Blast Radius finds every affected customer.', 4: 'Next, Resolution asks a human to approve the fix.', 5: 'Once you approve, the reroute is applied and Ghost Test verifies it.', 6: 'The incident is resolved. Follow-ups are the patch review and reconciliation.' }[k.step];
  if (kind === 'when') return whenAnswer(topic, k);
  if (kind === 'yesno') {
    const yesNo = yesNoAnswer(text, topic, k);
    if (yesNo) return yesNo;
  }
  if (!topic) {
    if (kind === 'why') return whyAnswer(null, agentId, k);
    if (kind === 'status' || kind === 'list' && /\b(doing|now)\b/.test(text)) return compose(agentId, 'status', null, text, k);
    return clarify(agentId);
  }
  if (kind === 'count') return countAnswer(topic, k);
  if (kind === 'why') return whyAnswer(topic, agentId, k);
  return listAnswer(topic, k) || clarify(agentId);
}

// The last question asked, so "that's not what I asked" can be answered properly.
let lastAsked = null;

// Answer an engineer's question. Tagged agents (or the selected one) answer alone, in their own
// voice; otherwise the agent who owns the topic, or whoever is on point, answers.
export function answer(question, chosenAgent, D, step, ctx) {
  const tags = taggedAgents(question);
  const statuses = agentStatuses(D, step, ctx);
  const k = knowledge(D, step, ctx, statuses);
  let text = cleanQuestion(question);
  let kind = kindOf(text);
  let skip = null;
  let apology = '';
  let askedBy = tags.length ? tags : chosenAgent ? [chosenAgent] : null;
  if (kind === 'correction') {
    if (!lastAsked) return [{ agent: askedBy?.[0] || STEP_OWNER[step], text: `Sorry about that. What would you like to know? ${EXAMPLES[askedBy?.[0] || STEP_OWNER[step]].map((example) => `“${example}”`).join(', ')}` }];
    // Re-read the previous question, skipping the reading that missed.
    ({ text, kind } = lastAsked);
    skip = lastAsked.topic;
    askedBy = askedBy || lastAsked.responders;
    apology = 'Sorry, I misread that. ';
  }
  const lead = askedBy?.[0];
  const ranked = rankTopics(text, lead).filter((topic) => topic !== skip);
  const topic = ranked[0] || null;
  if (skip && !topic) {
    return askedBy.map((agentId) => ({ agent: agentId, text: `Sorry. I read “${text}” as a question about ${skip}. What did you mean? For example: ${EXAMPLES[agentId].map((example) => `“${example}”`).join(', ')}` }));
  }
  // When several agents are tagged, the one who owns the subject answers first.
  const responders = askedBy ? [...askedBy].sort((a, b) => (b === TOPIC_OWNER[topic]) - (a === TOPIC_OWNER[topic])) : [TOPIC_OWNER[topic] || STEP_OWNER[step]];
  lastAsked = { text, kind, topic, responders };
  const replies = [];
  responders.forEach((agentId) => {
    let reply = apology + compose(agentId, kind, topic, text, k);
    const owner = TOPIC_OWNER[topic];
    // Agents answer outside their area too, but say whose results they are quoting.
    if (owner && owner !== agentId && ['count', 'list', 'why', 'yesno'].includes(kind) && !reply.startsWith("I'm not sure")) reply = `${apology}From ${agentById[owner].name}'s results: ${reply.slice(apology.length)}`;
    // Several tagged agents should not repeat one another word for word.
    const echo = replies.find((earlier) => earlier.text.endsWith(reply.replace(/^From [^:]+: /, '')));
    if (echo) reply = `Agreed with ${agentById[echo.agent].name}. From my side: ${statuses[agentId].lines[0]}`;
    replies.push({ agent: agentId, text: reply });
  });
  return replies;
}
