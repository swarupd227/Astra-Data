"""Adoption tracking during parallel run — story S9.2.2, continuing F9.2.

    "As a report owner, I want adoption of the released report tracked against the
    source during parallel run, so that we know users have moved before the source is
    switched off.

    Acceptance criteria:
    - Views on the Power BI report (Fabric activity) and the Tableau view (Metadata
      API) are both captured weekly; the ratio is shown on the Decommission Tracker
    - Configurable adoption threshold contributes to G4 readiness"

**Both plain Postgres platform tables, the identical footing `build_run`/`report_
deploy_run`/`promotion_run` already have — not estate-graph nodes.** No `astra.data.
adoption.*` event or §21 data-model table is named anywhere in the spec (confirmed by
direct search) for this fact; a real, invented, disclosed shape, on the same footing
`promotion_run` (S9.2.1) already established for "history, not current state."

**`adoption_config` is versioned exactly like `mender_config`** — a real, invented,
disclosed default (`adoption.DEFAULT_ADOPTION_THRESHOLD`), append-only, one row per
save, `ORDER BY version DESC LIMIT 1` for the current value. The AC's own literal
"configurable" is the reason this is a real store, not a bare module constant the way
`g3_card.DEFAULT_PARALLEL_WINDOW_WEEKS` still is.

**`adoption_snapshot` freezes the threshold it was captured against, on the row
itself.** A later change to the configured threshold must never retroactively change
whether last month's own snapshot "met" it — `threshold`/`meets_threshold` are stored,
not recomputed from whatever `adoption_config` says today. `source_views` is nullable:
a real, honest `NULL` when the source adapter's own usage capability was absent at
capture time (the real Tableau adapter's own current state, per S1.2.3's own disclosed
gap) — not a fabricated zero, the identical "an absent fact is not a zero" posture
`Capabilities.usage` already draws.
"""

from __future__ import annotations

import asyncpg

VERSION = 35
DESCRIPTION = "Adoption tracking (public.adoption_config, public.adoption_snapshot)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS public.adoption_config (
    id          text        PRIMARY KEY,
    graph       text        NOT NULL,
    version     integer     NOT NULL,
    threshold   numeric     NOT NULL,
    updated_by  text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
)
"""

_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS public.adoption_snapshot (
    id               text        PRIMARY KEY,
    graph            text        NOT NULL,
    workbook_id      text        NOT NULL,
    captured_at      timestamptz NOT NULL,
    source_views     integer,
    target_views     integer     NOT NULL,
    ratio            numeric,
    threshold        numeric     NOT NULL,
    meets_threshold  boolean,
    triggered_by     text        NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS adoption_config_version_idx "
    "ON public.adoption_config (graph, version DESC)",
    "CREATE INDEX IF NOT EXISTS adoption_snapshot_workbook_idx "
    "ON public.adoption_snapshot (graph, workbook_id, captured_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_CONFIG_DDL)
    await conn.execute(_SNAPSHOT_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
