import '../styles.css';
import './bank.css';
import { bankApi } from './api.js';

const app = document.querySelector('#app');
let bank = null;
let screen = 'home';
let busy = false;
let reportOpen = false;
let transferResult = null;
let toast = null;
let pollTimer = null;

document.title = 'Avera Mobile';

const paths = {
  arrowLeft: '<path d="m15 18-6-6 6-6"/>', bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  card: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18"/>', check: '<path d="m5 12 4 4L19 6"/>',
  chevron: '<path d="m9 18 6-6-6-6"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9a2.5 2.5 0 1 1 3.3 2.4c-.9.4-.9 1-.9 1.6M12 17h.01"/>',
  home: '<path d="m3 11 9-8 9 8M5 10v10h14V10M9 20v-6h6v6"/>', phone: '<rect x="7" y="2" width="10" height="20" rx="3"/><path d="M11 18h2"/>',
  receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6"/>', send: '<path d="m3 11 18-8-8 18-2-8zM11 13 21 3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  wallet: '<path d="M4 6h14a2 2 0 0 1 2 2v11H4a2 2 0 0 1-2-2V6a3 3 0 0 1 3-3h12v3M15 11h7v5h-7a2.5 2.5 0 0 1 0-5z"/>',
};

function icon(name, size = 20) {
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
}

function naira(value) {
  return `₦${new Intl.NumberFormat('en-NG', { maximumFractionDigits: 0 }).format(Number(value || 0))}`;
}

function isReviewing() {
  return bank && ['reported', 'investigating', 'awaiting_approval', 'patching'].includes(bank.status);
}

function activityRow(item) {
  const credit = item.direction === 'credit';
  return `<li class="transaction-row">
    <span class="transaction-icon ${credit ? 'credit' : ''}">${icon(credit ? 'wallet' : 'send', 17)}</span>
    <span class="transaction-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.subtitle)}</small></span>
    <span class="transaction-amount ${credit ? 'credit' : ''}">${credit ? '+' : '−'}${naira(item.amount_ngn)}</span>
  </li>`;
}

function customerNotice() {
  if (!bank.incident && bank.status !== 'awaiting_report') return '';
  if (bank.status === 'awaiting_report') return `<button class="account-notice warning" data-action="open-report"><span>${icon('help', 18)}</span><span><strong>Transfer needs attention</strong><small>We could not confirm it. Check your activity and tell us what happened.</small></span>${icon('chevron', 16)}</button>`;
  if (isReviewing()) return `<div class="account-notice"><span class="notice-spinner"></span><span><strong>We’re reviewing your report</strong><small>You can safely leave this page. We’ll update your account here.</small></span></div>`;
  if (bank.status === 'resolved') return `<div class="account-notice resolved"><span>${icon('check', 18)}</span><span><strong>Your money has been returned</strong><small>The extra debit was reversed. Future transfers can now be made normally.</small></span></div>`;
  return '';
}

function bottomNav(active) {
  return `<nav class="bottom-nav" aria-label="Main navigation"><button class="${active === 'home' ? 'active' : ''}" data-action="home">${icon('home', 19)}<span>Home</span></button><button data-action="transfer">${icon('send', 19)}<span>Transfers</span></button><button>${icon('receipt', 19)}<span>Payments</span></button><button>${icon('user', 19)}<span>Profile</span></button></nav>`;
}

function homeScreen() {
  return `<div class="mobile-view home-view"><header class="mobile-header"><a class="mobile-brand" href="/bank" aria-label="Avera home"><span>A</span><strong>Avera</strong></a><div class="header-actions"><button aria-label="Notifications">${icon('bell', 19)}<i></i></button><button class="avatar" aria-label="Profile">AO</button></div></header><main class="mobile-content">
    <div class="welcome"><p>Good morning, Amara</p><h1>Here’s your money.</h1></div>
    <section class="account-card"><div class="account-top"><span>Available balance</span><button aria-label="Hide balance">${icon('eye', 17)}</button></div><strong>${naira(bank.account.available_ngn)}<sup>.00</sup></strong><small>Everyday account · ${escapeHtml(bank.account.account_number)}</small><div class="card-rings"></div></section>
    <div class="quick-actions"><button data-action="transfer"><span>${icon('send', 20)}</span><small>Send</small></button><button><span>${icon('phone', 20)}</span><small>Airtime</small></button><button><span>${icon('receipt', 20)}</span><small>Pay bills</small></button><button><span>${icon('card', 20)}</span><small>Cards</small></button></div>
    ${customerNotice()}
    <section class="activity-section"><div class="section-title"><h2>Recent activity</h2><button>See all</button></div><ul>${bank.ledger.map(activityRow).join('')}</ul></section>
  </main>${bottomNav('home')}</div>`;
}

