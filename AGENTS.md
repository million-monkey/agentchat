# AGENTS.md

## Project Overview

AgentChat is a multi-agent chat simulation platform. The user is Jake. Keep explanations short and direct.

Technology stack:

* Backend: Python (FastAPI)
* Frontend: JavaScript, HTML
* Runtime: Docker Desktop on Windows
* Development environment: Local Docker container

Project structure:

* `/backend` - Python backend code
* `/frontend` - Frontend code and web assets
* `/sessions` - Chat session history and logs (auto-generated, gitignored)
* `config.json` - Runtime config (hop_limit, poll defaults, provider endpoints)
* `agents.json` - Agent definitions (personality, model, avatar)
* `REVIEW.md` - All issues from external review (undecided items)
* `TODO.md` - Issues selected for action (subset of REVIEW.md)
* `DONE.md` - Completed or reviewed/ignored items

---

## Important Development Rules

### Respect Existing Architecture

* Follow existing code patterns whenever possible.
* Prefer modifying existing files over creating new files.
* Keep changes focused and minimal.
* If a refactor is needed, outline it first and get approval before executing.

### Session Data

* Session history and logs are stored in `/sessions`.
* Treat session data as application data, not source code.
* Do not modify, delete, or rewrite session files unless explicitly instructed.

### Issue Tracking (REVIEW.md → TODO.md → DONE.md)

Issues flow through three files:

**`REVIEW.md`** — All findings from external review, plus any newly discovered bugs or improvements. Items live here until a decision is made.

**`TODO.md`** — Items from REVIEW.md that have been reviewed and selected for action. Work items to implement.

**`DONE.md`** — Final destination. Every item ends up here with one of two statuses:
  - `DONE/COMPLETED` — implemented and verified
  - `DONE/IGNORED` — reviewed and decided not to fix (with reason)

The process:
1. Before significant work, read REVIEW.md to understand the landscape.
2. When an issue is visited, decide: fix it (→ TODO.md) or ignore it (→ DONE.md).
3. When a TODO item is completed, move it to DONE.md as DONE/COMPLETED.
4. When instructed, add newly discovered issues to REVIEW.md.

---

## Docker Requirements

### Critical: Backend Has No `--reload`

Uvicorn runs without the `--reload` flag. Every backend Python change requires:

```
docker compose restart backend
```

Do not assume code changes are active until the container has been restarted.

### When Changes Require Restart

* Python backend code
* Dependency changes
* Dockerfile changes
* docker-compose changes
* Environment configuration changes
* Startup scripts
* Container configuration

### Before Declaring Success

1. Does this change require a Docker restart?
2. Does this change require a Docker rebuild?
3. Has the user requested verification?

If a restart or rebuild is required, perform it or clearly instruct the user.

### Volume Mount Caveats

Session files live on a Docker volume mounted from the Windows host. Through Docker Desktop's file-sharing layer:

* `os.replace` is likely not atomic
* Minimize disk writes — batch saves exist for this reason (see Backend Guidelines)

---

## Backend Guidelines

* Place backend logic in `/backend`.
* Follow existing Python conventions used by the project.
* Prefer clear, maintainable code over clever code.

### Key Architecture Decisions

**Config caching**: `load_config()` and `load_agents()` cache by file mtime. They re-read from disk only when the file's modification time changes. Use the `/reload` endpoint (or call it via `/reload` slash command) to force a fresh read.

**Per-session lock**: `send_message` and `continue_hop` hold an `asyncio.Lock` per `session_id`. A second request for the same session blocks until the first finishes. This prevents last-writer-wins data loss on concurrent messages.

**Batch session saves**: Agent responses accumulate in memory during a turn and are flushed to disk in a single write via `save_session_batch()`. This cuts N disk writes to 2 per turn and reduces corruption risk through the Docker volume layer.

### Debug Agents (in agents.json)

Use these for testing hop chains without real LLM calls:

* `debug/echo` — echoes back the user message
* `debug/slowburn` — responds after a delay
* `debug/hoptest` — responds and @mentions another agent
* `debug/fail` — returns an error

### LLM Providers

Provider routing is in `llm_client.py`. Supports OpenRouter, Mistral, Google, and modelrelay. Endpoints are configured in `config.json`.

### Hop Chain Behavior

* `hop_limit` is read from config, then session, then defaults to 3
* `/continue` endpoint extends the hop limit by `extra_hops`
* Hops are disabled in continuous and N-cycle poll modes
* `response_looks_complete()` returns true for responses under 100 chars (so short `@Sam` strings drive the chain)

---

## Frontend Guidelines

* Place frontend changes in `/frontend`.
* Keep JavaScript readable and modular.
* Minimize unnecessary UI changes.
* Preserve existing user workflows unless a task explicitly requires changes.
* State mutations go through `setState()`. When debugging state issues, open the browser console (Verbose level) to see the `[state]` log timeline.

### Known Limitation

Firefox's SSE reader has a character limit that can clip long streaming responses. This is a browser limitation, not a code bug.

---

## Testing and Validation

There is no automated test suite. Verification is done manually through the browser UI.

Before completing work:

* Review changed files for obvious errors.
* Check for Python syntax issues (`python -m py_compile` on changed files).
* Verify imports and dependencies.
* Confirm changes are consistent with existing architecture.
* Determine whether Docker restart/rebuild is required.

When reporting completed work:

* Summarize files changed.
* Summarize behavior changes.
* State whether Docker restart or rebuild is required.
* Mention any items that should be added to `REVIEW.md`.
