"""A minimal, provider-agnostic "give the LLM some tools, get back either a
tool call or a final answer" client — docs/adr/ADR-022. Deliberately thin:
direct HTTP calls via httpx, not each provider's SDK, because the only
capability needed here is single-turn tool-calling, not a full chat
application. This is what makes `POST /demo/live-run` (interceptor) and
`demo-agent`'s optional Ollama backend the *same* code path instead of two
independent integrations.

Model IDs default to a fixed constant per provider. Provider model
lineups change faster than this file will — override via
LLM_MODEL_{PROVIDER} env vars if a default stops working, rather than
treating the constant as guaranteed current.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

Provider = Literal["openai", "anthropic", "gemini", "ollama"]

_DEFAULT_MODELS: dict[Provider, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "gemini": "gemini-1.5-flash",
    "ollama": "llama3.1",
}

_REQUEST_TIMEOUT_SECONDS = 30.0
# Local CPU/GPU inference (no dedicated serving hardware, unlike a cloud
# provider's API) is legitimately much slower than a hosted endpoint,
# especially on a first call that has to load the model — found by actually
# running this against a real local Ollama instance, not assumed.
_OLLAMA_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180"))


class LLMProviderError(Exception):
    """Base class for a call that reached the provider but failed. Carries
    enough to map to a structured API error code rather than a bare 500 —
    see interceptor/main.py's /demo/live-run handler."""

    def __init__(self, provider: str, status_code: int, message: str) -> None:
        self.provider = provider
        self.status_code = status_code
        self.message = message
        super().__init__(f"{provider} call failed ({status_code}): {message}")


class LLMAuthError(LLMProviderError):
    """The provider rejected the key itself (401/403) — this is about the
    stored/pasted credential being wrong or revoked, not a transient issue."""


