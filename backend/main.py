"""
main.py
FastAPI application — all HTTP routes for AgentChat.
"""

import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import session_manager as sm
from session_manager import log_message
from agent_runner import run_turn
from prompt_builder import parse_mentions

app = FastAPI(title="AgentChat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    sm._ensure_dirs()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    title: str
    agent_ids: list[str]


class SendMessageRequest(BaseModel):
    content: str


class AddAgentRequest(BaseModel):
    agent_id: str


class ReorderAgentsRequest(BaseModel):
    agent_ids: list[str]


class ContinueHopRequest(BaseModel):
    content: str                 # user message (original or new, depending on context)
    remaining_agents: list[str]  # agents still queued when hop limit / pause hit
    starting_hop: int = 0        # hop level at which the limit / pause was hit
    extra_hops: int = 3          # how many additional hops to allow
    pause_for_user_response: bool = False  # if true, save content as user message first


class RenameSessionRequest(BaseModel):
    title: str


class SetPollOrderRequest(BaseModel):
    order: str  # 'fixed', 'random', or 'alpha'


class SetPollModeRequest(BaseModel):
    mode: str  # 'normal', 'continuous', or a number string like '3'


class SetPollThrottleRequest(BaseModel):
    delay_ms: int  # milliseconds delay between agent responses


class SetHopLimitRequest(BaseModel):
    limit: int  # max hops before chain pauses


class SetAllowHopsRequest(BaseModel):
    allow_hops: bool  # allow agent-to-agent @mentions to extend the queue


class SetPauseForUserRequest(BaseModel):
    pause_for_user: bool  # pause when an agent @mentions the user


class LogRequest(BaseModel):
    session_id: Optional[str] = None
    message: str


class DebugLogRequest(BaseModel):
    type: str
    data: str = ""


# ---------------------------------------------------------------------------
# Shared SSE streaming helper
# ---------------------------------------------------------------------------

async def _stream_and_save(
    session_id: str,
    run_turn_iter,
    log_label: str,
    session: dict,
):
    """Yields SSE chunks from run_turn, counting agent_done events.
    Saves all accumulated messages to disk in a single batch write
    once the generator exhausts.
    """
    saved_count = 0
    cancelled = False
    try:
        async for chunk in run_turn_iter:
            if chunk.startswith("data: "):
                try:
                    evt = json.loads(chunk[6:])
                    if evt.get("type") == "agent_done" and evt.get("content", "").strip():
                        saved_count += 1
                except Exception:
                    log_message(session_id, f"SSE PARSE ERROR | {log_label} — skipping malformed chunk")
            yield chunk
    except GeneratorExit:
        cancelled = True
        log_message(session_id, f"{log_label} CANCELLED | {saved_count} agent responses before cancel")
        raise
    finally:
        sm.save_session_batch(session)
        if cancelled:
            log_message(session_id, f"{log_label} SAVED ON CANCEL | {saved_count} agent responses flushed")
        else:
            log_message(session_id, f"{log_label} COMPLETE | {saved_count} agent responses saved")


# ---------------------------------------------------------------------------
# Agent library
# ---------------------------------------------------------------------------

@app.get("/agents")
def get_agents():
    return sm.load_agents()


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@app.get("/sessions")
def get_sessions():
    return sm.list_sessions()


@app.post("/sessions")
def create_session(req: CreateSessionRequest):
    all_agent_ids = {a["id"] for a in sm.load_agents()}
    bad = [aid for aid in req.agent_ids if aid not in all_agent_ids]
    if bad:
        raise HTTPException(400, f"Unknown agent ids: {bad}")
    session = sm.create_session(req.title, req.agent_ids)
    log_message(session["id"], f"POST /sessions | title={req.title!r} | agents={req.agent_ids}")
    return session


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "GET /sessions/{id} -> 404 NOT FOUND")
        raise HTTPException(404, "Session not found")
    return session


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    ok = sm.delete_session(session_id)
    if not ok:
        log_message(session_id, "DELETE /sessions/{id} -> 404 NOT FOUND")
        raise HTTPException(404, "Session not found")
    log_message(session_id, "DELETE /sessions/{id} -> deleted")
    return {"deleted": session_id}


