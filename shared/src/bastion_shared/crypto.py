"""AES-256-GCM helpers for BASTION's one reversible-secret case: BYOK LLM
provider credentials (docs/adr/ADR-022). Everything else in this codebase
that stores a secret (`agents.api_key_hash`, `api_tokens.token_hash`) hashes
it one-way, because only comparison is ever needed. A user's OpenAI/
Anthropic/Gemini key is different — BASTION must hand the plaintext back to
the provider on every call, so it has to be *recoverable*, not just
verifiable. Hence encryption here instead of the hashing pattern used
everywhere else.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH_BYTES = 12


class MasterKeyNotConfigured(Exception):
    """Raised if BYOK_MASTER_KEY is unset — fails closed: no key storage
    or retrieval can happen without an explicit master key, there is no
    silent fallback to an unencrypted or hardcoded key."""


def _load_master_key() -> bytes:
    raw = os.environ.get("BYOK_MASTER_KEY")
    if not raw:
        raise MasterKeyNotConfigured(
            "BYOK_MASTER_KEY is not set — required to store or use LLM provider credentials"
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise MasterKeyNotConfigured(
            f"BYOK_MASTER_KEY must decode to exactly 32 bytes (AES-256), got {len(key)}"
        )
    return key


def encrypt_secret(plaintext: str) -> tuple[bytes, bytes]:
    """Returns (ciphertext, nonce). A fresh random nonce is generated per
    call — AES-GCM's security guarantee depends on never reusing a
    (key, nonce) pair, so this is never left to the caller to supply."""
    key = _load_master_key()
    nonce = os.urandom(_NONCE_LENGTH_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ciphertext, nonce


def decrypt_secret(ciphertext: bytes, nonce: bytes) -> str:
    key = _load_master_key()
    plaintext = AESGCM(key).decrypt(nonce, bytes(ciphertext), None)
    return plaintext.decode("utf-8")
