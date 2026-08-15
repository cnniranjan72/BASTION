"""AES-256-GCM helpers for BASTION's one reversible-secret case: BYOK LLM
provider credentials (docs/adr/ADR-022). Everything else in this codebase
that stores a secret (`agents.api_key_hash`, `api_tokens.token_hash`) hashes
it one-way, because only comparison is ever needed. A user's OpenAI/
Anthropic/Gemini key is different — BASTION must hand the plaintext back to
the provider on every call, so it has to be *recoverable*, not just
verifiable. Hence encryption here instead of the hashing pattern used
everywhere else.

Key versioning (added after ADR-022 shipped, closing that ADR's own
"master-key rotation is not implemented" gap): every encrypted row stores
which key version it was encrypted under (`llm_credentials.key_version`),
so `BYOK_MASTER_KEY` can be rotated without making existing rows
undecryptable — `infra/scripts/rotate_byok_master_key.py` is the actual
rotation procedure, re-encrypting every row under the new current version.

Two supported configurations, chosen automatically from what's set:
- **Simple** (unchanged from before rotation existed): a single
  `BYOK_MASTER_KEY` env var. Implicitly key version 1, current version 1.
  This is what local dev/CI still use — nothing about that setup changes.
- **Versioned**: `BYOK_MASTER_KEYS` — a JSON object mapping version (as a
  string) to a base64-encoded 32-byte key, e.g. `{"1": "...", "2": "..."}`
  — plus `BYOK_MASTER_KEY_VERSION` naming which version new encryptions
  should use. Every version referenced by an existing row must stay present
  in the map until that row has been rotated forward.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH_BYTES = 12


class MasterKeyNotConfigured(Exception):
    """Raised if no master key is configured, or a specific version a
    caller asked for isn't present — fails closed either way: no key
    storage or retrieval can happen without an explicit, present key, there
    is no silent fallback to an unencrypted or hardcoded key."""


def _decode_key(raw: str, *, source: str) -> bytes:
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise MasterKeyNotConfigured(
            f"{source} must decode to exactly 32 bytes (AES-256), got {len(key)}"
        )
    return key


def _load_master_keys() -> dict[int, bytes]:
    versioned_raw = os.environ.get("BYOK_MASTER_KEYS")
    if versioned_raw:
        try:
            parsed = json.loads(versioned_raw)
        except json.JSONDecodeError as exc:
            raise MasterKeyNotConfigured("BYOK_MASTER_KEYS is set but is not valid JSON") from exc
        return {
            int(version): _decode_key(raw, source=f"BYOK_MASTER_KEYS[{version!r}]")
            for version, raw in parsed.items()
        }

    simple_raw = os.environ.get("BYOK_MASTER_KEY")
    if simple_raw:
        return {1: _decode_key(simple_raw, source="BYOK_MASTER_KEY")}

    raise MasterKeyNotConfigured(
        "neither BYOK_MASTER_KEY nor BYOK_MASTER_KEYS is set — required to "
        "store or use LLM provider credentials"
    )


def _current_key_version(keys: dict[int, bytes]) -> int:
    override = os.environ.get("BYOK_MASTER_KEY_VERSION")
    if override:
        return int(override)
    return max(keys)


def current_key_version() -> int:
    """The version new encryptions use right now — public so
    infra/scripts/rotate_byok_master_key.py can report progress (rows
    still on an old version) without reaching into this module's private
    key-loading helpers."""
    return _current_key_version(_load_master_keys())


def encrypt_secret(plaintext: str) -> tuple[bytes, bytes, int]:
    """Returns (ciphertext, nonce, key_version) — always encrypts under the
    *current* key version, never an older one. A fresh random nonce is
    generated per call — AES-GCM's security guarantee depends on never
    reusing a (key, nonce) pair, so this is never left to the caller to
    supply."""
    keys = _load_master_keys()
    version = _current_key_version(keys)
    nonce = os.urandom(_NONCE_LENGTH_BYTES)
    ciphertext = AESGCM(keys[version]).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ciphertext, nonce, version


def decrypt_secret(ciphertext: bytes, nonce: bytes, key_version: int) -> str:
    keys = _load_master_keys()
    key = keys.get(key_version)
    if key is None:
        raise MasterKeyNotConfigured(
            f"key version {key_version} is not present in the configured master key(s) "
            "— it may have been dropped from BYOK_MASTER_KEYS before every row "
            "encrypted under it was rotated forward"
        )
    plaintext = AESGCM(key).decrypt(nonce, bytes(ciphertext), None)
    return plaintext.decode("utf-8")
