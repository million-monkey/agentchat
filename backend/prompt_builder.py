import re


def parse_mentions(text: str, active_agent_ids: list[str]) -> list[str]:
    """Extract @mentioned agent ids from text. Case-insensitive, matches on id not display name."""
    mentioned = []
    pattern = r"@(\w+)"
    for match in re.finditer(pattern, text):
        name = match.group(1).lower()
        if name in active_agent_ids and name not in mentioned:
            mentioned.append(name)
    return mentioned


def response_looks_complete(text: str) -> bool:
    """
    Return True if the response appears to end naturally rather than being
    cut off mid-sentence by a max_tokens limit. Truncated responses should
    not drive the @mention chain forward.
    """
    stripped = text.strip()
    if not stripped:
        return False
    # Short responses are almost certainly not truncated (max_tokens=1000)
    if len(stripped) < 100:
        return True
    # Ends with sentence-ending punctuation, closing quote/paren, or emoji
    return bool(re.search(r'[.!?)\]"\u2019\u201d\U0001F000-\U0001FFFF]\s*$', stripped))


def build_system_prompt(
    agent: dict,
    session: dict,
    is_catch_up: bool,
    catch_up_text: str,
    active_agents: list[dict],
    config: dict,
) -> str:
    user_name = config.get("user_name", "User")
    agent_names = [a['name'] for a in active_agents if a["id"] != agent["id"]]
    others = ", ".join(agent_names + [user_name]) if agent_names else user_name

    agent_prompt = config.get("agent_prompt", "")
    system = f"""You are {agent['name']}. {agent['personality']}

You are participating in a group chat. The other participants are: {others}.
{user_name} is the human user hosting this conversation.
Respond in your own voice. Do not prefix your response with your name or anyone else's name.

{agent_prompt}"""

    if is_catch_up:
        system += f"""

You are joining this conversation partway through. Here is a summary of what you missed:
--- CATCH-UP ---
{catch_up_text}
--- END CATCH-UP ---
Respond naturally as if you've been brought up to speed."""

    return system


def build_messages_payload(
    agent: dict,
    session: dict,
    user_message: str,
    prior_responses: list[dict],
    config: dict,
) -> list[dict]:
    """Build the messages array for the LLM API call."""
    user_name = config.get("user_name", "User")
    history = []

    prefix = f"[{user_name}]: "
    for msg in session.get("messages", [])[-30:]:
        if msg["role"] == "user":
            content = msg["content"]
            if not re.match(r'^\[.+\]: ', content):
                content = prefix + content
            history.append({"role": "user", "content": content})
        elif msg["role"] == "agent":
            name = msg.get("agent_name", msg.get("agent_id", "Agent"))
            if msg.get("agent_id") == agent["id"]:
                history.append({"role": "assistant", "content": msg["content"]})
            else:
                history.append({"role": "user", "content": f"[{name}]: {msg['content']}"})

    content_parts = []
    if prior_responses:
        prior_lines = [
            f"[{r['agent_name']}]: {r['content']}"
            for r in prior_responses
        ]
        content_parts.append("\n".join(prior_lines))

    if user_message.strip():
        is_dup = False
        for msg in reversed(session.get("messages", [])[-30:]):
            if msg["role"] == "user":
                is_dup = (msg["content"] == user_message)
                break
        if not is_dup:
            content_parts.append(f"[{user_name}]: {user_message}")

    if content_parts:
        history.append({"role": "user", "content": "\n\n".join(content_parts)})
    return history
