"""Reproducing the exact context an agent saw.

S1.3.2's two acceptance criteria: from a ProvenanceRecord the console can re-materialise
the context at the recorded graph version and show that the hash matches, and graph
versions are addressable by event offset with retention of the programme lifetime plus
twelve months.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from astra_graph.context import ContextAssembler, ContractName
from astra_graph.provenance import (
    AgentMode,
    ContextVerifier,
    InMemoryProvenanceStore,
    VerificationOutcome,
    new_record,
)
from astra_graph.retention import (
    POLICY,
    RETENTION_MONTHS,
    InMemoryProgrammeStore,
    Programme,
    prunable_before,
)
from astra_graph.writes import GraphWriter

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS
from .fakes import InMemoryGraphRepository, historical

PRINCIPAL_VALUE = "agent:transpiler"
NOW = datetime(2027, 6, 1, tzinfo=UTC)


def verifier(repository: InMemoryGraphRepository) -> ContextVerifier:
    async def assembler_at(version: int) -> ContextAssembler:
        return ContextAssembler(await historical(repository, version))

    async def current_version() -> int:
        version, _at = await repository.current_version()
        return version

    return ContextVerifier(assembler_at, current_version=current_version)


async def record_for(repository, seeded, *, version: int, context_hash: str):
    return new_record(
        artefact_kind="MEASURE",
        artefact_ref="msr_01HX7",
        artefact_content_hash="sha256:deadbeef",
        agent="transpiler",
        agent_version="1.4.2",
        mode=AgentMode.GENERATED_PROVED,
        contract=ContractName.TRANSPILER_CALC,
        subject_id=seeded["calc"],
        context_hash=context_hash,
        graph_version=version,
        created_by=PRINCIPAL_VALUE,
    )


async def assemble_now(repository, seeded):
    """What an agent would have been given, and the version it was given it at."""
    assembled = await ContextAssembler(repository).assemble(
        ContractName.TRANSPILER_CALC, seeded["calc"]
    )
    version, _at = await repository.current_version()
    return assembled, version


# ------------------------------------------- criterion 1: re-materialise and compare


async def test_a_record_verifies_against_the_graph_at_its_version(repository, seeded) -> None:
    """S1.3.2's headline. The context is re-materialised, not looked up."""
    assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version, context_hash=assembled.context_hash
    )

    verification = await verifier(repository).verify_record(record)

    assert verification.outcome is VerificationOutcome.MATCH
    assert verification.matched
    assert verification.recomputed_hash == assembled.context_hash
    assert verification.graph_version == version


async def test_a_record_still_verifies_after_the_graph_moves_on(repository, seeded, writer) -> None:
    """The point of recording a version.

    Without one, a re-harvest a week later would make every provenance record look wrong.
    With one, the record is checked against the graph the agent actually saw.
    """
    assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version, context_hash=assembled.context_hash
    )

    await writer.set_node_properties(
        seeded["calc"],
        {"formula": "SUM([M]) / SUM([R]) * 100"},
        principal=_principal("user:a.mehta@artizent.example"),
    )

    # The context has genuinely changed…
    now = await ContextAssembler(repository).assemble(
        ContractName.TRANSPILER_CALC, seeded["calc"]
    )
    assert now.context_hash != assembled.context_hash

    # …and the record still verifies, because it names the version it was assembled at.
    verification = await verifier(repository).verify_record(record)

    assert verification.outcome is VerificationOutcome.MATCH


async def test_a_tampered_hash_is_a_mismatch_not_an_error(repository, seeded) -> None:
    """A wrong record is a finding. An auditor's tool that raised here would be useless."""
    _assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version, context_hash="sha256:" + "0" * 64
    )

    verification = await verifier(repository).verify_record(record)

    assert verification.outcome is VerificationOutcome.MISMATCH
    assert not verification.matched
    assert verification.recomputed_hash is not None
    assert verification.recomputed_hash != record.context_hash
    assert "does not describe what this graph would have given" in verification.detail


async def test_a_version_beyond_the_stream_is_unverifiable_not_a_mismatch(
    repository, seeded
) -> None:
    """A record citing a future state came from elsewhere or has been altered — which is a
    different finding from "the hash is wrong", and must not be reported as one."""
    assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version + 5_000, context_hash=assembled.context_hash
    )

    verification = await verifier(repository).verify_record(record)

    assert verification.outcome is VerificationOutcome.UNVERIFIABLE
    assert verification.recomputed_hash is None
    assert "cannot describe a future state" in verification.detail


