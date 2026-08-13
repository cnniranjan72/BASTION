"""Server -> client messages on WS /live/{agent_id} — API_SPEC.md §Realtime API."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LiveNode(BaseModel):
    span_id: UUID
    tool_name: str
    status: Literal["pending"]


class NodeAddedMessage(BaseModel):
    type: Literal["node_added"] = "node_added"
    node: LiveNode


class NodeUpdatedMessage(BaseModel):
    type: Literal["node_updated"] = "node_updated"
    span_id: UUID
    status: Literal["allowed", "blocked", "pending_approval", "completed", "failed"]
    latency_ms: float | None = None
    cost: float | None = None


class EdgeAddedMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["edge_added"] = "edge_added"
    from_: UUID = Field(alias="from")
    to: UUID


LiveMessage = Annotated[
    NodeAddedMessage | NodeUpdatedMessage | EdgeAddedMessage,
    Field(discriminator="type"),
]
