// Client for the Avera API (see backend/avera/API_CONTRACT.md).
// API traffic stays same-origin and is forwarded by Vite in development and by
// the Vercel rewrite in production. This avoids exposing the backend to browser CORS.
const BASE = '/api';

export class ApiError extends Error {
  constructor(status, detail, path) {
    super(typeof detail === 'string' ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
}

async function request(path, { method = 'GET', body, timeout = 20000, query } = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  const url = new URL(`${BASE}${path}`, window.location.origin);
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
  });
  try {
    const response = await fetch(url, {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!response.ok) {
      const detail = data && typeof data === 'object' ? data.detail : data;
      throw new ApiError(response.status, Array.isArray(detail) ? detail.map((item) => item.msg).join('; ') : detail, path);
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, error.name === 'AbortError' ? 'The API did not respond in time' : 'The API is unreachable', path);
  } finally {
    window.clearTimeout(timer);
  }
}

export const apiBase = BASE;

export const api = {
  health: () => request('/health', { timeout: 8000 }),
  serviceMap: () => request('/service-map'),
  observability: () => request('/observability'),
  investigate: () => request('/observability/investigate', { method: 'POST', timeout: 90000 }),
  replayObservations: (until) => request('/observability/replay', { method: 'POST', query: { until }, timeout: 90000 }),
  replayFixture: () => request('/replay', { method: 'POST' }),
  demoStart: () => request('/demo/start', { method: 'POST', timeout: 60000 }),
  demoInject: (options = {}) => request('/demo/inject', { method: 'POST', body: options, timeout: 90000 }),
  demoReset: () => request('/demo/reset', { method: 'POST', timeout: 60000 }),
  submitHypotheses: (hypotheses, runs = 5) => request('/hypotheses', { method: 'POST', body: { hypotheses, runs }, timeout: 60000 }),
  rerunGhostTest: (hypothesisId, runs = 5) => request('/ghost-test/rerun', { method: 'POST', body: { hypothesis_id: hypothesisId, runs }, timeout: 60000 }),
  simulateTwin: (toGateway, destinationBank = 'BANK_X', runs = 5) => request('/twin/simulate', { method: 'POST', body: { to_gateway: toGateway, destination_bank: destinationBank, runs }, timeout: 60000 }),
  ingest: (source, type, data, occurredAt) => request(`/ingest/${encodeURIComponent(source)}`, { method: 'POST', body: { type, data, ...(occurredAt ? { occurred_at: occurredAt } : {}) } }),
  approvals: (query) => request('/approvals', { query }),
  approval: (id) => request(`/approvals/${encodeURIComponent(id)}`),
  decide: (id, decision, payloadHash, reason) => request(`/approvals/${encodeURIComponent(id)}`, {
    method: 'POST',
    body: { decision, payload_hash: payloadHash, ...(reason ? { reason } : {}) },
  }),
  remediation: (incidentId) => request(`/remediation/${encodeURIComponent(incidentId)}`),
};

// Ordered SSE stream. EventSource resends Last-Event-ID on reconnect, so no record is lost.
export function openStream({ after = 0, onRecord, onOpen, onError }) {
  const source = new EventSource(`${BASE}/stream${after ? `?after=${after}` : ''}`);
  const types = ['event', 'complaint', 'incident', 'hypothesis', 'test_result', 'fix_plan', 'blast_radius', 'approval', 'resolution_event'];
  const handle = (message) => {
    try {
      onRecord(JSON.parse(message.data));
    } catch {
      // A malformed record is skipped; the next record still advances the cursor.
    }
  };
  types.forEach((type) => source.addEventListener(type, handle));
  source.onmessage = handle;
  source.onopen = () => onOpen?.();
  source.onerror = () => onError?.(source.readyState);
  return () => source.close();
}