async def test_a_subject_that_did_not_exist_yet_is_unverifiable(repository, seeded) -> None:
    """Version 1 is one event in: the calculated field has not been written."""
    assembled, _version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=1, context_hash=assembled.context_hash
    )

    verification = await verifier(repository).verify_record(record)

    assert verification.outcome is VerificationOutcome.UNVERIFIABLE
    assert "could not be re-materialised" in verification.detail


async def test_the_re_materialised_document_can_be_shown_not_just_the_verdict(
    repository, seeded
) -> None:
    """"show that the hash matches" — an auditor wants to see what was hashed."""
    assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version, context_hash=assembled.context_hash
    )

    verification = await verifier(repository).verify_record(record, include_document=True)

    assert verification.document == assembled.document
    assert verification.as_dict(include_document=True)["document"]["subject"]["id"] == (
        seeded["calc"]
    )


async def test_a_verification_omits_the_document_by_default(repository, seeded) -> None:
    """A context can be a quarter of a megabyte; a verdict is a few hundred bytes."""
    assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version, context_hash=assembled.context_hash
    )

    verification = await verifier(repository).verify_record(record)

    assert verification.document is None
    assert "document" not in verification.as_dict()


async def test_a_record_carries_the_graph_version_in_its_inputs(repository, seeded) -> None:
    """§4.2's ``inputs`` block, extended. Without it the record is not reproducible."""
    assembled, version = await assemble_now(repository, seeded)
    record = await record_for(
        repository, seeded, version=version, context_hash=assembled.context_hash
    )

    inputs = record.as_dict()["inputs"]

    assert inputs["context_hash"] == assembled.context_hash
    assert inputs["graph_version"] == version
    assert inputs["subject_ref"] == seeded["calc"]
    assert inputs["contract"] == "transpiler_calc"


# ------------------------------- criterion 2: versions addressable, retention computed


async def test_a_version_is_an_event_offset(repository, seeded, writer) -> None:
    """S1.3.2: "graph versions are addressable by event offset"."""
    before, _at = await repository.current_version()

    await writer.set_node_properties(
        seeded["calc"], {"formula": "SUM([M])"}, principal=_principal("agent:harvester")
    )

    after, at = await repository.current_version()
    assert after == before + 1, "one write, one event, one version"
    assert at is not None


async def test_the_empty_graph_is_version_zero() -> None:
    """An addressable version, not an error: nothing has happened yet."""
    version, at = await InMemoryGraphRepository().current_version()
    assert (version, at) == (0, None)


async def test_nothing_is_prunable_while_a_programme_is_open() -> None:
    """S1.3.2: retention is the programme lifetime plus twelve months, and an open
    programme has no lifetime yet."""
    state = prunable_before(
        [Programme(id="prg_1", name="RQA migration", started_at="2027-01-01T00:00:00Z")],
        now=NOW,
    )

    assert state.prunable_before is None
    assert state.policy == POLICY
    assert "still running" in state.reason


async def test_nothing_is_prunable_when_no_programme_is_recorded() -> None:
    """An empty table is not permission to delete."""
    state = prunable_before([], now=NOW)

    assert state.prunable_before is None
    assert "cannot tell whether it is holding evidence" in state.reason


async def test_a_closed_programme_holds_its_versions_for_twelve_months() -> None:
    programme = Programme(
        id="prg_1",
        name="RQA migration",
        started_at="2026-01-01T00:00:00Z",
        closed_at="2027-03-31T00:00:00Z",
    )

    assert programme.retain_until() == "2028-03-31T00:00:00.000Z"

    within = prunable_before([programme], now=datetime(2028, 3, 30, tzinfo=UTC))
    assert within.prunable_before is None
    assert "has not passed" in within.reason

    after = prunable_before([programme], now=datetime(2028, 4, 1, tzinfo=UTC))
    assert after.prunable_before == "2028-03-31T00:00:00.000Z"


async def test_the_floor_is_the_earliest_close_across_programmes() -> None:
    """A cutoff has to be safe for every programme sharing the graph."""
    early = Programme(
        id="a", name="RQA", started_at="2026-01-01T00:00:00Z", closed_at="2027-01-31T00:00:00Z"
    )
    late = Programme(
        id="b", name="GTAA", started_at="2026-01-01T00:00:00Z", closed_at="2027-09-30T00:00:00Z"
    )

    state = prunable_before([late, early], now=datetime(2029, 1, 1, tzinfo=UTC))

    assert state.prunable_before == "2028-01-31T00:00:00.000Z"


