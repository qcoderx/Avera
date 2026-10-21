// Folds the ordered /stream records into the view model the command centre renders.
// The model keeps the same shape as fixtures.js, so every view works on live or recorded data.

import { fixture } from './fixtures.js';

const ZONE_NODE = {
  'core-banking': 'core',
  gateway_a: 'ga',
  gateway_b: 'gb',
  gateway_c: 'gc',
  'mobile_app.release': 'app',
  'partner_bank.destination-bank-bank_x': 'bankx',
  'partner_bank.destination-bank-bank_y': 'banky',
  'partner_bank.destination-bank-bank_z': 'bankz',
};

const SERVICE_NODE = {
  'customer-app': 'app',
  'mobile-app': 'app',
  'transfer-ui': 'app',
  'ussd-channel': 'ussd',
  'api-gateway': 'api',
  'core-banking': 'core',
  'payment-router': 'router',
  gateway_a: 'ga',
  gateway_b: 'gb',
  gateway_c: 'gc',
  'destination-bank-bank_x': 'bankx',
  'destination-bank-bank_y': 'banky',
  'destination-bank-bank_z': 'bankz',
};

const PRETTY = {
  gateway_a: 'Gateway A', gateway_b: 'Gateway B', gateway_c: 'Gateway C',
  BANK_X: 'Bank X', BANK_Y: 'Bank Y', BANK_Z: 'Bank Z',
  'core-banking': 'Core banking', 'payment-router': 'Payment router', 'api-gateway': 'API gateway',
  'mobile_app.release': 'Mobile app release', 'customer-app': 'Mobile app', 'ussd-channel': 'USSD',
  payments_integration: 'Payments integration', payments_operations: 'Payments operations',
  core_banking: 'Core banking team', mobile_engineering: 'Mobile engineering', partner_liaison: 'Partner liaison',
  reconciliation: 'Reconciliation', customer_dispute: 'Customer Dispute', engineer: 'Engineering',
  blast_radius_agent: 'Blast Radius agent', resolution_agent: 'Resolution agent',
};

export function pretty(value) {
  if (!value) return '';
  if (PRETTY[value]) return PRETTY[value];
  const bank = /^partner_bank\.destination-bank-bank_(\w)$/.exec(value);
  if (bank) return `Bank ${bank[1].toUpperCase()}`;
  const route = /^(gateway_\w)\.bank_(\w)$/.exec(value);
  if (route) return `${PRETTY[route[1]]} → Bank ${route[2].toUpperCase()}`;
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

const ERROR_PHRASE = {
  ACK_TIMEOUT: 'are not receiving acknowledgements',
};

function clock(ts, seconds = true) {
  if (!ts) return '—';
  const date = new Date(ts);
  return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) });
}

