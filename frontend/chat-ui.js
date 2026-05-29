function setStatus(label, online = true) {
  document.getElementById('status-label').textContent = label;
  document.getElementById('status-dot').style.background = online
    ? (label === 'streaming' ? 'var(--warn)' : 'var(--success)')
    : 'var(--danger)';
  document.getElementById('status-dot').style.boxShadow = `0 0 6px ${online ? (label === 'streaming' ? 'var(--warn)' : 'var(--success)') : 'var(--danger)'}`;
}

async function sendMessage(queuedContent) {
  const input = document.getElementById('msg-input');
  const content = (queuedContent !== undefined) ? queuedContent : input.value.trim();
  if (!content || !state.currentSession) return;

  if (queuedContent === undefined) {
    if (content && state.inputHistory[state.inputHistory.length - 1] !== content) {
      state.inputHistory.push(content);
    }
    setState({ inputHistoryIndex: -1, inputDraft: '' });
  }

  if (content.startsWith('/') && queuedContent === undefined) {
    input.value = '';
    resizeTextarea(input);
    handleSlashCommand(content);
    return;
  }

  if (state._pausedForUser) {
    if (queuedContent === undefined) {
      input.value = '';
      resizeTextarea(input);
      return sendPauseResponse(content);
    }
    setState({ _pausedForUser: false, _pauseCtx: null });
    const card = document.querySelector('.msg-hop-limit');
    if (card) card.remove();
  }

  if (state.isStreaming && queuedContent === undefined) {
    if (state._pausedForUser) {
      input.value = '';
      resizeTextarea(input);
      return sendPauseResponse(content);
    }
    input.value = '';
    resizeTextarea(input);
    const userMsg = { role: 'user', content, timestamp: new Date().toISOString() };
    appendMessageToDOM(userMsg);
    scrollToBottom();
    state.currentSession.messages.push(userMsg);
    setState({ _pendingAfterCurrent: { content } });
    return;
  }

  if (queuedContent === undefined) {
    input.value = '';
    resizeTextarea(input);
  }

  setStreaming(true);

  if (queuedContent === undefined) {
    const userMsg = { role: 'user', content, timestamp: new Date().toISOString() };
    appendMessageToDOM(userMsg);
    scrollToBottom();
    state.currentSession.messages.push(userMsg);
  }

  setState({ _lastUserMessage: content });

  try {
    setState({ _abortController: new AbortController() });
    const response = await fetch(`${API}/sessions/${state.currentSession.id}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
      signal: state._abortController.signal,
    });

    if (!response.ok) {
      const err = await response.json();
      showError(err.detail || 'Failed to send message');
      setStreaming(false);
      return;
    }

    await readSSEStream(response);

  } catch (e) {
    if (e.name === 'AbortError') return;
    showError('Connection error: ' + e.message);
  } finally {
    await streamCleanup(state.currentSession.id);
    document.getElementById('msg-input').focus();
  }
}

async function continueHopChain(extraHops, btn) {
  const ctx = state._hopCtx;
  if (!ctx || !ctx.sessionId) return;

  const hopCard = btn?.closest('.msg-hop-limit');
  if (hopCard) hopCard.remove();

  setStreaming(true);

  try {
    setState({ _abortController: new AbortController() });
    const response = await fetch(`${API}/sessions/${ctx.sessionId}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: ctx.content,
        remaining_agents: ctx.remaining,
        starting_hop: ctx.startingHop || 0,
        extra_hops: extraHops === 999 ? 999 : extraHops,
      }),
      signal: state._abortController.signal,
    });

    if (!response.ok) {
      showError('Failed to continue agent chain');
      setStreaming(false);
      return;
    }

    await readSSEStream(response);
  } catch (e) {
    if (e.name === 'AbortError') return;
    showError('Connection error during hop continuation: ' + e.message);
  } finally {
    await streamCleanup(ctx.sessionId);
  }
}

