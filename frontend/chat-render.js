function renderAgentBar() {
  const bar = document.getElementById('agent-bar');
  bar.innerHTML = '';
  const placeholder = document.getElementById('agent-bar-placeholder');
  if (placeholder) placeholder.remove();

  if (!state.currentSession) return;

  let draggedEl = null;

  const activeIds = state.currentSession.active_agents;
  activeIds.forEach(aid => {
    const agent = state.allAgents.find(a => a.id === aid);
    if (!agent) return;
    const chip = document.createElement('div');
    chip.className = 'agent-chip';
    chip.draggable = true;
    chip.style.borderColor = agent.color + '55';

    chip.addEventListener('dragstart', e => {
      draggedEl = chip;
      chip.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });

    chip.addEventListener('dragover', e => {
      e.preventDefault();
      chip.classList.add('drag-over');
    });

    chip.addEventListener('dragleave', () => {
      chip.classList.remove('drag-over');
    });

    chip.addEventListener('drop', e => {
      e.preventDefault();
      chip.classList.remove('drag-over');
      if (!draggedEl || draggedEl === chip) return;

      chip.parentNode.insertBefore(draggedEl, chip.nextSibling);

      const newOrder = [...bar.querySelectorAll('.agent-chip')].map(c => c.dataset.aid);
      updateAgentOrder(newOrder);
      draggedEl = null;
    });

    chip.addEventListener('dragend', () => {
      chip.classList.remove('dragging');
      bar.querySelectorAll('.agent-chip').forEach(c => c.classList.remove('drag-over'));
      draggedEl = null;
    });

    chip.dataset.aid = aid;
    chip.innerHTML = `
      <span class="chip-avatar">${agentAvatarHtml(agent, 28)}</span>
      <span class="chip-name" style="color:${agent.color}">${agent.name}</span>
      <button class="chip-remove" onclick="removeAgentFromSession('${aid}')" title="Remove ${agent.name}">×</button>
    `;
    bar.appendChild(chip);
  });

  const addBtn = document.createElement('button');
  addBtn.id = 'add-agent-btn';
  addBtn.textContent = '+ Add agent';
  addBtn.onclick = openAddAgentModal;
  bar.appendChild(addBtn);
}

function renderMessages() {
  const container = document.getElementById('messages');
  container.innerHTML = '';
  if (!state.currentSession) return;
  state.currentSession.messages.forEach(msg => appendMessageToDOM(msg));
  scrollToBottom();
}

function appendMessageToDOM(msg) {
  const container = document.getElementById('messages');
  const el = buildMessageEl(msg);
  if (el) container.appendChild(el);
}

function agentAvatarHtml(agent, size = 32) {
  if (agent?.avatar) {
    return `<img src="${escHtml(agent.avatar)}" alt="${escHtml(agent.name)}" style="width:${size}px;height:${size}px;border-radius:50%;object-fit:cover;" onerror="this.style.display='none';this.nextSibling.style.display='inline';"><span style="display:none">🤖</span>`;
  }
  return '🤖';
}

function agentProviderMeta(agent) {
  if (!agent) return '';
  const raw = agent.model || '';
  if (!raw) return '';
  const slash = raw.indexOf('/');
  if (slash === -1) return raw;
  const provider = raw.slice(0, slash);
  const model = raw.slice(slash + 1);
  return `${provider}/${model}`;
}

function buildMessageEl(msg) {
  if (msg.role === 'user') {
    const cfg = state.config || {};
    const userName = cfg.user_name || 'You';
    const userAvatar = cfg.user_avatar || '';
    const div = document.createElement('div');
    div.className = 'msg-group msg-user';
    div.innerHTML = `
      <div class="msg-user-header">
        <div class="msg-user-name">${escHtml(userName)}</div>
        <div class="msg-time">${formatTime(msg.timestamp)}</div>
      </div>
      <div class="msg-user-body">
        <div class="bubble">${escHtml(msg.content)}</div>
        ${userAvatar ? `<div class="msg-user-avatar">${agentAvatarHtml({avatar: userAvatar, name: userName}, 48)}</div>` : ''}
      </div>
    `;
    return div;
  }

  if (msg.role === 'agent') {
    const agent = state.allAgents.find(a => a.id === msg.agent_id);
    const color = agent?.color || '#6b7896';
    const meta = agentProviderMeta(agent);
    const div = document.createElement('div');
    div.className = 'msg-group msg-agent';
    const hopBadge = msg.hop > 0 ? `<span class="msg-agent-hop">hop ${msg.hop}</span>` : '';
    div.innerHTML = `
      <div class="msg-agent-header">
        <div class="msg-agent-avatar">${agent ? agentAvatarHtml(agent, 48) : '🤖'}</div>
        <div>
          <div class="msg-agent-name" style="color:${color}">${msg.agent_name || msg.agent_id} ${hopBadge}</div>
          ${meta ? `<div class="msg-agent-meta">${escHtml(meta)} | ${formatTime(msg.timestamp)}</div>` : ''}
        </div>
      </div>
      <div class="bubble">${highlightMentions(escHtml(msg.content))}</div>
    `;
    return div;
  }

  if (msg.role === 'system') {
    const div = document.createElement('div');
    div.className = 'msg-system';
    div.innerHTML = `${formatTime(msg.timestamp)} - ${msg.content}`;
    return div;
  }

  return null;
}

