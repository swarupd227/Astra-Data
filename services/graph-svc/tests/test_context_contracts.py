"""Context contracts and the shared assembler.

S1.3.1's four acceptance criteria: a contract is a named GraphQL fragment plus a
serialiser producing a canonical document and its sha256; two calls over the same graph
state hash the same; the Transpiler contract carries exactly what §4.1.3 lists and nothing
else; and size is reported per call, with a contract over its budget failing rather than
truncating.
"""

from __future__ import annotations

import json

import pytest

from astra_graph.context import (
    CONTRACTS,
    Budget,
    ContextAssembler,
    ContextBudgetExceededError,
    ContextContract,
    ContractDefinitionError,
    ContractName,
    SectionSpec,
    ast_shape,
    canonical_json,
    context_hash,
    describe,
    matches,
    serialise,
    signature_of,
    validate_registry,
)
from astra_graph.context.canonical import CanonicalisationError
from astra_graph.errors import ElementNotFoundError, InvalidRequestError
from astra_graph.principal import Principal
from astra_graph.writes import GraphWriter, NodeWrite

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS
from .fakes import InMemoryGraphRepository

PRINCIPAL = Principal("agent:transpiler", run_id="run-context")

#: The AST the seeded "Margin %" would really carry, once E2's parser writes one.
MARGIN_AST = {
    "op": "DIV",
    "args": [
        {"fn": "SUM", "arg": {"field": "Margin"}},
        {"fn": "SUM", "arg": {"field": "Revenue"}},
    ],
}


def assembler(repository, **kwargs) -> ContextAssembler:
    return ContextAssembler(repository, **kwargs)


async def assemble(repository, seeded, **kwargs):
    return await assembler(repository, **kwargs).assemble(
        ContractName.TRANSPILER_CALC, seeded["calc"]
    )


# ---------------------------------------- criterion 1: a fragment, a document, a hash


async def test_the_contract_is_a_fragment_the_graphql_schema_accepts() -> None:
    """S1.3.1 criterion 1, first half. The fragment is checked, not just stored."""
    validate_registry(force=True)

    contract = CONTRACTS[ContractName.TRANSPILER_CALC]
    assert "fragment TranspilerSubject on CalculatedField" in contract.fragments
    assert contract.selection("TranspilerSubject")[0] == "id"


async def test_a_fragment_naming_a_property_the_ontology_lacks_is_refused() -> None:
    """The point of validating against the generated schema rather than a list of strings."""
    bad = ContextContract(
        name=ContractName.TRANSPILER_CALC,
        version="0.0.1",
        subject_type="CalculatedField",
        description="",
        spec_ref="",
        fragments="fragment Bad on CalculatedField { id nonsense_property }",
        sections=(SectionSpec("subject", "", "Bad", ""),),
        budget=Budget(bytes=1024, nodes=10),
    )

    with pytest.raises(ContractDefinitionError, match="nonsense_property"):
        bad.validate()


async def test_a_fragment_on_a_type_the_ontology_lacks_is_refused() -> None:
    bad = ContextContract(
        name=ContractName.TRANSPILER_CALC,
        version="0.0.1",
        subject_type="CalculatedField",
        description="",
        spec_ref="",
        fragments="fragment Bad on Sprocket { id }",
        sections=(SectionSpec("subject", "", "Bad", ""),),
        budget=Budget(bytes=1024, nodes=10),
    )

    with pytest.raises(ContractDefinitionError, match="Sprocket"):
        bad.validate()


async def test_a_fragment_no_section_uses_is_refused() -> None:
    """A contract's fragments are its inference boundary; a dead one is a mistake."""
    bad = ContextContract(
        name=ContractName.TRANSPILER_CALC,
        version="0.0.1",
        subject_type="CalculatedField",
        description="",
        spec_ref="",
        fragments=(
            "fragment Used on CalculatedField { id }\n"
            "fragment Forgotten on Parameter { id name }"
        ),
        sections=(SectionSpec("subject", "", "Used", ""),),
        budget=Budget(bytes=1024, nodes=10),
    )

    with pytest.raises(ContractDefinitionError, match="Forgotten"):
        bad.validate()


