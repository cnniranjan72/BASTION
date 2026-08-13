"""BASTION client SDK.

Thin wrapper per ARCHITECTURE.md §2.1: `BASTION.call(tool_name, payload, context)`
replaces a direct API/DB call, routing it through the interceptor instead.
Request/response shapes come from `bastion_shared` — the SDK never redefines
the wire schema, it imports it.

Phase 0 scaffolding only — the real client lands in Phase 1 (BUILD_PLAN.md).
"""

__version__ = "0.0.0"