function handleSSEEvent(evt) {
  switch (evt.type) {
    case 'agent_start':
      state._cycleStartedAgents.add(evt.agent_id);
      showTypingIndicator(evt);
      break;

    case 'token':
      appendToken(evt);
      break;

    case 'agent_done':
      finalizeAgentBubble(evt);
      if (state._stopAfterCurrent) {
        setState({ _stopAfterCurrent: false, _stopNow: true });
        if (state._abortController) state._abortController.abort();
      }
      break;

    case 'hop_limit':
      showHopLimitWarning(evt);
      break;

    case 'pause_for_user':
      showPauseForUser(evt);
      break;

    case 'turn_done':
      state._cycleStartedAgents.clear();
      break;

    case 'error':
      removeTypingIndicator(evt.agent_id);
      showError(`${evt.agent_id}: ${evt.content}`);
      break;
  }
}

function showTypingIndicator(evt) {
  const agent = state.allAgents.find(a => a.id === evt.agent_id);
  const color = evt.agent_color || agent?.color || '#6b7896';
  const meta = agentProviderMeta(agent);

  const container = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg-group msg-agent';
  div.dataset.streamingFor = evt.agent_id;

  const hopBadge = evt.hop > 0 ? `<span class="msg-agent-hop">hop ${evt.hop}</span>` : '';
  div.innerHTML = `
    <div class="msg-agent-header">
      <div class="msg-agent-avatar">${agent ? agentAvatarHtml(agent, 48) : '🤖'}</div>
      <div>
        <div class="msg-agent-name" style="color:${color}">${evt.agent_name} ${hopBadge}</div>
        ${meta ? `<div class="msg-agent-meta">${escHtml(meta)} | ${formatTime(new Date().toISOString())}</div>` : ''}
      </div>
    </div>
    <div class="bubble typing-indicator">
      <div class="typing-dots"><span></span><span></span><span></span></div>
    </div>
  `;
  container.appendChild(div);
  state.currentStreamingBubbles[evt.agent_id] = div;
  scrollToBottom();
}

function appendToken(evt) {
  const div = state.currentStreamingBubbles[evt.agent_id];
  if (!div) return;

  const bubble = div.querySelector('.bubble');
  if (!bubble) return;

  if (bubble.classList.contains('typing-indicator')) {
    bubble.classList.remove('typing-indicator');
    bubble.innerHTML = '';
    bubble.dataset.raw = '';
  }

  bubble.dataset.raw = (bubble.dataset.raw || '') + evt.content;
  bubble.innerHTML = highlightMentions(escHtml(bubble.dataset.raw));
  scrollToBottom();
}

function finalizeAgentBubble(evt) {
  const div = state.currentStreamingBubbles[evt.agent_id];
  if (div) {
    const bubble = div.querySelector('.bubble');
    if (bubble && bubble.classList.contains('typing-indicator')) {
      div.remove();
    }
    delete state.currentStreamingBubbles[evt.agent_id];
  }

  state.currentSession?.messages.push({
    role: 'agent',
    agent_id: evt.agent_id,
    agent_name: evt.agent_name,
    agent_avatar: evt.agent_avatar || '',
    content: evt.content,
    timestamp: new Date().toISOString(),
    hop: evt.hop,
  });
}

function removeTypingIndicator(agentId) {
  const div = state.currentStreamingBubbles[agentId];
  if (div) {
    const bubble = div.querySelector('.bubble');
    if (bubble?.classList.contains('typing-indicator')) div.remove();
    delete state.currentStreamingBubbles[agentId];
  }
}

function showHopLimitWarning(evt) {
  setState({ _hopCtx: {
    sessionId: state.currentSession?.id,
    content:   state._lastUserMessage || '',
    remaining: evt.remaining_agents || [],
    startingHop: evt.hop,
  } });

  const container = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg-hop-limit';
  div.innerHTML = `
    <p>⚡ Agents are getting chatty (hop ${evt.hop}). Continue the chain?</p>
    <div class="hop-actions">
      <button class="hop-btn primary" onclick="continueHopChain(3, this)">+3 more hops</button>
      <button class="hop-btn" onclick="continueHopChain(999, this)">Let it ride</button>
      <button class="hop-btn" onclick="this.closest('.msg-hop-limit').remove(); setStreaming(false);">Stop here</button>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
}

function showPauseForUser(evt) {
  const userName = evt.user_name || 'You';
  setState({ _pauseCtx: {
    sessionId: state.currentSession?.id,
    remaining: evt.remaining_agents || [],
  } });

  const container = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg-hop-limit';
  div.innerHTML = `
    <p>💬 ${escHtml(userName)} was mentioned</p>
    <div class="hop-actions">
      <button class="hop-btn primary" onclick="skipPause()">Skip</button>
      <button class="hop-btn" onclick="stopPause()">Stop</button>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
  setState({ _pausedForUser: true });
  addDebugLog('pause_shown', `agent=${evt.agent_id}`);
}
