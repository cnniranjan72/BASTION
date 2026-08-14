-- U3 follow-up: outbox_events needs parent_span_id too. Found fixing a real
-- bug this phase surfaced (see docs/adr/ADR-002 and PROGRESS.md's U3 entry)
-- — the aggregator's notification handler needs the CallAttempted event's
-- own parent_span_id to build the correct node_added/edge_added broadcast,
-- and re-deriving it from a fresh Postgres fold (the old approach) is
-- exactly the bug: it reflects the *current* state, not the state at the
-- time this specific event happened.
ALTER TABLE outbox_events ADD COLUMN parent_span_id uuid;