async function skipPause() {
  const ctx = state._pauseCtx;
  if (!ctx || !ctx.sessionId) return;

  setState({ _pausedForUser: false, _pauseCtx: null });

  const card = document.querySelector('.msg-hop-limit');
  if (card) card.remove();

  let remaining = ctx.remaining || [];
  if (remaining.length === 0) {
    const session = await fetch(`${API}/sessions/${ctx.sessionId}`).then(r => r.json()).catch(() => null);
    if (!session || session.poll_mode !== 'continuous' || !session.active_agents?.length) return;
    remaining = session.active_agents;
  }

  addDebugLog('skip', '');

  setStreaming(true);

  try {
    setState({ _abortController: new AbortController() });
    const response = await fetch(`${API}/sessions/${ctx.sessionId}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: '',
        remaining_agents: remaining,
        starting_hop: 0,
        extra_hops: 999,
        pause_for_user_response: false,
      }),
      signal: state._abortController.signal,
    });

    if (!response.ok) {
      showError('Failed to skip');
      setStreaming(false);
      return;
    }

    await readSSEStream(response);
  } catch (e) {
    if (e.name === 'AbortError') return;
    showError('Connection error: ' + e.message);
  } finally {
    await streamCleanup(ctx.sessionId);
  }
}

function stopPause() {
  addDebugLog('stop', 'from_pause');
  setState({ _pausedForUser: false, _pendingAfterCurrent: null, _pauseCtx: null });

  const card = document.querySelector('.msg-hop-limit');
  if (card) card.remove();

  if (state.isStreaming) {
    if (state._abortController) {
      state._abortController.abort();
      setState({ _abortController: null });
    }
  }

  setState({ _stopNow: true });
  const sysMsg = { role: 'system', content: '⏹️ Conversation stopped', timestamp: new Date().toISOString() };
  appendMessageToDOM(sysMsg);
  scrollToBottom();
}

async function sendPauseResponse(content) {
  const ctx = state._pauseCtx;
  if (!ctx || !ctx.sessionId) return;

  addDebugLog('pause_response', content.slice(0, 80));
  setState({ _pausedForUser: false });

  const card = document.querySelector('.msg-hop-limit');
  if (card) card.remove();
  const pauseMsg = document.querySelector('.msg-pause-prompt');
  if (pauseMsg) pauseMsg.remove();

  const userMsg = { role: 'user', content, timestamp: new Date().toISOString() };
  appendMessageToDOM(userMsg);
  scrollToBottom();
  state.currentSession.messages.push(userMsg);

  if (!ctx.remaining || ctx.remaining.length === 0) {
    sendMessage(content);
    return;
  }

  setStreaming(true);

  try {
    setState({ _abortController: new AbortController() });
    const response = await fetch(`${API}/sessions/${ctx.sessionId}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content,
        remaining_agents: ctx.remaining,
        starting_hop: 0,
        extra_hops: 999,
        pause_for_user_response: true,
      }),
      signal: state._abortController.signal,
    });

    if (!response.ok) {
      showError('Failed to continue after pause');
      setStreaming(false);
      return;
    }

    await readSSEStream(response);
  } catch (e) {
    if (e.name === 'AbortError') return;
    showError('Connection error: ' + e.message);
  } finally {
    await streamCleanup(ctx.sessionId);
  }
}

