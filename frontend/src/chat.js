// Agent channel: agents post what they are doing; engineers can ask any of them a question.
// Built once outside the app root so live re-renders never interrupt a conversation.

import { AGENTS, agentById, answer } from './agents.js';

const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const time = (date) => date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const avatar = (id, mode = 'idle') => `/assets/agents/${id}/agent-${mode}.png`;

export function createChat({ director, context }) {
  const root = document.createElement('div');
  root.className = 'agent-chat';
  root.innerHTML = `
    <button class="chat-launcher" type="button" aria-expanded="false" aria-controls="agent-chat-panel">
      <span class="launcher-avatars">${AGENTS.map((agent) => `<img src="${avatar(agent.id)}" alt="" style="--agent:${agent.color}"/>`).join('')}</span>
      <span class="launcher-copy"><strong>Agent channel</strong><small data-chat-ticker>Agents are listening</small></span>
      <em class="launcher-unread" hidden>0</em>
    </button>
    <section class="chat-panel" id="agent-chat-panel" aria-label="Agent channel" hidden>
      <header class="chat-head">
        <div><span class="chat-live"><i></i> Live</span><strong>Agent channel</strong><small>Agents report every 5 seconds. Ask them anything about the incident.</small></div>
        <button type="button" data-chat-close aria-label="Close agent channel">×</button>
      </header>
      <div class="chat-roster" role="tablist" aria-label="Ask an agent">
        <button type="button" class="active" data-chat-target="" role="tab" aria-selected="true"><b>All</b><small>Whoever is on point</small></button>
        ${AGENTS.map((agent) => `<button type="button" data-chat-target="${agent.id}" role="tab" aria-selected="false" style="--agent:${agent.color}"><img src="${avatar(agent.id)}" alt=""/><b>${agent.name}</b><small data-roster-status="${agent.id}">—</small></button>`).join('')}
      </div>
      <ol class="chat-log" aria-live="polite"></ol>
      <div class="chat-suggestions">${['What are you doing?', 'How many customers are affected?', 'What is pending approval?', 'Why are you confident?'].map((q) => `<button type="button" data-chat-suggest="${escape(q)}">${q}</button>`).join('')}</div>
      <form class="chat-form">
        <input name="question" autocomplete="off" maxlength="240" placeholder="Ask the agents…" aria-label="Ask the agents"/>
        <button type="submit">Send</button>
      </form>
    </section>`;
  document.body.appendChild(root);

  const launcher = root.querySelector('.chat-launcher');
  const panel = root.querySelector('.chat-panel');
  const log = root.querySelector('.chat-log');
  const unread = root.querySelector('.launcher-unread');
  const ticker = root.querySelector('[data-chat-ticker]');
  const input = root.querySelector('input');
  let target = '';
  let rendered = 0;
  let unseen = 0;

  const setOpen = (open) => {
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', String(open));
    root.classList.toggle('open', open);
    if (open) {
      unseen = 0;
      unread.hidden = true;
      log.scrollTop = log.scrollHeight;
      input.focus();
    }
  };

  const draw = () => {
    const messages = director.messages;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    for (; rendered < messages.length; rendered += 1) {
      const message = messages[rendered];
      const agent = agentById[message.agent];
      const item = document.createElement('li');
      item.className = `chat-message ${message.agent === 'engineer' ? 'from-engineer' : ''} kind-${message.kind}`;
      item.style.setProperty('--agent', agent?.color || 'var(--wine)');
      item.innerHTML = message.agent === 'engineer'
        ? `<div class="bubble"><p>${escape(message.text)}</p><time>${time(message.at)}</time></div>`
        : `<img src="${avatar(message.agent, message.kind === 'handoff' ? 'result' : 'idle')}" alt=""/><div class="bubble"><span>${agent.name}${message.kind === 'handoff' ? ' · handoff' : message.kind === 'answer' ? ' · reply' : ''}</span><p>${escape(message.text)}</p><time>${time(message.at)}</time></div>`;
      log.appendChild(item);
      if (panel.hidden && message.agent !== 'engineer') unseen += 1;
    }
    while (log.children.length > 120) log.removeChild(log.firstChild);
    if (atBottom || messages.at(-1)?.agent === 'engineer') log.scrollTop = log.scrollHeight;
    unread.hidden = !unseen;
    unread.textContent = unseen > 99 ? '99+' : String(unseen);
    const last = [...messages].reverse().find((message) => message.agent !== 'engineer');
    if (last) ticker.textContent = `${agentById[last.agent].name}: ${last.text}`;
  };

  const drawRoster = () => {
    AGENTS.forEach((agent) => {
      const status = director.statuses?.[agent.id];
      const cell = root.querySelector(`[data-roster-status="${agent.id}"]`);
      if (cell && status) cell.textContent = status.headline;
      root.querySelector(`[data-chat-target="${agent.id}"]`)?.classList.toggle('on-point', Boolean(status?.active));
    });
  };

  const ask = (question) => {
    const text = question.trim();
    if (!text) return;
    director.postUser(text);
    root.classList.add('typing');
    const { D, step, ctx } = context();
    const replies = answer(text, target || null, D, step, ctx);
    replies.forEach((replyItem, index) => {
      window.setTimeout(() => {
        director.post(replyItem.agent, replyItem.text, 'answer');
        if (index === replies.length - 1) root.classList.remove('typing');
      }, 650 + index * 750);
    });
  };

  launcher.addEventListener('click', () => setOpen(panel.hidden));
  root.querySelector('[data-chat-close]').addEventListener('click', () => setOpen(false));
  root.querySelector('.chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    ask(input.value);
    input.value = '';
  });
  root.querySelectorAll('[data-chat-suggest]').forEach((button) => button.addEventListener('click', () => ask(button.dataset.chatSuggest)));
  root.querySelectorAll('[data-chat-target]').forEach((button) => button.addEventListener('click', () => {
    target = button.dataset.chatTarget;
    root.querySelectorAll('[data-chat-target]').forEach((other) => {
      other.classList.toggle('active', other === button);
      other.setAttribute('aria-selected', String(other === button));
    });
    input.placeholder = target ? `Ask ${agentById[target].name}…` : 'Ask the agents…';
    input.focus();
  }));

  director.subscribe((kind) => {
    if (kind === 'message') draw();
    else drawRoster();
  });
  drawRoster();

  return { open: () => setOpen(true), element: root };
}
