async function init() {
  try {
    const [sessions, agents, config] = await Promise.all([
      fetch(`${API}/sessions`).then(r => r.json()),
      fetch(`${API}/agents`).then(r => r.json()),
      fetch(`${API}/config`).then(r => r.json()),
    ]);
    setState({ sessions, allAgents: agents, config });
    renderSidebar();
  } catch (e) {
    showError('Cannot connect to backend. Is Docker running?');
    setStatus('offline', false);
  }
}

init();