class LLMRateLimitedError(LLMProviderError):
    """429 — the user's own provider-side rate limit or quota, not
    anything BASTION enforces. This is the concrete mechanism behind
    "handle user-side API key limits properly": relay it legibly, don't
    swallow it into a generic failure."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema `properties`/`required`/etc.


@dataclass
class ToolCallDecision:
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class LLMDecision:
    """Exactly one of these is set. A tool call means the agent loop should
    invoke that tool (through the real interceptor) and continue; final_text
    means the model is done and has nothing further to call."""

    tool_call: ToolCallDecision | None
    final_text: str | None


def _model_for(provider: Provider, model: str | None) -> str:
    if model:
        return model
    override = os.environ.get(f"LLM_MODEL_{provider.upper()}")
    return override or _DEFAULT_MODELS[provider]


async def call_llm_with_tools(
    *,
    provider: Provider,
    api_key: str | None,
    messages: list[dict[str, str]],
    tools: list[ToolSpec],
    model: str | None = None,
) -> LLMDecision:
    """`messages` is `[{"role": "system"|"user"|"assistant", "content": str}]`.
    `api_key` is None only for `provider="ollama"` (local, no auth)."""
    resolved_model = _model_for(provider, model)
    timeout = _OLLAMA_REQUEST_TIMEOUT_SECONDS if provider == "ollama" else _REQUEST_TIMEOUT_SECONDS
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider == "openai":
                return await _call_openai(client, api_key, resolved_model, messages, tools)
            if provider == "anthropic":
                return await _call_anthropic(client, api_key, resolved_model, messages, tools)
            if provider == "gemini":
                return await _call_gemini(client, api_key, resolved_model, messages, tools)
            return await _call_ollama(client, resolved_model, messages, tools)
    except httpx.TimeoutException as exc:
        raise LLMProviderError(provider, 504, f"request timed out after {timeout:.0f}s") from exc


def _raise_for_provider_status(provider: str, response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise LLMAuthError(provider, response.status_code, response.text[:500])
    if response.status_code == 429:
        raise LLMRateLimitedError(provider, response.status_code, response.text[:500])
    if response.status_code >= 400:
        raise LLMProviderError(provider, response.status_code, response.text[:500])


async def _call_openai(
    client: httpx.AsyncClient,
    api_key: str | None,
    model: str,
    messages: list[dict[str, str]],
    tools: list[ToolSpec],
) -> LLMDecision:
    body = {
        "model": model,
        "messages": messages,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ],
        "tool_choice": "auto",
    }
    response = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
    )
    _raise_for_provider_status("openai", response)
    message = response.json()["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        import json as _json

        call = tool_calls[0]["function"]
        return LLMDecision(
            tool_call=ToolCallDecision(
                tool_name=call["name"], arguments=_json.loads(call["arguments"] or "{}")
            ),
            final_text=None,
        )
    return LLMDecision(tool_call=None, final_text=message.get("content") or "")


async def _call_anthropic(
    client: httpx.AsyncClient,
    api_key: str | None,
    model: str,
    messages: list[dict[str, str]],
    tools: list[ToolSpec],
) -> LLMDecision:
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 1024,
        "messages": turns,
        "tools": [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ],
    }
    if system:
        body["system"] = system
    response = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
    )
    _raise_for_provider_status("anthropic", response)
    content = response.json()["content"]
    for block in content:
        if block["type"] == "tool_use":
            return LLMDecision(
                tool_call=ToolCallDecision(tool_name=block["name"], arguments=block["input"]),
                final_text=None,
            )
    text = "".join(b["text"] for b in content if b["type"] == "text")
    return LLMDecision(tool_call=None, final_text=text)


async def _call_gemini(
    client: httpx.AsyncClient,
    api_key: str | None,
    model: str,
    messages: list[dict[str, str]],
    tools: list[ToolSpec],
) -> LLMDecision:
    system_instruction = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
        if m["role"] != "system"
    ]
    body: dict[str, Any] = {
        "contents": contents,
        "tools": [
            {
                "function_declarations": [
                    {"name": t.name, "description": t.description, "parameters": t.parameters}
                    for t in tools
                ]
            }
        ],
    }
    if system_instruction:
        body["system_instruction"] = {"parts": [{"text": system_instruction}]}
    response = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key or ""},
        json=body,
    )
    _raise_for_provider_status("gemini", response)
    candidate = response.json()["candidates"][0]
    parts = candidate["content"]["parts"]
    for part in parts:
        if "functionCall" in part:
            call = part["functionCall"]
            return LLMDecision(
                tool_call=ToolCallDecision(tool_name=call["name"], arguments=call.get("args", {})),
                final_text=None,
            )
    text = "".join(p.get("text", "") for p in parts)
    return LLMDecision(tool_call=None, final_text=text)


async def _call_ollama(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, str]],
    tools: list[ToolSpec],
) -> LLMDecision:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    body = {
        "model": model,
        "messages": messages,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ],
        "stream": False,
    }
    try:
        response = await client.post(f"{base_url}/api/chat", json=body)
    except httpx.ConnectError as exc:
        # Ollama is only ever reachable if it's running on the *same
        # machine* as whatever process is making this call — a hosted
        # deployment's OLLAMA_BASE_URL still defaults to its own
        # localhost, which is never where a browser visitor's Ollama
        # install actually is. Spelled out explicitly here since "could
        # not reach localhost" reads as a local-dev message even when
        # this call is happening on a remote server.
        raise LLMProviderError(
            "ollama",
            503,
            f"could not reach Ollama at {base_url} from this server — Ollama must be running on "
            "the same machine as the BASTION backend making this call (`ollama serve`), which is "
            "never true for a hosted deployment reached from your own browser",
        ) from exc
    _raise_for_provider_status("ollama", response)
    message = response.json()["message"]
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        call = tool_calls[0]["function"]
        arguments = call["arguments"]
        if isinstance(arguments, str):
            import json as _json

            arguments = _json.loads(arguments or "{}")
        return LLMDecision(
            tool_call=ToolCallDecision(tool_name=call["name"], arguments=arguments),
            final_text=None,
        )
    return LLMDecision(tool_call=None, final_text=message.get("content") or "")
