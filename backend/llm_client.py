import json
import os
import asyncio
import time
from typing import AsyncGenerator

import httpx

from prompt_builder import build_system_prompt, build_messages_payload
from session_manager import log_message


def _parse_agent_model(agent: dict) -> tuple[str, str]:
    """Parse 'model' field (format 'provider/model') into (provider_name, model_string)."""
    raw = agent.get("model", "")
    if raw and "/" in raw:
        provider, _, model = raw.partition("/")
        return provider.strip(), model.strip()
    if not raw:
        raise RuntimeError(
            f"Agent '{agent.get('id', '?')}' has no 'model' field. "
            f"Each agent must have 'model' in 'provider/model' format."
        )
    raise RuntimeError(
        f"Agent '{agent.get('id', '?')}' has invalid model: '{raw}'. "
        f"Must be 'provider/model' format (e.g. 'mistral/mistral-small-latest')."
    )


def _get_provider_conf(provider_name: str, config: dict) -> dict:
    return config.get("providers", {}).get(provider_name, {})


def _get_api_url(name: str, pconf: dict, config: dict) -> str:
    return pconf.get("base_url") or "https://openrouter.ai/api/v1/chat/completions"


def _get_api_key(name: str, pconf: dict, config: dict) -> str:
    key = pconf.get("api_key")
    if key:
        return key
    env_var = pconf.get("api_key_env") or f"{name.upper()}_API_KEY"
    key = os.environ.get(env_var, "")
    if not key and env_var == "OPENROUTER_API_KEY":
        raise RuntimeError("OPENROUTER_API_KEY not set in environment")
    return key


