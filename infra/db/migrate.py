"""Tiny SQL migration runner — no ORM, no Alembic, just numbered .sql files
applied in order and tracked in schema_migrations. Deliberately simple: the
events table's append-only discipline (DATA_MODEL.md) is easiest to reason
about with raw SQL, and a migration tool is no exception.

Usage: uv run python infra/db/migrate.py
Reads DATABASE_URL from the environment (falls back to the local Docker
Compose default, matching interceptor/aggregator's config.py).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DATABASE_URL = "postgresql://bastion:bastion@localhost:5442/bastion"


async def run_migrations(database_url: str) -> list[str]:
    conn = await asyncpg.connect(database_url)
    applied: list[str] = []
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     text PRIMARY KEY,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        already_applied = {
            row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already_applied:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", path.name)
            applied.append(path.name)
            print(f"applied {path.name}")
    finally:
        await conn.close()
    return applied


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    applied = asyncio.run(run_migrations(database_url))
    if not applied:
        print("no pending migrations")


if __name__ == "__main__":
    main()
