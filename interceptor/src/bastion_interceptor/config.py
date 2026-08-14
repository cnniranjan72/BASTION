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


def load_config() -> Config:
    return Config(
        port=int(os.environ.get("INTERCEPTOR_PORT", "4001")),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion"
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
    )


config = load_config()