function duration(fromTs, toTs) {
  if (!fromTs || !toTs) return '—';
  const seconds = Math.max(0, Math.round((new Date(toTs) - new Date(fromTs)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function naira(value) {
  const amount = Number(value) || 0;
  if (amount >= 1e9) return `₦${(amount / 1e9).toFixed(1)}bn`;
  if (amount >= 1e6) return `₦${(amount / 1e6).toFixed(1)}m`;
  if (amount >= 1e3) return `₦${(amount / 1e3).toFixed(1)}k`;
  return `₦${amount.toLocaleString('en-NG')}`;
}

function emptyRun() {
  return {
    started: false,
    eventCount: 0,
    transfers: 0,
    transferTimes: [],
    failureTimes: [],
    firstTs: null,
    lastTs: null,
    firstFailureTs: null,
    codes: {},
    serviceCodes: {},
    latency: {},
    release: null,
    evidence: new Map(),
    complaints: [],
    incident: null,
    hypotheses: new Map(),
    tests: new Map(),
    reruns: [],
    fixPlan: null,
    blast: null,
    approvals: new Map(),
    resolutions: [],
    feed: [],
    milestones: {},
  };
}

export class LiveStore {
  constructor() {
    this.run = emptyRun();
    this.lastSequence = 0;
    this.version = 0;
    this.listeners = new Set();
    this.pending = false;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    if (this.pending) return;
    this.pending = true;
    window.setTimeout(() => {
      this.pending = false;
      this.listeners.forEach((listener) => listener());
    }, 250);
  }

  reset() {
    this.run = emptyRun();
    this.version += 1;
    this.notify();
  }

  feed(record, text, tone, channel) {
    this.run.feed.push({ seq: record.sequence, ts: record.data.ts || record.occurred_at, text, tone, channel });
    if (this.run.feed.length > 60) this.run.feed.shift();
  }

  milestone(key, ts) {
    if (!this.run.milestones[key]) this.run.milestones[key] = ts;
  }

  ingest(record) {
    if (record.sequence <= this.lastSequence) return;
    this.lastSequence = record.sequence;
    const { type, data } = record;
    // A new demo run restarts simulator event IDs; everything before it belongs to the old run.
    if (type === 'event' && data.id === 'evt-sim-000001') this.run = emptyRun();
    const run = this.run;
    const ts = data.ts || record.occurred_at;

    if (type === 'event') {
      run.started = true;
      run.eventCount += 1;
      run.firstTs ||= ts;
      run.lastTs = ts;
      run.codes[data.code] = (run.codes[data.code] || 0) + 1;
      const key = `${data.service}:${data.code}`;
      run.serviceCodes[key] = (run.serviceCodes[key] || 0) + 1;
      if (data.latency_ms != null) {
        const entry = run.latency[data.service] || (run.latency[data.service] = { sum: 0, count: 0 });
        entry.sum += data.latency_ms;
        entry.count += 1;
      }
      run.evidence.set(data.id, `${pretty(data.service)} · ${data.code?.replace(/_/g, ' ').toLowerCase()}`);
      if (data.code === 'TRANSFER_REQUESTED') {
        run.transfers += 1;
        run.transferTimes.push(Date.parse(ts));
      }
      if (data.code === 'ACK_TIMEOUT') {
        run.failureTimes.push(Date.parse(ts));
        if (!run.firstFailureTs) {
          run.firstFailureTs = ts;
          this.milestone('failure', ts);
          this.feed(record, `${pretty(data.service)} stops acknowledging ${pretty(data.payload?.destination_bank) || 'transfers'}`, 'coral', 'Signal');
        }
      }
      if (data.code === 'APP_RELEASED') {
        run.release = { ts, version: data.payload?.app_version || data.payload?.version };
        this.feed(record, `App ${run.release.version ? `v${run.release.version} ` : ''}released`, 'amber', 'Change');
      }
      if (data.code === 'CHANNEL_SWITCH' && !run.milestones.switch) {
        this.milestone('switch', ts);
        this.feed(record, 'Mobile customers switch to USSD', 'amber', 'Channels');
      }
      if (data.code === 'RETRY_LOOP' && !run.milestones.retry) {
        this.milestone('retry', ts);
        this.feed(record, 'Customers retry stuck transfers', 'coral', 'Behaviour');
      }
    } else if (type === 'complaint') {
      run.complaints.push({ id: data.id, ts, channel: data.channel, text: data.raw_text, customer: data.customer_token });
      if (run.complaints.length === 1) this.milestone('complaint', ts);
      if (run.complaints.length <= 3 || run.complaints.length % 5 === 0) this.feed(record, `“${data.raw_text}”`, 'coral', `Complaint · ${data.channel}`);
    } else if (type === 'incident') {
      if (run.incident && run.incident.id !== data.id) {
        // A re-investigation opens a new incident; its evidence and workflow start fresh.
        Object.assign(run, { hypotheses: new Map(), tests: new Map(), reruns: [], fixPlan: null, blast: null, resolutions: [] });
        ['hypotheses', 'validated', 'plan', 'impact', 'approvals', 'resolved'].forEach((key) => { delete run.milestones[key]; });
        this.feed(record, `Re-investigation opened ${data.id}`, 'teal', 'Avera');
      }
      const groups = (data.fingerprint?.groups || []).map(({ transactions, ...group }) => group);
      const behaviours = {};
      (data.fingerprint?.behaviours || []).forEach((item) => { behaviours[item.kind] = (behaviours[item.kind] || 0) + 1; });
      run.incident = { id: data.id, status: data.status, openedAt: data.opened_at, suspectZones: data.suspect_zones || [], signals: data.signals?.length || 0, groups, behaviours };
      this.milestone('incident', ts);
      this.feed(record, `Incident ${data.id} ${data.status}`, 'teal', 'Avera');
    } else if (type === 'hypothesis') {
      run.hypotheses.set(data.id, data);
      if (run.hypotheses.size === 1) this.milestone('hypotheses', ts);
    } else if (type === 'test_result') {
      if (data.purpose === 'post_fix_rerun') {
        run.reruns.push(data);
      } else {
        run.tests.set(data.hypothesis_id, data);
        if (data.verdict === 'validated' && !run.milestones.validated) {
          this.milestone('validated', ts);
          const hypothesis = run.hypotheses.get(data.hypothesis_id);
          this.feed(record, `${pretty(hypothesis?.zone)} clears the ${Math.round(data.confidence * 100)}% confidence bar`, 'violet', 'Ghost Test');
        }
      }
    } else if (type === 'fix_plan') {
      run.fixPlan = data;
      this.milestone('plan', ts);
      this.feed(record, 'Fix plan prepared with rollback', 'teal', 'Resolution');
    } else if (type === 'blast_radius') {
      const { customers, ...blast } = data;
      run.blast = blast;
      this.milestone('impact', ts);
      this.feed(record, `${blast.customer_count.toLocaleString('en-NG')} affected customers found`, 'amber', 'Blast Radius');
    } else if (type === 'approval') {
      const previous = run.approvals.get(data.id);
      run.approvals.set(data.id, { ...data, seq: previous?.seq ?? record.sequence });
      if (!previous) {
        this.milestone('approvals', ts);
        this.feed(record, `${kindLabel(data.kind)} awaits ${pretty(data.payload?.required_role)} approval`, 'coral', 'Approval');
      } else if (previous.status !== data.status && data.status !== 'pending') {
        this.feed(record, `${kindLabel(data.kind)} ${data.status}${data.approver ? ` by ${data.approver}` : ''}`, data.status === 'approved' ? 'teal' : 'coral', 'Approval');
      }
    } else if (type === 'resolution_event') {
      run.resolutions.push(data);
      if (data.status === 'resolved') this.milestone('resolved', ts);
      this.feed(record, resolutionText(data), data.status === 'resolved' || data.status === 'verified_pass' ? 'teal' : 'violet', 'Resolution');
    }
    this.version += 1;
    this.notify();
  }

  step() {
    const run = this.run;
    if (run.resolutions.some((item) => item.status === 'resolved')) return 6;
    if ([...run.approvals.values()].some((item) => item.status === 'pending')) return 5;
    if (run.blast || run.fixPlan) return 4;
    if (run.tests.size) return 3;
    if (run.incident) return 2;
    if (run.firstFailureTs || run.complaints.length) return 1;
    return 0;
  }
}

export function kindLabel(kind) {
  return { sms: 'Customer message', config: 'Routing change', code: 'Code review', reconciliation: 'Reconciliation batch' }[kind] || pretty(kind);
}

function resolutionText(event) {
  const detail = event.detail || {};
  return {
    apply_approved_reroute: `Reroute applied: ${pretty(detail.from_route)} → ${pretty(detail.to_route)}`,
    verify_after_approved_reroute: event.status === 'verified_pass' ? 'Ghost Tests rerun green after the reroute' : 'Post-fix verification failed',
    mark_incident_resolved: 'Incident resolved and verified',
    open_connector_patch_for_review: 'Connector patch opened for engineer review',
  }[event.action] || `${pretty(event.action)} · ${pretty(event.status)}`;
}

function percent(numerator, denominator) {
  return denominator ? (numerator / denominator) * 100 : 100;
}

function healthState(value) {
  return value >= 99 ? 'healthy' : value >= 90 ? 'watch' : 'critical';
}

function buckets(times, from, to, count) {
  const out = new Array(count).fill(0);
  const span = Math.max(1, to - from);
  times.forEach((time) => { out[Math.min(count - 1, Math.max(0, Math.floor(((time - from) / span) * count)))] += 1; });
  return out;
}

function hypothesisState(test) {
  if (!test) return 'testing';
  return { validated: 'validated', rejected: 'rejected', inconclusive: 'inconclusive' }[test.verdict] || 'testing';
}

// Build the fixture-shaped view model from the live run.
export function viewModel(store, routes, displayStep) {
  const run = store.run;
  const step = displayStep ?? store.step();
  const base = fixture;
  const sc = (service, code) => run.serviceCodes[`${service}:${code}`] || 0;
  const approvals = run.incident ? [...run.approvals.values()].filter((item) => item.payload?.incident_id === run.incident.id) : [];
  const approvalOf = (kind) => approvals.filter((item) => item.kind === kind).sort((a, b) => b.seq - a.seq)[0];
  const config = approvalOf('config');
  const sms = approvalOf('sms');
  const code = approvalOf('code');
  const recon = approvalOf('reconciliation');
  const failing = (run.incident?.groups || []).filter((group) => group.failures > 0).sort((a, b) => b.failures - a.failures)[0];
  const topError = Object.keys(ERROR_PHRASE).find((key) => run.codes[key]) || null;

  // Hypotheses ranked by Ghost Test confidence, then the model's prior rank.
  const ranked = [...run.hypotheses.values()].map((hypothesis) => ({ hypothesis, test: run.tests.get(hypothesis.id) }))
    .sort((a, b) => (b.test?.confidence ?? -1) - (a.test?.confidence ?? -1) || a.hypothesis.prior_rank - b.hypothesis.prior_rank);
  const label = new Map(ranked.map((item, index) => [item.hypothesis.id, `H${index + 1}`]));
  const hypotheses = ranked.map(({ hypothesis, test }, index) => ({
    id: label.get(hypothesis.id),
    realId: hypothesis.id,
    node: ZONE_NODE[hypothesis.zone] || null,
    short: shortClaim(hypothesis),
    rank: index + 1,
    confidence: test ? Math.round(test.confidence * 100) : 0,
    state: hypothesisState(test),
    zone: pretty(hypothesis.zone),
    owner: (hypothesis.routed_to || []).map(pretty).join(', ') || 'Unassigned',
    claim: hypothesis.claim,
    evidence: hypothesis.evidence_for.map((id) => run.evidence.get(id) || id),
    against: hypothesis.evidence_against.map((id) => run.evidence.get(id) || id).join('; ') || 'No contrary observations recorded.',
    disproof: hypothesis.disproof,
  }));
  const top = hypotheses[0];

  const tests = ranked.filter((item) => item.test).map(({ hypothesis, test }) => ({
    id: test.id.length > 18 ? `T-${String(ranked.findIndex((item) => item.test === test) + 1).padStart(2, '0')}` : test.id,
    realId: test.id,
    hypothesis: label.get(hypothesis.id),
    name: test.test_plan,
    runs: `${test.reproduced}/${test.runs}`,
    result: { validated: 'failed as predicted', rejected: 'hypothesis disproved', inconclusive: 'inconclusive' }[test.verdict],
    duration: test.control ? `Control ${pretty(test.control.route)} · ${test.control.reproduced}/${test.control.runs}` : 'No control registered',
    trace: test.trace.map((item) => item.latency_ms ?? (item.error_code ? 900 : 20)),
    peak: Math.max(0, ...test.trace.map((item) => item.latency_ms || 0)),
    verdict: { validated: 'match', rejected: 'pass', inconclusive: 'reject' }[test.verdict],
  }));
  const testRuns = ranked.reduce((sum, item) => sum + (item.test ? item.test.runs + (item.test.control?.runs || 0) : 0), 0);
  const validatedTest = ranked.find((item) => item.test?.verdict === 'validated');

  // Service health from the observed acknowledgement flow.
  const acked = run.codes.ACK_RECEIVED || 0;
  const timeouts = run.codes.ACK_TIMEOUT || 0;
  const gb = percent(sc('gateway_b', 'ACK_RECEIVED'), sc('gateway_b', 'ACK_RECEIVED') + sc('gateway_b', 'ACK_TIMEOUT'));
  const coreLatency = run.latency['core-banking'] ? Math.round(run.latency['core-banking'].sum / run.latency['core-banking'].count) : null;
  const services = [
    { name: 'Mobile app', value: 100, delta: `${sc('transfer-ui', 'RETRY_LOOP')} retry loops` },
    { name: 'API gateway', value: 100, delta: `${run.transfers.toLocaleString('en-NG')} requests` },
    { name: 'Core banking', value: percent(run.codes.DEBIT_POSTED || 0, run.transfers), delta: coreLatency != null ? `${coreLatency} ms` : '—' },
    { name: 'Payment router', value: percent(acked, acked + timeouts), delta: `${sc('payment-router', 'IDEMPOTENT_RETRY')} idempotent retries` },
    { name: 'Gateway B', value: gb, delta: `${sc('gateway_b', 'ACK_TIMEOUT').toLocaleString('en-NG')} timeouts` },
    { name: 'Bank X connector', value: percent(sc('destination-bank-bank_x', 'CREDIT_ACCEPTED') - sc('gateway_b', 'ACK_TIMEOUT'), sc('destination-bank-bank_x', 'CREDIT_ACCEPTED')), delta: timeouts ? 'credits with no ack' : 'acknowledged' },
  ].map((item) => ({ ...item, value: Number(item.value.toFixed(2)), state: healthState(item.value) }));

  const span = run.transferTimes.length ? [Math.min(...run.transferTimes), Math.max(...run.transferTimes)] : [0, 1];
  const throughput = run.transferTimes.length ? buckets(run.transferTimes, span[0], span[1], 28) : base.health.throughput;
  const failureAt = run.failureTimes.length ? (Math.min(...run.failureTimes) - span[0]) / Math.max(1, span[1] - span[0]) : null;

  const blast = run.blast;
  // Complainers are the distinct customers behind complaint records; everyone else affected stayed silent.
  const complained = new Set(run.complaints.map((item) => item.customer).filter(Boolean)).size;
  const affected = blast?.customer_count ?? 0;
  const silent = Math.max(0, affected - complained);
  const behaviours = run.incident?.behaviours || {};
  const behaviourRows = [
    ['Retried transfers', behaviours.retries || 0],
    ['Refreshed history', behaviours.repeated_refreshes || 0],
    ['Switched channel', behaviours.channel_switching || 0],
  ].map(([labelText, value]) => ({ label: labelText, value, share: affected ? Math.round((value / affected) * 100) : 0 }));

  const plan = run.fixPlan;
  const resolved = run.resolutions.some((item) => item.status === 'resolved');
  const applied = run.resolutions.some((item) => item.action === 'apply_approved_reroute' && item.status === 'applied');
  const verified = run.resolutions.some((item) => item.status === 'verified_pass');
  const stepStatus = (title) => {
    const lower = title.toLowerCase();
    if (lower.includes('reroute') || lower.includes('contain')) return applied ? 'applied' : config?.status === 'pending' ? 'approval' : config?.status || 'prepared';
    if (lower.includes('reconcil')) return recon?.status === 'pending' ? 'approval' : recon?.status === 'approved' ? 'approved' : 'queued';
    if (lower.includes('patch')) return code ? (code.status === 'pending' ? 'review' : code.status) : 'prepared';
    return 'queued';
  };
  const planSteps = plan ? [...plan.steps.map((item, index) => ({ n: String(index + 1).padStart(2, '0'), owner: pretty(item.owner), title: item.title, detail: item.description, status: stepStatus(item.title) })),
    { n: String(plan.steps.length + 1).padStart(2, '0'), owner: 'Ghost Test', title: 'Verify service restoration', detail: plan.verification, status: verified ? 'verified' : 'waiting' }] : [];

  const approvalItems = approvals.sort((a, b) => a.seq - b.seq).map((item) => ({
    id: item.id,
    level: item.level === 'level_3' ? 3 : 2,
    kind: kindLabel(item.kind),
    rawKind: item.kind,
    title: approvalTitle(item),
    owner: pretty(item.payload?.required_role),
    requiredRole: item.payload?.required_role,
    status: item.status,
    closedReason: item.payload?.closed_reason,
    risk: item.kind === 'sms' ? 'Low' : item.kind === 'code' ? 'Medium' : item.kind === 'reconciliation' ? 'Medium' : 'Medium',
    created: clock(item.payload?.requested_at, false),
    expires: clock(item.payload?.expires_at, false),
    approver: item.approver,
    decidedAt: item.decided_at,
    payload: item.payload || {},
    payloadHash: item.payload?.payload_hash,
  }));

  const m = run.milestones;
  const timeline = [
    m.failure && { at: m.failure, time: clock(m.failure, false), title: 'Acknowledgements stop', detail: `${pretty(failing?.gateway) || 'A gateway'} stops acknowledging ${pretty(failing?.destination) || 'transfers'}.`, type: 'signal' },
    run.release && { at: run.release.ts, time: clock(run.release.ts, false), title: 'App release ships', detail: `Mobile app ${run.release.version ? `v${run.release.version}` : 'release'} goes live in the same window.`, type: 'signal' },
    m.complaint && { at: m.complaint, time: clock(m.complaint, false), title: 'First complaints', detail: `${complained} customer report${complained === 1 ? '' : 's'} received so far.`, type: 'signal' },
    m.incident && { at: m.incident, time: clock(m.incident, false), title: 'Incident opened', detail: `Signal fusion linked ${run.incident?.signals.toLocaleString('en-NG')} signals into ${run.incident?.id}.`, type: 'agent' },
    m.hypotheses && { at: m.hypotheses, time: clock(m.hypotheses, false), title: 'Hypotheses routed', detail: `${hypotheses.length} explanations sent to Ghost Test with disproof conditions.`, type: 'agent' },
    m.validated && { at: m.validated, time: clock(m.validated, false), title: 'Cause validated', detail: `${validatedTest ? pretty(validatedTest.hypothesis.zone) : 'Top hypothesis'} reached ${top?.confidence ?? 0}% confidence.`, type: 'verified' },
    m.impact && { at: m.impact, time: clock(m.impact, false), title: 'Impact calculated', detail: `${affected.toLocaleString('en-NG')} customers and ${blast?.txn_count.toLocaleString('en-NG')} transactions affected.`, type: 'agent' },
    m.approvals && { at: m.approvals, time: clock(m.approvals, false), title: 'Approvals requested', detail: `${approvalItems.length} actions entered the approval queue.`, type: 'approval' },
    ...run.resolutions.map((item) => ({ at: item.ts, time: clock(item.ts, false), title: resolutionText(item), detail: item.status.replace(/_/g, ' '), type: item.verified_by_ghost_test ? 'verified' : 'agent' })),
  ].filter(Boolean).sort((a, b) => new Date(a.at) - new Date(b.at));

  const milestoneTimes = [run.firstTs, m.failure, m.incident, m.validated, m.impact, m.approvals, m.resolved];
  const demoSteps = base.demoSteps.map((item, index) => ({ ...item, time: milestoneTimes[index] ? clock(milestoneTimes[index], false) : '—' }));

  const pending = approvalItems.filter((item) => item.status === 'pending');
  const incidentTitle = failing
    ? `Transfers to ${pretty(failing.destination)} ${ERROR_PHRASE[topError] || 'are failing'}`
    : step >= 1 ? 'Transfers are failing without an alert' : base.health.nominalLabel;

  const agents = [
    { ...base.agents[0], status: step >= 2 ? 'result' : 'listening', state: run.incident ? 'Incident fingerprinted' : 'Listening to signals', detail: `${run.eventCount.toLocaleString('en-NG')} signals fused` },
    { ...base.agents[1], status: step === 3 ? 'working' : validatedTest ? 'result' : 'listening', state: validatedTest ? `${label.get(validatedTest.hypothesis.id)} validated` : run.tests.size ? 'Testing hypotheses' : 'Waiting for hypotheses', detail: `${run.tests.size} tests complete` },
    { ...base.agents[2], status: blast ? 'result' : step >= 3 ? 'working' : 'listening', state: blast ? 'Impact found' : 'Waiting for a validated cause', detail: blast ? `${affected.toLocaleString('en-NG')} found` : '—' },
    { ...base.agents[3], status: resolved ? 'result' : 'listening', state: resolved ? 'Incident resolved' : pending.length ? 'Waiting for approval' : 'Standing by', detail: `${pending.length} decision${pending.length === 1 ? '' : 's'} pending` },
  ];

  const signals = run.feed.slice(-7).map((item) => ({ time: clock(item.ts), channel: item.channel, text: item.text, tone: item.tone }));

  return {
    ...base,
    live: true,
    step,
    incident: {
      ...base.incident,
      id: run.incident?.id || 'No incident',
      severity: run.incident ? 'High' : '—',
      status: run.incident?.status || 'listening',
      title: incidentTitle,
      elapsed: duration(run.incident?.openedAt, run.lastTs),
      openedAt: clock(run.incident?.openedAt, false),
      firstSignal: clock(m.failure || m.complaint),
      detected: duration(m.failure || m.complaint, m.incident),
    },
    health: { ...base.health, services, throughput, failureAt, transferCount: run.transfers },
    agents,
    signals: signals.length ? signals : [{ time: clock(new Date()), channel: 'Avera', text: run.started ? 'Listening to live traffic' : 'Waiting for the demo to start', tone: 'teal' }],
    hypotheses,
    tests,
    testSummary: { runs: testRuns, reproduced: validatedTest?.test.reproduced ?? 0, plans: tests.length, controls: ranked.filter((item) => item.test?.control).length, elapsed: duration(m.hypotheses, m.validated || run.lastTs) },
    impact: {
      complained,
      transactions: blast?.txn_count ?? 0,
      affected,
      silent,
      exposure: blast ? naira(blast.exposure_ngn) : '—',
      prevention: affected ? Number(((silent / affected) * 100).toFixed(1)) : 0,
      segments: behaviourRows,
      segmentsTitle: 'Behaviour signals',
      firstAffected: clock(m.failure, false),
      snapshot: clock(m.impact),
    },
    fixPlan: plan ? {
      containmentEta: plan.eta_containment,
      fullEta: plan.eta_full,
      reconciliationEta: recon ? `${(recon.payload.item_count || 0).toLocaleString('en-NG')} items` : '—',
      reconciliationLabel: 'Reconciliation batch',
      simulationSuccess: config?.payload?.simulated_success_rate != null ? Number((config.payload.simulated_success_rate * 100).toFixed(1)) : null,
      steps: planSteps,
      rollback: plan.rollback,
      cause: validatedTest ? `${pretty(validatedTest.hypothesis.zone)}: ${validatedTest.hypothesis.claim}` : plan.steps[0]?.title,
      owners: plan.owners.map(pretty),
    } : null,
    approvals: approvalItems,
    sms: sms?.payload?.message || '',
    timeline,
    demoSteps,
    times: { failure: clock(m.failure, false), release: clock(run.release?.ts, false), openedAt: run.incident?.openedAt || null, firstSignalAt: m.failure || m.complaint || null },
    mapStates: liveMapStates({ step, hypotheses, blast, complained, affected, silent, routes, run, applied, config, failing }),
  };
}

function shortClaim(hypothesis) {
  const zone = pretty(hypothesis.zone);
  return hypothesis.claim.length <= 38 ? hypothesis.claim : `${zone} under test`;
}

function approvalTitle(item) {
  const p = item.payload || {};
  if (item.kind === 'config') return `Move ${pretty(p.destination_bank)} traffic to ${pretty(p.to_route?.split('.')[0])}`;
  if (item.kind === 'sms') return `Notify ${(p.recipient_count || 0).toLocaleString('en-NG')} affected customers`;
  if (item.kind === 'reconciliation') return `Prepare reconciliation of ${(p.item_count || 0).toLocaleString('en-NG')} transfers`;
  if (item.kind === 'code') return 'Patch Gateway B acknowledgement handling';
  return kindLabel(item.kind);
}

// Map visuals follow the fixture story beats; numbers, suspects and routes come from the live run.
function liveMapStates({ step, hypotheses, blast, complained, affected, silent, routes, run, applied, config, failing }) {
  return fixture.mapStates.map((state, index) => {
    if (index !== step) return state;
    const next = structuredClone(state);
    const metrics = { ...next.metrics };
    if (failing) metrics.gb = `${(run.codes.ACK_TIMEOUT || 0).toLocaleString('en-NG')} timeouts`;
    if (run.latency['core-banking']) metrics.core = `${Math.round(run.latency['core-banking'].sum / run.latency['core-banking'].count)} ms`;
    next.metrics = metrics;
    if (index >= 2 && index <= 4) {
      next.hypotheses = {};
      next.status = { ...next.status };
      hypotheses.forEach((item) => {
        if (!item.node) return;
        const mapped = { validated: 'proven', rejected: 'ruled', inconclusive: 'open', testing: 'testing' }[item.state];
        next.hypotheses[item.node] = { id: item.id, state: index === 2 && item.state === 'testing' ? 'open' : mapped, score: item.confidence, short: item.short };
        if (!next.status[item.node]) next.status[item.node] = item.state === 'validated' ? 'critical' : item.state === 'rejected' ? 'cleared' : 'suspect';
        if (item.state === 'rejected' && index >= 3) next.status[item.node] = 'cleared';
      });
    }
    if (next.customers) {
      const found = blast ? `${complained.toLocaleString('en-NG')} complained · ${silent.toLocaleString('en-NG')} never did` : next.customers.detail;
      next.customers = { ...next.customers, detail: index >= 4 ? found : next.customers.detail };
    }
    if (routes?.BANK_X && routes.BANK_X !== 'gateway_b' && applied) next.reroute = 'active';
    next.insight = { ...next.insight, facts: liveFacts(index, next.insight.facts, { hypotheses, affected, complained, silent, run, config }) };
    return next;
  });
}

function liveFacts(index, facts, { hypotheses, affected, complained, silent, run, config }) {
  const validated = hypotheses.find((item) => item.state === 'validated');
  const top = hypotheses[0];
  switch (index) {
    case 1: return [['Failed transfers', (run.codes.ACK_TIMEOUT || 0).toLocaleString('en-NG')], ['Alerts raised', '0'], ['Customer reports', String(run.complaints.length)]];
    case 2: return [['Signals fused', run.incident?.signals.toLocaleString('en-NG') || '—'], ['Suspect zones', String(run.incident?.suspectZones.length || 0)], ['Hypotheses', `${hypotheses.length} competing`]];
    case 3: return [['Tests run', String(run.tests.size)], ['Top hypothesis', top ? `${top.id} · ${top.confidence}%` : '—'], ['Validated', validated ? validated.zone : 'Testing']];
    case 4: return [['Validated cause', validated ? `${validated.id} · ${validated.confidence}%` : '—'], ['Affected customers', affected.toLocaleString('en-NG')], ['Never complained', silent.toLocaleString('en-NG')]];
    case 5: return [['Simulated success', config?.payload?.simulated_success_rate != null ? `${(config.payload.simulated_success_rate * 100).toFixed(1)}%` : '—'], ['Containment ETA', run.fixPlan?.eta_containment || '—'], ['Rollback trigger', config?.payload?.rollback ? `errors > ${config.payload.rollback.threshold * 100}%` : '—']];
    case 6: return [['Ghost Test rerun', run.resolutions.some((item) => item.status === 'verified_pass') ? 'Passed' : 'Pending'], ['Bank X route', 'Gateway C'], ['Follow-ups', String(run.resolutions.find((item) => item.status === 'resolved')?.detail?.open_follow_ups?.length || 0)]];
    default: return facts;
  }
}

export { SERVICE_NODE, ZONE_NODE, clock };
