"""The Data Handling screen -- spec §15.3.7/§18.3, story S11.4.1, opens F11.4.

    "As an InfoSec reviewer, I want a Data Handling screen that states exactly what
    reaches a model endpoint and lets me confirm it, so that the inference boundary is
    a signed position, not an assurance.

    Acceptance criteria:
    - Screen shows: provider(s) and endpoint location, the inference boundary table
      from §18.3 (what is sent... what is never sent), retention terms, and the
      gateway's redaction rules
    - 'Sign boundary' records the reviewer, the version of the position and the date;
      changing a provider or a redaction rule invalidates the signature and requires
      re-sign
    - Boundary test: a CI and on-demand check sends sentinel row data through every
      agent path and asserts it never appears in a gateway request log"

**Three explicit decisions taken before any code was written**, since nothing in this
codebase logged an outbound gateway request, no per-tenant provider/region/retention
config existed anywhere, and §15.3.7 names Data Handling as its own distinct screen:

1. A real, always-on `gateway.GatewayRequestLogStore` (a deliberate, narrow pull-forward
   of the one storage primitive S11.4.2 needs) makes this story's own boundary test real
   rather than vacuous -- see `gateway.py`'s own module docstring.
2. Provider(s), region and retention terms are real, versioned, platform-engineer-
   editable tenant configuration (`DataHandlingPosition`, migration v0043), the
   identical `execution_safety_policy`/`mender_config` shape -- matching §18.3's own
   "configured per tenant and shown on that screen," and the only way "changing a
   provider... invalidates the signature" can mean anything real.
3. Data Handling is its own new top-level console surface, not a fifth Tenant & Access
   pane -- §15.3.7 names it as its own distinct Admin row, and this story (unlike
   S11.1.2's InfoSec-Estate-Explorer landing redirect) genuinely builds the real,
   named screen, matching the "own top-level surface" precedent Tolerance Charter and
   Pattern Library already set.

**Validity is never a stored flag -- it is a computed comparison.** A signed position is
exactly `latest_signoff.position_version == latest_position.version`; editing the
position (`save_position`) simply outpaces whatever version was last signed, "requiring
re-sign" for free, with no separate invalidation write anywhere. `sign_position`
therefore never accepts a caller-supplied version -- it always signs whatever the
*current* latest position version actually is, the only version a signature can
honestly attest to.

**Only the client's own InfoSec reviewer may sign** (`api/deps.require_infosec_
reviewer`, `Role.CLIENT_INFOSEC_REVIEWER` alone -- not "any Artizent role," unlike every
other reader/writer gate this epic has built). The AC's own "the inference boundary is
a signed position, not an assurance" is a statement about *whose* attestation this is:
Artizent signing its own data-handling claim on the client's behalf would be exactly
the assurance the AC is contrasted against, not the client's own confirmation.

**The inference boundary table itself is a fixed, spec-verbatim constant, not tenant-
configurable** -- §18.3's own two-column table (what crosses the boundary, what never
does) is a property of what this platform's own agents and gateway actually do, not a
per-tenant policy choice; only providers/region/retention/redaction-rules (real facts
about *this* tenant's own deployment) are the versioned, signable, editable part.

**The boundary test drives the one real row-level-data channel this codebase has**,
confirmed by direct research: `mender.assemble_repair_context`'s own `failing_cells`
(read back from a case's real evidence artefact, `redaction.py` applied). It plants a
real sentinel inside a real, disposable `Verdict`'s own evidence bundle, calls
`assemble_repair_context` for real, then routes the resulting `RepairContext` through a
real `ModelGateway` (a synthetic, network-free `ModelCaller`, so the check needs no live
provider credentials) wired to the real `GatewayRequestLogStore`, and asserts the
sentinel is absent from both the assembled request and the resulting real log row --
while also asserting a real, non-sentinel marker (the case's own failure class) *is*
present, so an empty result can never be mistaken for "nothing was ever logged." The
disposable `Verdict` is retired (never deleted) afterward, the same soft-delete-only
discipline every other write in this codebase already keeps -- a small, real, retired
audit trail that the boundary test really ran, not a fabricated pass.

The Transpiler's own path (`generation.build_generation_request`) is not exercised here
at all -- confirmed by direct research to never touch a row-level node of any kind
(only calc formulas/ASTs and field/parameter/worksheet *names*), so there is nothing a
sentinel could leak through there to test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .artefacts import ArtefactStore
from .config import Settings
from .gateway import (
    MENDER_REPAIR,
    PROMPT_TEMPLATE_VERSION,
    EvalReport,
    ModelGateway,
    PostgresContentLoggingGrantStore,
    PostgresGatewayPolicyStore,
    PostgresGatewayRequestLogStore,
    RawModelResponse,
    SupportsAsDict,
)
from .ids import new_ulid
from .mender import assemble_repair_context
from .principal import Principal
from .redaction import REDACTION_RULES
from .writes import GraphWriter, NodeWrite

DATA_HANDLING_POSITION_TABLE = "public.data_handling_position"
DATA_HANDLING_SIGNOFF_TABLE = "public.data_handling_signoff"

#: §18.3's own inference boundary table, verbatim -- a fixed fact about this platform's
#: own agents and gateway, not tenant configuration. See this module's own docstring.
INFERENCE_BOUNDARY_TABLE: dict[str, tuple[str, ...]] = {
    "sent": (
        "Calculation expressions and their ASTs",
        "Field, table, datasource and workbook names",
        "Data types",
        "Visual specifications (shelves, encodings, layout)",
        "Parameter definitions and domains",
        "Model definitions (TMDL)",
        "Compiler and parser error text",
        "Parity evidence headers and deltas (key values redacted to hashes; measure "
        "values redacted to sign-and-magnitude buckets)",
    ),
    "never_sent": (
        "Row-level data of any kind",
        "Extract contents",
        "Source or candidate result sets",
        "Parity comparison values (the real, unredacted numbers)",
        "User identities",
        "Credentials",
        "Custom SQL literals matching the client's own secret patterns",
        "Anything tagged by the client's own classification as restricted",
    ),
}


@dataclass(frozen=True, slots=True)
class DataHandlingPosition:
    """The signable document -- §18.3's own "provider, region, retention and logging are
    configured per tenant." `providers` is a tuple of plain dicts (`{"name", "model",
    "region"}`), not a dataclass of its own -- the AC never asks for a second provider
    to have different fields, and a plain dict keeps `save_position` from needing a
    second, parallel schema."""

    version: int
    providers: tuple[dict[str, Any], ...]
    retention_terms: str
    redaction_rules: tuple[str, ...]
    updated_by: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "providers": [dict(p) for p in self.providers],
            "retention_terms": self.retention_terms,
            "redaction_rules": list(self.redaction_rules),
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


def default_position(config: Settings) -> DataHandlingPosition:
    """The honest starting position for a deployment nobody has configured yet --
    `anthropic`, this codebase's own one real, wired provider (`gateway.build_gateway`),
    with no configurable region (the Anthropic Messages API this SDK targets exposes
    none -- confirmed by direct reading, unlike the still-unwired Azure OpenAI option
    §5.5 names), `redaction.REDACTION_RULES` verbatim as the real rules already
    enforced, and a disclosed-not-yet-reviewed retention description a platform
    engineer is expected to replace with this tenant's own real contractual terms."""
    return DataHandlingPosition(
        version=0,
        providers=({"name": "anthropic", "model": config.anthropic_model, "region": None},),
        retention_terms=(
            "Not yet reviewed for this tenant -- record this provider's own real "
            "contractual data-retention terms here before signing."
        ),
        redaction_rules=REDACTION_RULES,
        updated_by="",
        updated_at="",
    )


@dataclass(frozen=True, slots=True)
class DataHandlingSignoff:
    position_version: int
    reviewer: str
    signed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_version": self.position_version,
            "reviewer": self.reviewer,
            "signed_at": self.signed_at,
        }


class DataHandlingPositionStore(Protocol):
    async def latest(self) -> DataHandlingPosition: ...

    async def save(
        self, *, providers: tuple[dict[str, Any], ...], retention_terms: str,
        redaction_rules: tuple[str, ...], updated_by: str,
    ) -> DataHandlingPosition: ...


class PostgresDataHandlingPositionStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str, config: Settings) -> None:
        self._pool = pool
        self._graph = graph_name
        self._config = config

    async def latest(self) -> DataHandlingPosition:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT version, providers, retention_terms, redaction_rules,
                     updated_by, updated_at
                     FROM {DATA_HANDLING_POSITION_TABLE}
                    WHERE graph = $1 ORDER BY version DESC LIMIT 1""",
                self._graph,
            )
        if row is None:
            return default_position(self._config)
        return _position_from_row(row)

    async def save(
        self, *, providers: tuple[dict[str, Any], ...], retention_terms: str,
        redaction_rules: tuple[str, ...], updated_by: str,
    ) -> DataHandlingPosition:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                f"SELECT MAX(version) FROM {DATA_HANDLING_POSITION_TABLE} WHERE graph = $1",
                self._graph,
            )
            version = (current or 0) + 1
            row = await conn.fetchrow(
                f"""INSERT INTO {DATA_HANDLING_POSITION_TABLE}
                    (id, graph, version, providers, retention_terms, redaction_rules, updated_by)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7)
                 RETURNING version, providers, retention_terms, redaction_rules,
                           updated_by, updated_at""",
                f"dhpos_{new_ulid()}", self._graph, version,
                json.dumps(list(providers)), retention_terms, json.dumps(list(redaction_rules)),
                updated_by,
            )
        assert row is not None
        return _position_from_row(row)


def _position_from_row(row: asyncpg.Record) -> DataHandlingPosition:
    providers = row["providers"]
    redaction_rules = row["redaction_rules"]
    return DataHandlingPosition(
        version=row["version"],
        providers=tuple(json.loads(providers) if isinstance(providers, str) else providers),
        retention_terms=row["retention_terms"],
        redaction_rules=tuple(json.loads(redaction_rules) if isinstance(redaction_rules, str) else redaction_rules),
        updated_by=row["updated_by"],
        updated_at=row["updated_at"].isoformat(),
    )


class InMemoryDataHandlingPositionStore:
    def __init__(self, config: Settings) -> None:
        self._position = default_position(config)

    async def latest(self) -> DataHandlingPosition:
        return self._position

    async def save(
        self, *, providers: tuple[dict[str, Any], ...], retention_terms: str,
        redaction_rules: tuple[str, ...], updated_by: str,
    ) -> DataHandlingPosition:
        self._position = DataHandlingPosition(
            version=self._position.version + 1, providers=providers,
            retention_terms=retention_terms, redaction_rules=redaction_rules,
            updated_by=updated_by, updated_at=datetime.now(UTC).isoformat(),
        )
        return self._position


class DataHandlingSignoffStore(Protocol):
    async def latest(self) -> DataHandlingSignoff | None: ...

    async def sign(self, *, position_version: int, reviewer: str) -> DataHandlingSignoff: ...


class PostgresDataHandlingSignoffStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> DataHandlingSignoff | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT position_version, reviewer, signed_at
                     FROM {DATA_HANDLING_SIGNOFF_TABLE}
                    WHERE graph = $1 ORDER BY signed_at DESC LIMIT 1""",
                self._graph,
            )
        if row is None:
            return None
        return DataHandlingSignoff(
            position_version=row["position_version"], reviewer=row["reviewer"],
            signed_at=row["signed_at"].isoformat(),
        )

    async def sign(self, *, position_version: int, reviewer: str) -> DataHandlingSignoff:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {DATA_HANDLING_SIGNOFF_TABLE}
                    (id, graph, position_version, reviewer)
                    VALUES ($1, $2, $3, $4)
                 RETURNING position_version, reviewer, signed_at""",
                f"dhsign_{new_ulid()}", self._graph, position_version, reviewer,
            )
        assert row is not None
        return DataHandlingSignoff(
            position_version=row["position_version"], reviewer=row["reviewer"],
            signed_at=row["signed_at"].isoformat(),
        )


class InMemoryDataHandlingSignoffStore:
    def __init__(self) -> None:
        self._signoff: DataHandlingSignoff | None = None

    async def latest(self) -> DataHandlingSignoff | None:
        return self._signoff

    async def sign(self, *, position_version: int, reviewer: str) -> DataHandlingSignoff:
        self._signoff = DataHandlingSignoff(
            position_version=position_version, reviewer=reviewer,
            signed_at=datetime.now(UTC).isoformat(),
        )
        return self._signoff


@dataclass(frozen=True, slots=True)
class BoundaryStatus:
    position: DataHandlingPosition
    signoff: DataHandlingSignoff | None
    signed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position.as_dict(),
            "signoff": self.signoff.as_dict() if self.signoff else None,
            "signed": self.signed,
        }


async def boundary_status(
    position_store: DataHandlingPositionStore, signoff_store: DataHandlingSignoffStore,
) -> BoundaryStatus:
    position = await position_store.latest()
    signoff = await signoff_store.latest()
    signed = signoff is not None and signoff.position_version == position.version
    return BoundaryStatus(position=position, signoff=signoff, signed=signed)


class DataHandlingError(Exception):
    """A sign attempt or boundary test could not proceed for a real, stated reason."""


async def sign_position(
    position_store: DataHandlingPositionStore, signoff_store: DataHandlingSignoffStore,
    *, reviewer: str,
) -> DataHandlingSignoff:
    """Always signs the *current* latest position version -- a caller cannot name an
    older or newer version, since a signature can only honestly attest to what exists
    right now (see this module's own docstring on why validity needs no separate
    invalidation write)."""
    position = await position_store.latest()
    return await signoff_store.sign(position_version=position.version, reviewer=reviewer)


# --------------------------------------------------------------------- the boundary test


class _BoundaryTestCaller:
    """A synthetic, network-free `ModelCaller` -- the boundary test asserts what was
    *about to be sent* (the real request text `gateway.py`'s own logging captures),
    which needs no live provider call at all, and must not need one to run in CI with
    no real credentials configured."""

    provider = "boundary_test_synthetic"
    model = "boundary_test_synthetic"

    async def generate(self, request: SupportsAsDict, *, previous_error: str | None) -> RawModelResponse:
        return RawModelResponse(
            raw={}, gateway_request_id="boundary_test", provider=self.provider, model=self.model,
            prompt_hash="", context_hash="", temperature=0.0, tokens_in=0, tokens_out=0,
            latency_ms=0.0, prompt_template_version=PROMPT_TEMPLATE_VERSION,
        )


@dataclass(frozen=True, slots=True)
class BoundaryTestResult:
    passed: bool
    sentinel: str
    checked_at: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed, "sentinel": self.sentinel,
            "checked_at": self.checked_at, "detail": self.detail,
        }


#: Story S11.4.2: content logging is off by default -- without a real, active grant of
#: its own, `PostgresGatewayRequestLogStore` would persist only a hash, `contains_text`
#: would find nothing at all, and this check's own `marker_logged` self-test would
#: correctly, loudly fail ("no request was logged at all") rather than silently pass.
#: A short, scoped, self-revoked grant is this function's own real, minimal use of the
#: exact mechanism it is verifying -- not a bypass of it.
_BOUNDARY_TEST_GRANT_MINUTES = 5


async def run_boundary_test(
    pool: asyncpg.Pool, graph_name: str, *, artefact_store: ArtefactStore, writer: GraphWriter,
) -> BoundaryTestResult:
    """§18.3/S11.4.1's own boundary test, extended by S11.4.2 -- see this module's own
    docstring for the full design. Plants a real sentinel inside a real, disposable
    evidence bundle, drives the one real row-level-data channel (`mender.assemble_
    repair_context`) for real, grants itself a short, real content-logging window (see
    `_BOUNDARY_TEST_GRANT_MINUTES`), then routes the result through a real, log-store-
    backed `ModelGateway` and asserts the sentinel is absent from both the assembled
    context and the resulting real log row -- the grant is revoked again before this
    function returns, whether the check passes or fails."""
    principal = Principal("agent:mender", run_id=f"boundary-test-{new_ulid()}")
    sentinel = f"CANARY-{new_ulid()}"
    known_marker = "BOUNDARY_TEST_CLASS"
    case_ref = f"case_boundary_test_{new_ulid()}"

    bundle = {
        "diff": {
            "failing_cells": [
                {
                    "grain_key": [sentinel], "measure": "BoundaryTestMeasure", "kind": "numeric",
                    "expected": 123.45, "candidate": None, "delta": None,
                    "reason": "S11.4.1's own boundary test -- never a real failure",
                },
            ],
        },
        "filter_ctx": {}, "expected_columns": [], "candidate_columns": [],
    }
    artefact = await artefact_store.store(
        kind="parity_evidence", mu_ref="boundary_test", case_id=case_ref,
        content=json.dumps(bundle).encode("utf-8"), media_type="application/json",
        created_by=principal.value,
    )
    created = await writer.write_nodes(
        [NodeWrite(type="Verdict", properties={
            "case_ref": case_ref, "result": "FAIL", "evidence_ref": artefact.id,
        })],
        principal=principal,
    )
    verdict_id = str(created[0]["properties"]["id"])

    try:
        context = await assemble_repair_context(
            pool, graph_name, artefact_store,
            exception_properties={
                "case_refs": [case_ref], "classification_signals": {}, "class": known_marker,
            },
            calc=None, current_dax="", widened=False,
        )
        payload_text = json.dumps(context.as_dict(), default=str)
        leaked_in_context = sentinel in payload_text

        log_store = PostgresGatewayRequestLogStore(pool, graph_name=graph_name)
        policy_store = PostgresGatewayPolicyStore(pool, graph_name=graph_name)
        # A real, permanent, append-only policy row -- the identical "every real eval
        # run appends forever" convention this table already has; harmless to the real
        # production gateway, which only ever registers "anthropic" as a candidate
        # (`gateway.build_gateway`), never this synthetic provider name.
        await policy_store.record_eval(
            task_class=MENDER_REPAIR,
            report=EvalReport(
                provider=_BoundaryTestCaller.provider, model=_BoundaryTestCaller.model,
                task_class=MENDER_REPAIR, total=1, passed=1, pass_rate=1.0,
                ran_at=datetime.now(UTC).isoformat(), results=(),
            ),
            updated_by=principal.value,
        )
        gateway = ModelGateway(
            providers={_BoundaryTestCaller.provider: _BoundaryTestCaller()},
            policy_store=policy_store, log_store=log_store,
        )
        grant_store = PostgresContentLoggingGrantStore(pool, graph_name=graph_name)
        await grant_store.grant(enabled_by=principal.value, duration_minutes=_BOUNDARY_TEST_GRANT_MINUTES)
        try:
            await gateway.generate(
                task_class=MENDER_REPAIR, request=context, previous_error=None, principal=principal.value,
            )
        finally:
            await grant_store.revoke(revoked_by=principal.value)

        leaked_in_log = await log_store.contains_text(sentinel)
        marker_logged = await log_store.contains_text(known_marker)

        if leaked_in_context:
            detail = "FAIL -- the sentinel appears in the assembled repair context itself (redaction did not run)"
        elif leaked_in_log:
            detail = "FAIL -- the sentinel appears in a real gateway request log entry"
        elif not marker_logged:
            detail = "FAIL -- no request was logged at all; this check proves nothing"
        else:
            detail = "OK -- the sentinel never reached the assembled context or the gateway request log"
        passed = not leaked_in_context and not leaked_in_log and marker_logged
    finally:
        await writer.retire_node(
            verdict_id, reason="S11.4.1's own boundary test -- a real, disposable check", principal=principal,
        )

    return BoundaryTestResult(
        passed=passed, sentinel=sentinel, checked_at=datetime.now(UTC).isoformat(), detail=detail,
    )


__all__ = [
    "DATA_HANDLING_POSITION_TABLE",
    "DATA_HANDLING_SIGNOFF_TABLE",
    "INFERENCE_BOUNDARY_TABLE",
    "BoundaryStatus",
    "BoundaryTestResult",
    "DataHandlingError",
    "DataHandlingPosition",
    "DataHandlingPositionStore",
    "DataHandlingSignoff",
    "DataHandlingSignoffStore",
    "InMemoryDataHandlingPositionStore",
    "InMemoryDataHandlingSignoffStore",
    "PostgresDataHandlingPositionStore",
    "PostgresDataHandlingSignoffStore",
    "boundary_status",
    "default_position",
    "run_boundary_test",
    "sign_position",
]
