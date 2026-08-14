#!/bin/sh
# Same reasoning as interceptor/docker-entrypoint.sh — the aggregator only
# ever verifies tokens, so it only ever needs the public key.
set -e

if [ -n "$JWT_PUBLIC_KEY_PEM" ]; then
    mkdir -p "$(dirname "${JWT_PUBLIC_KEY_PATH:-/app/infra/keys/jwt_public.pem}")"
    printf '%s' "$JWT_PUBLIC_KEY_PEM" > "${JWT_PUBLIC_KEY_PATH:-/app/infra/keys/jwt_public.pem}"
fi

exec uvicorn bastion_aggregator.main:app --host 0.0.0.0 --port "${AGGREGATOR_PORT:-4002}"
