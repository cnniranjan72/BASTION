#!/bin/sh
# On Render (and any deploy without docker-compose's shared jwtkeys volume),
# interceptor and aggregator are separate services with no shared filesystem
# — the Ed25519 keypair has to travel as env vars instead. If the PEM
# content env vars are set, write them to the file paths config.py already
# expects (JWT_PRIVATE_KEY_PATH/JWT_PUBLIC_KEY_PATH) before starting the
# app; config.py itself is untouched, so local/docker-compose dev (which
# mounts the real files and never sets these) behaves exactly as before.
set -e

if [ -n "$JWT_PRIVATE_KEY_PEM" ]; then
    mkdir -p "$(dirname "${JWT_PRIVATE_KEY_PATH:-/app/infra/keys/jwt_private.pem}")"
    printf '%s' "$JWT_PRIVATE_KEY_PEM" > "${JWT_PRIVATE_KEY_PATH:-/app/infra/keys/jwt_private.pem}"
fi

if [ -n "$JWT_PUBLIC_KEY_PEM" ]; then
    mkdir -p "$(dirname "${JWT_PUBLIC_KEY_PATH:-/app/infra/keys/jwt_public.pem}")"
    printf '%s' "$JWT_PUBLIC_KEY_PEM" > "${JWT_PUBLIC_KEY_PATH:-/app/infra/keys/jwt_public.pem}"
fi

exec uvicorn bastion_interceptor.main:app --host 0.0.0.0 --port "${INTERCEPTOR_PORT:-4001}"
