"""The promotion pipeline record — story S9.2.1, opening F9.2.

    "As a platform engineer, I want the Steward to promote ACCEPTED MUs per train
    through the Fabric deployment pipeline, so that release is a pipeline stage with
    evidence, not a manual copy.

    Acceptance criteria:
    - Promotion dev -> test -> prod via Fabric deployment pipelines with the client's
      approval rules; MA-08 (L3) to test, MA-09 (L2, explicit PM approval) to production
    - Release Board shows per train: MUs by pipeline stage, blockers, and the release
      evidence bundle
    - Parallel-run window (default 4 weeks) starts at production deployment and is
      visible per MU and per site"

**A plain Postgres platform table, the identical footing `build_run`/`report_deploy_
run`/`commercial_ledger` already have — not an estate-graph node.** A promotion attempt
is history, the same kind of fact those three tables already record; `release.py`'s own
docstring explains why this story does not touch `GateDecision.gate` (no G-numbered
release gate exists anywhere in the spec's own four-gate table, §13.1).

**The row itself IS the release evidence bundle the AC asks for — no second, duplicated
snapshot via `ArtefactStore`.** `build_run`/`report_deploy_run` already establish this
convention: `steps` (a real, ordered step log, including a failed attempt's own detail)
plus the real `workspace`/git refs/approver on the one row a promotion attempt writes is
already a complete, queryable bundle, read directly by the Release Board rather than
re-serialised into a second artefact store the way `g3_card`'s own rendered-card
snapshot needed to be (a card is a *view*; this row already *is* the record).

**`UNIQUE (graph, workbook_id, to_stage)` makes each workbook's own promotion to a given
stage a one-time real fact, not a growing log of retries under one identity.** A retry
after a FAILED attempt still needs its own row (evidence of what actually happened), so
this is `ON CONFLICT ... DO UPDATE` territory in `release.py`, not a hard INSERT-once
guard the way `commercial_ledger`'s own double-invoice guard is — the constraint exists
so "this workbook's current test-stage promotion" and "this workbook's current
prod-stage promotion" are each a single, current row a query can join back onto
directly, the latest attempt always overwriting the one before it for that same
`(workbook, stage)` pair.
"""

from __future__ import annotations

import asyncpg

VERSION = 34
DESCRIPTION = "The promotion pipeline record (public.promotion_run)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_PROMOTION_DDL = """
CREATE TABLE IF NOT EXISTS public.promotion_run (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    workbook_id       text        NOT NULL,
    to_stage          text        NOT NULL,
    workspace         text        NOT NULL,
    state             text        NOT NULL,
    steps             jsonb       NOT NULL,
    model_git_ref     text,
    report_deploy_id  text,
    approved_by       text,
    approver_role     text,
    rationale         text,
    triggered_by      text        NOT NULL,
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz NOT NULL,
    UNIQUE (graph, workbook_id, to_stage)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS promotion_run_stage_idx "
    "ON public.promotion_run (graph, to_stage, state)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_PROMOTION_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