async def test_one_open_programme_holds_everything_back() -> None:
    closed = Programme(
        id="a", name="RQA", started_at="2026-01-01T00:00:00Z", closed_at="2027-01-31T00:00:00Z"
    )
    still_running = Programme(id="b", name="GTAA", started_at="2026-01-01T00:00:00Z")

    state = prunable_before([closed, still_running], now=datetime(2030, 1, 1, tzinfo=UTC))

    assert state.prunable_before is None
    assert "GTAA" in state.reason


@pytest.mark.parametrize(
    ("closed", "expected"),
    [
        # Calendar months, not 365 days: an auditor reads "a year after we closed" as the
        # anniversary, and 2028 is a leap year.
        ("2027-02-28T00:00:00Z", "2028-02-28T00:00:00.000Z"),
        # The day is clamped where the month is short.
        ("2027-08-31T00:00:00Z", "2028-08-31T00:00:00.000Z"),
        ("2027-12-31T00:00:00Z", "2028-12-31T00:00:00.000Z"),
    ],
)
async def test_the_retention_floor_lands_on_the_anniversary(closed, expected) -> None:
    programme = Programme(
        id="a", name="RQA", started_at="2026-01-01T00:00:00Z", closed_at=closed
    )
    assert programme.retain_until() == expected


async def test_retention_months_is_the_figure_the_story_names() -> None:
    assert RETENTION_MONTHS == 12


async def test_a_closed_programme_cannot_be_re_closed() -> None:
    """A retention floor that can be moved is not a floor."""
    store = InMemoryProgrammeStore()
    programme = await store.open_programme(
        name="RQA", started_at="2027-01-01T00:00:00Z", created_by="user:pm@artizent.example"
    )
    assert await store.close_programme(programme.id, closed_at="2027-06-01T00:00:00Z")

    assert await store.close_programme(programme.id, closed_at="2025-01-01T00:00:00Z") is None


# --------------------------------------------------------------------- the store


async def test_records_are_readable_by_subject() -> None:
    """The Migration Unit page shows every artefact's provenance for one report (§15.4)."""
    store = InMemoryProvenanceStore()
    for ref in ("msr_a", "msr_b"):
        await store.record(
            new_record(
                artefact_kind="MEASURE",
                artefact_ref=ref,
                artefact_content_hash="sha256:x",
                agent="transpiler",
                agent_version="1.4.2",
                mode=AgentMode.DETERMINISTIC,
                contract=ContractName.TRANSPILER_CALC,
                subject_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                context_hash="sha256:y",
                graph_version=7,
            )
        )

    found = await store.for_subject("01ARZ3NDEKTSV4RRFFQ69G5FAV")

    assert {r.artefact_ref for r in found} == {"msr_a", "msr_b"}


# ------------------------------------------------------------------ the HTTP surface


