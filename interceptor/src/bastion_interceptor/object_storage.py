"""Object storage for large event payloads — U9 (v2 upgrade),
UPGRADE_ARCHITECTURE.md §12. Tool-call payloads (large tool responses,
documents an agent read, etc.) don't belong in Postgres rows. A payload at
or above `config.object_storage_payload_threshold_bytes` (serialized JSON
size) gets uploaded here instead, content-addressed by its SHA-256 hash
(real dedup: an identical payload — the exact scenario a retried or
repeated tool call produces — is never re-uploaded), and `events.payload`
stores a small pointer object in its place:
`{"storage": "s3", "uri": "...", "hash": "...", "size_bytes": ...}`.
Anything under the threshold stays inline in Postgres, untouched.

MinIO is the local dev/CI S3-compatible backend (`infra/docker/docker-compose.yml`),
matching UPGRADE_ARCHITECTURE.md §18's "S3-compatible" stack entry —
`aioboto3` talks to it via the plain S3 API, so the same code works
unmodified against real AWS S3 in a real deployment (just a different
`endpoint_url`).

Scope, stated explicitly rather than silently assumed complete (ADR-011):
`upload_if_large` is wired into `db.insert_event` (every write goes
through it, both the `events` row and its `outbox_events` copy — the two
always carry identical payload content, pointer or not); `resolve_payload`
is wired into `db.get_call_attempted_payload` only — not into
`get_events_for_trace` (its rows are raw `asyncpg.Record`s, `payload` one
field among several, not cleanly wrappable without a larger interface
change) or the aggregator's `fold_events_to_graph` (a separate service
entirely). A payload big enough to be offloaded flowing into one of those
not-yet-retrofitted consumers would see the pointer object instead of the
real data — a real, bounded gap, not a claim this is fully rolled out
everywhere.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import aioboto3
import structlog
from botocore.exceptions import ClientError

from .config import config

log = structlog.get_logger()

POINTER_STORAGE_KIND = "s3"


def _is_pointer(payload: dict[str, Any]) -> bool:
    return payload.get("storage") == POINTER_STORAGE_KIND and "uri" in payload


def _session() -> aioboto3.Session:
    return aioboto3.Session()


def _client_kwargs() -> dict[str, Any]:
    return {
        "endpoint_url": config.object_storage_endpoint_url,
        "aws_access_key_id": config.object_storage_access_key,
        "aws_secret_access_key": config.object_storage_secret_key,
    }


async def ensure_bucket() -> None:
    """Idempotent — called once at startup (main.py's lifespan). Creating a
    bucket that already exists is a no-op error we swallow, not a crash;
    the bucket persisting across restarts (a MinIO volume, or real S3) is
    the normal case.

    Fails open, not closed — same reasoning as upload_if_large below: this
    runs in the startup path (main.py's lifespan), which the ASGI server
    awaits *before* binding its port at all, so an unreachable/unprovisioned
    bucket must never prevent the whole service from starting. A real
    outage here degrades to "payloads over the threshold are stored inline
    instead" (upload_if_large's own except-branch already handles that per
    call), never to "the service never comes up."""
    try:
        async with _session().client("s3", **_client_kwargs()) as s3:
            try:
                await s3.head_bucket(Bucket=config.object_storage_bucket)
            except Exception:
                await s3.create_bucket(Bucket=config.object_storage_bucket)
                log.info("object storage bucket created", bucket=config.object_storage_bucket)
    except Exception:
        log.exception(
            "object storage unreachable at startup — continuing without it "
            "(large payloads will be stored inline until it's available)",
            bucket=config.object_storage_bucket,
        )


async def upload_if_large(payload: dict[str, Any]) -> dict[str, Any]:
    """Returns `payload` unchanged if it's under the threshold (the common
    case — most events are small), or a pointer object if it was uploaded.
    Never re-uploads an identical payload: the object key is the payload's
    own content hash, and a HEAD check skips the PUT entirely when it
    already exists.

    Fails open, not closed: this is called from db.insert_event, on the
    same path CLAUDE.md rule #4's "never blocks on non-essential work"
    protects — an object storage outage must degrade to "store this
    payload inline after all" (an oversized row, still a correct and
    complete event), never to "the event write itself fails." Any
    exception during the upload attempt is caught and logged; the
    original, unmodified payload is returned exactly as if it had been
    under the threshold all along."""
    serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
    size_bytes = len(serialized)
    if size_bytes < config.object_storage_payload_threshold_bytes:
        return payload

    content_hash = hashlib.sha256(serialized).hexdigest()
    key = f"payloads/{content_hash}.json"

    try:
        async with _session().client("s3", **_client_kwargs()) as s3:
            already_exists = True
            try:
                await s3.head_object(Bucket=config.object_storage_bucket, Key=key)
            except ClientError:
                already_exists = False
            if not already_exists:
                await s3.put_object(
                    Bucket=config.object_storage_bucket,
                    Key=key,
                    Body=serialized,
                    ContentType="application/json",
                )
                log.info("payload offloaded to object storage", key=key, size_bytes=size_bytes)
    except Exception:
        log.exception(
            "object storage upload failed open — storing payload inline instead",
            size_bytes=size_bytes,
        )
        return payload

    return {
        "storage": POINTER_STORAGE_KIND,
        "uri": f"s3://{config.object_storage_bucket}/{key}",
        "hash": content_hash,
        "size_bytes": size_bytes,
    }


async def resolve_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The inverse of upload_if_large: given whatever's actually stored in
    `events.payload`, returns the real payload either way — a plain
    inline payload passes through untouched, a pointer object is fetched
    and its content returned. Callers never need to know which case
    they're in."""
    if not _is_pointer(payload):
        return payload

    uri = payload["uri"]
    key = uri.removeprefix(f"s3://{config.object_storage_bucket}/")
    async with _session().client("s3", **_client_kwargs()) as s3:
        response = await s3.get_object(Bucket=config.object_storage_bucket, Key=key)
        body = await response["Body"].read()
    result: dict[str, Any] = json.loads(body)
    return result
