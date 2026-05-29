const API = 'http://localhost:8000';

let state = {
  sessions: [],
  allAgents: [],
  currentSession: null,
  isStreaming: false,
  _pendingQueue: [],
  _stopAfterCurrent: false,
  _stopNow: false,
  mentionSearch: null,
  mentionSelectedIndex: 0,
  currentStreamingBubbles: {},
  inputHistory: [],
  inputHistoryIndex: -1,
  inputDraft: '',
  _pausedForUser: false,
  _abortController: null,
  _cycleStartedAgents: new Set(),
  _pendingContinue: null,
  _pendingAfterCurrent: null,
};

function setState(patch) {
  Object.assign(state, patch);
  console.debug('[state]', JSON.stringify(patch), new Error().stack?.split('\n')[2]?.trim() || '');
}
