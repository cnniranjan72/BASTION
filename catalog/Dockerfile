# Same multi-stage shape as interceptor/Dockerfile (see its comment for why
# the whole workspace is copied into the builder stage even though this
# package has no runtime dependency on any other workspace member) — kept
# consistent rather than inventing a second build pattern for one small
# service.
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.11.30

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY interceptor/ interceptor/
COPY aggregator/ aggregator/
COPY sdk-python/ sdk-python/
COPY demo-agent/ demo-agent/
COPY catalog/ catalog/

RUN uv sync --frozen --no-dev --package bastion-catalog

FROM python:3.11-slim AS runtime

RUN useradd --create-home --uid 1000 bastion
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/catalog /app/catalog

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER bastion
EXPOSE 4003

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:4003/healthz')" || exit 1

CMD ["uvicorn", "bastion_catalog.main:app", "--host", "0.0.0.0", "--port", "4003"]
