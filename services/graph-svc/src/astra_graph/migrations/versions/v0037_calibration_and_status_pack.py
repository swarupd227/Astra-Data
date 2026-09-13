"""Calibration baseline and Status Pack storage -- story S10.2.1, opening F10.2.

    "As a programme manager, I want the Programme Board, Wave Board, Calibration Report
    and Status Pack, so that the programme's state is one screen and the status pack
    writes itself.

    Acceptance criteria:
    - Status Pack: generated weekly as an editable narrative with the numbers and
      charts of the Board, exportable to PDF and PPTX; edits are stored with the version
    - Calibration Report screen per F13.2"

**Two plain Postgres platform tables, the identical footing `promotion_run`/`adoption_
snapshot`/`decommission_confirmation` already have -- neither is an estate-graph node.**
No `astra.data.calibration.*` or `astra.data.status_pack.*` event or §21 data-model table
names either fact anywhere in the spec; both are real, invented, disclosed shapes this
story builds for the first time. See `calibration_wave.py`'s and `status_pack.py`'s own
module docstrings for why "Calibration Report" here means F13.1/S13.1.2's Calibration
Wave report (§15.3.1's own row content matches that story, not F13.2's confidence-
calibration screen the backlog AC's own cross-reference names).

**`calibration_baseline` is append-only, versioned -- unlike `decommission_confirmation`'s
own overwrite-per-workbook shape.** Signing a Calibration Report freezes a point-in-time
snapshot the Programme Board thereafter measures against (S13.1.2: "writes the calibrated
baseline that the Programme Board thereafter measures against"); a later signing must not
erase an earlier one's own historical record, so each sign call inserts a new row rather
than updating one in place -- the identical "a growing history, not a current-state
overwrite" shape `mender_config`/`adoption_config` already set for their own versioned
stores, applied here because the AC's own word is "the baseline," a fact worth keeping a
trail of, not "has this been done" (a current-state question `decommission_confirmation`'s
own overwrite shape correctly answers).

**`status_pack` is also versioned, one row per edit -- "edits are stored with the
version" is the AC's own literal words.** A `(graph, week_of, version)` row per edit
means every past edit stays inspectable, the same reasoning `calibration_baseline`
above just gave, rather than an in-place update that would lose what a narrative used to
say.

**No ontology changes accompany this migration** -- confirmed by `tools/migration_
check.py`: neither table's own data is derived from or written onto any ontology node or
edge.
"""

from __future__ import annotations

import asyncpg

VERSION = 37
DESCRIPTION = "Calibration baseline and Status Pack storage (S10.2.1)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_CALIBRATION_BASELINE_DDL = """
CREATE TABLE IF NOT EXISTS public.calibration_baseline (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    version           integer     NOT NULL,
    report            jsonb       NOT NULL,
    signed_by         text        NOT NULL,
    countersigned_by  text        NOT NULL,
    signed_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_STATUS_PACK_DDL = """
CREATE TABLE IF NOT EXISTS public.status_pack (
    id             text        PRIMARY KEY,
    graph          text        NOT NULL,
    week_of        date        NOT NULL,
    version        integer     NOT NULL,
    narrative      text        NOT NULL,
    report         jsonb       NOT NULL,
    generated_by   text        NOT NULL,
    generated_at   timestamptz NOT NULL DEFAULT now(),
    published_at   timestamptz,
    UNIQUE (graph, week_of, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS calibration_baseline_graph_idx "
    "ON public.calibration_baseline (graph, version DESC)",
    "CREATE INDEX IF NOT EXISTS status_pack_graph_week_idx "
    "ON public.status_pack (graph, week_of DESC, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_CALIBRATION_BASELINE_DDL)
    await conn.execute(_STATUS_PACK_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