@app.delete("/sessions/{session_id}/messages")
def clear_messages(session_id: str):
    session = sm.clear_session_messages(session_id)
    if not session:
        log_message(session_id, "DELETE /sessions/{id}/messages -> 404 NOT FOUND")
        raise HTTPException(404, "Session not found")
    log_message(session_id, "DELETE /sessions/{id}/messages -> cleared")
    return session


@app.patch("/sessions/{session_id}/title")
def rename_session(session_id: str, req: RenameSessionRequest):
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "Title cannot be empty")
    if len(title) > 200:
        raise HTTPException(400, "Title too long (max 200 chars)")
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PATCH /sessions/{id}/title -> 404 NOT FOUND")
        raise HTTPException(404, "Session not found")
    session["title"] = title
    sm.save_session(session)
    log_message(session_id, f"RENAME | title={title!r}")
    return session


# ---------------------------------------------------------------------------
# Agent roster management within a session
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/agents")
def add_agent(session_id: str, req: AddAgentRequest):
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "POST /sessions/{id}/agents -> 404 session not found")
        raise HTTPException(404, "Session not found")

    agent = sm.get_agent_by_id(req.agent_id)
    if not agent:
        log_message(session_id, f"POST /sessions/{session_id}/agents -> 404 agent '{req.agent_id}' not found")
        raise HTTPException(404, f"Agent '{req.agent_id}' not found")

    if req.agent_id in session["active_agents"]:
        log_message(session_id, f"POST /sessions/{session_id}/agents -> 400 agent '{req.agent_id}' already in session")
        raise HTTPException(400, f"Agent '{req.agent_id}' is already in this session")

    config = sm.load_config()
    catch_up_text = sm.build_catch_up_context(session, config.get("catch_up_message_count", 20))

    session = sm.add_agent_to_session(session_id, req.agent_id)
    log_message(session_id, f"POST /sessions/{session_id}/agents | agent={req.agent_id}")

    sm.append_message(session_id, {
        "role": "system",
        "content": f"{agent['name']} joined the conversation.",
        "agent_id": req.agent_id,
    })

    return {
        "session": sm.load_session(session_id),
        "catch_up_text": catch_up_text,
    }


@app.delete("/sessions/{session_id}/agents/{agent_id}")
def remove_agent(session_id: str, agent_id: str):
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "DELETE /sessions/{id}/agents/{aid} -> 404")
        raise HTTPException(404, "Session not found")

    agent = sm.get_agent_by_id(agent_id)
    name = agent['name'] if agent else agent_id

    session = sm.remove_agent_from_session(session_id, agent_id)
    log_message(session_id, f"DELETE /sessions/{session_id}/agents/{agent_id} | agent={agent_id}")
    sm.append_message(session_id, {
        "role": "system",
        "content": f"{name} left the conversation.",
        "agent_id": agent_id,
    })
    return sm.load_session(session_id)


@app.put("/sessions/{session_id}/agents/reorder")
def reorder_agents(session_id: str, req: ReorderAgentsRequest):
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/agents/reorder -> 404 NOT FOUND")
        raise HTTPException(404, "Session not found")

    # Validate all submitted IDs are currently active in this session
    current = set(session["active_agents"])
    submitted = set(req.agent_ids)
    not_in_session = submitted - current
    if not_in_session:
        raise HTTPException(400, f"Agent(s) not in this session: {sorted(not_in_session)}")
    missing_from_request = current - submitted
    if missing_from_request:
        raise HTTPException(400, f"Reorder must include all active agents. Missing: {sorted(missing_from_request)}")

    result = sm.reorder_agents(session_id, req.agent_ids)
    if not result:
        raise HTTPException(404, "Session not found")
    return result


# ---------------------------------------------------------------------------
# Poll order — controls agent response ordering in run_turn()
# ---------------------------------------------------------------------------

@app.put("/sessions/{session_id}/poll_order")
def set_poll_order(session_id: str, req: SetPollOrderRequest):
    if req.order not in ("fixed", "random", "alpha"):
        raise HTTPException(400, "Order must be 'fixed', 'random', or 'alpha'")
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/poll_order -> 404")
        raise HTTPException(404, "Session not found")
    session["poll_order"] = req.order
    sm.save_session(session)
    log_message(session_id, f"POLL ORDER SET | order={req.order}")
    return {"poll_order": req.order}


