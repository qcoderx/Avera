// Turns agent and model output (snake_case ids, service slugs, SCREAMING codes, run-on
// "Observations:" paragraphs) into text an engineer can read at a glance. They need to know what the agents are doing

const ACRONYMS = new Set(['ACK', 'API', 'USSD', 'SMS', 'ETA', 'ID', 'HTTP', 'NIP', 'OTP']);
const SERVICES = { 'payment-router': 'payment router', 'api-gateway': 'API gateway', 'core-banking': 'core banking', 'mobile-app': 'mobile app', 'customer-app': 'customer app', 'transfer-ui': 'transfer screen', 'ussd-gateway': 'USSD gateway' };

const letter = (value) => value.toUpperCase();
const words = (value) => value.split(/[_-]+/).filter(Boolean);

function code(value) {
  return words(value).map((word) => (ACRONYMS.has(word.toUpperCase()) ? word.toUpperCase() : word.toLowerCase())).join(' ');
}

// Bank, gateway and service ids. A route (gateway_b.bank_x) reads as its gateway.
function names(text) {
  return text
    .replace(/(?:partner_bank\.)?destination-bank-bank_([a-z])\b/gi, (_, id) => `Bank ${letter(id)}`)
    .replace(/\b(?:destination|partner)-bank-([a-z])\b/gi, (_, id) => `Bank ${letter(id)}`)
    .replace(/\bgateway-([a-z])\b/gi, (_, id) => `Gateway ${letter(id)}`)
    .replace(/\bgateway_([a-z])\.bank_[a-z]\b/gi, (_, id) => `Gateway ${letter(id)}`)
    .replace(/\bgateway_([a-z])\b/gi, (_, id) => `Gateway ${letter(id)}`)
    .replace(/\bbank_([a-z])\b/gi, (_, id) => `Bank ${letter(id)}`)
    .replace(/\b(payment-router|api-gateway|core-banking|mobile-app|customer-app|transfer-ui|ussd-gateway)\b/gi, (match) => SERVICES[match.toLowerCase()]);
}

// Plain-text rewrite: safe for chat bubbles and attributes as well as HTML.
export function humanize(text) {
  if (text == null) return '';
  return names(String(text)
    .replace(/^\s*inference:\s*/i, '')
    .replace(/\s*\((?:obs|evt|ghost|plan)-[^)]*\)/g, '')
    .replace(/\b([a-z]+(?:_[a-z]+)+):([^\s,;]+?)(?=[\s,;]|\.(?:\s|$)|$)/g, (_, metric, scope) => `${metric.replace(/_/g, ' ')} (${names(scope)})`))
    .replace(/\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b/g, code)
    .replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (match) => match.replace(/_/g, ' '))
    .replace(/\s{2,}/g, ' ')
    .replace(/^./, (first) => first.toUpperCase())
    .trim();
}

const sentence = (value) => {
  const clean = value.trim().replace(/^(and|but)\s+/i, '').replace(/[.;,\s]+$/, '');
  return clean ? clean[0].toUpperCase() + clean.slice(1) : '';
};

// A claim becomes one headline sentence plus the observations behind it.
export function claimParts(text) {
  const plain = humanize(text);
  const [head, ...rest] = plain.split(/\s*\bObservations?:\s*/i);
  const sentences = head.split(/(?<=[.!?])\s+(?=[A-Z])/);
  const summary = sentence(sentences.shift() || '');
  const points = sentences.map(sentence);
  rest.join(' ').split(/(?<=[.!?])\s+(?=[A-Z])/).forEach((part, index) => {
    // The first observation sentence is usually a comma list: one bullet per item.
    const items = index === 0 && (part.match(/,\s/g) || []).length >= 1 ? part.split(/,\s+|;\s+/) : [part];
    items.map(sentence).forEach((item) => points.push(item));
  });
  return { summary, points: points.filter(Boolean) };
}

const escapeHtml = (value) => value.replace(/[&<>"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));

export function claimHtml(text, { headline = 'strong' } = {}) {
  const { summary, points } = claimParts(text);
  return `<${headline} class="claim-summary">${escapeHtml(summary)}.</${headline}>${points.length ? `<ul class="claim-points">${points.map((point) => `<li>${escapeHtml(point)}</li>`).join('')}</ul>` : ''}`;
}
