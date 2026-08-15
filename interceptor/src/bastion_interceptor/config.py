from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Config:
    port: int
    database_url: str
    # U8 (v2 upgrade): a second, deliberately non-superuser connection —
    # `database_url` above connects as `bastion`, the Postgres bootstrap
    # superuser, which unconditionally bypasses Row-Level Security no
    # matter what policies exist (docs/adr/ADR-009). Only
    # `org_scoped_connection` (db.py) ever uses this.
    app_database_url: str
    redis_url: str
    env: str
    # Phase 3 approval flow. Absolute deadline for a pending approval before
    # it's force-transitioned to timed_out (checked lazily on each
    # GET /approvals/{id} long-poll, not by a background sweeper — see
    # docs/ARCHITECTURE.md's approval-flow section). Long-poll wait is how
    # long a single GET /approvals/{id} call blocks before returning
    # "still pending" — short enough that HTTP connections/load balancers
    # don't mind, the SDK just calls again.
    approval_ttl_seconds: float
    approval_long_poll_seconds: float
    # Phase 5 auth. Paths only, not contents — read lazily on first actual
    # use (auth.py), not at import time, so a service that never needs one
    # (e.g. the aggregator never needs the private key) never fails on it.
    # infra/keys/generate_dev_keys.py creates these for local dev/CI.
    jwt_private_key_path: str
    jwt_public_key_path: str
    refresh_token_ttl_days: int
    # U3 (v2 upgrade). Only the outbox publisher process actually connects
    # to this — the interceptor's own hot path never touches Kafka
    # directly (§4.2: Kafka is distribution, not on the critical write path).
    kafka_bootstrap_servers: str
    # U5 (v2 upgrade), UPGRADE_ARCHITECTURE.md §6's reconciliation loop: how
    # often this instance re-checks its cached policy versions against
    # Postgres, self-healing any drift from a missed Redis pub/sub message.
    # Bounds the worst-case staleness window after a missed broadcast.
    policy_reconciliation_interval_seconds: float
    # U9 (v2 upgrade), UPGRADE_ARCHITECTURE.md §11: every query on this pool
    # gets this as its statement_timeout-equivalent (asyncpg's per-query
    # command_timeout) — a hung/runaway query can no longer hold a
    # connection (and, transitively, exhaust the pool) forever.
    db_query_timeout_seconds: float
    # U9, §12: payloads at or above this size (bytes, serialized JSON) get
    # offloaded to object storage instead of stored inline in Postgres.
    object_storage_payload_threshold_bytes: int
    object_storage_endpoint_url: str
    object_storage_access_key: str
    object_storage_secret_key: str
    object_storage_bucket: str
    # U12 (v2 upgrade): OTLP/HTTP endpoint traces are exported to — Jaeger's
    # all-in-one container accepts this directly, no separate Collector.
    otel_exporter_otlp_endpoint: str


def load_config() -> Config:
    return Config(
        port=int(os.environ.get("INTERCEPTOR_PORT", "4001")),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion"
        ),
        app_database_url=os.environ.get(
            "APP_DATABASE_URL", "postgresql://bastion_app:bastion_app@localhost:5442/bastion"
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6389"),
        env=os.environ.get("NODE_ENV", "development"),
        approval_ttl_seconds=float(os.environ.get("APPROVAL_TTL_SECONDS", "300")),
        approval_long_poll_seconds=float(os.environ.get("APPROVAL_LONG_POLL_SECONDS", "25")),
        jwt_private_key_path=os.environ.get(
            "JWT_PRIVATE_KEY_PATH", str(_REPO_ROOT / "infra" / "keys" / "jwt_private.pem")
        ),
        jwt_public_key_path=os.environ.get(
            "JWT_PUBLIC_KEY_PATH", str(_REPO_ROOT / "infra" / "keys" / "jwt_public.pem")
        ),
        refresh_token_ttl_days=int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "30")),
        kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        policy_reconciliation_interval_seconds=float(
            os.environ.get("POLICY_RECONCILIATION_INTERVAL_SECONDS", "30")
        ),
        db_query_timeout_seconds=float(os.environ.get("DB_QUERY_TIMEOUT_SECONDS", "30")),
        object_storage_payload_threshold_bytes=int(
            os.environ.get("OBJECT_STORAGE_PAYLOAD_THRESHOLD_BYTES", str(8 * 1024))
        ),
        object_storage_endpoint_url=os.environ.get(
            "OBJECT_STORAGE_ENDPOINT_URL", "http://localhost:9010"
        ),
        object_storage_access_key=os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "bastion"),
        object_storage_secret_key=os.environ.get("OBJECT_STORAGE_SECRET_KEY", "bastion123"),
        object_storage_bucket=os.environ.get("OBJECT_STORAGE_BUCKET", "bastion-payloads"),
        otel_exporter_otlp_endpoint=os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4319"
        ),
    )


config = load_config()
