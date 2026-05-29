"""
agent_runner.py
Orchestrates multi-agent conversation turns — manages the hop guard,
@mention chain resolution, polling modes, and pause-for-user flow.
"""

import json
import random
import asyncio
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from session_manager import load_session, log_message
from prompt_builder import parse_mentions, response_looks_complete
from llm_client import stream_agent_response


def parse_poll_mode(poll_mode: str) -> int:
    if poll_mode == "continuous":
        return 999999
    if poll_mode.isdigit() and int(poll_mode) > 0:
        return int(poll_mode)
    return 1


def build_queue(
    cycle: int,
    mentioned_agent_ids: list[str],
    active_agent_ids: list[str],
    session: dict,
    starting_hop: int = 0,
) -> list[tuple[str, int]]:
    if cycle == 1 and mentioned_agent_ids:
        return [(aid, starting_hop) for aid in mentioned_agent_ids if aid in active_agent_ids]
    poll_order = session.get("poll_order", "fixed")
    ordered = active_agent_ids[:]
    if poll_order == "alpha":
        ordered.sort()
    elif poll_order == "random":
        random.shuffle(ordered)
    return [(aid, 0) for aid in ordered]


def advance_hop_queue(
    queue: list[tuple[str, int]],
    queued_this_turn: set[tuple[str, int]],
    full_response: str,
    active_agent_ids: list[str],
    agent_id: str,
    hop: int,
    session_id: str,
) -> None:
    mentioned = parse_mentions(full_response, active_agent_ids)
    if not mentioned:
        log_message(session_id, f"MENTION PARSE | agent={agent_id} | no mentions found")
    still_pending_ids = {aid for aid, _ in queue}
    for mid in mentioned:
        next_hop = hop + 1
        slot = (mid, next_hop)
        if mid in still_pending_ids:
            old_slot = next(e for e in queue if e[0] == mid)
            if old_slot[1] < next_hop:
                queue.remove(old_slot)
                queued_this_turn.discard(old_slot)
                queued_this_turn.add(slot)
                queue.append(slot)
                log_message(session_id, f"QUEUED BUMP | agent={mid} | hop={old_slot[1]}->{next_hop} | triggered_by={agent_id}")
        elif slot not in queued_this_turn:
            queued_this_turn.add(slot)
            queue.append(slot)
            log_message(session_id, f"QUEUED | agent={mid} | hop={next_hop} | triggered_by={agent_id}")


def should_pause_for_user(
    full_response: str,
    config: dict,
    live_session: dict,
) -> Optional[str]:
    if not full_response:
        return None
    if not live_session.get("pause_for_user", config.get("pause_for_user", False)):
        return None
    user_name = config.get("user_name", "User")
    if re.search(r'@' + re.escape(user_name) + r'\b', full_response, re.IGNORECASE):
        return user_name
    return None


