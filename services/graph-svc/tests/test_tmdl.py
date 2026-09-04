"""TMDL emission — story S4.3.1.

    "Emission is deterministic from the approved design version; the same version always
    produces byte-identical TMDL."

Everything ``emit_tmdl`` needs is the frozen design document; nothing here touches a
database, so every test is a plain function of a document fixture.
"""

from __future__ import annotations

from astra_graph.tmdl import emit_tmdl, safe_name


def _document(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "family_id": "fam_one",
        "semantic_model_id": "sem_one",
        "grain_statement": "One row per Desk and Trade Date.",
        "design_generated_at": "2027-03-01T09:00:00.000Z",
        "version": "sha256:abcdef",
        "tables": [
            {
                "id": "mt_positions",
                "name": "positions",
                "schema": "risk",
                "source_table_refs": ["t_positions"],
                "mode": "import",
                "mode_reason": "an extract already exists for this table",
                "row_estimate": 5_000_000,
                "custom_sql": False,
                "family_ref": "fam_one",
            },
            {
                "id": "mt_desk",
                "name": "desk",
                "schema": "risk",
                "source_table_refs": ["t_desk"],
                "mode": "directquery",
                "mode_reason": "no extract exists for this connection",
                "row_estimate": 40,
                "custom_sql": False,
                "family_ref": "fam_one",
            },
        ],
        "relationships": [
            {
                "from_table": "mt_desk",
                "to_table": "mt_positions",
                "cardinality": "one_to_many",
                "confidence": "row_estimate",
                "reason": "the from-table has 40 rows against 5,000,000",
                "join_clause": "positions.desk_id = desk.id",
            }
        ],
        "candidate_measures": [
            {"name": "Margin %", "source_calc_refs": ["calc1", "calc2"], "dedup_decision": "merged 2 calculations"},
        ],
        "conformed_dimensions": [],
        "refresh_policy": {"mode": "scheduled", "schedule": "daily"},
        "open_questions": [],
        "rls_role_detail": [
            {"name": "Analyst", "expression": "[Desk] = USERNAME()", "source_workbook_ids": ["wb1"]},
        ],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- determinism


def test_emitting_the_same_document_twice_is_byte_identical() -> None:
    document = _document()
    first = emit_tmdl(document)
    second = emit_tmdl(document)
    assert first.files == second.files


def test_a_changed_generated_at_timestamp_does_not_change_the_output() -> None:
    a = emit_tmdl(_document(design_generated_at="2027-03-01T09:00:00.000Z"))
    b = emit_tmdl(_document(design_generated_at="2028-11-11T23:59:59.999Z"))
    assert a.files == b.files


def test_a_changed_version_hash_does_not_change_the_output() -> None:
    """The version hash names the document; it is not part of it (the same key
    `model_lifecycle.hashable_document` excludes for the identical reason)."""
    a = emit_tmdl(_document(version="sha256:aaa"))
    b = emit_tmdl(_document(version="sha256:bbb"))
    assert a.files == b.files


def test_table_order_in_the_input_does_not_change_the_output() -> None:
    document = _document()
    reversed_tables = dict(document)
    reversed_tables["tables"] = list(reversed(document["tables"]))  # type: ignore[index]
    assert emit_tmdl(document).files == emit_tmdl(reversed_tables).files


# -------------------------------------------------------------------------------- shape


def test_the_folder_shape_matches_the_spec() -> None:
    bundle = emit_tmdl(_document())
    assert set(bundle.files) == {
        "model.tmdl",
        "tables/positions.tmdl",
        "tables/desk.tmdl",
        "tables/_Measures.tmdl",
        "relationships.tmdl",
        "roles/Analyst.tmdl",
    }


def test_no_relationships_means_no_relationships_file() -> None:
    bundle = emit_tmdl(_document(relationships=[]))
    assert "relationships.tmdl" not in bundle.files


def test_no_measures_means_no_measures_table() -> None:
    bundle = emit_tmdl(_document(candidate_measures=[]))
    assert "tables/_Measures.tmdl" not in bundle.files


def test_no_rls_roles_means_no_roles_folder() -> None:
    bundle = emit_tmdl(_document(rls_role_detail=[]))
    assert not any(path.startswith("roles/") for path in bundle.files)


def test_files_are_utf8_bytes() -> None:
    bundle = emit_tmdl(_document())
    for content in bundle.files.values():
        assert isinstance(content, bytes)
        content.decode("utf-8")  # does not raise


# ------------------------------------------------------------------------------ content


def test_model_file_names_the_family_and_grain() -> None:
    bundle = emit_tmdl(_document())
    text = bundle.files["model.tmdl"].decode("utf-8")
    assert "fam_one" in text
    assert "One row per Desk and Trade Date." in text


def test_table_file_states_the_storage_mode_and_source() -> None:
    bundle = emit_tmdl(_document())
    text = bundle.files["tables/positions.tmdl"].decode("utf-8")
    assert "mode: import" in text
    assert "risk.positions" in text
    assert "column-level detail" in text  # the disclosed gap, stated, not hidden


def test_measures_carry_a_display_folder_named_for_the_family() -> None:
    bundle = emit_tmdl(_document(family_name="Risk Positions"))
    text = bundle.files["tables/_Measures.tmdl"].decode("utf-8")
    assert 'displayFolder: "Risk Positions"' in text


def test_measures_display_folder_falls_back_to_family_id_when_no_name_given() -> None:
    bundle = emit_tmdl(_document())  # no "family_name" key
    text = bundle.files["tables/_Measures.tmdl"].decode("utf-8")
    assert 'displayFolder: "fam_one"' in text


def test_measures_carry_no_fabricated_dax() -> None:
    bundle = emit_tmdl(_document())
    text = bundle.files["tables/_Measures.tmdl"].decode("utf-8")
    assert "Margin %" in text
    assert "NOT YET TRANSPILED" in text
    assert "calc1" in text and "calc2" in text


def test_relationship_parses_columns_from_the_join_clause() -> None:
    bundle = emit_tmdl(_document())
    text = bundle.files["relationships.tmdl"].decode("utf-8")
    assert "positions.desk_id" in text
    assert "desk.id" in text
    assert "could not be parsed" not in text


def test_an_unparseable_join_clause_falls_back_with_a_disclosed_comment() -> None:
    document = _document()
    document["relationships"][0]["join_clause"] = "not a real join clause"  # type: ignore[index]
    bundle = emit_tmdl(document)
    text = bundle.files["relationships.tmdl"].decode("utf-8")
    assert "could not be parsed" in text


def test_role_file_states_the_expression_and_the_table_gap() -> None:
    bundle = emit_tmdl(_document())
    text = bundle.files["roles/Analyst.tmdl"].decode("utf-8")
    assert "[Desk] = USERNAME()" in text
    assert "not yet assigned" in text


def test_an_empty_document_does_not_crash() -> None:
    bundle = emit_tmdl({"family_id": "fam_empty", "semantic_model_id": "sem_empty"})
    assert bundle.files.keys() == {"model.tmdl"}


# ---------------------------------------------------------------------------- safe_name


def test_safe_name_keeps_simple_names_unchanged() -> None:
    assert safe_name("positions") == "positions"


def test_safe_name_replaces_unsafe_characters() -> None:
    assert "/" not in safe_name("Risk/Positions")
    assert safe_name("Risk/Positions") == "Risk_Positions"


def test_safe_name_of_an_empty_string_is_not_empty() -> None:
    assert safe_name("") == "unnamed"