def _build_headers(name: str, pconf: dict, config: dict, api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    extra = pconf.get("extra_headers")
    if extra:
        headers.update(extra)
    elif name == "openrouter":
        headers["HTTP-Referer"] = "http://localhost:3000"
        headers["X-Title"] = "AgentChat"
    return headers


async def stream_agent_response(
    agent: dict,
    session: dict,
    user_message: str,
    prior_responses: list[dict],
    config: dict,
    is_catch_up: bool = False,
    catch_up_text: str = "",
    active_agents: list[dict] | None = None,
    hop: int = 0,
    turn: int = 0,
    cycle: int = 0,
) -> AsyncGenerator[str, None]:
    """
    Stream one agent's response token by token.
    Yields SSE-formatted strings ending with agent_done.
    """
    if active_agents is None:
        active_agents = []

    if agent.get("model") == "debug/echo":
        full_text = user_message
        yield f"data: {json.dumps({'type': 'token', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text})}\n\n"
        yield f"data: {json.dumps({'type': 'agent_done', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text, 'hop': hop, 'turn': turn, 'cycle': cycle})}\n\n"
        return

    if agent.get("model") == "debug/slowburn":
        full_text = f"This is a slow response from {agent['name']}. Each character streams individually to simulate a slow model or poor connection. Watching the typing indicator fill in character by character is the whole point."
        for char in full_text:
            yield f"data: {json.dumps({'type': 'token', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': char})}\n\n"
            await asyncio.sleep(0.04)
        yield f"data: {json.dumps({'type': 'agent_done', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text, 'hop': hop, 'turn': turn, 'cycle': cycle})}\n\n"
        return

    if agent.get("model") == "debug/hoptest":
        others = [a for a in active_agents if a["id"] != agent["id"]]
        if not others:
            full_text = "I seem to be the only one here."
        else:
            mentions = " ".join(f"@{a['name']}" for a in others)
            full_text = f"Hey {mentions} \u2014 your turn."
        yield f"data: {json.dumps({'type': 'token', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text})}\n\n"
        yield f"data: {json.dumps({'type': 'agent_done', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text, 'hop': hop, 'turn': turn, 'cycle': cycle})}\n\n"
        return

    if agent.get("model") == "debug/fail":
        log_message(session["id"], f"DEBUG FAIL | agent={agent['id']} | intentional server error")
        raise RuntimeError("debug/fail triggered — intentional server error")

    try:
        system_prompt = build_system_prompt(agent, session, is_catch_up, catch_up_text, active_agents, config)
        messages = build_messages_payload(agent, session, user_message, prior_responses, config)

        provider_name, model_string = _parse_agent_model(agent)
        pconf = _get_provider_conf(provider_name, config)
        api_key = _get_api_key(provider_name, pconf, config)
        api_url = _get_api_url(provider_name, pconf, config)
        headers = _build_headers(provider_name, pconf, config, api_key)

        stream_enabled = pconf.get("stream_responses", config.get("stream_responses", True))
        payload = {
            "model": model_string,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "max_tokens": config.get("max_tokens_per_response", 1000),
            "stream": stream_enabled,
        }

        if config.get("log_requests"):
            safe_headers = {k: v if k.lower() != "authorization" else "Bearer sk-..." for k, v in headers.items()}
            log_message(session["id"],
                f"LLM REQUEST | provider={provider_name} | agent={agent['id']} | model={model_string}\n"
                f"  URL: {api_url}\n"
                f"  Headers: {json.dumps(safe_headers)}\n"
                f"  Body: {json.dumps(payload, indent=2)}"
            )

        full_text = ""
        timeout = pconf.get("timeout") or 60.0
        start_time = time.monotonic()
        first_chunk = None
        last_chunk = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            if stream_enabled:
                async with client.stream("POST", api_url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        log_message(
                            session["id"],
                            f"LLM ERROR | provider={provider_name} | agent={agent['id']} "
                            f"| status={response.status_code} | body={error_body.decode()[:300]}",
                        )
                        yield f"data: {json.dumps({'type': 'error', 'agent_id': agent['id'], 'content': f'API error {response.status_code}'})}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            if first_chunk is None:
                                first_chunk = data
                            last_chunk = data
                            delta = data["choices"][0]["delta"].get("content", "")
                            if delta:
                                full_text += delta
                                yield f"data: {json.dumps({'type': 'token', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': delta})}\n\n"
                        except (json.JSONDecodeError, KeyError, IndexError):
                            log_message(session["id"], f"LLM SSE PARSE | agent={agent['id']} — skipping malformed chunk: {data_str[:80]}")
                            continue
            else:
                resp = await client.post(api_url, headers=headers, json=payload)
                if resp.status_code != 200:
                    log_message(
                        session["id"],
                        f"LLM ERROR | provider={provider_name} | agent={agent['id']} "
                        f"| status={resp.status_code} | body={resp.text[:300]}",
                    )
                    yield f"data: {json.dumps({'type': 'error', 'agent_id': agent['id'], 'content': f'API error {resp.status_code}'})}\n\n"
                    return
                data = resp.json()
                full_text = data["choices"][0]["message"].get("content", "")
                if full_text:
                    yield f"data: {json.dumps({'type': 'token', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text})}\n\n"

    except httpx.TimeoutException:
        log_message(session["id"], f"LLM TIMEOUT | provider={provider_name} | agent={agent['id']}")
        yield f"data: {json.dumps({'type': 'error', 'agent_id': agent['id'], 'content': 'Request timed out'})}\n\n"
        return
    except Exception as e:
        log_message(session["id"], f"LLM EXCEPTION | agent={agent['id']} | {e}")
        yield f"data: {json.dumps({'type': 'error', 'agent_id': agent['id'], 'content': str(e)})}\n\n"
        return

    elapsed = time.monotonic() - start_time

    if config.get("log_responses"):
        if stream_enabled:
            summary = {
                "id": (first_chunk or {}).get("id"),
                "object": (first_chunk or {}).get("object"),
                "model": (first_chunk or {}).get("model"),
                "created": (first_chunk or {}).get("created"),
                "choices": (last_chunk or {}).get("choices", []),
                "usage": (last_chunk or {}).get("usage"),
                "content": full_text,
            }
        else:
            summary = data
        log_message(session["id"],
            f"LLM RESPONSE | provider={provider_name} | agent={agent['id']} | model={model_string} | duration={elapsed:.2f}s\n"
            f"  Response: {json.dumps(summary)}"
        )

    if not full_text:
        log_message(session["id"], f"EMPTY RESPONSE | provider={provider_name} | agent={agent['id']} | model={payload['model']}")

    user_name = config.get("user_name", "User")
    for pfx in [f"[{agent['name']}]: ", f"[{agent['id']}]: ", f"[{user_name}]: "]:
        if full_text.startswith(pfx):
            full_text = full_text[len(pfx):]
            break

    yield f"data: {json.dumps({'type': 'agent_done', 'agent_id': agent['id'], 'agent_name': agent['name'], 'agent_avatar': agent.get('avatar', ''), 'agent_color': agent.get('color', ''), 'content': full_text, 'hop': hop, 'turn': turn, 'cycle': cycle})}\n\n"
