"""
session_manager.py
Handles creating, loading, saving, and managing sessions.
Sessions are stored as JSON files in /sessions/.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

SESSIONS_DIR = "/sessions"
AGENTS_FILE = "/agents.json"
CONFIG_FILE = "/config.json"


_session_locks: dict[str, asyncio.Lock] = {}


def get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


def _ensure_dirs():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    # Clean up any leftover .tmp files from interrupted atomic writes
    for fname in os.listdir(SESSIONS_DIR):
        if fname.endswith(".tmp"):
            try:
                os.remove(os.path.join(SESSIONS_DIR, fname))
            except Exception:
                pass


def log_message(session_id: str, msg: str):
    path = os.path.join(SESSIONS_DIR, f"logs_{session_id}.log")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass


_config_cache = None
_config_mtime = 0
_agents_cache = None
_agents_mtime = 0


def load_config() -> dict:
    global _config_cache, _config_mtime
    mtime = os.path.getmtime(CONFIG_FILE)
    if _config_cache is None or mtime != _config_mtime:
        with open(CONFIG_FILE) as f:
            _config_cache = json.load(f)
        _config_mtime = mtime
    return _config_cache


def load_agents() -> list[dict]:
    global _agents_cache, _agents_mtime
    mtime = os.path.getmtime(AGENTS_FILE)
    if _agents_cache is None or mtime != _agents_mtime:
        with open(AGENTS_FILE) as f:
            _agents_cache = json.load(f)
        _agents_mtime = mtime
    return _agents_cache


def get_agent_by_id(agent_id: str) -> Optional[dict]:
    for agent in load_agents():
        if agent["id"] == agent_id:
            return agent
    return None


def _session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"session_{session_id}.json")


def list_sessions() -> list[dict]:
    sessions = []
    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            # Return summary only (not full message list)
            last_msg = None
            if data.get("messages"):
                last_msg = data["messages"][-1].get("timestamp")
            sessions.append({
                "id": data["id"],
                "title": data["title"],
                "active_agents": data["active_agents"],
                "message_count": sum(1 for m in data.get("messages", []) if m["role"] in ("user", "agent")),
                "created": data["created"],
                "last_message": last_msg,
            })
        except Exception:
            # Extract session id from filename (session_{id}.json)
            sid = fname.replace("session_", "").replace(".json", "")
            log_message(sid, f"LIST SESSIONS SKIP | corrupted or unreadable file: {fname}")
            continue
    # Sort by last activity: sessions with messages sort by last message
    # timestamp; sessions with no messages sort by created. Most recent first.
    def _sort_key(s):
        return s["last_message"] or s["created"] or ""
    sessions.sort(key=_sort_key, reverse=True)
    return sessions


def create_session(title: str, agent_ids: list[str]) -> dict:
    session_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    config = load_config()
    session = {
        "id": session_id,
        "title": title,
        "created": now,
        "active_agents": agent_ids,
        "poll_order": "fixed",
        "poll_mode": config.get("poll_mode", "normal"),
        "poll_throttle": max(500, config.get("poll_throttle", 1000)),
        "hop_limit": config.get("hop_limit", 3),
        "allow_hops": True,
        "pause_for_user": config.get("pause_for_user", False),
        "messages": [],
    }
    path = _session_path(session_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(session, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    log_message(session_id, f"SESSION CREATED | title={title!r} | agents={agent_ids}")
    return session


def load_session(session_id: str) -> Optional[dict]:
    path = _session_path(session_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_session(session: dict):
    path = _session_path(session["id"])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(session, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_session_batch(session: dict):
    """Stamp any untimestamped messages and save to disk in one write.
    Used by _stream_and_save to batch all agent responses into a single write.
    """
    now = datetime.now(timezone.utc).isoformat()
    for msg in session.get("messages", []):
        if not msg.get("timestamp"):
            msg["timestamp"] = now
    save_session(session)


def delete_session(session_id: str) -> bool:
    path = _session_path(session_id)
    if os.path.exists(path):
        os.remove(path)
        log_path = os.path.join(SESSIONS_DIR, f"logs_{session_id}.log")
        if os.path.exists(log_path):
            os.remove(log_path)
        return True
    log_message(session_id, "DELETE FAILED | session file not found")
    return False


def clear_session_messages(session_id: str) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        log_message(session_id, "CLEAR MESSAGES FAILED | session not found")
        return None
    session["messages"] = []
    save_session(session)
    log_path = os.path.join(SESSIONS_DIR, f"logs_{session_id}.log")
    if os.path.exists(log_path):
        os.remove(log_path)
    return session


def add_debug_log(session_id: str, entry_type: str, data: str = ""):
    """Append a debug event to session['messages'] with role='system' (excluded from LLM context)."""
    session = load_session(session_id)
    if not session:
        return None
    parts = [f"[{entry_type}]"]
    if data:
        parts.append(f"({data})")
    session["messages"].append({
        "role": "system",
        "content": " ".join(parts),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    save_session(session)
    return session


def append_message(session_id: str, message: dict) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        log_message(session_id, "APPEND MESSAGE FAILED | session not found")
        return None
    message["timestamp"] = datetime.now(timezone.utc).isoformat()
    role = message.get("role", "?")
    content_preview = message.get("content", "")[:80]
    agent = message.get("agent_name") or message.get("agent_id") or ""
    log_message(session_id, f"MESSAGE {role} | {agent} | {content_preview!r}")
    session["messages"].append(message)
    save_session(session)
    return session


def reorder_agents(session_id: str, agent_ids: list[str]) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        log_message(session_id, f"REORDER AGENTS FAILED | session not found")
        return None
    session["active_agents"] = agent_ids
    save_session(session)
    log_message(session_id, f"AGENTS REORDERED | order={agent_ids}")
    return session


def add_agent_to_session(session_id: str, agent_id: str) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        log_message(session_id, f"ADD AGENT FAILED | agent={agent_id} | session not found")
        return None
    if agent_id not in session["active_agents"]:
        session["active_agents"].append(agent_id)
        save_session(session)
        log_message(session_id, f"AGENT ADDED | agent={agent_id}")
    return session


def remove_agent_from_session(session_id: str, agent_id: str) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        log_message(session_id, f"REMOVE AGENT FAILED | agent={agent_id} | session not found")
        return None
    session["active_agents"] = [a for a in session["active_agents"] if a != agent_id]
    save_session(session)
    log_message(session_id, f"AGENT REMOVED | agent={agent_id}")
    return session


def build_catch_up_context(session: dict, count: int) -> str:
    """Build a brief catch-up summary from the last N messages.
    Only includes user and agent messages — system messages (join/leave
    events, debug logs, slash-command output) are excluded as they are
    noisy and irrelevant context for a joining agent.
    """
    messages = session.get("messages", [])
    # Filter to only conversational messages before applying the count limit
    conversational = [m for m in messages if m["role"] in ("user", "agent")]
    recent = conversational[-count:] if len(conversational) > count else conversational
    if not recent:
        return "This is the start of the conversation."
    lines = []
    for msg in recent:
        if msg["role"] == "user":
            lines.append(f"User: {msg['content']}")
        elif msg["role"] == "agent":
            lines.append(f"{msg.get('agent_name', msg.get('agent_id', 'Agent'))}: {msg['content']}")
    return "\n".join(lines)
