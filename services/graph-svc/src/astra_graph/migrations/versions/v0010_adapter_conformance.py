"""Adapter conformance reports, and the promotion they gate.

S2.1.2: *"Suite output is a signed report stored in the artefact store and linked from
Platform Health"* and *"A failing conformance run blocks adapter promotion to a tenant"*.

**Why a table rather than the artefact store.** §5.2 gives object storage and content
addressing to `artefact-svc`, which does not exist — the same position provenance records
were in at S1.3.2, and the same answer: the record lives here behind a port, and moving it to
`artefact-svc` changes one adapter and nothing else. The report is stored whole, with its
content hash and signature, so relocating it later cannot alter what it says.

**Why the whole report and not a verdict.** "The adapter passed" is not evidence; the report
is. An engineer asking six months later why a Tableau adapter was allowed onto a client's
estate needs the checks that ran, what they found, and the corpus they ran against. A stored
boolean would answer none of that, and a failing report is the more important one to keep —
it is the reason a promotion was refused.

**Promotion is per tenant and per build.** A conformance report is about one adapter build:
one name, one version, one interface version, one grammar version. Promoting `tableau 1.4`
on the strength of `tableau 1.2`'s report would be promoting an adapter nobody has tested,
so the promotion carries the report it rests on and the four identifiers must match.
"""

from __future__ import annotations

import asyncpg

VERSION = 10
DESCRIPTION = "Adapter conformance reports and tenant promotion"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_REPORTS = """
CREATE TABLE IF NOT EXISTS public.adapter_conformance (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    adapter           text        NOT NULL,
    adapter_version   text        NOT NULL,
    interface_version text        NOT NULL,
    grammar_version   text,
    corpus            text        NOT NULL DEFAULT '',
    passed            boolean     NOT NULL,
    checks_passed     integer     NOT NULL DEFAULT 0,
    checks_failed     integer     NOT NULL DEFAULT 0,
    checks_skipped    integer     NOT NULL DEFAULT 0,
    content_hash      text        NOT NULL,
    signed            boolean     NOT NULL DEFAULT false,
    signature         text,
    algorithm         text,
    key_id            text,
    report            jsonb       NOT NULL,
    recorded_by       text        NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now()
)
"""

_PROMOTIONS = """
CREATE TABLE IF NOT EXISTS public.adapter_promotion (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    adapter           text        NOT NULL,
    adapter_version   text        NOT NULL,
    interface_version text        NOT NULL,
    grammar_version   text,
    report_id         text        NOT NULL REFERENCES public.adapter_conformance (id),
    state             text        NOT NULL DEFAULT 'PROMOTED'
                      CHECK (state IN ('PROMOTED', 'REVOKED')),
    reason            text        NOT NULL DEFAULT '',
    promoted_by       text        NOT NULL,
    promoted_at       timestamptz NOT NULL DEFAULT now(),
    revoked_by        text,
    revoked_at        timestamptz,
    revocation_reason text
)
"""

_INDEXES = (
    # Platform Health reads the newest report per adapter on every load.
    "CREATE INDEX IF NOT EXISTS adapter_conformance_newest_idx "
    "ON public.adapter_conformance (graph, adapter, recorded_at DESC)",
    # Promoting requires finding a *passing* report for one exact build.
    "CREATE INDEX IF NOT EXISTS adapter_conformance_build_idx "
    "ON public.adapter_conformance (graph, adapter, adapter_version, passed)",
    # One promoted build per adapter per tenant. Two would mean the platform could not say
    # which adapter it is running, and the promotion record exists precisely to answer that.
    "CREATE UNIQUE INDEX IF NOT EXISTS adapter_promotion_one_active_idx "
    "ON public.adapter_promotion (graph, adapter) WHERE state = 'PROMOTED'",
    "CREATE INDEX IF NOT EXISTS adapter_promotion_history_idx "
    "ON public.adapter_promotion (graph, adapter, promoted_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_REPORTS)
    await conn.execute(_PROMOTIONS)
    for statement in _INDEXES:
        await conn.execute(statement)
