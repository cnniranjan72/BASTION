"""AES-256-GCM round-trip, fail-closed behavior, and key-versioning/rotation
support (ADR-022 + its "master-key rotation" follow-up) — the one
reversible-secret mechanism in this codebase, so it gets its own direct
test independent of anything that stores a key through it.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from bastion_shared.crypto import MasterKeyNotConfigured, decrypt_secret, encrypt_secret
from cryptography.exceptions import InvalidTag


def _b64_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BYOK_MASTER_KEYS", raising=False)
    monkeypatch.delenv("BYOK_MASTER_KEY_VERSION", raising=False)
    monkeypatch.setenv("BYOK_MASTER_KEY", _b64_key())


def test_round_trip_recovers_the_exact_plaintext() -> None:
    ciphertext, nonce, version = encrypt_secret("sk-super-secret-key")
    assert decrypt_secret(ciphertext, nonce, version) == "sk-super-secret-key"


def test_simple_byok_master_key_is_implicitly_version_1() -> None:
    _, _, version = encrypt_secret("sk-super-secret-key")
    assert version == 1


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    plaintext = "sk-super-secret-key"
    ciphertext, _, _ = encrypt_secret(plaintext)
    assert plaintext.encode("utf-8") not in ciphertext


def test_each_encryption_uses_a_fresh_nonce() -> None:
    ciphertext_a, nonce_a, _ = encrypt_secret("same-plaintext")
    ciphertext_b, nonce_b, _ = encrypt_secret("same-plaintext")
    assert nonce_a != nonce_b
    assert ciphertext_a != ciphertext_b


def test_tampered_ciphertext_fails_to_decrypt() -> None:
    ciphertext, nonce, version = encrypt_secret("sk-super-secret-key")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(InvalidTag):
        decrypt_secret(tampered, nonce, version)


def test_wrong_nonce_fails_to_decrypt() -> None:
    ciphertext, nonce, version = encrypt_secret("sk-super-secret-key")
    wrong_nonce = bytes([nonce[0] ^ 0xFF]) + nonce[1:]
    with pytest.raises(InvalidTag):
        decrypt_secret(ciphertext, wrong_nonce, version)


def test_missing_master_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
    with pytest.raises(MasterKeyNotConfigured):
        encrypt_secret("sk-super-secret-key")


def test_master_key_of_wrong_length_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYOK_MASTER_KEY", base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(MasterKeyNotConfigured):
        encrypt_secret("sk-super-secret-key")


class TestKeyVersioningAndRotation:
    """The actual rotation story: BYOK_MASTER_KEYS + BYOK_MASTER_KEY_VERSION,
    exercised the way infra/scripts/rotate_byok_master_key.py really uses
    them — decrypt an old row under its own recorded version, re-encrypt
    under the new current version, and the old version can then be dropped."""

    def test_versioned_config_encrypts_under_the_declared_current_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"1": _b64_key(), "2": _b64_key()}))
        monkeypatch.setenv("BYOK_MASTER_KEY_VERSION", "2")

        ciphertext, nonce, version = encrypt_secret("sk-secret")
        assert version == 2
        assert decrypt_secret(ciphertext, nonce, version) == "sk-secret"

    def test_current_version_defaults_to_the_highest_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"1": _b64_key(), "3": _b64_key()}))
        # BYOK_MASTER_KEY_VERSION deliberately not set.
        _, _, version = encrypt_secret("sk-secret")
        assert version == 3

    def test_a_row_encrypted_under_an_old_version_still_decrypts_after_rotation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key_v1 = _b64_key()
        monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"1": key_v1}))
        monkeypatch.setenv("BYOK_MASTER_KEY_VERSION", "1")
        old_ciphertext, old_nonce, old_version = encrypt_secret("sk-secret")

        # Rotate: a new version 2 key is introduced and made current, but
        # version 1's key is kept around (the real rotation script's job is
        # exactly to re-encrypt every row so this can eventually be dropped).
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"1": key_v1, "2": _b64_key()}))
        monkeypatch.setenv("BYOK_MASTER_KEY_VERSION", "2")

        assert decrypt_secret(old_ciphertext, old_nonce, old_version) == "sk-secret"
        _, _, new_version = encrypt_secret("sk-secret-2")
        assert new_version == 2

    def test_decrypting_a_version_dropped_from_the_key_map_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key_v1 = _b64_key()
        monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"1": key_v1}))
        monkeypatch.setenv("BYOK_MASTER_KEY_VERSION", "1")
        ciphertext, nonce, version = encrypt_secret("sk-secret")

        # Rotation completed and version 1's key was removed entirely — any
        # row still claiming version 1 (a rotation bug, or a row missed by
        # the script) must fail loudly, not silently misdecrypt or crash
        # with a raw KeyError.
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"2": _b64_key()}))
        monkeypatch.setenv("BYOK_MASTER_KEY_VERSION", "2")

        with pytest.raises(MasterKeyNotConfigured):
            decrypt_secret(ciphertext, nonce, version)

    def test_malformed_byok_master_keys_json_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BYOK_MASTER_KEY", raising=False)
        monkeypatch.setenv("BYOK_MASTER_KEYS", "not valid json")
        with pytest.raises(MasterKeyNotConfigured):
            encrypt_secret("sk-secret")

    def test_byok_master_keys_takes_precedence_over_the_simple_var_when_both_are_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # BYOK_MASTER_KEY is already set by the autouse fixture above.
        monkeypatch.setenv("BYOK_MASTER_KEYS", json.dumps({"5": _b64_key()}))
        monkeypatch.setenv("BYOK_MASTER_KEY_VERSION", "5")
        _, _, version = encrypt_secret("sk-secret")
        assert version == 5