async def run_turn(
    session: dict,
    user_message: str,
    mentioned_agent_ids: list[str],
    config: dict,
    agents_by_id: dict,
    catch_up_text_for_new: dict,
    starting_hop: int = 0,
) -> AsyncGenerator[str, None]:
    """
    Orchestrate a full conversation turn:
    - Build agent queue from @mentions or all active agents
    - Stream each agent's response
    - Parse @mentions from complete responses and extend queue
    - Enforce hop guard, pausing when limit is reached
    """
    active_agent_ids = session["active_agents"]
    active_agents = [agents_by_id[aid] for aid in active_agent_ids if aid in agents_by_id]
    # Read hop_limit from config first so /continue's extended_config takes effect.
    # Fall back to session-level override, then global default.
    hop_limit = config.get("hop_limit") or session.get("hop_limit") or 5

    turn = max(0, len([m for m in session.get("messages", []) if m["role"] == "user"]) - 1)

    log_message(session["id"], f"TURN START | message={user_message[:80]!r} | poll_mode={session.get('poll_mode', config.get('poll_mode', 'normal'))} | poll_order={session.get('poll_order','fixed')}")

    # Poll mode — cycle agents multiple times per user turn.
    # 'normal'     = respond once (default), hops enabled by default
    # 'continuous' = keep cycling until manually stopped, hops disabled (use with caution)
    # '3'          = cycle exactly N times, hops disabled
    # Values of 0 or invalid strings are treated as normal.
    poll_mode = session.get("poll_mode", config.get("poll_mode", "normal"))
    max_cycles = parse_poll_mode(poll_mode)
    if max_cycles > 1:
        hop_limit = 999999  # disable hop limit for cycling modes

    # prior_responses accumulates within a single cycle only.
    # Between cycles, completed responses are injected into session["messages"]
    # so build_messages_payload history picks them up as proper conversation history.
    prior_responses: list[dict] = []

    cycle = 0
    pause_triggered = False
    while cycle < max_cycles:
        cycle += 1
        prior_responses = []  # reset for each cycle — cross-cycle context comes from session["messages"]

        # Build queue for this cycle
        queue = build_queue(cycle, mentioned_agent_ids, active_agent_ids, session, starting_hop)

        log_message(session["id"], f"CYCLE {cycle}/{max_cycles} | queue={[a for a, _ in queue]}")

        # visited and queued reset each cycle — agents can respond once per cycle.
        visited_this_turn: list[tuple[str, int]] = []
        queued_this_turn: set[tuple[str, int]] = set(queue)
        hop_pause_sent = False
        cycle_responses: list[dict] = []  # track this cycle's responses to inject into history

        # Throttle after user message, before first agent response
        #if user_message and cycle == 1:
        #    throttle_ms = session.get("poll_throttle", config.get("poll_throttle", 1000))
        #    await asyncio.sleep(throttle_ms / 1000)

        while queue:
            agent_id, hop = queue.pop(0)

            # Skip if already responded at this hop (safety net)
            if (agent_id, hop) in visited_this_turn:
                continue

            # Hop guard — pause and ask user before continuing
            if hop >= hop_limit and not hop_pause_sent:
                hop_pause_sent = True
                remaining = [agent_id] + [a for a, _ in queue]
                log_message(session["id"], f"HOP LIMIT REACHED | hop={hop} | remaining={remaining}")
                yield f"data: {json.dumps({'type': 'hop_limit', 'hop': hop, 'remaining_agents': remaining})}\n\n"
                break

            visited_this_turn.append((agent_id, hop))

            agent = agents_by_id.get(agent_id)
            if not agent:
                log_message(session["id"], f"AGENT NOT FOUND | agent_id={agent_id}")
                continue

            yield f"data: {json.dumps({'type': 'agent_start', 'agent_id': agent_id, 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'hop': hop})}\n\n"

            is_catch_up = agent_id in catch_up_text_for_new
            catch_up_text = catch_up_text_for_new.get(agent_id, "")
            full_response = ""

            async for chunk in stream_agent_response(
                agent=agent,
                session=session,
                user_message=user_message,
                prior_responses=prior_responses,
                config=config,
                is_catch_up=is_catch_up,
                catch_up_text=catch_up_text,
                active_agents=active_agents,
                hop=hop,
                turn=turn,
                cycle=cycle,
            ):
                yield chunk
                if chunk.startswith("data: "):
                    try:
                        evt = json.loads(chunk[6:])
                        if evt.get("type") == "agent_done":
                            full_response = evt.get("content", "")
                    except Exception:
                        log_message(session["id"], f"AGENT DONE PARSE | agent={agent_id} — skipping malformed chunk: {chunk[:80]}")
                        pass

            # Add to context for subsequent agents this turn
            if full_response:
                prior_responses.append({
                    "agent_id": agent_id,
                    "agent_name": agent["name"],
                    "agent_avatar": agent.get("avatar", ""),
                    "content": full_response,
                })
                cycle_responses.append({
                    "role": "agent",
                    "agent_id": agent_id,
                    "agent_name": agent["name"],
                    "agent_avatar": agent.get("avatar", ""),
                    "content": full_response,
                    "hop": hop,
                    "turn": turn,
                    "cycle": cycle,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            # Only follow @mentions from responses that look complete.
            # Truncated responses (cut off by max_tokens) should not drive the chain.
            allow_hops = session.get("allow_hops", True)
            
            # We do not allow hops in continuous mode as it is redundant
            # We do not allow hops in N mode as we want to limit to N exactly.
            if poll_mode != "normal":
                allow_hops = False

            if full_response and response_looks_complete(full_response) and allow_hops:
                advance_hop_queue(queue, queued_this_turn, full_response, active_agent_ids, agent_id, hop, session["id"])
            elif full_response and not response_looks_complete(full_response):
                log_message(session["id"], f"TRUNCATED RESPONSE | agent={agent_id} | hop={hop} | skipping mention parse")

            # Re-read session from disk to pick up live setting changes
            # from /poll_throttle, /pause_for_user, etc.
            live = load_session(session["id"]) or session

            # Pause for user — if the agent @mentioned the user and pause_for_user is
            # enabled, stop the queue and wait for the user to respond first.
            pause_user = should_pause_for_user(full_response, config, live)
            if pause_user:
                remaining = [aid for aid, _ in queue]
                log_message(session["id"], f"PAUSE FOR USER | agent={agent_id} | mentioned={pause_user} | remaining={remaining}")
                yield f"data: {json.dumps({'type': 'pause_for_user', 'agent_id': agent_id, 'user_name': pause_user, 'remaining_agents': remaining})}\n\n"
                pause_triggered = True
                break

            # skip throttle after the last agent in the queue unless continuous or N
            if queue or poll_mode != "normal":
                throttle_ms = live.get("poll_throttle", config.get("poll_throttle", 1000))
                await asyncio.sleep(throttle_ms / 1000)

        # If pause_for_user was triggered, stop the outer cycle loop too.
        # Yield turn_done first so the frontend clears streaming state and
        # _cycleStartedAgents — without it the UI locks and the set accumulates.
        if pause_triggered:
            yield f"data: {json.dumps({'type': 'turn_done'})}\n\n"
            break

        # Inject this cycle's responses into the in-memory session so the next
        # cycle's build_messages_payload history includes them. This is what
        # prevents agents from repeating themselves across cycles.
        if cycle_responses:
            session["messages"].extend(cycle_responses)
            log_message(session["id"], f"CYCLE {cycle} COMPLETE | {len(cycle_responses)} responses injected into session history")

        yield f"data: {json.dumps({'type': 'turn_done'})}\n\n"