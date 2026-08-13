"""In-memory policy evaluation. Phase 1 hardcodes a single illustrative rule
(matching ARCHITECTURE.md §2.3's example) so /intercept has real allow/block
behavior to test causal ordering against; the YAML DSL + compiler + hot
reload land in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    action: str  # "allow" | "block"
    reason: str | None = None


def evaluate(tool_name: str, args: dict[str, Any]) -> Decision:
    if tool_name == "db.query":
        query = str(args.get("query", "")).strip().upper()
        database = args.get("database")
        if query.startswith("DELETE") and database == "production":
            return Decision(
                action="block",
                reason="DELETE queries on the production database are blocked by policy",
            )
    return Decision(action="allow")
