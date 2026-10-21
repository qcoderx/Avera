// Client for the Avera API (see backend/avera/API_CONTRACT.md).
// In development the Vite server proxies /api to the backend; a deployed build can
// point straight at the API with VITE_AVERA_API_BASE.

const BASE = (import.meta.env.VITE_AVERA_API_BASE || '/api').replace(/\/$/, '');
const TOKEN_KEY = 'avera.approver-tokens';

export class ApiError extends Error {
  constructor(status, detail, path) {
    super(typeof detail === 'string' ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
}

async function request(path, { method = 'GET', body, token, timeout = 20000, query } = {}) {
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
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
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
  devToken: (role) => request('/dev/auth/token', { method: 'POST', body: { role } }),
  approvals: (token, query) => request('/approvals', { token, query }),
  approval: (id, token) => request(`/approvals/${encodeURIComponent(id)}`, { token }),
  decide: (id, token, decision, payloadHash, reason) => request(`/approvals/${encodeURIComponent(id)}`, {
    method: 'POST',
    token,
    body: { decision, payload_hash: payloadHash, ...(reason ? { reason } : {}) },
  }),
  remediation: (incidentId, token) => request(`/remediation/${encodeURIComponent(incidentId)}`, { token }),
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

// Approver tokens live only for this browser session and are scoped by role.
function decodeClaims(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '=')));
  } catch {
    return null;
  }
}

function readTokens() {
  try {
    return JSON.parse(window.sessionStorage.getItem(TOKEN_KEY) || '{}');
  } catch {
    return {};
  }
}

function writeTokens(tokens) {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
  } catch {
    // Storage can be unavailable (private mode); the token then lasts for this page only.
  }
  memoryTokens = tokens;
}

let memoryTokens = readTokens();

export const auth = {
  get(role) {
    const entry = memoryTokens[role];
    if (!entry) return null;
    if (entry.exp && entry.exp * 1000 < Date.now() + 15000) {
      auth.clear(role);
      return null;
    }
    return entry;
  },
  set(token) {
    const claims = decodeClaims(token.trim());
    if (!claims?.role || !claims?.sub) throw new Error('That token has no approver role or subject');
    if (claims.exp && claims.exp * 1000 < Date.now()) throw new Error('That token has already expired');
    writeTokens({ ...memoryTokens, [claims.role]: { token: token.trim(), role: claims.role, sub: claims.sub, exp: claims.exp } });
    return memoryTokens[claims.role];
  },
  clear(role) {
    const next = { ...memoryTokens };
    delete next[role];
    writeTokens(next);
  },
  async signIn(role) {
    const response = await api.devToken(role);
    return auth.set(response.access_token);
  },
};
