"""The G2 workflow's pure logic — story S4.2.1.

    "Model Proposal (client view) renders: what the model is, what reports use it, what
    changes for the business user... Approval requires the data owner's role and domain
    scope."

The graph/database reads and writes (seeding questions, writing a GateDecision, the
question thread itself) are exercised against real PostgreSQL in the integration suite.
What is tested here is the plain-language rendering and the domain-scope check — the parts
where being subtly wrong would either mislead a client reading the proposal or let someone
approve a design outside their own domain.
"""

from __future__ import annotations

import pytest

from astra_graph.errors import ForbiddenError
from astra_graph.g2 import check_domain_scope, plain_language_summary

# --------------------------------------------------------------------- plain_language_summary


def _document(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tables": [{"id": "t1"}, {"id": "t2"}],
        "candidate_measures": [{"name": "Margin %"}],
        "rls_roles": [],
        "refresh_policy": {"mode": "unknown", "schedule": None},
    }
    base.update(overrides)
    return base


def test_states_table_and_measure_counts() -> None:
    summary = plain_language_summary(_document())
    assert "2 tables into 1 measure" in summary


def test_singular_table_and_measure_are_not_pluralised() -> None:
    summary = plain_language_summary(_document(tables=[{"id": "t1"}], candidate_measures=[{"name": "M"}]))
    assert "1 table into 1 measure." in summary


def test_no_rls_is_stated_plainly() -> None:
    summary = plain_language_summary(_document(rls_roles=[]))
    assert "No row-level security is applied" in summary


def test_rls_present_is_stated_with_a_count() -> None:
    summary = plain_language_summary(_document(rls_roles=[{"name": "RLS — Desk"}]))
    assert "Row-level security is applied (1 role)" in summary


def test_scheduled_refresh_names_the_schedule() -> None:
    summary = plain_language_summary(
        _document(refresh_policy={"mode": "scheduled", "schedule": "daily"})
    )
    assert "refreshes on a daily schedule" in summary


def test_directquery_refresh_says_theres_no_schedule() -> None:
    summary = plain_language_summary(_document(refresh_policy={"mode": "directquery", "schedule": None}))
    assert "queried live" in summary
    assert "no refresh schedule" in summary


def test_mixed_refresh_is_stated_honestly() -> None:
    summary = plain_language_summary(_document(refresh_policy={"mode": "mixed", "schedule": None}))
    assert "Some data refreshes on a schedule; some is queried live." in summary


def test_unknown_refresh_mode_is_not_fabricated() -> None:
    summary = plain_language_summary(_document(refresh_policy={"mode": None, "schedule": None}))
    assert "has not been determined yet" in summary


def test_empty_document_does_not_crash() -> None:
    summary = plain_language_summary({})
    assert "0 tables into 0 measures" in summary


# -------------------------------------------------------------------------- check_domain_scope


def test_a_matching_domain_is_allowed() -> None:
    check_domain_scope("Risk", frozenset({"risk", "treasury"}))  # does not raise


def test_domain_matching_is_case_insensitive_on_the_family_side() -> None:
    check_domain_scope("RISK", frozenset({"risk"}))


def test_a_domain_outside_the_asserted_scope_is_refused() -> None:
    with pytest.raises(ForbiddenError, match="domain"):
        check_domain_scope("Risk", frozenset({"treasury"}))


def test_an_empty_asserted_scope_refuses_a_family_with_a_domain() -> None:
    with pytest.raises(ForbiddenError):
        check_domain_scope("Risk", frozenset())


def test_a_family_with_no_domain_assigned_is_not_refused() -> None:
    # A disclosed gap (ADR 0030): nobody assigns a domain automatically yet, so an unset
    # domain must not make every approval impossible.
    check_domain_scope(None, frozenset())
    check_domain_scope(None, frozenset({"risk"}))
