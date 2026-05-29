# AgentChat

Last updated: 2026-05-29

A locally-hosted multi-agent chat app. Multiple AI personas share sessions, converse with each other, and can @mention one another — with a hop guard to prevent token spirals.

Purpose: Just a fun weekend project so I could play around with AI and work with Claude Code and OpenCode.

This is CC0 in case anyone wants to use it for something.
The free icons are from https://game-icons.net/ and are also CC.

## Quick start

### 1. Add your API key

```bash
cp .env.example .env
# Edit .env and add your API keys:
# OPENROUTER_API_KEY=your_key_here
```

### 2. Start everything

```bash
docker compose up -d --build
```

Then open **http://localhost:3000** in your browser.

That's it.

---

## Usage

- **New session** — click `+ New` in the sidebar, pick a title and agents (1–4 recommended)
- **Send message** — type in the input box, press Enter
- **@mention an agent** — type `@Name` to route your message to a specific agent. Type `@` to get autocomplete
- **Agent-to-agent chat** — agents can @mention each other. The hop guard pauses the chain at the configured limit
- **Add agent mid-session** — click `+ Add agent` in the agent bar. They'll receive a catch-up summary
- **Remove agent** — click `×` on their chip in the agent bar
- **Delete session** — hover over a session in the sidebar and click `×`
- **Agent chat order** — drag and drop agents in top agent bar to change their order in the chat.

---

## Configuration

Edit `config.json` (no restart needed — it's read per request):

| Key | Default | Description |
|-----|---------|-------------|
| `hop_limit` | 3 | Max agent-to-agent hops before chain pauses |
| `catch_up_message_count` | 20 | Messages shown to a newly-added agent as context |
| `max_tokens_per_response` | 1000 | Hard cap per agent response |
| `stream_responses` | true | Stream tokens in real time |
| `log_requests` | false | Log full LLM request bodies to session log |
| `log_responses` | false | Log full LLM response text to session log |
| `agent_prompt` | — | Global instruction injected into every agent's system prompt |

---

## Agents

Edit `agents.json` to customize. Each agent has:

```json
{
  "id": "sam",
  "name": "Sam",
  "avatar": "images/avatar.png",
  "color": "#6366F1",
  "model": "google/gemini-2.0-flash-exp",
  "personality": "..."
}
```

To use a different model for a specific agent, change the `"model"` field to any model available on your OpenRouter free tier. Some options:
- `google/gemini-2.0-flash-exp` (free)
- `meta-llama/llama-3.3-70b-instruct` (free)
- `mistralai/mistral-7b-instruct` (free)
- `deepseek/deepseek-chat` (free)

### Debug providers

Special model names that skip the LLM entirely — useful for testing and debugging locally:

| Model | Behaviour |
|-------|-----------|
| `debug/echo` | Agent echoes the user's input message verbatim. |
| `debug/slowburn` | Streams one character every 40ms to simulate a slow model or poor connection. |
| `debug/hoptest` | @mentions every other active agent by display name. |
| `debug/fail` | Raises a 500 Internal Server Error for testing error handling. |

---

## Slash commands

Type these in the chat input to control the session at runtime:

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/stop` | — | Stops the current agent response chain, or clears a paused hop-limit prompt. |
| `/title` | `<new title>` | Renames the current session. |
| `/hop_limit` | `<n>` | Sets max agent-to-agent hops before chain pauses. |
| `/allow_hops` | `true` or `false` | Enables or disables agent-to-agent @mention chaining. When `false`, agents won't trigger each other but user @mentions still work. |
| `/pause_for_user` | `true` or `false` | When `true`, if an agent @mentions you, the turn pauses and waits for your response before continuing. |
| `/poll_order` | `fixed`, `random`, or `alpha` | Sets the order agents respond in each poll cycle. `fixed` = session order, `random` = shuffled, `alpha` = alphabetical. |
| `/poll_mode` | `normal`, `continuous`, or a number `N` | `normal` = each agent responds once per turn with hops, `continuous` = agents keep cycling indefinitely, disables hops, `N` = agents cycle N times per turn, disables hops. |
| `/poll_throttle` | `<ms>` | Sets the delay between each agent's response in a poll cycle. |
| `/clear` | — | Clears chat history from session, keeps session settings. |
| `/status` | — | Shows all current session settings (id, title, agents, poll, hop limit). |
| `/agents` | — | Lists all available agents and their config (id, model, color, avatar). |
| `/reload` | — | Reloads `agents.json` and `config.json` from disk without restarting Docker. |
| `/export` | — | Downloads the current session as a markdown file. |
| `/help` | — | Shows this help message. |

---

## Session files

Sessions are stored as human-readable JSON in `sessions/`. You can open, read, and edit them in any text editor. They persist across Docker restarts.

---

## File structure

```
agentchat/
├── docker-compose.yml
├── .env                    ← your API keys
├── config.json             ← tuning knobs
├── agents.json             ← agent library
├── sessions/               ← session files (auto-created)
├── backend/
│   ├── main.py             ← FastAPI routes
│   ├── agent_runner.py     ← LLM calls + hop guard
│   ├── session_manager.py  ← session CRUD
│   └── requirements.txt
└── frontend/
    └── index.html          ← html
    └── style.css           ← stylesheet
    └── script.js           ← scripts
```
