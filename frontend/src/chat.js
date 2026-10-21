// Agent channel: agents post what they are doing; engineers can ask any of them a question.
// Built once outside the app root so live re-renders never interrupt a conversation.

import { AGENTS, agentById, answer, taggedAgents } from './agents.js';

const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const time = (date) => date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const avatar = (id, mode = 'idle') => `/assets/agents/${id}/agent-${mode}.png`;
const HANDLES = { observability: 'Observability', ghost: 'GhostTest', blast: 'BlastRadius', resolution: 'Resolution' };
// Escape first, then mark @tags so engineers can see who they addressed.
const highlight = (text) => escape(text).replace(/@([\w-]+)/g, (match, name) => (taggedAgents(match).length ? `<mark>@${name}</mark>` : match));

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
        <div><span class="chat-live"><i></i> Live</span><strong>Agent channel</strong><small>Agents report every 5 seconds. Type @ to tag one; only tagged agents reply.</small></div>
        <button type="button" data-chat-close aria-label="Close agent channel">×</button>
      </header>
      <div class="chat-roster" role="tablist" aria-label="Ask an agent">
        <button type="button" class="active" data-chat-target="" role="tab" aria-selected="true"><b>All</b><small>Whoever is on point</small></button>
        ${AGENTS.map((agent) => `<button type="button" data-chat-target="${agent.id}" role="tab" aria-selected="false" style="--agent:${agent.color}"><img src="${avatar(agent.id)}" alt=""/><b>${agent.name}</b><small data-roster-status="${agent.id}">—</small></button>`).join('')}
      </div>
      <ol class="chat-log" aria-live="polite"></ol>
      <div class="chat-suggestions">${['What are you doing?', 'How many customers are affected?', 'What is pending approval?', 'Why are you confident?'].map((q) => `<button type="button" data-chat-suggest="${escape(q)}">${q}</button>`).join('')}</div>
      <form class="chat-form">
        <ul class="mention-menu" role="listbox" aria-label="Tag an agent" hidden>${AGENTS.map((agent) => `<li role="option" data-mention="${agent.id}" style="--agent:${agent.color}"><img src="${avatar(agent.id)}" alt=""/><b>@${HANDLES[agent.id]}</b><small>${agent.name}</small></li>`).join('')}</ul>
        <input name="question" autocomplete="off" maxlength="240" placeholder="Ask the agents… type @ to tag one" aria-label="Ask the agents"/>
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
  const menu = root.querySelector('.mention-menu');
  const options = [...menu.querySelectorAll('[data-mention]')];
  let menuIndex = 0;
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
        ? `<div class="bubble"><p>${highlight(message.text)}</p><time>${time(message.at)}</time></div>`
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
    // A tag in the message wins over the roster selection: only tagged agents reply.
    const replies = answer(text, taggedAgents(text).length ? null : target || null, D, step, ctx);
    replies.forEach((replyItem, index) => {
      window.setTimeout(() => {
        director.post(replyItem.agent, replyItem.text, 'answer');
        if (index === replies.length - 1) root.classList.remove('typing');
      }, 650 + index * 750);
    });
  };

  const mentionQuery = () => input.value.slice(0, input.selectionStart ?? input.value.length).match(/(^|\s)@(\w*)$/);
  const visibleOptions = () => options.filter((option) => !option.hidden);
  const refreshMenu = () => {
    const query = mentionQuery();
    if (!query) {
      menu.hidden = true;
      return;
    }
    const needle = query[2].toLowerCase();
    options.forEach((option) => {
      const id = option.dataset.mention;
      option.hidden = Boolean(needle) && !(`${HANDLES[id]} ${agentById[id].name} ${id}`.toLowerCase().includes(needle));
    });
    const visible = visibleOptions();
    menu.hidden = !visible.length;
    menuIndex = Math.min(menuIndex, Math.max(visible.length - 1, 0));
    visible.forEach((option, index) => option.classList.toggle('active', index === menuIndex));
  };
  const pickMention = (id) => {
    const caret = input.selectionStart ?? input.value.length;
    const before = input.value.slice(0, caret).replace(/@\w*$/, `@${HANDLES[id]} `);
    input.value = before + input.value.slice(caret).replace(/^\s+/, '');
    input.setSelectionRange(before.length, before.length);
    menu.hidden = true;
    input.focus();
  };
  input.addEventListener('input', () => {
    menuIndex = 0;
    refreshMenu();
  });
  input.addEventListener('keydown', (event) => {
    if (menu.hidden) return;
    const visible = visibleOptions();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      menuIndex = (menuIndex + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length;
      refreshMenu();
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      if (visible[menuIndex]) pickMention(visible[menuIndex].dataset.mention);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      menu.hidden = true;
    }
  });
  input.addEventListener('blur', () => window.setTimeout(() => { menu.hidden = true; }, 120));
  menu.addEventListener('mousedown', (event) => {
    const option = event.target.closest('[data-mention]');
    if (!option) return;
    event.preventDefault();
    pickMention(option.dataset.mention);
  });

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
    input.placeholder = target ? `Ask ${agentById[target].name}…` : 'Ask the agents… type @ to tag one';
    input.focus();
  }));

  director.subscribe((kind) => {
    if (kind === 'message') draw();
    else drawRoster();
  });
  drawRoster();

  return { open: () => setOpen(true), element: root };
}
