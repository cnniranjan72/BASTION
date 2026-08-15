"""POST /demo/live-run — a real LLM (via a stored BYOK credential, or local
Ollama) decides which tool to call against the same prompt-injection ticket
scenario `demo-agent` runs on a schedule with a deterministic stand-in
(docs/ARCHITECTURE.md §17). Every tool call this endpoint's LLM decides to
make still goes through the real interceptor/policy-engine path — see
docs/adr/ADR-022.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from .llm_credentials_api import LlmProvider


class LiveDemoRunRequest(BaseModel):
    provider: Literal["openai", "anthropic", "gemini", "ollama"]
    # Required for openai/anthropic/gemini, ignored (must be omitted) for
    # ollama — validated in the endpoint, not here, since it's a
    # cross-field check pydantic v2 would need a model_validator for and
    # the plain 400 the endpoint raises is clearer for this one case.
    credential_id: UUID | None = None


class LiveDemoStep(BaseModel):
    tool_name: str
    args: dict[str, Any]
    decision: Literal["allowed", "blocked", "pending_approval"]
    reason: str | None = None
    result: Any | None = None


class LiveDemoRunResponse(BaseModel):
    trace_id: UUID
    provider: LlmProvider | Literal["ollama"]
    steps: list[LiveDemoStep]
    final_text: str | None
