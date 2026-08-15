from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Config:
    port: int
    database_url: str
    redis_url: str
    env: str
    # Phase 5 auth. Only the public key — the aggregator verifies JWTs, it
    # never issues them (AUTH.md: verifiable "without calling the auth
    # service"). infra/keys/generate_dev_keys.py creates this for local
    # dev/CI.
    jwt_public_key_path: str
    kafka_bootstrap_servers: str
    # U15 CI-fix follow-up: same reasoning as interceptor/config.py's fields
    # of the same name — a managed Kafka reachable over the public internet
    # needs SASL_SSL auth; local dev/CI's plaintext broker is unaffected.
    kafka_security_protocol: str
    kafka_sasl_mechanism: str
    kafka_sasl_username: str
    kafka_sasl_password: str
    # U9 (v2 upgrade), UPGRADE_ARCHITECTURE.md §11 — same reasoning as
    # interceptor/config.py's field of the same name.
    db_query_timeout_seconds: float
    # U11 (v2 upgrade), UPGRADE_ARCHITECTURE.md §13's backpressure section:
    # multiple updates to the same node within this window collapse to a
    # single delivered message (the latest state) — tunable, per the spec's
    # explicit request. 0 disables coalescing entirely (deliver
    # immediately, one message per update) — what every existing exact-
    # sequence test in this suite that predates U11 uses.
    ws_batch_window_seconds: float
    # U12 (v2 upgrade) — same reasoning as interceptor/config.py's field of
    # the same name.
    otel_exporter_otlp_endpoint: str
    # U13 CI-fix/perf follow-up — same reasoning as interceptor/config.py's
    # fields of the same name.
    otel_max_export_batch_size: int
    otel_schedule_delay_millis: int


def load_config() -> Config:
    return Config(
        port=int(os.environ.get("AGGREGATOR_PORT", "4002")),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion"
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6389"),
        env=os.environ.get("NODE_ENV", "development"),
        jwt_public_key_path=os.environ.get(
            "JWT_PUBLIC_KEY_PATH", str(_REPO_ROOT / "infra" / "keys" / "jwt_public.pem")
        ),
        kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        kafka_security_protocol=os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
        kafka_sasl_mechanism=os.environ.get("KAFKA_SASL_MECHANISM", ""),
        kafka_sasl_username=os.environ.get("KAFKA_SASL_USERNAME", ""),
        kafka_sasl_password=os.environ.get("KAFKA_SASL_PASSWORD", ""),
        db_query_timeout_seconds=float(os.environ.get("DB_QUERY_TIMEOUT_SECONDS", "30")),
        ws_batch_window_seconds=float(os.environ.get("WS_BATCH_WINDOW_SECONDS", "0.1")),
        otel_exporter_otlp_endpoint=os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4319"
        ),
        otel_max_export_batch_size=int(os.environ.get("OTEL_MAX_EXPORT_BATCH_SIZE", "2048")),
        otel_schedule_delay_millis=int(os.environ.get("OTEL_SCHEDULE_DELAY_MILLIS", "10000")),
    )


config = load_config()
