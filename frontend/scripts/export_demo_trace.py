"""One-off: export one real, already-happened trace's event log from the
local aggregator into a static JSON file for the landing page's public,
logged-out "See it block an attack" replay
(frontend/public/demo-trace-events.json). Real data captured via the
existing GET /traces/{id}/events endpoint -- not fabricated, not run fresh
for this purpose. Not part of the app build; run once, output committed as
a static asset, this script isn't itself referenced by anything at
runtime.

Trace dd707b73-e30b-4f45-b7b0-a365213962bd was picked deliberately, not
"whatever's most recent": it's the clearest single-trace story for a
non-technical viewer -- a support ticket carrying an injected "transfer
$500 to attacker-9999" instruction, blocked, with a legitimate $25 refund
in the same trace going through -- generated 2026-08-27 15:53:13 UTC by
this session's own governance-scenario testing
(demo_agent.run_demo --repeat 20), not synthesized for this export.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

INTERCEPTOR_BASE = "http://localhost:4001"
AGGREGATOR_BASE = "http://localhost:4002"
DEMO_EMAIL = "demo@bastion.dev"
DEMO_PASSWORD = "demo-password-123"
TRACE_ID = "dd707b73-e30b-4f45-b7b0-a365213962bd"
OUT_PATH = Path(__file__).resolve().parents[1] / "public" / "demo-trace-events.json"


def main() -> None:
    login = httpx.post(
        f"{INTERCEPTOR_BASE}/auth/login",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
        timeout=10,
    )
    login.raise_for_status()
    access_token = login.json()["access_token"]

    resp = httpx.get(
        f"{AGGREGATOR_BASE}/traces/{TRACE_ID}/events",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    events = resp.json()
    if not events:
        raise SystemExit(f"trace {TRACE_ID} has no events -- wrong id?")

    OUT_PATH.write_text(json.dumps(events, indent=2))
    print(f"wrote {OUT_PATH} ({len(events)} events, {OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