# ---------------------------------------------------------------------------
# Poll mode — controls agent response cycling in run_turn()
# ---------------------------------------------------------------------------

@app.put("/sessions/{session_id}/poll_mode")
def set_poll_mode(session_id: str, req: SetPollModeRequest):
    if req.mode not in ("normal", "continuous") and not (req.mode.isdigit() and int(req.mode) > 0):
        raise HTTPException(400, "Mode must be 'normal', 'continuous', or a positive number")
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/poll_mode -> 404")
        raise HTTPException(404, "Session not found")
    session["poll_mode"] = req.mode
    sm.save_session(session)
    log_message(session_id, f"POLL MODE SET | mode={req.mode}")
    return {"poll_mode": req.mode}


# ---------------------------------------------------------------------------
# Poll throttle — delay between agent responses in run_turn()
# ---------------------------------------------------------------------------

@app.put("/sessions/{session_id}/poll_throttle")
def set_poll_throttle(session_id: str, req: SetPollThrottleRequest):
    if req.delay_ms < 0:
        raise HTTPException(400, "Delay must be a non-negative integer")
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/poll_throttle -> 404")
        raise HTTPException(404, "Session not found")
    # Enforce minimum throttle of 500ms to respect free-tier rate limits
    delay_ms = max(500, req.delay_ms)
    session["poll_throttle"] = delay_ms
    sm.save_session(session)
    log_message(session_id, f"POLL THROTTLE SET | requested={req.delay_ms}ms | applied={delay_ms}ms")
    return {"poll_throttle": delay_ms}


# ---------------------------------------------------------------------------
# Hop limit — max agent-to-agent hops before chain pauses
# ---------------------------------------------------------------------------

@app.put("/sessions/{session_id}/hop_limit")
def set_hop_limit(session_id: str, req: SetHopLimitRequest):
    if req.limit < 0:
        raise HTTPException(400, "Hop limit must be a non-negative integer")
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/hop_limit -> 404")
        raise HTTPException(404, "Session not found")
    session["hop_limit"] = req.limit
    sm.save_session(session)
    log_message(session_id, f"HOP LIMIT SET | limit={req.limit}")
    return {"hop_limit": req.limit}


# ---------------------------------------------------------------------------
# Allow hops — controls whether agent-to-agent @mentions extend the queue
# ---------------------------------------------------------------------------

@app.put("/sessions/{session_id}/allow_hops")
def set_allow_hops(session_id: str, req: SetAllowHopsRequest):
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/allow_hops -> 404")
        raise HTTPException(404, "Session not found")
    session["allow_hops"] = req.allow_hops
    sm.save_session(session)
    log_message(session_id, f"ALLOW HOPS SET | allow_hops={req.allow_hops}")
    return {"allow_hops": req.allow_hops}


# ---------------------------------------------------------------------------
# Pause for user — pause turn when an agent @mentions the user
# ---------------------------------------------------------------------------

@app.put("/sessions/{session_id}/pause_for_user")
def set_pause_for_user(session_id: str, req: SetPauseForUserRequest):
    session = sm.load_session(session_id)
    if not session:
        log_message(session_id, "PUT /sessions/{id}/pause_for_user -> 404")
        raise HTTPException(404, "Session not found")
    session["pause_for_user"] = req.pause_for_user
    sm.save_session(session)
    log_message(session_id, f"PAUSE FOR USER SET | pause_for_user={req.pause_for_user}")
    return {"pause_for_user": req.pause_for_user}