async def test_the_current_version_is_published(client, seeded) -> None:
    response = await client.get("/v1/graph-versions/current", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    assert response.json()["graph_version"] > 0


async def test_a_record_is_written_and_verified_over_http(client, repository, seeded) -> None:
    """S1.3.2 criterion 1, end to end: the path the console takes."""
    assembled, version = await assemble_now(repository, seeded)

    created = await client.post(
        "/v1/provenance",
        json={
            "artefact_kind": "MEASURE",
            "artefact_ref": "msr_01HX7",
            "artefact_content_hash": "sha256:deadbeef",
            "agent": "transpiler",
            "agent_version": "1.4.2",
            "mode": "GENERATED_PROVED",
            "contract": "transpiler_calc",
            "subject_id": seeded["calc"],
            "context_hash": assembled.context_hash,
            "graph_version": version,
            "prompt_hash": "sha256:cafe",
            "model": "claude-opus-5",
            "tokens_in": 2140,
            "tokens_out": 310,
            "confidence": 0.91,
        },
        headers=ARTIZENT_HEADERS,
    )
    assert created.status_code == 201
    record_id = created.json()["id"]
    assert created.json()["inputs"]["graph_version"] == version

    verified = await client.post(
        f"/v1/provenance/{record_id}:verify", headers=ARTIZENT_HEADERS
    )

    assert verified.status_code == 200
    body = verified.json()["verification"]
    assert body["outcome"] == "MATCH"
    assert body["matched"] is True
    assert body["recomputed_context_hash"] == assembled.context_hash


async def test_a_failed_verification_is_a_finding_not_an_error_status(
    client, repository, seeded
) -> None:
    _assembled, version = await assemble_now(repository, seeded)
    created = await client.post(
        "/v1/provenance",
        json={
            "artefact_kind": "MEASURE",
            "artefact_ref": "msr_bad",
            "artefact_content_hash": "sha256:deadbeef",
            "agent": "transpiler",
            "agent_version": "1.4.2",
            "mode": "GENERATED_PROVED",
            "contract": "transpiler_calc",
            "subject_id": seeded["calc"],
            "context_hash": "sha256:" + "0" * 64,
            "graph_version": version,
        },
        headers=ARTIZENT_HEADERS,
    )

    verified = await client.post(
        f"/v1/provenance/{created.json()['id']}:verify", headers=ARTIZENT_HEADERS
    )

    assert verified.status_code == 200, "a finding, not a failure of the request"
    assert verified.json()["verification"]["outcome"] == "MISMATCH"


async def test_a_claim_can_be_verified_without_a_stored_record(
    client, repository, seeded
) -> None:
    """The audit path does not depend on this service holding the record — §5.2 gives
    provenance linkage to artefact-svc, which will hold it instead."""
    assembled, version = await assemble_now(repository, seeded)

    response = await client.post(
        "/v1/provenance:verify",
        json={
            "contract": "transpiler_calc",
            "subject_id": seeded["calc"],
            "graph_version": version,
            "context_hash": assembled.context_hash,
        },
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "MATCH"
    assert response.json()["record_id"] is None


async def test_the_document_can_be_asked_for_over_http(client, repository, seeded) -> None:
    assembled, version = await assemble_now(repository, seeded)

    response = await client.post(
        "/v1/provenance:verify?include_document=true",
        json={
            "contract": "transpiler_calc",
            "subject_id": seeded["calc"],
            "graph_version": version,
            "context_hash": assembled.context_hash,
        },
        headers=ARTIZENT_HEADERS,
    )

    assert response.json()["document"]["subject"]["id"] == seeded["calc"]


async def test_a_missing_record_is_a_404(client) -> None:
    response = await client.post(
        "/v1/provenance/prov_nothing:verify", headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 404


async def test_retention_is_published(client) -> None:
    response = await client.get("/v1/retention", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["policy"] == "programme lifetime plus 12 months"
    assert body["prunable_before"] is None
    assert body["pruning_implemented"] is False


async def test_a_programme_can_be_opened_and_closed_over_http(client) -> None:
    opened = await client.post(
        "/v1/programmes",
        json={"name": "RQA migration", "started_at": "2027-01-01T00:00:00Z"},
        headers=ARTIZENT_HEADERS,
    )
    assert opened.status_code == 201
    assert opened.json()["open"] is True
    assert opened.json()["retain_until"] is None

    programme_id = opened.json()["id"]
    closed = await client.post(
        f"/v1/programmes/{programme_id}:close",
        json={"closed_at": "2027-09-30T00:00:00Z"},
        headers=ARTIZENT_HEADERS,
    )
    assert closed.json()["retain_until"] == "2028-09-30T00:00:00.000Z"

    again = await client.post(
        f"/v1/programmes/{programme_id}:close",
        json={"closed_at": "2027-01-02T00:00:00Z"},
        headers=ARTIZENT_HEADERS,
    )
    assert again.status_code == 404, "a floor that can be moved is not a floor"


async def test_provenance_endpoints_need_an_artizent_role(client) -> None:
    for path in ("/v1/graph-versions/current", "/v1/retention"):
        response = await client.get(path, headers=CLIENT_HEADERS)
        assert response.status_code == 403, path


async def test_platform_health_shows_the_version_and_the_retention_floor(client) -> None:
    """The operator's view of both, on the screen §15.3.3 names."""
    from astra_graph.harvest import InMemoryHarvestStore, InMemoryScheduleStore

    app = client._transport.app
    app.state.schedule_store = InMemoryScheduleStore()
    app.state.harvest_store = InMemoryHarvestStore()

    body = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()

    assert "graph_version" in body["graph_version"]
    assert body["retention"]["policy"] == "programme lifetime plus 12 months"
    assert body["retention"]["pruning_implemented"] is False


def _principal(value: str):
    from astra_graph.principal import Principal

    return Principal(value)


@pytest.fixture
def repository() -> InMemoryGraphRepository:
    return InMemoryGraphRepository()


@pytest.fixture
def writer(repository: InMemoryGraphRepository) -> GraphWriter:
    return GraphWriter(repository)
