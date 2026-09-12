"""Unit prices and the commercial ledger — story S9.1.2, closing F9.1/E9.

    "As a programme manager, I want G3 acceptance to trigger the invoicing event under
    the fixed-price contract, so that commercial recognition is a platform event, not a
    spreadsheet.

    Acceptance criteria:
    - mu.accepted event with MU, tier and unit price is emitted and exported to the
      programme's commercial ledger; the Programme Board shows accepted units by tier
      against plan"

**Both are plain Postgres platform tables, the identical footing `mender_config`/
`g2_question`/`scope_decision` already have — not estate-graph nodes.** Confirmed by
direct grep: `ScopeDecision` (the real source of a workbook's own tier) is itself a
plain Postgres table, not an AGE node; a unit price and a ledger row are the same kind
of platform-side fact.

**`unit_price_schedule` is a single current-value row per tier, not versioned like
`mender_config`.** A fixed-price contract's own price is a real business fact worth
correcting over time, but this story's own AC asks only that a real, current unit price
be carried on the event — not that every past price be retained for audit. A disclosed
simplification, not an oversight; a future story can widen this the identical additive
way nothing has ever needed to widen `g2_question`.

**`commercial_ledger` carries a `UNIQUE (graph, workbook_id)` constraint.** "G3
acceptance triggers invoicing" only means something if accepting the same MU twice
cannot invoice it twice — `record_acceptance`'s own `ON CONFLICT (graph, workbook_id) DO
NOTHING` (see `invoicing.py`) relies on this constraint to make a re-approval of an
already-accepted workbook a real no-op, not a double-billed line.
"""

from __future__ import annotations

import asyncpg

VERSION = 33
DESCRIPTION = "Unit prices and the commercial ledger (public.unit_price_schedule, public.commercial_ledger)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_PRICE_DDL = """
CREATE TABLE IF NOT EXISTS public.unit_price_schedule (
    graph        text        NOT NULL,
    tier         text        NOT NULL,
    unit_price   numeric     NOT NULL,
    updated_by   text        NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (graph, tier)
)
"""

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.commercial_ledger (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    workbook_id       text        NOT NULL,
    tier              text        NOT NULL,
    unit_price        numeric     NOT NULL,
    gate_decision_id  text        NOT NULL,
    recorded_by       text        NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, workbook_id)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS commercial_ledger_tier_idx "
    "ON public.commercial_ledger (graph, tier)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_PRICE_DDL)
    await conn.execute(_LEDGER_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
