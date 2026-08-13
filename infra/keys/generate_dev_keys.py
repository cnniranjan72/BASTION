"""Generates a dev/test Ed25519 keypair for signing JWT access tokens
(AUTH.md: "signed with an asymmetric key so the interceptor/aggregator
services can verify it without calling the auth service"). Idempotent —
does nothing if both files already exist. Run before starting either
service or the test suite; CI runs this as its own step (see .github/
workflows/ci.yml). Never commit the private key — this directory is
gitignored (infra/keys/*.pem).
"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEYS_DIR = Path(__file__).parent
PRIVATE_KEY_PATH = KEYS_DIR / "jwt_private.pem"
PUBLIC_KEY_PATH = KEYS_DIR / "jwt_public.pem"


def main() -> None:
    if PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists():
        print(f"keys already exist at {KEYS_DIR}, skipping")
        return

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PRIVATE_KEY_PATH.write_bytes(private_pem)
    PUBLIC_KEY_PATH.write_bytes(public_pem)
    print(f"generated dev JWT keypair at {KEYS_DIR}")


if __name__ == "__main__":
    main()
