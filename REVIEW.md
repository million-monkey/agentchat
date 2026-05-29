# AgentChat — Architecture Review

> Generated: 2026-05-29

## Summary

The architecture is well-thought-out for a personal tool — the atomic writes, SSE backpressure design, hop guard, and catch-up context system are all smart.

---

## Undecided Items

Items from the external review that haven't been visited yet. Each needs a decision: fix it (→ TODO.md) or ignore it (→ DONE.md).

### #12. `response_looks_complete()` heuristic is brittle

The function uses a regex check for sentence-ending punctuation to decide whether to follow `@mentions`. A response ending with an ellipsis (`...`) or a markdown list item (`- something`) will incorrectly be treated as truncated. This heuristic is load-bearing — a false negative silently breaks the entire hop chain — and deserves at minimum a test suite.

### #13. No input sanitization on `agent.avatar` paths

In `chat-render.js`, agent avatars are used as `<img src="...">` without sanitization beyond `escHtml`. An `agents.json` entry with `"avatar": "javascript:alert(1)"` would execute in-browser. Low risk for a local-only tool, but an XSS vector if ever exposed to a network.

### #15. `httpx.AsyncClient` instantiated per-request

In `stream_agent_response`, a new `httpx.AsyncClient` is created for every LLM call. A module-level or app-level client with connection pooling would reduce overhead in continuous poll mode.

### #16. No return type annotations on `session_manager.py` functions

Adding them would catch several places where `Optional[dict]` is returned but not checked by callers.
