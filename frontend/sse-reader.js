async function readSSEStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    if (state._stopNow) break;
    const { done, value } = await reader.read();
    if (done || state._stopNow) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (state._stopNow) break;
      if (!line.startsWith('data: ')) continue;
      const dataStr = line.slice(6).trim();
      if (!dataStr) continue;
      try {
        const evt = JSON.parse(dataStr);
        handleSSEEvent(evt);
        if (state._pendingAfterCurrent &&
            evt.type === 'agent_done' &&
            Object.keys(state.currentStreamingBubbles).length === 0) {
          const activeIds = state.currentSession.active_agents;
          let remaining = activeIds.filter(aid => !state._cycleStartedAgents.has(aid));
          if (remaining.length === 0) remaining = activeIds;
          setState({ _pendingContinue: { content: state._pendingAfterCurrent.content, remaining }, _pendingAfterCurrent: null });
        }
      } catch (e) {}
    }

    // Stop after processing all events in this chunk if user queued a message
    // while streaming and no pause was triggered. If pause was triggered,
    // let the stream end naturally (after turn_done) so pause_for_user event
    // is processed by the UI first.
    if (state._pendingContinue && !state._pausedForUser) {
      setState({ _stopNow: true });
      if (state._abortController) state._abortController.abort();
    }
  }
}

async function streamCleanup(sessionId) {
  Object.keys(state.currentStreamingBubbles).forEach(removeTypingIndicator);
  setState({ currentStreamingBubbles: {} });
  setStreaming(false);
  const fresh = await fetch(`${API}/sessions/${sessionId}`).then(r => r.json()).catch(() => null);
  if (fresh && state.currentSession?.id === sessionId) {
    setState({ currentSession: fresh });
    const si = state.sessions.find(s => s.id === fresh.id);
    if (si) si.message_count = fresh.messages.length;
    renderSidebar();
  }
}
