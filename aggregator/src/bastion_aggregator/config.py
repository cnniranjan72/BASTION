from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    port: int
    database_url: str
    redis_url: str
    env: str


def load_config() -> Config:
    return Config(
        port=int(os.environ.get("AGGREGATOR_PORT", "4002")),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion"
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        env=os.environ.get("NODE_ENV", "development"),
    )


config = load_config()