# ---------------------------------------------------------------------------
# Sending a message — the main SSE streaming endpoint
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/message")
async def send_message(session_id: str, req: SendMessageRequest):
    async with sm.get_session_lock(session_id):
        session = sm.load_session(session_id)
        if not session:
            log_message(session_id, "POST /sessions/{id}/message -> 404 session not found")
            raise HTTPException(404, "Session not found")

        if not session["active_agents"]:
            log_message(session_id, "POST /sessions/{id}/message -> 400 no active agents")
            raise HTTPException(400, "No agents in this session. Add at least one agent first.")

        config = sm.load_config()
        all_agents = sm.load_agents()
        agents_by_id = {a["id"]: a for a in all_agents}

        # Parse @mentions from user message
        active_ids = session["active_agents"]
        mentioned = parse_mentions(req.content, active_ids)

        log_message(session_id, f"POST /sessions/{session_id}/message | content={req.content[:100]!r} | mentions={mentioned}")

        # Save user message to session file and use the returned session so
        # run_turn sees the latest messages (including this user message).
        session = sm.append_message(session_id, {
            "role": "user",
            "content": req.content,
        })

        async def event_stream():
            async for chunk in _stream_and_save(
                session_id,
                run_turn(
                    session=session,
                    user_message=req.content,
                    mentioned_agent_ids=mentioned,
                    config=config,
                    agents_by_id=agents_by_id,
                    catch_up_text_for_new={},
                ),
                "TURN",
                session,
            ):
                yield chunk

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


# ---------------------------------------------------------------------------
# Continue hop chain after hop-limit pause
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/continue")
async def continue_hop(session_id: str, req: ContinueHopRequest):
    """Resume agent chain after the user approves continuing past the hop limit."""
    async with sm.get_session_lock(session_id):
        # Validate session exists
        if not sm.load_session(session_id):
            log_message(session_id, "POST /sessions/{id}/continue -> 404")
            raise HTTPException(404, "Session not found")

        if not req.remaining_agents:
            log_message(session_id, "CONTINUE REJECTED | empty remaining_agents — frontend sent an empty list")
            raise HTTPException(400, "No remaining agents to continue")

        config = sm.load_config()
        all_agents = sm.load_agents()
        agents_by_id = {a["id"]: a for a in all_agents}

        # Set hop limit to starting_hop + extra_hops so the chain continues
        # from where it paused rather than restarting from 0
        extended_config = {**config, "hop_limit": req.starting_hop + req.extra_hops}

        log_message(session_id, f"CONTINUE HOP | remaining={req.remaining_agents} | starting_hop={req.starting_hop} | extra_hops={req.extra_hops} | new_limit={req.starting_hop + req.extra_hops}")

        # Save user message BEFORE creating the generator, so it's written
        # to disk even if the client disconnects before the stream starts.
        if req.pause_for_user_response:
            sm.append_message(session_id, {
                "role": "user",
                "content": req.content,
            })

        async def event_stream():
            # Reload session HERE inside the stream so we get the latest messages
            # including any responses saved before the hop limit was hit.
            fresh_session = sm.load_session(session_id)
            if not fresh_session:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Session not found on continue'})}\n\n"
                return

            async for chunk in _stream_and_save(
                session_id,
                run_turn(
                    session=fresh_session,
                    user_message=req.content,
                    mentioned_agent_ids=req.remaining_agents,
                    config=extended_config,
                    agents_by_id=agents_by_id,
                    catch_up_text_for_new={},
                    starting_hop=req.starting_hop,
                ),
                "CONTINUE",
                fresh_session,
            ):
                yield chunk

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


# ---------------------------------------------------------------------------
# Reload config and agents from disk (hot-reload without Docker restart)
# ---------------------------------------------------------------------------

@app.post("/reload")
def reload_config():
    """Re-read agents.json and config.json from disk.
    Call this after editing either file to pick up changes without restarting.
    """
    try:
        agents = sm.load_agents()
        config = sm.load_config()
        return {
            "status": "ok",
            "agents_loaded": len(agents),
            "config_keys": list(config.keys()),
        }
    except Exception as e:
        raise HTTPException(500, f"Reload failed: {e}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/config")
def get_config():
    return sm.load_config()


@app.post("/log")
def client_log(req: LogRequest):
    msg = f"FRONTEND | {req.message}"
    if req.session_id:
        log_message(req.session_id, msg)
    else:
        print(f"[CLIENT_LOG] {msg}")
    return {"ok": True}


@app.post("/sessions/{session_id}/debug_log")
def add_debug_log(session_id: str, req: DebugLogRequest):
    sm.add_debug_log(session_id, req.type, req.data)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}
