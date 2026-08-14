# Multi-stage build for the interceptor service (BUILD_PLAN.md Phase 9).
#
# The whole repo is copied into the builder stage rather than just
# interceptor/ + shared/, because this is a uv workspace: uv.lock and the
# root pyproject.toml describe every member together, and `uv sync`
# validates that every declared workspace member directory exists even when
# --package limits what actually gets installed. Only the interceptor's own
# source and the resulting venv make it into the runtime stage, so this
# doesn't bloat the final image. For a leaner build, the next step would be
# vendoring a pre-built bastion-shared wheel instead of the workspace source
# tree — skipped here to keep the build reproducible from source with one
# `uv sync`, the same way dev/CI build it.
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.11.30

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY interceptor/ interceptor/
COPY aggregator/ aggregator/
COPY sdk-python/ sdk-python/
COPY demo-agent/ demo-agent/

RUN uv sync --frozen --no-dev --package bastion-interceptor

FROM python:3.11-slim AS runtime

RUN useradd --create-home --uid 1000 bastion
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/shared /app/shared
COPY --from=builder /app/interceptor /app/interceptor
# infra/db is included so this image can double as the one-shot migration
# runner in docker-compose/k8s (`python infra/db/migrate.py`) — it only
# needs asyncpg, which the interceptor already depends on, so a separate
# image just for migrations isn't worth the extra build. Same reasoning for
# infra/keys/generate_dev_keys.py as a one-shot key-generation step — the
# .pem files themselves are never copied in (.dockerignore).
COPY infra/db/ infra/db/
COPY infra/keys/generate_dev_keys.py infra/keys/generate_dev_keys.py
COPY interceptor/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
# infra/keys is where the jwtkeys named volume gets mounted (docker-compose)
# — chown it to the non-root user *before* that mount happens, so Docker's
# first-mount copy of this directory's permissions into the new volume
# lets `bastion` actually write the generated keys there. Also where
# docker-entrypoint.sh writes JWT_*_KEY_PEM env vars out to files, when a
# deploy target (Render) has no shared volume at all.
RUN chown -R bastion:bastion /app/infra/keys

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER bastion
EXPOSE 4001

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:4001/healthz')" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