async def test_a_nested_selection_is_refused() -> None:
    """Traversal is the resolution plan's job; a fragment that navigates would hide it.

    The schema rejects this one first, which is the stronger result: every field a
    contract can select is scalar, so nesting is not merely disallowed by policy — there
    is nothing to nest into. The flatness guard below is the independent check, for a
    schema that one day has an object-valued field.
    """
    bad = ContextContract(
        name=ContractName.TRANSPILER_CALC,
        version="0.0.1",
        subject_type="CalculatedField",
        description="",
        spec_ref="",
        fragments="fragment Bad on CalculatedField { id name { first } }",
        sections=(SectionSpec("subject", "", "Bad", ""),),
        budget=Budget(bytes=1024, nodes=10),
    )

    with pytest.raises(ContractDefinitionError, match="must not have a selection"):
        bad.validate()


async def test_the_flatness_guard_refuses_a_nested_selection_on_its_own() -> None:
    from astra_graph.context.contract import _selections

    with pytest.raises(ContractDefinitionError, match="flat by design"):
        _selections("fragment Bad on CalculatedField { id name { first } }")


async def test_a_fragment_selecting_the_same_field_twice_is_refused() -> None:
    """Harmless in GraphQL, ambiguous here: the serialiser emits fields once."""
    from astra_graph.context.contract import _selections

    with pytest.raises(ContractDefinitionError, match="twice"):
        _selections("fragment Bad on CalculatedField { id name name }")


async def test_a_fragments_block_may_hold_only_fragments() -> None:
    from astra_graph.context.contract import _selections

    with pytest.raises(ContractDefinitionError, match="only fragment definitions"):
        _selections("query Nope { schema_version }")


async def test_a_section_is_over_nodes_or_over_edges() -> None:
    with pytest.raises(ContractDefinitionError, match="over nodes"):
        SectionSpec("x", "", "F", "", kind="sideways")


async def test_the_assembler_returns_a_canonical_document_and_its_sha256(
    repository, seeded
) -> None:
    """S1.3.1 criterion 1, second half."""
    assembled = await assemble(repository, seeded)

    assert assembled.context_hash.startswith("sha256:")
    assert len(assembled.context_hash) == len("sha256:") + 64
    assert assembled.payload == canonical_json(assembled.document)
    assert assembled.context_hash == context_hash(assembled.payload)
    assert json.loads(assembled.payload.decode("utf-8")) == assembled.document


async def test_canonical_json_removes_every_degree_of_freedom() -> None:
    one = canonical_json({"b": 1, "a": {"d": 2, "c": 3}})
    other = canonical_json({"a": {"c": 3, "d": 2}, "b": 1})

    assert one == other
    assert one == b'{"a":{"c":3,"d":2},"b":1}'


async def test_canonical_json_keeps_unicode_as_text() -> None:
    """A workbook named in Devanagari hashes the same as it reads."""
    assert canonical_json({"name": "मार्जिन"}) == '{"name":"मार्जिन"}'.encode()


async def test_a_value_json_cannot_hold_is_refused_not_smuggled() -> None:
    with pytest.raises(CanonicalisationError):
        canonical_json({"x": float("nan")})


# ------------------------------------------- criterion 2: the same state, the same hash


async def test_two_calls_over_the_same_graph_produce_the_same_hash(repository, seeded) -> None:
    """S1.3.1 criterion 2. §4.2 puts this hash in every provenance record and §5.4 caches
    on it, so a context that hashed differently each time would break both."""
    first = await assemble(repository, seeded)
    second = await assemble(repository, seeded)

    assert first.context_hash == second.context_hash
    assert first.payload == second.payload


async def test_a_different_row_order_does_not_change_the_hash(repository, seeded) -> None:
    """The failure this guards against would be intermittent: PostgreSQL is free to return
    rows in a different order between two identical queries."""
    first = await assemble(repository, seeded)

    repository.edges = dict(reversed(list(repository.edges.items())))
    repository.nodes = dict(reversed(list(repository.nodes.items())))
    second = await assemble(repository, seeded)

    assert first.context_hash == second.context_hash


async def test_a_change_to_the_graph_does_change_the_hash(repository, seeded, writer) -> None:
    """The other half: a hash that never changed would be worthless."""
    before = await assemble(repository, seeded)

    await writer.set_node_properties(
        seeded["calc"], {"formula": "SUM([M]) / SUM([R]) * 100"}, principal=PRINCIPAL
    )
    after = await assemble(repository, seeded)

    assert before.context_hash != after.context_hash


