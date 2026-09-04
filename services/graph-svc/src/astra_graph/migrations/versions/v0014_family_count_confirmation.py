"""The family count becomes a measured value — story S3.1.3.

    "As a programme manager, I want the family count to be recorded as a calibration
    input at the end of Month 1, so that the planning assumption is replaced by a
    measured value with a date."

§14.3 / Appendix A carry the planning assumption itself (~150 shared governed models) as a
spec constant, not a per-programme row — nothing here stores it. What this migration adds is
the other half: the *measured* value a Programme Manager confirms, with who and when, so the
assumption in the spec and the fact on the programme record can finally be compared.

**Three plain columns, not a JSON blob like ``clustering_json``.** S3.1.1's clustering figures
are an open-ended stats shape (distribution, histogram) that only the Cartographer writes and
only ever needs to be read back whole. This is the opposite: a fixed, three-field record
(count, who, when) that a query might filter or sort on later — a JSON blob would hide exactly
the columns retention.py's own `programme` table already models everything else as.

**"Confirm family count" does not write the Calibration Report.** §14.3 gives that document to
the Calibration Wave (E13, not yet built) — its whole shape (class mix, coverage, parity rate,
C4 reasons, and family count alongside them) doesn't exist as a record anywhere yet, so there
is nothing to write into ahead of that epic. The programme record is the durable input the
Calibration Report will read `family_count`/`family_count_confirmed_at`/
`family_count_confirmed_by` from when E13 builds it — same relationship v0012's
`clustering_json` already has to this same future report.

No ontology change: the programme record is a platform table, not an estate-graph node.
"""

from __future__ import annotations

import asyncpg

VERSION = 14
DESCRIPTION = "Confirmed family count on the programme record"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "ALTER TABLE public.programme ADD COLUMN IF NOT EXISTS family_count integer"
    )
    await conn.execute(
        "ALTER TABLE public.programme "
        "ADD COLUMN IF NOT EXISTS family_count_confirmed_at timestamptz"
    )
    await conn.execute(
        "ALTER TABLE public.programme "
        "ADD COLUMN IF NOT EXISTS family_count_confirmed_by text"
    )
