# Same workspace-build reasoning as interceptor/Dockerfile.
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.11.30

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY interceptor/ interceptor/
COPY aggregator/ aggregator/
COPY sdk-python/ sdk-python/
COPY demo-agent/ demo-agent/

RUN uv sync --frozen --no-dev --package bastion-aggregator

FROM python:3.11-slim AS runtime

RUN useradd --create-home --uid 1000 bastion
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/shared /app/shared
COPY --from=builder /app/aggregator /app/aggregator
COPY aggregator/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh && mkdir -p /app/infra/keys && chown -R bastion:bastion /app/infra

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER bastion
EXPOSE 4002

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:4002/healthz')" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