async def test_re_harvesting_without_a_semantic_change_keeps_the_hash(
    repository, seeded, writer
) -> None:
    """Audit metadata is deliberately outside the contract.

    A workbook re-harvested with nothing changed gets a new ``updated_at`` and
    ``updated_by``. If those were in the context, the gateway's context-hash cache would
    never hit and no provenance record could be checked by re-assembling.
    """
    before = await assemble(repository, seeded)

    await writer.set_node_properties(
        seeded["calc"], {"formula": "SUM([M]) / SUM([R])"}, principal=Principal("agent:harvester")
    )
    after = await assemble(repository, seeded)

    assert repository.nodes[seeded["calc"]]["properties"]["updated_by"] == "agent:harvester"
    assert before.context_hash == after.context_hash


async def test_the_contract_version_is_part_of_the_hash(repository, seeded) -> None:
    """A context assembled under a new contract cannot be mistaken for the old one."""
    assembled = await assemble(repository, seeded)
    assert assembled.document["contract"] == {"name": "transpiler_calc", "version": "1.0.0"}


# --------------------------------- criterion 3: exactly the shape §4.1.3 names, and no more


async def test_the_transpiler_contract_carries_the_closure_and_the_parameters(
    repository, seeded
) -> None:
    """S1.3.1 criterion 3, the first two clauses."""
    document = (await assemble(repository, seeded)).document

    assert document["subject"]["id"] == seeded["calc"]
    # §4.1.3 says *transitive*: nested_calc is a direct dependency, field is one further.
    assert [c["id"] for c in document["dependency_calculations"]] == [seeded["nested_calc"]]
    assert [f["id"] for f in document["dependency_fields"]] == [seeded["field"]]
    assert [p["id"] for p in document["parameters"]] == [seeded["parameter"]]
    assert document["parameters"][0]["domain"] == "range"


async def test_the_subject_is_not_repeated_among_its_own_dependencies(
    repository, seeded
) -> None:
    """The closure query includes its anchor; a calculation does not depend on itself."""
    document = (await assemble(repository, seeded)).document

    assert seeded["calc"] not in [c["id"] for c in document["dependency_calculations"]]


async def test_the_contract_carries_the_target_column_not_just_the_table(
    repository, seeded
) -> None:
    """S1.3.1 criterion 3: "the target ModelTable **columns** those fields MAPS_TO".

    The column is a property of the MAPS_TO edge, because §4.1.1 declares no column node.
    A contract that carried only the table would leave the Transpiler unable to write a
    column reference.
    """
    document = (await assemble(repository, seeded)).document

    assert [t["id"] for t in document["model_tables"]] == [seeded["model_table"]]
    assert document["model_tables"][0]["name"] == "fact_positions"
    assert document["model_columns"] == [
        {
            "from_id": seeded["field"],
            "to_id": seeded["model_table"],
            "target_column": "notional",
        }
    ]


async def test_the_contract_carries_matching_patterns(repository, seeded, writer) -> None:
    """S1.3.1 criterion 3, the last clause — and the section S1.1.2 could only promise."""
    await writer.set_node_properties(
        seeded["calc"], {"formula_ast": MARGIN_AST}, principal=PRINCIPAL
    )
    await _pattern(writer, name="Ratio of sums", shape="DIV(SUM(a), SUM(b))")
    await _pattern(writer, name="Something else", shape="SUM(a)")

    document = (await assemble(repository, seeded)).document

    assert [p["name"] for p in document["patterns"]] == ["Ratio of sums"]
    assert document["patterns"][0]["target_template"] == "DIVIDE(SUM({a}), SUM({b}))"


async def test_a_retired_pattern_is_not_offered(repository, seeded, writer) -> None:
    """It was withdrawn because it produced wrong output. Offering it invites that again."""
    await writer.set_node_properties(
        seeded["calc"], {"formula_ast": MARGIN_AST}, principal=PRINCIPAL
    )
    await _pattern(writer, name="Withdrawn", shape="DIV(SUM(a), SUM(b))", state="RETIRED")
    await _pattern(writer, name="Candidate", shape="DIV(SUM(a), SUM(b))", state="CANDIDATE")

    document = (await assemble(repository, seeded)).document

    assert [p["name"] for p in document["patterns"]] == ["Candidate"]


