"""The programme record's clustering figures — story S3.1.1.

    "The run on the BlackRock estate completes in under 30 minutes; family count,
    distribution and the histogram of members per family are written to the programme
    record."

§21's ``programme`` row does not name a column for this — its listed columns are
``charter_version``, ``calibration_baseline_json`` and ``scope_json``, and retention.py's
own docstring already answers the pattern this asks for: "the rest of §21's ``programme``
columns... arrive with the epics that read them." This is that epic.

**One JSON column, not a run-history table.** The acceptance criterion is that the *latest*
run's figures land on the programme record — not that every run is individually queryable
later. A persisted run ledger is a real thing a future story could ask for (progress while
a run is in flight, for instance, the way ``harvest_run`` gives the Harvester's own runs);
nothing here asks for it, and building it speculatively would be exactly the kind of
ahead-of-the-story scope this project's stories are deliberately taken one at a time to
avoid.

No ontology change: the programme record is a platform table, not an estate-graph node.
"""

from __future__ import annotations

import asyncpg

VERSION = 12
DESCRIPTION = "Clustering figures on the programme record"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "ALTER TABLE public.programme ADD COLUMN IF NOT EXISTS clustering_json jsonb"
    )
