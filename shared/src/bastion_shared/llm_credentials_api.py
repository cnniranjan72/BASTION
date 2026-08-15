"""GET/POST /llm-keys, DELETE /llm-keys/{id} — BYOK LLM provider credentials.
docs/adr/ADR-022: a user-supplied OpenAI/Anthropic/Gemini API key, stored
encrypted (reversibly, unlike agents.api_key_hash/api_tokens.token_hash)
so BASTION can present the plaintext back to the provider on each call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

LlmProvider = Literal["openai", "anthropic", "gemini"]


class CreateLlmCredentialRequest(BaseModel):
    provider: LlmProvider
    label: str = Field(min_length=1, max_length=100, description="e.g. 'personal OpenAI key'.")
    api_key: str = Field(min_length=1, description="Never stored or returned in plaintext.")


class LlmCredentialResponse(BaseModel):
    id: UUID
    provider: LlmProvider
    label: str
    key_last4: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
