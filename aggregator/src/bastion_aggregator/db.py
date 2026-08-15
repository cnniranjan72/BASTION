from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import asyncpg
from bastion_shared import TraceGraph

from .config import config


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            config.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
            command_timeout=config.db_query_timeout_seconds,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() was not called")
        return self._pool

    async def get_events_for_trace(self, trace_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                "SELECT * FROM events WHERE trace_id = $1 ORDER BY sequence_number ASC", trace_id
            ),
        )

    async def get_trace_summary(self, trace_id: UUID) -> asyncpg.Record | None:
        return cast(
            "asyncpg.Record | None",
            await self.pool.fetchrow("SELECT * FROM trace_summaries WHERE trace_id = $1", trace_id),
        )

    async def list_trace_summaries(
        self,
        org_id: UUID,
        *,
        limit: int = 50,
        agent_id: UUID | None = None,
        status: str | None = None,
        tool_name: str | None = None,
        policy_name: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> list[asyncpg.Record]:
        """U16 (v2 upgrade), Trace Explorer (FRONTEND_V2.md) -- API_SPEC.md
        previously flagged agent_id/status/from/to as "not implemented yet";
        tool/policy are new. tool_name searches `graph_snapshot`'s folded
        nodes (the fast-path projection already has it, no need to touch
        `events`); policy_name goes back to `events` + `policies` since
        neither `trace_summaries` nor `graph_snapshot` denormalizes a
        policy name onto a node (`shared/graph.py`'s `GraphNode.reason` is
        free text, not a policy reference)."""
        conditions = ["org_id = $1"]
        params: list[Any] = [org_id]

        def _add(condition_template: str, value: Any) -> None:
            params.append(value)
            conditions.append(condition_template.format(len(params)))

        if agent_id is not None:
            _add("agent_id = ${}", agent_id)
        if status is not None:
            _add("status = ${}", status)
        if started_after is not None:
            _add("started_at >= ${}", started_after)
        if started_before is not None:
            _add("started_at <= ${}", started_before)
        if tool_name is not None:
            _add(
                """EXISTS (
                    SELECT 1 FROM jsonb_array_elements(graph_snapshot->'nodes') node
                    WHERE node->>'tool_name' = ${}
                )""",
                tool_name,
            )
        if policy_name is not None:
            _add(
                """trace_id IN (
                    SELECT e.trace_id FROM events e
                    JOIN policies p ON p.id = (e.payload->>'policy_id')::uuid
                    WHERE e.event_type IN ('CallAllowed', 'CallBlocked', 'CallPendingApproval')
                      AND p.name = ${}
                )""",
                policy_name,
            )

        params.append(limit)
        query = f"""
            SELECT * FROM trace_summaries
            WHERE {" AND ".join(conditions)}
            ORDER BY started_at DESC
            LIMIT ${len(params)}
        """
        return cast(list[asyncpg.Record], await self.pool.fetch(query, *params))

    async def upsert_trace_summary(self, *, org_id: UUID, graph: TraceGraph) -> None:
        """A projection rebuildable from `events`, per DATA_MODEL.md: if this
        table and `events` ever disagree, `events` wins. Upsert because a
        trace can only reach a terminal state once, but re-processing the
        same notification twice (at-least-once delivery, see
        docs/ARCHITECTURE.md) must stay idempotent.

        Individual columns use native Python values (UUID/datetime objects,
        what asyncpg's non-jsonb codecs expect); graph_snapshot uses the
        JSON-mode dump (what the jsonb codec's json.dumps encoder expects) —
        same object, two different dumps for two different destinations.
        """
        await self.pool.execute(
            """
            INSERT INTO trace_summaries
                (trace_id, agent_id, org_id, status, total_cost, total_calls,
                 blocked_calls, started_at, ended_at, graph_snapshot)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (trace_id) DO UPDATE SET
                status = EXCLUDED.status,
                total_cost = EXCLUDED.total_cost,
                total_calls = EXCLUDED.total_calls,
                blocked_calls = EXCLUDED.blocked_calls,
                ended_at = EXCLUDED.ended_at,
                graph_snapshot = EXCLUDED.graph_snapshot
            """,
            graph.trace_id,
            graph.agent_id,
            org_id,
            graph.status,
            graph.total_cost,
            graph.total_calls,
            graph.blocked_calls,
            graph.started_at,
            graph.ended_at,
            graph.model_dump(mode="json"),
        )

    async def get_org_id_for_agent(self, agent_id: UUID) -> UUID | None:
        return cast(
            "UUID | None",
            await self.pool.fetchval("SELECT org_id FROM agents WHERE id = $1", agent_id),
        )

    async def get_agent_name(self, agent_id: UUID) -> str | None:
        return cast(
            "str | None",
            await self.pool.fetchval("SELECT name FROM agents WHERE id = $1", agent_id),
        )

    async def list_agents_for_org(self, org_id: UUID) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch("SELECT id, name FROM agents WHERE org_id = $1", org_id),
        )

    # -- U16 (v2 upgrade): Threat Center, Agent Health, Cost Center, Command
    # Center -- FRONTEND_V2.md's remaining "supporting surfaces". Every
    # query here is a real aggregate over `events`/`trace_summaries`/
    # `policies`/`agents`; the handful of places the spec's own mock text
    # isn't literally something this system tracks (e.g. "99.97%
    # availability") are documented in docs/adr/ADR-021, not silently
    # invented here.

    async def get_threat_summary(self, org_id: UUID, *, window_days: int) -> dict[str, Any]:
        blocked_total = await self.pool.fetchval(
            """
            SELECT COUNT(*) FROM events e JOIN agents a ON a.id = e.agent_id
            WHERE a.org_id = $1 AND e.event_type = 'CallBlocked'
              AND e.created_at >= now() - make_interval(days => $2)
            """,
            org_id,
            window_days,
        )
        top_policies = await self.pool.fetch(
            """
            SELECT p.id AS policy_id, p.name AS policy_name, COUNT(*) AS block_count
            FROM events e
            JOIN agents a ON a.id = e.agent_id
            JOIN policies p ON p.id = (e.payload->>'policy_id')::uuid
            WHERE a.org_id = $1 AND e.event_type = 'CallBlocked'
              AND e.created_at >= now() - make_interval(days => $2)
            GROUP BY p.id, p.name
            ORDER BY block_count DESC
            LIMIT 10
            """,
            org_id,
            window_days,
        )
        timeline = await self.pool.fetch(
            """
            SELECT date_trunc('day', e.created_at) AS day, COUNT(*) AS blocked_count
            FROM events e JOIN agents a ON a.id = e.agent_id
            WHERE a.org_id = $1 AND e.event_type = 'CallBlocked'
              AND e.created_at >= now() - make_interval(days => $2)
            GROUP BY day ORDER BY day
            """,
            org_id,
            window_days,
        )
        return {
            "blocked_total": blocked_total or 0,
            "top_policies": top_policies,
            "timeline": timeline,
        }

    async def get_agent_stats(self, agent_id: UUID, *, window_days: int) -> asyncpg.Record:
        row = await self.pool.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE event_type = 'CallAttempted') AS calls_total,
              COUNT(*) FILTER (WHERE event_type = 'CallBlocked') AS blocked_total,
              COUNT(*) FILTER (WHERE event_type = 'CallFailed') AS failed_total,
              COUNT(*) FILTER (WHERE event_type = 'CallPendingApproval') AS pending_approval_total,
              AVG((payload->>'latency_ms')::float)
                FILTER (WHERE event_type = 'CallCompleted') AS avg_latency_ms,
              COALESCE(SUM((payload->>'cost')::numeric)
                FILTER (WHERE event_type = 'CallCompleted'), 0) AS estimated_cost_total
            FROM events
            WHERE agent_id = $1 AND created_at >= now() - make_interval(days => $2)
            """,
            agent_id,
            window_days,
        )
        assert row is not None
        return row

    async def get_agent_top_tools(
        self, agent_id: UUID, *, window_days: int
    ) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                """
                SELECT payload->>'tool_name' AS tool_name, COUNT(*) AS count
                FROM events
                WHERE agent_id = $1 AND event_type = 'CallAttempted'
                  AND created_at >= now() - make_interval(days => $2)
                GROUP BY tool_name ORDER BY count DESC LIMIT 10
                """,
                agent_id,
                window_days,
            ),
        )

    async def get_agent_call_rate_trend(self, agent_id: UUID) -> asyncpg.Record:
        """Real baseline comparison for the "tool-call frequency increased
        N.Nx over baseline" anomaly flag (FRONTEND_V2.md) -- last 24h vs.
        this same agent's own daily average over the *preceding* 7 days
        (excluding today, so a spike doesn't dilute its own baseline)."""
        row = await self.pool.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE created_at >= now() - interval '1 day' AND event_type = 'CallAttempted'
              ) AS last_24h,
              COUNT(*) FILTER (
                WHERE created_at >= now() - interval '8 days'
                  AND created_at < now() - interval '1 day'
                  AND event_type = 'CallAttempted'
              ) AS prior_7d
            FROM events WHERE agent_id = $1
            """,
            agent_id,
        )
        assert row is not None
        return row

    async def get_cost_by_agent(self, org_id: UUID, *, window_days: int) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                """
                SELECT a.id AS agent_id, a.name AS agent_name,
                       COALESCE(SUM((e.payload->>'cost')::numeric), 0) AS cost
                FROM events e JOIN agents a ON a.id = e.agent_id
                WHERE a.org_id = $1 AND e.event_type = 'CallCompleted'
                  AND e.created_at >= now() - make_interval(days => $2)
                GROUP BY a.id, a.name
                ORDER BY cost DESC
                """,
                org_id,
                window_days,
            ),
        )

    async def get_cost_by_tool(self, org_id: UUID, *, window_days: int) -> list[asyncpg.Record]:
        """CallCompleted's payload (CallOutcomePayload) has no tool_name --
        only CallAttempted does -- so this joins back to the CallAttempted
        event of the *same span* to attribute the completed call's cost to
        the tool that was actually invoked."""
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                """
                SELECT att.payload->>'tool_name' AS tool_name,
                       COALESCE(SUM((comp.payload->>'cost')::numeric), 0) AS cost
                FROM events comp
                JOIN events att ON att.span_id = comp.span_id AND att.event_type = 'CallAttempted'
                JOIN agents a ON a.id = comp.agent_id
                WHERE a.org_id = $1 AND comp.event_type = 'CallCompleted'
                  AND comp.created_at >= now() - make_interval(days => $2)
                GROUP BY tool_name
                ORDER BY cost DESC
                LIMIT 10
                """,
                org_id,
                window_days,
            ),
        )

    async def get_estimated_savings_from_policy_enforcement(
        self, org_id: UUID, *, window_days: int
    ) -> float:
        """ADR-021: a blocked call never runs, so it never has a real
        recorded cost -- this estimates what it *would* have cost using
        this same org's own real average cost per completed call, for the
        same (agent, tool) pair, not a guessed/global number."""
        value = await self.pool.fetchval(
            """
            WITH tool_avg_cost AS (
                SELECT comp.agent_id, att.payload->>'tool_name' AS tool_name,
                       AVG((comp.payload->>'cost')::numeric) AS avg_cost
                FROM events comp
                JOIN events att ON att.span_id = comp.span_id AND att.event_type = 'CallAttempted'
                JOIN agents a ON a.id = comp.agent_id
                WHERE a.org_id = $1 AND comp.event_type = 'CallCompleted'
                  AND comp.payload->>'cost' IS NOT NULL
                  AND comp.created_at >= now() - make_interval(days => $2)
                GROUP BY comp.agent_id, tool_name
            ),
            blocked_counts AS (
                SELECT blk.agent_id, att.payload->>'tool_name' AS tool_name,
                       COUNT(*) AS blocked_count
                FROM events blk
                JOIN events att ON att.span_id = blk.span_id AND att.event_type = 'CallAttempted'
                JOIN agents a ON a.id = blk.agent_id
                WHERE a.org_id = $1 AND blk.event_type = 'CallBlocked'
                  AND blk.created_at >= now() - make_interval(days => $2)
                GROUP BY blk.agent_id, tool_name
            )
            SELECT COALESCE(SUM(bc.blocked_count * COALESCE(tac.avg_cost, 0)), 0)
            FROM blocked_counts bc
            LEFT JOIN tool_avg_cost tac
              ON tac.agent_id = bc.agent_id AND tac.tool_name = bc.tool_name
            """,
            org_id,
            window_days,
        )
        return float(value or 0)

    async def get_availability_stats(self, org_id: UUID, *, window_days: int) -> asyncpg.Record:
        row = await self.pool.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE e.event_type = 'CallCompleted') AS completed,
              COUNT(*) FILTER (WHERE e.event_type = 'CallFailed') AS failed
            FROM events e JOIN agents a ON a.id = e.agent_id
            WHERE a.org_id = $1 AND e.event_type IN ('CallCompleted', 'CallFailed')
              AND e.created_at >= now() - make_interval(days => $2)
            """,
            org_id,
            window_days,
        )
        assert row is not None
        return row

    async def get_last_incident_at(self, org_id: UUID) -> datetime | None:
        return cast(
            "datetime | None",
            await self.pool.fetchval(
                """
                SELECT MAX(e.created_at) FROM events e JOIN agents a ON a.id = e.agent_id
                WHERE a.org_id = $1 AND e.event_type = 'CallBlocked'
                """,
                org_id,
            ),
        )

    async def get_recent_activity(self, org_id: UUID, *, limit: int = 10) -> list[asyncpg.Record]:
        return cast(
            list[asyncpg.Record],
            await self.pool.fetch(
                """
                SELECT e.agent_id, a.name AS agent_name,
                       att.payload->>'tool_name' AS tool_name,
                       e.event_type AS decision, e.created_at AS at
                FROM events e
                JOIN agents a ON a.id = e.agent_id
                LEFT JOIN events att ON att.span_id = e.span_id AND att.event_type = 'CallAttempted'
                WHERE a.org_id = $1
                  AND e.event_type IN ('CallAllowed', 'CallBlocked', 'CallPendingApproval')
                ORDER BY e.created_at DESC
                LIMIT $2
                """,
                org_id,
                limit,
            ),
        )


db = Database()
