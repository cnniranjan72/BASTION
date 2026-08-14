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
    )


config = load_config()
