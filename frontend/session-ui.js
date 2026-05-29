function renderSidebar() {
  const list = document.getElementById('session-list');
  list.innerHTML = '';
  if (state.sessions.length === 0) {
    list.innerHTML = `<div style="padding:12px 8px;font-family:var(--mono);font-size:11px;color:var(--text3);">No sessions yet</div>`;
    return;
  }
  state.sessions.forEach(s => {
    const div = document.createElement('div');
    div.className = 'session-item' + (state.currentSession?.id === s.id ? ' active' : '');
    div.dataset.id = s.id;
    div.innerHTML = `
      <div class="session-title">${escHtml(s.title)}</div>
      <div class="session-meta">${s.active_agents.length} agents · ${s.message_count} msgs</div>
      <button class="session-delete" onclick="deleteSession('${s.id}', event)" title="Delete session">×</button>
    `;
    div.addEventListener('click', () => loadSession(s.id));
    list.appendChild(div);
  });
}

async function loadSession(sessionId) {
  abortStream();
  try {
    const session = await fetch(`${API}/sessions/${sessionId}`).then(r => r.json());
    setState({ currentSession: session });
    renderSidebar();
    renderAgentBar();
    renderMessages();
    document.getElementById('no-session-msg').style.display = 'none';
    document.getElementById('messages').style.display = 'flex';
    document.getElementById('input-bar').style.display = 'flex';
    scrollToBottom();
    document.getElementById('msg-input').focus();
  } catch (e) {
    showError('Failed to load session');
  }
}

async function updateAgentOrder(agentIds) {
  if (!state.currentSession) return;
  try {
    const res = await fetch(`${API}/sessions/${state.currentSession.id}/agents/reorder`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_ids: agentIds }),
    });
    if (!res.ok) { showError('Failed to reorder agents'); return; }
    setState({ currentSession: await res.json() });
  } catch (e) {
    showError('Failed to reorder agents: ' + e.message);
  }
}

async function deleteSession(sessionId, e) {
  e.stopPropagation();
  abortStream();
  if (!confirm('Delete this session?')) return;
  try {
    await fetch(`${API}/sessions/${sessionId}`, { method: 'DELETE' });
  } catch (e) {
    showError('Failed to delete session: ' + e.message);
    return;
  }
  setState({ sessions: state.sessions.filter(s => s.id !== sessionId) });
  if (state.currentSession?.id === sessionId) {
    setState({ currentSession: null });
    document.getElementById('no-session-msg').style.display = 'flex';
    document.getElementById('messages').style.display = 'none';
    document.getElementById('input-bar').style.display = 'none';
    document.getElementById('agent-bar').innerHTML = `<span id="agent-bar-placeholder" style="font-family:var(--mono);font-size:11px;color:var(--text3);">no session selected</span>`;
  }
  renderSidebar();
}

async function removeAgentFromSession(agentId) {
  if (!state.currentSession) return;
  const agent = state.allAgents.find(a => a.id === agentId);
  if (!confirm(`Remove ${agent?.name || agentId} from this session?`)) return;
  const updated = await fetch(`${API}/sessions/${state.currentSession.id}/agents/${agentId}`, { method: 'DELETE' }).then(r => r.json()).catch(e => { showError('Failed to remove agent: ' + e.message); return null; });
  if (!updated) return;
  setState({ currentSession: updated });
  renderAgentBar();
  renderMessages();
}

function openNewSessionModal() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal">
      <h2>🗨️ New session</h2>
      <div class="modal-field">
        <label>Session title</label>
        <input type="text" id="new-session-title" placeholder="e.g. Gaming debate" autofocus>
      </div>
      <div class="modal-field">
        <label>Choose agents (pick 1–4)</label>
        <div class="agent-select-grid" id="new-agent-grid"></div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn btn-primary" id="create-session-btn" onclick="createSession()" disabled>Create</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const grid = document.getElementById('new-agent-grid');
  const selected = new Set();

  state.allAgents.forEach(agent => {
    const item = document.createElement('div');
    item.className = 'agent-select-item';
    item.innerHTML = `
      <span class="asi-avatar">${agentAvatarHtml(agent, 48)}</span>
      <div class="asi-info">
        <div class="asi-name">${agent.name}</div>
        <div class="asi-voice">${(agent.personality || '').substring(0, 160)}</div>
      </div>
      <span class="asi-check">✓</span>
    `;
    item.addEventListener('click', () => {
      if (selected.has(agent.id)) {
        selected.delete(agent.id);
        item.classList.remove('selected');
      } else {
        selected.add(agent.id);
        item.classList.add('selected');
      }
      document.getElementById('create-session-btn').disabled = selected.size === 0;
    });
    grid.appendChild(item);
  });

  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  document.getElementById('new-session-title').addEventListener('keydown', e => { if (e.key === 'Enter') createSession(); });
  setState({ _newSessionSelected: selected });
}

async function createSession() {
  const title = document.getElementById('new-session-title').value.trim() || 'Untitled session';
  const agentIds = [...state._newSessionSelected];
  if (!agentIds.length) return;

  let session;
  try {
    session = await fetch(`${API}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, agent_ids: agentIds }),
    }).then(r => r.json());
  } catch (e) {
    showError('Failed to create session: ' + e.message);
    return;
  }

  document.querySelector('.modal-overlay')?.remove();
  state.sessions.unshift({
    id: session.id, title: session.title,
    active_agents: session.active_agents,
    message_count: 0, created: session.created,
  });
  setState({ sessions: [...state.sessions] });
  renderSidebar();
  loadSession(session.id);
}

function openAddAgentModal() {
  if (!state.currentSession) return;
  const activeIds = new Set(state.currentSession.active_agents);
  const available = state.allAgents.filter(a => !activeIds.has(a.id));

  if (!available.length) {
    showError('All agents are already in this session');
    return;
  }

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal">
      <h2>➕ Add agent to session</h2>
      <div class="modal-field">
        <div class="agent-select-grid" id="add-agent-grid"></div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

  const grid = document.getElementById('add-agent-grid');
  available.forEach(agent => {
    const item = document.createElement('div');
    item.className = 'agent-select-item';
    item.innerHTML = `
      <span class="asi-avatar">${agentAvatarHtml(agent, 48)}</span>
      <div class="asi-info">
        <div class="asi-name">${agent.name}</div>
        <div class="asi-voice">${(agent.personality || '').substring(0, 160)}</div>
      </div>
    `;
    item.addEventListener('click', () => addAgentToSession(agent.id));
    grid.appendChild(item);
  });
}

async function addAgentToSession(agentId) {
  if (!state.currentSession) return;
  document.querySelector('.modal-overlay')?.remove();
  try {
    const result = await fetch(`${API}/sessions/${state.currentSession.id}/agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId }),
    }).then(r => r.json());

    setState({ currentSession: result.session });
    renderAgentBar();
    renderMessages();
  } catch (e) {
    showError('Failed to add agent');
  }
}