async def test_a_pattern_for_another_adapter_is_not_offered(repository, seeded, writer) -> None:
    await writer.set_node_properties(
        seeded["calc"], {"formula_ast": MARGIN_AST}, principal=PRINCIPAL
    )
    await _pattern(writer, name="Tableau one", shape="DIV(SUM(a), SUM(b))", adapter="tableau")
    await _pattern(writer, name="Qlik one", shape="DIV(SUM(a), SUM(b))", adapter="qlik")

    document = (
        await assemble(repository, seeded, adapter="tableau")
    ).document

    assert [p["name"] for p in document["patterns"]] == ["Tableau one"]


async def test_an_unshapeable_ast_yields_no_patterns_rather_than_failing(
    repository, seeded, writer
) -> None:
    """A calculation the matcher cannot shape can still be translated from first
    principles; denying the whole context would be the worse answer."""
    await _pattern(writer, name="Any", shape="DIV(SUM(a), SUM(b))")

    document = (await assemble(repository, seeded)).document

    assert document["patterns"] == []


async def test_the_document_holds_exactly_the_sections_the_spec_names(
    repository, seeded
) -> None:
    """S1.3.1 criterion 3's last two words: **nothing else**."""
    document = (await assemble(repository, seeded)).document

    assert set(document) == {
        "contract",
        "subject_id",
        "subject",
        "dependency_fields",
        "dependency_calculations",
        "parameters",
        "model_tables",
        "model_columns",
        "patterns",
    }


async def test_no_audit_metadata_crosses_into_a_context(repository, seeded) -> None:
    """Every token is spent on every call, and every field is inference-boundary surface."""
    document = (await assemble(repository, seeded)).document
    forbidden = {
        "created_by",
        "created_at",
        "created_in_run",
        "updated_by",
        "updated_at",
        "retired_at",
        "retired_by",
        "retirement_reason",
        "side",
    }

    for key, value in document.items():
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict):
                assert not (set(entry) & forbidden), f"{key} carries audit metadata"


async def test_a_parameters_observed_values_do_not_cross_the_boundary(
    repository, seeded, writer
) -> None:
    """§4.1.3 asks for "the Parameter domains"; observed values are data users entered,
    and §18.3 puts data on the far side of the inference boundary."""
    await writer.set_node_properties(
        seeded["parameter"],
        {"current_values_seen": ["2027-01-14", "2027-01-15"]},
        principal=PRINCIPAL,
    )

    document = (await assemble(repository, seeded)).document

    assert "current_values_seen" not in document["parameters"][0]
    assert b"2027-01-14" not in canonical_json(document)


async def test_an_absent_optional_property_is_omitted_not_nulled(repository, seeded) -> None:
    """The graph does not distinguish "no value" from "not written"; emitting null would
    assert something it never said."""
    document = (await assemble(repository, seeded)).document

    assert "class" not in document["subject"], "the Transpiler has not classified it yet"
    assert "lod_type" not in document["subject"]


async def test_a_subject_of_the_wrong_type_is_refused(repository, seeded) -> None:
    with pytest.raises(InvalidRequestError, match="takes a CalculatedField"):
        await assembler(repository).assemble(
            ContractName.TRANSPILER_CALC, seeded["workbook"]
        )