function transferScreen() {
  return `<div class="mobile-view transfer-view"><header class="transfer-header"><button data-action="home" aria-label="Go back">${icon('arrowLeft', 22)}</button><h1>Send money</h1><span></span></header><main class="transfer-content">
    <div class="recipient-card"><span>TA</span><div><small>Sending to</small><strong>${escapeHtml(bank.beneficiary.name)}</strong><p>${escapeHtml(bank.beneficiary.bank)} · ${escapeHtml(bank.beneficiary.account_number)}</p></div><i>${icon('check', 14)}</i></div>
    <form id="transfer-form"><label class="amount-field"><span>How much?</span><div><b>₦</b><input name="amount" type="number" min="100" max="1000000" step="100" value="25000" inputmode="numeric" required></div></label>
      <div class="source-account"><span>${icon('wallet', 18)}</span><div><small>From</small><strong>Everyday account</strong><p>${naira(bank.account.available_ngn)} available</p></div>${icon('chevron', 16)}</div>
      <label class="note-field"><span>Narration <small>optional</small></span><input name="note" maxlength="40" placeholder="What’s this for?"></label><div class="transfer-summary"><span>Transfer fee</span><strong>₦0.00</strong></div>
      <button class="send-button" type="submit" ${busy ? 'disabled' : ''}>${busy ? '<i></i> Sending…' : 'Send money'}</button></form>
  </main></div>`;
}

function reportSheet() {
  if (!reportOpen) return '';
  const defaultText = bank.status === 'awaiting_report' ? `I made one ${naira(bank.transfer.amount_ngn)} transfer, it timed out, and I can see two debits.` : '';
  return `<div class="sheet-backdrop" data-action="close-report"><section class="report-sheet" role="dialog" aria-modal="true" aria-labelledby="report-title"><div class="sheet-handle"></div><div class="sheet-heading"><div><h2 id="report-title">Report an issue</h2><p>Tell us what happened with your account.</p></div><button data-action="close-report" aria-label="Close">${icon('close', 19)}</button></div><form id="report-form"><label><span>What happened?</span><textarea name="complaint" minlength="10" maxlength="500" required>${escapeHtml(defaultText)}</textarea></label><button type="submit" ${busy || bank.status !== 'awaiting_report' ? 'disabled' : ''}>${busy ? 'Sending…' : bank.status === 'awaiting_report' ? 'Send report' : 'No issue to report'}</button></form></section></div>`;
}

function resultSheet() {
  if (!transferResult) return '';
  const failed = transferResult === 'timeout';
  return `<div class="sheet-backdrop result-backdrop"><section class="result-sheet" role="dialog" aria-modal="true"><span class="result-mark ${failed ? 'failed' : ''}">${icon(failed ? 'close' : 'check', 28)}</span><h2>${failed ? 'We couldn’t confirm this transfer' : 'Transfer sent'}</h2><p>${failed ? 'The request timed out. Check your activity before trying again.' : `Your ${naira(bank.transfer.amount_ngn)} transfer to Tobi Adeyemi was successful.`}</p><button data-action="dismiss-result">${failed ? 'View account activity' : 'Done'}</button></section></div>`;
}

function render() {
  if (!bank) { app.innerHTML = '<div class="bank-loading"><span>A</span><p>Opening Avera…</p></div>'; return; }
  app.innerHTML = `<div class="bank-stage"><div class="phone-shadow"></div><div class="phone-frame"><div class="phone-island"><i></i></div><div class="phone-screen">${screen === 'transfer' ? transferScreen() : homeScreen()}${toast ? `<div class="bank-toast">${icon('check', 16)}<span>${escapeHtml(toast)}</span></div>` : ''}${reportSheet()}${resultSheet()}<button class="report-widget" data-action="open-report">${icon('help', 17)}<span>Report an issue</span></button></div><div class="phone-home-indicator"></div></div></div>`;
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = window.setInterval(async () => {
    try {
      bank = await bankApi.state(); render();
      if (['resolved', 'failed'].includes(bank.status)) { window.clearInterval(pollTimer); pollTimer = null; }
    } catch (_) { /* A transient status refresh must not break the bank UI. */ }
  }, 900);
}

async function submitTransfer(form) {
  if (busy) return;
  busy = true; render();
  try {
    bank = await bankApi.transfer(Number(new FormData(form).get('amount')));
    transferResult = bank.status === 'awaiting_report' ? 'timeout' : 'success';
  } catch (error) { toast = error.message; }
  finally { busy = false; render(); }
}

async function submitReport(form) {
  if (busy) return;
  busy = true; render();
  try {
    bank = await bankApi.report(new FormData(form).get('complaint'));
    reportOpen = false; toast = 'Your report has been sent.';
    window.setTimeout(() => { toast = null; render(); }, 2800);
    startPolling();
  } catch (error) { toast = error.message; }
  finally { busy = false; render(); }
}

app.addEventListener('submit', (event) => {
  event.preventDefault();
  if (event.target.id === 'transfer-form') submitTransfer(event.target);
  if (event.target.id === 'report-form') submitReport(event.target);
});

app.addEventListener('click', (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'transfer') { screen = 'transfer'; render(); }
  if (action === 'home') { screen = 'home'; render(); }
  if (action === 'open-report') { reportOpen = true; render(); }
  if (action === 'close-report' && (event.target === button || button.tagName === 'BUTTON')) { reportOpen = false; render(); }
  if (action === 'dismiss-result') { transferResult = null; screen = 'home'; render(); }
});

bankApi.state().then((state) => { bank = state; render(); if (isReviewing()) startPolling(); }).catch((error) => {
  app.innerHTML = `<div class="bank-loading"><span>!</span><p>${escapeHtml(error.message)}</p></div>`;
});
