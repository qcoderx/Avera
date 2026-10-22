const BASE = '/api/bank-demo';

async function request(path, method = 'GET', body) {
  const response = await fetch(BASE + path, {
    method,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || 'The bank request failed.');
  return payload;
}

export const bankApi = {
  state: () => request('/state'),
  reset: () => request('/reset', 'POST'),
  transfer: (amount_ngn) => request('/transfer', 'POST', { amount_ngn }),
  report: (complaint) => request('/report', 'POST', { complaint }),
};
