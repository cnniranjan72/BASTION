"""Re-encrypts every `llm_credentials` row still on an old BYOK_MASTER_KEY
version to the current one — the actual rotation procedure ADR-022 flagged
as a documented gap and shared/src/bastion_shared/crypto.py's module
docstring now supports. Direct asyncpg against DATABASE_URL, same pattern
as infra/db/migrate.py/infra/db/seed_dev.py — no service needs to be
running.

**Before running**: set BYOK_MASTER_KEYS/BYOK_MASTER_KEY_VERSION in the
environment to include *both* the old key(s) still in use by existing rows
and the new key as the current version — decrypting an old row and
re-encrypting it both happen against whatever the environment says right
now, so the old key must still be present or those rows can't be read.

Usage:
    uv run python infra/scripts/rotate_byok_master_key.py --dry-run
    uv run python infra/scripts/rotate_byok_master_key.py

Once a run reports zero rows remaining on an old version, that version's
key can be safely dropped from BYOK_MASTER_KEYS.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg
from bastion_shared import current_key_version, decrypt_secret, encrypt_secret

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion")


async def rotate(*, dry_run: bool, database_url: str = DATABASE_URL) -> int:
    target_version = current_key_version()
    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            "SELECT id, key_ciphertext, key_nonce, key_version FROM llm_credentials "
            "WHERE key_version != $1",
            target_version,
        )
        print(f"target version: {target_version}")
        print(f"rows to rotate: {len(rows)}")
        for row in rows:
            plaintext = decrypt_secret(row["key_ciphertext"], row["key_nonce"], row["key_version"])
            if dry_run:
                print(f"  {row['id']}: v{row['key_version']} -> v{target_version} (dry run)")
                continue
            new_ciphertext, new_nonce, new_version = encrypt_secret(plaintext)
            assert new_version == target_version
            await conn.execute(
                "UPDATE llm_credentials SET key_ciphertext = $1, key_nonce = $2, key_version = $3 "
                "WHERE id = $4",
                new_ciphertext,
                new_nonce,
                new_version,
                row["id"],
            )
            print(f"  {row['id']}: v{row['key_version']} -> v{new_version}")
        return len(rows)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be rotated, write nothing"
    )
    args = parser.parse_args()
    rotated = asyncio.run(rotate(dry_run=args.dry_run))
    if args.dry_run:
        print(f"\n{rotated} row(s) would be rotated. Rerun without --dry-run to apply.")
    else:
        print(f"\n{rotated} row(s) rotated.")