async function startContinuedTurn(content, remaining) {
  if (!state.currentSession) return;

  setStreaming(true);

  try {
    setState({ _abortController: new AbortController() });
    const response = await fetch(`${API}/sessions/${state.currentSession.id}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content,
        remaining_agents: remaining,
        starting_hop: 0,
        extra_hops: 999,
        pause_for_user_response: true,
      }),
      signal: state._abortController.signal,
    });

    if (!response.ok) {
      showError('Failed to continue turn');
      setStreaming(false);
      return;
    }

    await readSSEStream(response);
  } catch (e) {
    if (e.name === 'AbortError') return;
    showError('Connection error: ' + e.message);
  } finally {
    await streamCleanup(state.currentSession.id);
  }
}

function setStreaming(val) {
  const patch = { isStreaming: val };
  if (val) { patch._stopNow = false; patch._stopAfterCurrent = false; }
  setState(patch);
  setStatus(val ? 'streaming' : 'ready');
  if (!val && state._pendingContinue) {
    const { content, remaining } = state._pendingContinue;
    setState({ _pendingContinue: null, _pausedForUser: false, _pauseCtx: null });
    const card = document.querySelector('.msg-hop-limit');
    if (card) card.remove();
    const pauseMsg = document.querySelector('.msg-pause-prompt');
    if (pauseMsg) pauseMsg.remove();
    startContinuedTurn(content, remaining);
    return;
  }
  if (!val && state._pendingQueue.length > 0) {
    const msg = state._pendingQueue.shift();
    sendMessage(msg);
  }
}

function handleInputChange(el) {
  resizeTextarea(el);
  const val = el.value;
  const cursor = el.selectionStart;
  const before = val.slice(0, cursor);
  const match = before.match(/@(\w*)$/);

  if (match) {
    const search = match[1].toLowerCase();
    setState({ mentionSearch: { search, start: cursor - match[0].length, end: cursor } });
    showMentionDropdown(search);
  } else {
    hideMentionDropdown();
  }
}

function handleInputKeydown(e) {
  if (state.mentionSearch) {
    const dd = document.getElementById('mention-dropdown');
    const items = dd.querySelectorAll('.mention-option');
    if (e.key === 'ArrowDown') { e.preventDefault(); setState({ mentionSelectedIndex: Math.min(state.mentionSelectedIndex + 1, items.length - 1) }); updateMentionSelection(); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); setState({ mentionSelectedIndex: Math.max(state.mentionSelectedIndex - 1, 0) }); updateMentionSelection(); return; }
    if (e.key === 'Enter' || e.key === 'Tab') {
      const selected = items[state.mentionSelectedIndex];
      if (selected) { e.preventDefault(); insertMention(selected.dataset.id, selected.dataset.name); return; }
    }
    if (e.key === 'Escape') { hideMentionDropdown(); return; }
  }

  if (e.key === 'ArrowUp' && !e.shiftKey && state.inputHistory.length > 0) {
    const input = document.getElementById('msg-input');
    if (input.selectionStart === 0) {
      e.preventDefault();
      if (state.inputHistoryIndex === -1) {
        setState({ inputDraft: input.value, inputHistoryIndex: state.inputHistory.length - 1 });
      } else if (state.inputHistoryIndex > 0) {
        setState({ inputHistoryIndex: state.inputHistoryIndex - 1 });
      }
      input.value = state.inputHistory[state.inputHistoryIndex];
      resizeTextarea(input);
      input.setSelectionRange(input.value.length, input.value.length);
      return;
    }
  }

  if (e.key === 'ArrowDown' && !e.shiftKey && state.inputHistoryIndex !== -1) {
    const input = document.getElementById('msg-input');
    e.preventDefault();
    if (state.inputHistoryIndex < state.inputHistory.length - 1) {
      setState({ inputHistoryIndex: state.inputHistoryIndex + 1 });
      input.value = state.inputHistory[state.inputHistoryIndex];
    } else {
      setState({ inputHistoryIndex: -1 });
      input.value = state.inputDraft;
    }
    resizeTextarea(input);
    input.setSelectionRange(input.value.length, input.value.length);
    return;
  }

  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function showMentionDropdown(search) {
  if (!state.currentSession) return;
  const activeIds = state.currentSession.active_agents;
  const matches = state.allAgents.filter(a =>
    activeIds.includes(a.id) && a.name.toLowerCase().startsWith(search)
  );
  const dd = document.getElementById('mention-dropdown');
  if (!matches.length) { hideMentionDropdown(); return; }

  setState({ mentionSelectedIndex: 0 });
  dd.innerHTML = matches.map((a, i) => `
    <div class="mention-option${i === 0 ? ' selected' : ''}" data-id="${a.id}" data-name="${a.name}"
      onclick="insertMention('${a.id}', '${a.name}')">
      <span class="m-avatar">${agentAvatarHtml(a, 16)}</span>
      <span class="m-name">${a.name}</span>
    </div>
  `).join('');
  dd.style.display = 'block';
}

function updateMentionSelection() {
  const items = document.querySelectorAll('.mention-option');
  items.forEach((el, i) => el.classList.toggle('selected', i === state.mentionSelectedIndex));
}

function hideMentionDropdown() {
  document.getElementById('mention-dropdown').style.display = 'none';
  setState({ mentionSearch: null });
}

function insertMention(agentId, agentName) {
  const input = document.getElementById('msg-input');
  if (!state.mentionSearch) return;
  const { start, end } = state.mentionSearch;
  const val = input.value;
  const newVal = val.slice(0, start) + `@${agentName} ` + val.slice(end);
  input.value = newVal;
  const newCursor = start + agentName.length + 2;
  input.setSelectionRange(newCursor, newCursor);
  hideMentionDropdown();
  input.focus();
}

async function handleSlashCommand(cmd) {
  const parts = cmd.split(' ');
  const command = parts[0].toLowerCase();

  switch (command) {
    case '/stop':
      if (state.isStreaming) {
        addDebugLog('stop', 'during_stream');
        setState({ _pendingContinue: null, _pendingAfterCurrent: null, _stopAfterCurrent: true });
        const sysMsg = { role: 'system', content: '⏹️ Conversation stopped', timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } else if (state._hopCtx) {
        addDebugLog('stop', 'from_hop');
        const hopCard = document.querySelector('.msg-hop-limit');
        if (hopCard) hopCard.remove();
        setState({ _hopCtx: null });
        setStreaming(false);
        const sysMsg = { role: 'system', content: '⏹️ Conversation stopped', timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } else {
        showError('No active conversation to stop');
      }
      break;

    case '/poll_order': {
      const order = parts[1];
      if (!['fixed', 'random', 'alpha'].includes(order)) {
        showError('Usage: /poll_order fixed|random|alpha');
        break;
      }
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        await fetch(`${API}/sessions/${state.currentSession.id}/poll_order`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order }),
        });
        const sysMsg = { role: 'system', content: `🔄 Poll order set to ${order}`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to set poll order');
      }
      break;
    }

    case '/poll_mode': {
      const mode = parts[1];
      if (!mode || (mode !== 'normal' && mode !== 'continuous' && !/^\d+$/.test(mode))) {
        showError('Usage: /poll_mode normal|continuous|<n>');
        break;
      }
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        await fetch(`${API}/sessions/${state.currentSession.id}/poll_mode`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode }),
        });
        const sysMsg = { role: 'system', content: `🔄 Poll mode set to ${mode}`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to set poll mode');
      }
      break;
    }

    case '/poll_throttle': {
      const delayMs = parseInt(parts[1], 10);
      if (isNaN(delayMs) || delayMs < 0) {
        showError('Usage: /poll_throttle <milliseconds> (e.g. 500)');
        break;
      }
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        await fetch(`${API}/sessions/${state.currentSession.id}/poll_throttle`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ delay_ms: delayMs }),
        });
        const sysMsg = { role: 'system', content: `⏱️ Poll throttle set to ${delayMs}ms`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to set poll throttle');
      }
      break;
    }

    case '/title': {
      const newTitle = parts.slice(1).join(' ').trim();
      if (!newTitle) {
        showError('Usage: /title <new session title>');
        break;
      }
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        const updated = await fetch(`${API}/sessions/${state.currentSession.id}/title`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: newTitle }),
        }).then(r => r.json());
        state.currentSession.title = updated.title;
        const si = state.sessions.find(s => s.id === state.currentSession.id);
        if (si) si.title = updated.title;
        renderSidebar();
        const sysMsg = { role: 'system', content: `📝 Session renamed to "${updated.title}"`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to rename session');
      }
      break;
    }

    case '/help': {
      const helpLines = [
        `<b>/stop</b> — stops the current agent response chain or clears a paused hop-limit prompt`,
        `<b>/title</b> &lt;new title&gt; — renames the current session`,
        `<b>/hop_limit</b> &lt;n&gt; — sets max agent-to-agent hops before chain pauses`,
        `<b>/allow_hops</b> true|false — enables or disables agent-to-agent @mention chaining`,
        `<b>/pause_for_user</b> true|false — pauses the turn when an agent @mentions you`,
        `<b>/poll_order</b> fixed|random|alpha — sets agent response order`,
        `<b>/poll_mode</b> normal|continuous|&lt;n&gt; — sets agent cycling mode`,
        `<b>/poll_throttle</b> &lt;ms&gt; — sets delay between agent responses`,
        `<b>/clear</b> — clears chat bubbles from the UI (does not touch session file)`,
        `<b>/status</b> — shows all current session settings`,
        `<b>/agents</b> — lists all available agents and their config`,
        `<b>/reload</b> — reloads agents.json and config.json without restarting Docker`,
        `<b>/export</b> — downloads the current session as a markdown file`,
        `<b>/help</b> — shows this help message`,
      ].join('<br>');
      const sysMsg = { role: 'system', content: helpLines, timestamp: new Date().toISOString() };
      appendMessageToDOM(sysMsg);
      scrollToBottom();
      break;
    }

    case '/agents': {
      const rows = state.allAgents.map(a =>
        `<b>${escHtml(a.name)}</b> — id=${escHtml(a.id)} model=${escHtml(a.model)} color=${escHtml(a.color)} avatar=${escHtml(a.avatar)}`
      ).join('<br>');
      const sysMsg = { role: 'system', content: rows, timestamp: new Date().toISOString() };
      appendMessageToDOM(sysMsg);
      scrollToBottom();
      break;
    }

    case '/status': {
      if (!state.currentSession) { showError('No active session'); break; }
      const s = state.currentSession;
      const agentNames = s.active_agents.map(aid => {
        const agent = state.allAgents.find(a => a.id === aid);
        return agent ? `${agent.name} (${aid})` : aid;
      }).join(', ');
      const html = [
        `<b>ID:</b>        ${escHtml(s.id)}`,
        `<b>Title:</b>     ${escHtml(s.title)}`,
        `<b>Created:</b>   ${escHtml(s.created)}`,
        `<b>Agents:</b>    ${escHtml(agentNames)}`,
        `<b>Poll:</b>      order=${escHtml(s.poll_order)} mode=${escHtml(s.poll_mode)} throttle=${s.poll_throttle}ms`,
        `<b>Hop limit:</b> ${s.hop_limit}`,
        `<b>Allow hops:</b> ${s.allow_hops !== false ? 'true' : 'false'}`,
        `<b>Pause for user:</b> ${s.pause_for_user === true ? 'true' : 'false'}`,
      ].join('<br>');
      const sysMsg = { role: 'system', content: html, timestamp: new Date().toISOString() };
      appendMessageToDOM(sysMsg);
      scrollToBottom();
      break;
    }

    case '/clear': {
      if (!state.currentSession) { showError('No active session'); break; }
      fetch(`${API}/sessions/${state.currentSession.id}/messages`, { method: 'DELETE' })
        .then(r => r.json())
        .then(session => {
          setState({ currentSession: session });
          document.getElementById('messages').innerHTML = '';
        })
        .catch(() => showError('Failed to clear messages'));
      break;
    }

    case '/reload': {
      try {
        const res = await fetch(`${API}/reload`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const [freshAgents, freshConfig] = await Promise.all([
          fetch(`${API}/agents`).then(r => r.json()),
          fetch(`${API}/config`).then(r => r.json()),
        ]);
        setState({ allAgents: freshAgents, config: freshConfig });
        if (state.currentSession) renderAgentBar();
        const sysMsg = { role: 'system', content: `♻️ Reloaded — ${data.agents_loaded} agents, config refreshed`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Reload failed: ' + e.message);
      }
      break;
    }

    case '/export': {
      if (!state.currentSession) { showError('No active session'); break; }
      const s = state.currentSession;
      const cfg = state.config || {};
      const userName = cfg.user_name || 'User';
      const lines = [];

      lines.push(`# ${s.title}`);
      lines.push('');
      lines.push(`**Session ID:** ${s.id}`);
      lines.push(`**Created:** ${s.created ? new Date(s.created).toLocaleString() : 'unknown'}`);
      const agentNames = s.active_agents.map(aid => {
        const a = state.allAgents.find(x => x.id === aid);
        return a ? a.name : aid;
      }).join(', ');
      lines.push(`**Agents:** ${agentNames}`);
      lines.push('');
      lines.push('---');
      lines.push('');

      s.messages.forEach(msg => {
        if (msg.role === 'user') {
          lines.push(`**${userName}**`);
          lines.push(msg.content);
          lines.push('');
        } else if (msg.role === 'agent') {
          const hopNote = msg.hop > 0 ? ` *(hop ${msg.hop})*` : '';
          lines.push(`**${msg.agent_name || msg.agent_id}**${hopNote}`);
          lines.push(msg.content);
          lines.push('');
        } else if (msg.role === 'system') {
          const plain = (msg.content || '').replace(/<[^>]+>/g, '');
          if (plain.trim()) {
            lines.push(`> *${plain.trim()}*`);
            lines.push('');
          }
        }
      });

      const slug = s.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || s.id;
      const filename = `agentchat-${slug}.md`;

      const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);

      const sysMsg = { role: 'system', content: `💾 Exported as ${escHtml(filename)}`, timestamp: new Date().toISOString() };
      appendMessageToDOM(sysMsg);
      scrollToBottom();
      break;
    }

    case '/hop_limit': {
      const limit = parseInt(parts[1], 10);
      if (isNaN(limit) || limit < 0) {
        showError('Usage: /hop_limit <n> (non-negative integer)');
        break;
      }
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        await fetch(`${API}/sessions/${state.currentSession.id}/hop_limit`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ limit }),
        });
        const sysMsg = { role: 'system', content: `🔄 Hop limit set to ${limit}`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to set hop limit');
      }
      break;
    }

    case '/allow_hops': {
      const val = parts[1];
      if (val !== 'true' && val !== 'false') {
        showError('Usage: /allow_hops true|false');
        break;
      }
      const allow = val === 'true';
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        await fetch(`${API}/sessions/${state.currentSession.id}/allow_hops`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ allow_hops: allow }),
        });
        const sysMsg = { role: 'system', content: `🔄 Agent-to-agent hops ${allow ? 'enabled' : 'disabled'}`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to set allow_hops');
      }
      break;
    }

    case '/pause_for_user': {
      const val = parts[1];
      if (val !== 'true' && val !== 'false') {
        showError('Usage: /pause_for_user true|false');
        break;
      }
      const pause = val === 'true';
      if (!state.currentSession) { showError('No active session'); break; }
      try {
        await fetch(`${API}/sessions/${state.currentSession.id}/pause_for_user`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pause_for_user: pause }),
        });
        const sysMsg = { role: 'system', content: `🔄 Pause for user ${pause ? 'enabled' : 'disabled'}`, timestamp: new Date().toISOString() };
        appendMessageToDOM(sysMsg);
        scrollToBottom();
      } catch (e) {
        showError('Failed to set pause_for_user');
      }
      break;
    }
  }
  addDebugLog('slash', cmd);
}
