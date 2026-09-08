"""Routing a real model defect to the Foundry -- story S8.2.2, continuing F8.2/E8. Pure
pieces only (`ModelDefectEvidence`, `_family_grain_fields`'s own comma-split parse,
`MODEL_DEFECT_CLASSES`); `detect_model_defect`/`route_to_foundry` are graph-coupled
throughout (every real check needs a real `ParityCase`/`ModelFamily`/`IN_FAMILY` fact) and
are covered end to end in `test_integration_foundry_routing.py` instead, the same "pure
core, graph-coupled shell" split this epic's own prior stories already established.
"""

from __future__ import annotations

from astra_graph.foundry_routing import (
    MODEL_DEFECT_CLASSES,
    ModelDefectEvidence,
    _family_grain_fields,
)


def test_model_defect_classes_are_exactly_key_missing_and_aggregation() -> None:
    """The AC's own two named triggers -- no other §11.1 class is ever routed to the
    Foundry."""
    assert set(MODEL_DEFECT_CLASSES) == {"KEY_MISSING", "AGGREGATION"}


def test_model_defect_evidence_as_dict_round_trips_every_field() -> None:
    evidence = ModelDefectEvidence(
        family_id="fam_1", reason="a real reason", signals={"case_id": "case_1"},
    )
    assert evidence.as_dict() == {
        "family_id": "fam_1", "reason": "a real reason", "signals": {"case_id": "case_1"},
    }


def test_family_grain_fields_parses_the_comma_joined_string() -> None:
    """The identical read `cartographer._family_summary` already performs over
    `ModelFamily.grain` (`", ".join(proposal.grain)` on write)."""
    assert _family_grain_fields({"grain": "Desk, Date, Region"}) == frozenset(
        {"Desk", "Date", "Region"}
    )


def test_family_grain_fields_strips_whitespace_and_drops_empties() -> None:
    assert _family_grain_fields({"grain": "Desk,  , Date ,"}) == frozenset({"Desk", "Date"})


def test_family_grain_fields_is_empty_for_an_unset_grain() -> None:
    assert _family_grain_fields({}) == frozenset()
    assert _family_grain_fields({"grain": None}) == frozenset()
    assert _family_grain_fields({"grain": ""}) == frozenset()
