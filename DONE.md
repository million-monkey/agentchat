# DONE.md

Track record of completed work and reviewed/ignored items.

## DONE/COMPLETED

| ID | Summary | Date | Notes |
|---|---|---|---|
| #1 | Per-session concurrency lock | 2026-05-29 | `asyncio.Lock` per `session_id`, held across `/message` and `/continue` streams |
| #3 | CORS lockdown | 2026-05-29 | `allow_origins` locked to `["http://localhost:3000"]` |
| #4 | Config/agents disk cache | 2026-05-29 | mtime-based cache in `load_config()` and `load_agents()`, re-reads only when files change |
| #8 | poll_mode digit validation | 2026-05-29 | `/poll_mode` endpoint now rejects `"0"` with HTTP 400 |
| #9 | Frontend state audit trail | 2026-05-29 | Added `setState()` with `console.debug` logging, all direct `state.xxx = yyy` converted across 6 JS files |
| #10 | run_turn decomposition | 2026-05-29 | Extracted `parse_poll_mode`, `build_queue`, `advance_hop_queue`, `should_pause_for_user`. `run_turn` down from 217 to 181 lines |
| #11 | /continue timing hazard | 2026-05-29 | `append_message` moved out of `event_stream()` generator into route handler body |
| — | hop_limit from config | 2026-05-29 | `create_session()` reads `config.get("hop_limit", 3)` instead of hardcoding |
| — | _ensure_dirs on startup | 2026-05-29 | Moved to `@app.on_event("startup")`, removed from per-request handlers |
| — | .gitignore | 2026-05-29 | Ignores `.env`, `__pycache__/`, `sessions/`, OS/IDE files |

## DONE/IGNORED

| ID | Summary | Date | Reason |
|---|---|---|---|
| #2 | Duplicate xmodel keys in agents.json | 2026-05-29 | Debug-only fields, duplicate keys work fine in practice |
| #5 | O(n²) hop priority lookup | 2026-05-29 | Only 6-8 agents max — microseconds per lookup, not worth the complexity |
| #6 | build_messages_payload dedup | 2026-05-29 | After tracing the flow, the dedup is actually correct — the user message is already in session history |
| #7 | list_sessions() reads full files | 2026-05-29 | Acceptable for a local single-user tool with few sessions |
| #14 | Debug model if-cascade | 2026-05-29 | Only 4 debug models, the `if` blocks are clear enough |
