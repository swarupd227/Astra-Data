"""The visual-mapping ruleset -- story S6.1.1.

    "Mapping table from Appendix B (mark type x encodings -> visual type) is data,
    versioned, and editable by the architect."

Every test here is a plain function of the dataclasses -- no database, matching
`test_conformance_rules.py`'s own footing for the identical shape of table.
"""

from __future__ import annotations

import pytest

from astra_graph.visual_mapping import DEFAULT_MAPPINGS, MappingRule, VisualMappingRuleset


def test_every_default_row_names_exactly_one_of_target_or_redesign_reason() -> None:
    for rule in DEFAULT_MAPPINGS:
        assert bool(rule.target_visual_type) != bool(rule.redesign_reason)


def test_a_rule_cannot_declare_both_a_target_and_a_redesign_reason() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        MappingRule("bar", target_visual_type="clusteredColumnChart", redesign_reason="no")


def test_a_rule_cannot_declare_neither() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        MappingRule("bar")


def test_rule_for_matches_case_insensitively() -> None:
    ruleset = VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)
    assert ruleset.rule_for("BAR") is not None
    assert ruleset.rule_for("Bar") is not None
    assert ruleset.rule_for("bar").target_visual_type == "clusteredColumnChart"


def test_rule_for_an_unknown_mark_type_is_none() -> None:
    ruleset = VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)
    assert ruleset.rule_for("polygon-mesh") is None


def test_gantt_is_flagged_for_redesign_by_default() -> None:
    ruleset = VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)
    rule = ruleset.rule_for("ganttbar")
    assert rule is not None
    assert rule.target_visual_type is None
    assert "client approval" in rule.redesign_reason


def test_as_dict_round_trips_through_mappingrule_construction() -> None:
    rule = MappingRule("bar", target_visual_type="clusteredColumnChart", notes="n")
    restored = MappingRule(**rule.as_dict())
    assert restored == rule


def test_ruleset_as_dict_carries_every_rule() -> None:
    ruleset = VisualMappingRuleset(version=3, rules=DEFAULT_MAPPINGS, updated_by="architect", updated_at="2026-01-01T00:00:00Z")
    body = ruleset.as_dict()
    assert body["version"] == 3
    assert len(body["rules"]) == len(DEFAULT_MAPPINGS)
    assert body["updated_by"] == "architect"
