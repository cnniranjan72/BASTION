"""Kafka client auth kwargs — one place both the outbox publisher
(interceptor) and consumer (aggregator) agree on how to turn
KAFKA_SECURITY_PROTOCOL/KAFKA_SASL_* config into aiokafka constructor
kwargs. Local dev/CI's single-node Kafka is plaintext (docker-compose has
no SASL setup, matching infra/docker/docker-compose.yml) — this only adds
kwargs when a deployment target's config actually opts into SASL_SSL
(e.g. a managed Kafka that requires auth over the public internet),
never changing local/CI behavior by default.
"""

from __future__ import annotations

import ssl
from typing import Any


def kafka_client_kwargs(
    *,
    security_protocol: str,
    sasl_mechanism: str,
    sasl_username: str,
    sasl_password: str,
) -> dict[str, Any]:
    if security_protocol == "PLAINTEXT":
        return {}
    kwargs: dict[str, Any] = {
        "security_protocol": security_protocol,
        # aiokafka, unlike some other Kafka clients, does not build a
        # default SSL context itself -- SSL/SASL_SSL raises ValueError at
        # construction time without one explicitly passed.
        "ssl_context": ssl.create_default_context(),
    }
    if security_protocol == "SASL_SSL":
        kwargs["sasl_mechanism"] = sasl_mechanism
        kwargs["sasl_plain_username"] = sasl_username
        kwargs["sasl_plain_password"] = sasl_password
    return kwargs
