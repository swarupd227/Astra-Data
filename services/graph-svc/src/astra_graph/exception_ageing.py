"""Exception ageing and the Mender close rate, on the Programme Board -- story S8.3.2,
continuing F8.3/E8.

    "As a programme manager, I want exception ageing and close rate on the Programme
    Board, so that residue does not accumulate unseen.

    Acceptance criteria:
    - Tile shows open exceptions by class and age band; Mender close rate (failures
      closed without an ExceptionCase / failures) with the R1 target >= 0.70"

§16.6's own Accuracy metrics table, verbatim: "Mender close rate | Failing MUs closed
without an ExceptionCase / failing MUs | >= 0.70 | Parity Dashboard". §25 repeats the
identical target under "Accuracy of repair; size of the human residue."

**"Failures closed without an ExceptionCase" cannot be built literally.** Confirmed,
again: `classification.classify_run` (S8.1.1) opens a real `ExceptionCase` for every
FAIL it reads -- its own two silent skips are for evidence it cannot read at all, never
for a failure that "didn't need a case." No live failure is ever resolved *without* one
existing. The honest reading -- and the one both §16.6's own routing ("Parity
Dashboard", the same surface `parity_dashboard.py`'s own Mender-passes trend already
lives) and §25's own "size of the human residue" wording point to -- is "closed without
ever needing a human decision at the Exception Desk": the Mender's own repair loop
closed it unassisted.

**That is a real, queryable graph fact, and not the one `ExceptionCase.decision` alone
would give.** `mender.mend_exception`'s own success-close write (confirmed by direct
read) sets only `state`/`passes_consumed` -- `closed_by`/`closed_at` are left null.
Every human-driven close sets `closed_by`/`closed_at` for real, and that includes
S6.2.1's own older `visual_redesign.close_redesign_exception` (confirmed by direct read:
it sets `closed_by`/`closed_at`/`desktop_commit_hash`, never `decision`) -- so
`decision IS NULL` alone would have wrongly counted a human-closed `VISUAL_REDESIGN`
case as Mender-closed. The real signal this module uses is `state == "CLOSED" and
closed_by is None`.

**`VISUAL_REDESIGN` is excluded from the close-rate ratio, both numerator and
denominator -- it is a real `ExceptionCase` but not a real *failure* in §16.6's own
sense.** It is opened by report composition finding an unmapped/flagged visual
(S6.1.1/S6.2.1), never by a parity diff; the Mender has no artefact to repair against
one (no calculated field, no DAX) and never attempts to. Counting it would understate
the real repair-accuracy signal the metric exists to give. It is *not* excluded from the
open-exceptions-by-class-and-age-band breakdown below -- that half of the tile is
honestly about every real kind of residue sitting in the queue, `VISUAL_REDESIGN`
included, exactly as the AC's own plain "open exceptions by class" asks.

**Age bands are a new, invented, disclosed bucketing** (`under 1 day`, `1-3 days`,
`3-7 days`, `7+ days`) -- the same "a real, defensible, disclosed number" footing
`estate.USAGE_BANDS` already set for view-count banding. `estate.Band`/`estate._band_of`
are reused verbatim here (cross-epic private reuse, the same accepted convention
`case_execution._resolve_site`'s own import already established) rather than
reinventing an identical bucketing shape. Age itself reuses `exception_desk._age_seconds`
verbatim (real wall-clock time since `created_at`), the identical fact the Exception
Desk's own queue already sorts by.

**The tile is one estate-wide aggregate, not scoped to a single programme or
workbook** -- `ExceptionCase` carries no `programme_ref`, and every other read-only
Programme Board pane this session has built (G2 cycle time, calculation class mix, rule
coverage) is estate-wide too; a per-programme scope would need a real programme-to-case
link this codebase has never had reason to build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from .estate import Band, _band_of  # cross-epic private reuse; see module docstring
from .exception_desk import _age_seconds  # cross-epic private reuse; see module docstring
from .graph.queries import NODE_INDEX_TABLE
from .lineage import hydrate

#: §16.6/§25's own literal R1 floor.
MENDER_CLOSE_RATE_TARGET = 0.70

#: A real `ExceptionCase` class, but not a real *failure* -- see this module's own
#: docstring for why it is excluded from the close-rate ratio alone.
_NOT_A_FAILURE_CLASS = "VISUAL_REDESIGN"

#: The queue's own live states (`exception_desk._QUEUE_STATES`, duplicated as a literal
#: here rather than imported, since importing a private *and* re-exporting its exact
#: identity is no clearer than restating the two real values it names).
_OPEN_STATES = frozenset({"OPEN", "BLOCKED"})

AGE_BANDS: tuple[Band, ...] = (
    Band("under_1d", "under 1 day", 0, 86400),
    Band("1_3d", "1-3 days", 86400, 3 * 86400),
    Band("3_7d", "3-7 days", 3 * 86400, 7 * 86400),
    Band("7d_plus", "7+ days", 7 * 86400, None),
)

_AGE_BAND_LABELS = {band.key: band.label for band in AGE_BANDS}


@dataclass(frozen=True, slots=True)
class MenderCloseRate:
    mender_closed: int
    total_failures: int
    rate: float | None
    target: float
    meets_target: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mender_closed": self.mender_closed, "total_failures": self.total_failures,
            "rate": self.rate, "target": self.target, "meets_target": self.meets_target,
        }


def aggregate_ageing(cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The pure aggregation over already-hydrated `ExceptionCase` properties -- the
    "pure core, graph-coupled shell" split this epic's own prior stories already
    established (`parity_dashboard.aggregate_dashboard`/`.parity_dashboard`)."""
    band_counts: dict[tuple[str, str], int] = {}
    for properties in cases.values():
        if properties.get("state") not in _OPEN_STATES:
            continue
        failure_class = str(properties.get("class") or "UNKNOWN")
        band_key = _band_of(AGE_BANDS, _age_seconds(properties.get("created_at")))
        key = (failure_class, band_key)
        band_counts[key] = band_counts.get(key, 0) + 1

    entries = [
        {
            "class": failure_class, "age_band": band_key,
            "age_band_label": _AGE_BAND_LABELS.get(band_key, "unknown age"), "count": count,
        }
        for (failure_class, band_key), count in sorted(band_counts.items())
    ]

    eligible = [
        properties for properties in cases.values()
        if properties.get("class") != _NOT_A_FAILURE_CLASS
    ]
    total_failures = len(eligible)
    mender_closed = sum(
        1 for properties in eligible
        if properties.get("state") == "CLOSED" and not properties.get("closed_by")
    )
    rate = (mender_closed / total_failures) if total_failures else None
    close_rate = MenderCloseRate(
        mender_closed=mender_closed, total_failures=total_failures, rate=rate,
        target=MENDER_CLOSE_RATE_TARGET,
        meets_target=(rate >= MENDER_CLOSE_RATE_TARGET) if rate is not None else None,
    )

    return {
        "open_by_class_and_age_band": entries,
        "age_bands": [{"key": band.key, "label": band.label} for band in AGE_BANDS],
        "total_open": sum(entry["count"] for entry in entries),
        "mender_close_rate": close_rate.as_dict(),
    }


async def _live_exception_cases(pool: asyncpg.Pool, graph_name: str) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        return await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])


async def exception_ageing(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    """The graph-coupled shell: every live `ExceptionCase` (open, blocked or closed),
    handed to `aggregate_ageing`."""
    cases = await _live_exception_cases(pool, graph_name)
    return aggregate_ageing(cases)


__all__ = ["AGE_BANDS", "MENDER_CLOSE_RATE_TARGET", "MenderCloseRate", "aggregate_ageing", "exception_ageing"]
