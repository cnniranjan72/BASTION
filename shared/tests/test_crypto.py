"""AES-256-GCM round-trip and fail-closed behavior (ADR-022) — the one
reversible-secret mechanism in this codebase, so it gets its own direct
test independent of anything that stores a key through it.
"""

from __future__ import annotations

import base64
import os

import pytest
from bastion_shared.crypto import MasterKeyNotConfigured, decrypt_secret, encrypt_secret
from cryptography.exceptions import InvalidTag


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYOK_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())


def test_round_trip_recovers_the_exact_plaintext() -> None:
    ciphertext, nonce = encrypt_secret("sk-super-secret-key")
    assert decrypt_secret(ciphertext, nonce) == "sk-super-secret-key"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    plaintext = "sk-super-secret-key"
    ciphertext, _ = encrypt_secret(plaintext)
    assert plaintext.encode("utf-8") not in ciphertext


def test_each_encryption_uses_a_fresh_nonce() -> None:
    ciphertext_a, nonce_a = encrypt_secret("same-plaintext")
    ciphertext_b, nonce_b = encrypt_secret("same-plaintext")
    assert nonce_a != nonce_b
    assert ciphertext_a != ciphertext_b


def test_tampered_ciphertext_fails_to_decrypt() -> None:
    ciphertext, nonce = encrypt_secret("sk-super-secret-key")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(InvalidTag):
        decrypt_secret(tampered, nonce)


def test_wrong_nonce_fails_to_decrypt() -> None:
    ciphertext, nonce = encrypt_secret("sk-super-secret-key")
    wrong_nonce = bytes([nonce[0] ^ 0xFF]) + nonce[1:]
    with pytest.raises(InvalidTag):
        decrypt_secret(ciphertext, wrong_nonce)


def test_missing_master_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
    with pytest.raises(MasterKeyNotConfigured):
        encrypt_secret("sk-super-secret-key")


def test_master_key_of_wrong_length_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYOK_MASTER_KEY", base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(MasterKeyNotConfigured):
        encrypt_secret("sk-super-secret-key")
