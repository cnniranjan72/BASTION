"""Policy DSL compiler + evaluator (ARCHITECTURE.md §2.3) and the in-memory
cache that lets /intercept avoid a DB round-trip per call. Replaces Phase 1's
hardcoded rule.

Condition expressions (e.g. "amount > 50") are parsed with `ast.parse` and
walked by a hand-rolled interpreter over a small allow-listed set of node
types — never `eval()`. A policy engine that could be tricked into running
arbitrary Python via a condition string would be a code-injection
vulnerability in a security product; the allow-list makes that structurally
impossible rather than relying on sanitization.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from bastion_shared import PolicyDefinition
from pydantic import TypeAdapter


class PolicyConditionError(ValueError):
    """Raised at compile time (POST /policies, or cache load) for a
    condition expression outside the safe subset — fails fast, before it
    ever reaches the hot path."""


_COMPARATORS: dict[type[ast.cmpop], Any] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.List,
    ast.Tuple,
    *_COMPARATORS.keys(),
)


def _validate(node: ast.AST) -> None:
    if not isinstance(node, _ALLOWED_NODES):
        raise PolicyConditionError(f"disallowed expression element: {type(node).__name__}")
    for child in ast.iter_child_nodes(node):
        _validate(child)


def compile_condition(expr: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PolicyConditionError(f"invalid condition syntax: {expr!r}") from exc
    _validate(tree)
    return tree


def _eval(node: ast.AST, args: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, args)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return args.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, args) for e in node.elts]
    if isinstance(node, ast.BoolOp):
        values = (_eval(v, args) for v in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp):
        return not _eval(node.operand, args)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, args)
        for op, comparator_node in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator_node, args)
            if not _COMPARATORS[type(op)](left, right):
                return False
            left = right
        return True
    raise PolicyConditionError(f"unsupported expression element: {type(node).__name__}")


def evaluate_condition(compiled: ast.Expression, args: dict[str, Any]) -> bool:
    return bool(_eval(compiled, args))


@dataclass(frozen=True)
class CompiledRule:
    tool: str
    action: str
    pattern: re.Pattern[str] | None
    database: str | None
    condition: ast.Expression | None


@dataclass(frozen=True)
class CompiledPolicy:
    policy_id: UUID
    policy_set_id: UUID
    rules: list[CompiledRule]


def compile_policy(
    policy_id: UUID, policy_set_id: UUID, definition: PolicyDefinition
) -> CompiledPolicy:
    rules = [
        CompiledRule(
            tool=rule.match.tool,
            action=rule.action,
            pattern=re.compile(rule.match.pattern) if rule.match.pattern else None,
            database=rule.match.database,
            condition=compile_condition(rule.condition) if rule.condition else None,
        )
        for rule in definition
    ]
    return CompiledPolicy(policy_id=policy_id, policy_set_id=policy_set_id, rules=rules)


_definition_adapter = TypeAdapter(PolicyDefinition)


def compile_policy_from_raw(
    policy_id: UUID, policy_set_id: UUID, raw_definition: list[dict[str, Any]]
) -> CompiledPolicy:
    """definition comes back from Postgres as plain dicts (jsonb codec) —
    validate into PolicyRule objects before compiling."""
    definition = _definition_adapter.validate_python(raw_definition)
    return compile_policy(policy_id, policy_set_id, definition)


@dataclass(frozen=True)
class Decision:
    action: str  # "allow" | "block" | "require_approval"
    reason: str | None = None


def evaluate(compiled: CompiledPolicy | None, tool_name: str, args: dict[str, Any]) -> Decision:
    # No policy assigned to this agent: safe default, matches the trailing
    # `match: "*" -> allow` convention every example policy in
    # ARCHITECTURE.md §2.3 ends with.
    if compiled is None:
        return Decision(action="allow")

    for rule in compiled.rules:
        if rule.tool != "*" and rule.tool != tool_name:
            continue
        if rule.pattern is not None and not rule.pattern.search(str(args.get("query", ""))):
            continue
        if rule.database is not None and args.get("database") != rule.database:
            continue
        if rule.condition is not None and not evaluate_condition(rule.condition, args):
            continue

        if rule.action == "allow":
            return Decision(action="allow")
        return Decision(action=rule.action, reason=f"blocked by policy rule for tool '{rule.tool}'")

    return Decision(action="allow")


class PolicyCache:
    """In-memory, keyed by policy_set_id (not policy_id) so a lookup by an
    agent's default_policy_set_id always resolves to whichever version is
    currently cached as active — updated by activate_policy + the Redis
    pub/sub hot-reload listener (redis_bus.py), never by a restart."""

    def __init__(self) -> None:
        self._by_set: dict[UUID, CompiledPolicy] = {}

    def get(self, policy_set_id: UUID | None) -> CompiledPolicy | None:
        if policy_set_id is None:
            return None
        return self._by_set.get(policy_set_id)

    def put(self, compiled: CompiledPolicy) -> None:
        self._by_set[compiled.policy_set_id] = compiled

    def evict(self, policy_set_id: UUID) -> None:
        self._by_set.pop(policy_set_id, None)

    def cached_set_ids(self) -> set[UUID]:
        """U5 (v2 upgrade): lets the reconciliation loop (policy_reconciler.py)
        find entries that are cached here but no longer active in Postgres —
        a plain `get` per known-active set can't detect that direction of
        drift on its own, since it never even looks at what's cached for a
        set Postgres no longer considers active."""
        return set(self._by_set.keys())

    def __len__(self) -> int:
        return len(self._by_set)


policy_cache = PolicyCache()
