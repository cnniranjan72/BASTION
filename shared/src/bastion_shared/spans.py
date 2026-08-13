"""POST /spans/{span_id}/complete — reports the outcome of a call the SDK
executed locally after BASTION allowed it, emitting CallCompleted/CallFailed.

Not in the original API_SPEC.md: that doc describes the interceptor
"executing the real downstream call" itself (ARCHITECTURE.md §2.2 step d),
but neither DATA_MODEL.md nor API_SPEC.md's InterceptRequest gives the
interceptor anything to reach a downstream system with (no target URL/DSN,
no adapter registry). The consistent reading is that BASTION decides and
logs; the SDK executes locally and reports back so the event log stays
complete. Documented as a deliberate spec extension in API_SPEC.md and
docs/ARCHITECTURE.md §7, not a silent guess.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class CompleteSpanRequest(BaseModel):
    status: Literal["completed", "failed"]
    latency_ms: float
    cost: float | None = None
    result: Any | None = None
    error: str | None = None


class CompleteSpanResponse(BaseModel):
    span_id: UUID
    status: Literal["completed", "failed"]
