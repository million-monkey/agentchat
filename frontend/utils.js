function abortStream() {
  if (!state.isStreaming) return;
  setState({ _stopNow: true, _pendingContinue: null, _pendingAfterCurrent: null });
  if (state._abortController) {
    state._abortController.abort();
    setState({ _abortController: null });
  }
}

function escHtml(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function highlightMentions(html) {
  return html.replace(/@(\w+)/g, '<span class="mention">@$1</span>');
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  } catch { return ''; }
}

function scrollToBottom() {
  const el = document.getElementById('messages');
  if (el) el.scrollTop = el.scrollHeight;
}

function resizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function showError(msg) {
  const toast = document.createElement('div');
  toast.className = 'error-toast';
  toast.textContent = '\u26A0 ' + msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
  if (state.currentSession?.id) {
    fetch(`${API}/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.currentSession.id, message: msg }),
    }).catch(() => {});
  }
}

function addDebugLog(type, data) {
  if (!state.currentSession?.id) return;
  fetch(`${API}/sessions/${state.currentSession.id}/debug_log`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, data }),
  }).catch(() => {});
}