async def test_a_missing_subject_is_not_found(repository) -> None:
    with pytest.raises(ElementNotFoundError):
        await assembler(repository).assemble(
            ContractName.TRANSPILER_CALC, "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        )


# ----------------------------------------- criterion 4: size reported, budget enforced


async def test_size_is_reported_per_call(repository, seeded) -> None:
    """S1.3.1 criterion 4, first half."""
    assembled = await assemble(repository, seeded)
    usage = assembled.usage()

    assert usage["size_bytes"] == len(assembled.payload)
    assert usage["node_count"] == 5, "subject, two dependencies, a parameter, a model table"
    assert usage["budget_bytes"] == 256 * 1024
    assert 0 < usage["bytes_used"] < 1
    assert 0 < usage["nodes_used"] < 1


async def test_a_context_over_its_byte_budget_fails_the_call(repository, seeded, writer) -> None:
    """S1.3.1 criterion 4, second half: fails rather than truncating silently."""
    await writer.set_node_properties(
        seeded["calc"], {"formula": "X" * 400_000}, principal=PRINCIPAL
    )

    with pytest.raises(ContextBudgetExceededError) as raised:
        await assemble(repository, seeded)

    assert raised.value.status_code == 413
    assert raised.value.actual["bytes"] > raised.value.budget["bytes"]
    assert "truncated" in str(raised.value)


async def test_a_context_over_its_node_budget_fails_the_call(repository, seeded, writer) -> None:
    from astra_graph.writes import EdgeWrite

    contract = CONTRACTS[ContractName.TRANSPILER_CALC]
    extra = await writer.write_nodes(
        [
            NodeWrite(
                type="Field",
                properties={"name": f"F{i}", "datatype": "real", "role": "measure"},
            )
            for i in range(contract.budget.nodes + 1)
        ],
        principal=PRINCIPAL,
    )
    for record in extra:
        await writer.write_edge(
            EdgeWrite(
                type="DEPENDS_ON",
                from_id=seeded["calc"],
                to_id=str(record["properties"]["id"]),
                properties={},
            ),
            principal=PRINCIPAL,
        )

    with pytest.raises(ContextBudgetExceededError) as raised:
        await assemble(repository, seeded)

    assert raised.value.actual["nodes"] > raised.value.budget["nodes"]


async def test_the_budget_failure_reports_both_dimensions(repository, seeded, writer) -> None:
    """The useful question on seeing this is "by how much, and in which direction"."""
    await writer.set_node_properties(
        seeded["calc"], {"formula": "X" * 400_000}, principal=PRINCIPAL
    )

    with pytest.raises(ContextBudgetExceededError) as raised:
        await assemble(repository, seeded)

    assert set(raised.value.payload()) == {"error", "message", "budget", "actual"}
    assert set(raised.value.actual) == {"bytes", "nodes"}


async def test_a_budget_must_be_positive_in_both_dimensions() -> None:
    with pytest.raises(ContractDefinitionError, match="positive"):
        Budget(bytes=0, nodes=10)


# ---------------------------------------------------------------------- AST shapes


@pytest.mark.parametrize(
    ("ast", "expected"),
    [
        (MARGIN_AST, "DIV(SUM(a), SUM(b))"),
        ({"fn": "SUM", "arg": {"field": "Margin"}}, "SUM(a)"),
        # The same identifier twice is the same capture: DIV(a, a) is not DIV(a, b).
        (
            {"op": "DIV", "args": [{"field": "M"}, {"field": "M"}]},
            "DIV(a, a)",
        ),
        (
            {"op": "DIV", "args": [{"field": "M"}, {"field": "R"}]},
            "DIV(a, b)",
        ),
        # A field and a parameter of the same name are two things.
        (
            {"op": "ADD", "args": [{"field": "X"}, {"parameter": "X"}]},
            "ADD(a, b)",
        ),
        # Literal values never reach a shape: they can carry client data, and two
        # calculations differing only in a constant should match the same pattern.
        ({"fn": "GT", "args": [{"field": "X"}, 42]}, "GT(a, <num>)"),
        ({"fn": "GT", "args": [{"field": "X"}, 99]}, "GT(a, <num>)"),
    ],
)
async def test_ast_shapes(ast, expected) -> None:
    assert ast_shape(ast) == expected


async def test_a_shape_does_not_depend_on_key_insertion_order() -> None:
    one = {"op": "DIV", "args": [{"field": "M"}], "comment": "x"}
    other = {"comment": "x", "args": [{"field": "M"}], "op": "DIV"}

    assert ast_shape(one) == ast_shape(other)


async def test_a_signature_matches_on_shape_and_adapter() -> None:
    signature = signature_of(MARGIN_AST, adapter="tableau")

    assert signature == {"ast_shape": "DIV(SUM(a), SUM(b))", "adapter": "tableau"}
    assert matches(signature, shape="DIV(SUM(a), SUM(b))", adapter="tableau")
    assert not matches(signature, shape="SUM(a)", adapter="tableau")
    assert not matches(signature, shape="DIV(SUM(a), SUM(b))", adapter="qlik")
    # A deployment that cannot name its adapter still matches on shape.
    assert matches(signature, shape="DIV(SUM(a), SUM(b))", adapter=None)


async def test_a_signature_that_is_not_a_signature_matches_nothing() -> None:
    assert not matches("not json", shape="SUM(a)", adapter=None)
    assert not matches(None, shape="SUM(a)", adapter=None)
    assert not matches(42, shape="SUM(a)", adapter=None)


# ------------------------------------------------------------------- the serialiser


async def test_the_serialiser_takes_only_the_fields_the_fragment_names() -> None:
    out = serialise({"id": "1", "name": "x", "secret": "y"}, ["id", "name"])
    assert out == {"id": "1", "name": "x"}


# ------------------------------------------------------------------ the HTTP surface


async def test_the_contracts_are_published(client) -> None:
    """§18.3 makes "what crosses the boundary" something InfoSec signs. This is it."""
    response = await client.get("/v1/contexts", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    contract = body["contracts"][0]
    assert contract["name"] == "transpiler_calc"
    assert contract["subject_type"] == "CalculatedField"
    assert "fragment TranspilerSubject" in contract["fragments"]
    fields = {s["name"]: s["fields"] for s in contract["sections"]}
    assert fields["parameters"] == ["id", "name", "datatype", "domain", "default"]


async def test_a_context_is_assembled_over_http(client, seeded) -> None:
    response = await client.get(
        f"/v1/contexts/transpiler_calc/{seeded['calc']}", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context_hash"].startswith("sha256:")
    assert body["usage"]["size_bytes"] > 0
    assert body["document"]["subject"]["id"] == seeded["calc"]


async def test_an_over_budget_context_is_a_413_over_http(client, seeded, writer) -> None:
    await writer.set_node_properties(
        seeded["calc"], {"formula": "X" * 400_000}, principal=PRINCIPAL
    )

    response = await client.get(
        f"/v1/contexts/transpiler_calc/{seeded['calc']}", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 413
    assert response.json()["error"] == "context_budget_exceeded"
    assert response.json()["actual"]["bytes"] > response.json()["budget"]["bytes"]


async def test_an_unknown_contract_names_the_ones_that_exist(client, seeded) -> None:
    response = await client.get(
        f"/v1/contexts/not_a_real_contract/{seeded['calc']}", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 400
    assert "transpiler_calc" in response.json()["message"]


async def test_a_declared_contract_with_no_registered_shape_says_so(client, seeded) -> None:
    """MODELLER_FAMILY (story S4.1.1) is a real ``ContractName`` with no ``ContextContract``
    registered — see that enum member's own docstring for why. This endpoint cannot
    assemble it, and says that rather than pretending the name does not exist."""
    response = await client.get(
        f"/v1/contexts/modeller_family/{seeded['calc']}", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 400
    assert "modeller_family" in response.json()["message"]
    assert "no registered" in response.json()["message"]


async def test_context_endpoints_need_an_artizent_role(client, seeded) -> None:
    for path in ("/v1/contexts", f"/v1/contexts/transpiler_calc/{seeded['calc']}"):
        response = await client.get(path, headers=CLIENT_HEADERS)
        assert response.status_code == 403, path


async def test_describe_reports_every_contract_as_data() -> None:
    contracts = describe()
    assert [c["name"] for c in contracts] == ["transpiler_calc"]
    assert all(section["kind"] in {"node", "edge"} for section in contracts[0]["sections"])


# ------------------------------------------------------------------------ helpers


async def _pattern(
    writer: GraphWriter,
    *,
    name: str,
    shape: str,
    state: str = "ACTIVE",
    adapter: str | None = None,
) -> str:
    signature: dict[str, object] = {"ast_shape": shape}
    if adapter:
        signature["adapter"] = adapter
    created = await writer.write_nodes(
        [
            NodeWrite(
                type="Pattern",
                properties={
                    "name": name,
                    "class": "C2",
                    "source_signature": signature,
                    "target_template": "DIVIDE(SUM({a}), SUM({b}))",
                    "promotion_state": state,
                },
            )
        ],
        principal=PRINCIPAL,
    )
    return str(created[0]["properties"]["id"])


@pytest.fixture
def repository() -> InMemoryGraphRepository:
    return InMemoryGraphRepository()
