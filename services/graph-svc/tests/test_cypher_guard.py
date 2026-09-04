"""The read-only Cypher guard.

S1.1.2 requires the endpoint to be read-only. The transaction is what enforces that (see
the integration suite); this is the layer that turns a refusal into a message a person can
act on, and that stops a query escaping its dollar-quoted container.
"""

from __future__ import annotations

import pytest

from astra_graph.cypher import (
    CypherRejected,
    accept,
    derive_columns,
    strip_literals_and_comments,
)


def reason(query: str, **kwargs) -> CypherRejected:
    with pytest.raises(CypherRejected) as caught:
        accept(query, **kwargs)
    return caught.value


# ------------------------------------------------------------------- containment


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN n $$ ; DROP TABLE x; -- ",
        "MATCH (n) WHERE n.id = $$evil$$ RETURN n",
        "MATCH (n) RETURN n $tag$ something $tag$",
    ],
)
def test_dollar_quote_is_rejected(query) -> None:
    """The query is interpolated into $$ ... $$, so a delimiter inside it could escape."""
    assert reason(query).code == "dollar_quote"


def test_statement_separator_is_rejected() -> None:
    assert reason("MATCH (n:Site) RETURN n; MATCH (m) RETURN m").code == "statement_separator"


def test_a_semicolon_inside_a_string_literal_is_not_a_separator() -> None:
    accepted = accept("MATCH (n:Site) WHERE n.name = 'a; b' RETURN n")
    assert accepted.columns == ("n",)


# ------------------------------------------------------------------- read-only


@pytest.mark.parametrize(
    ("query", "keyword"),
    [
        ("CREATE (n:Site {name:'x'}) RETURN n", "CREATE"),
        ("MATCH (n:Site) SET n.name = 'x' RETURN n", "SET"),
        ("MATCH (n:Site) DELETE n RETURN 1 AS ok", "DELETE"),
        ("MATCH (n:Site) DETACH DELETE n RETURN 1 AS ok", "DELETE, DETACH"),
        ("MERGE (n:Site {name:'x'}) RETURN n", "MERGE"),
        ("MATCH (n:Site) REMOVE n.name RETURN n", "REMOVE"),
    ],
)
def test_write_clauses_are_rejected_by_name(query, keyword) -> None:
    rejected = reason(query)
    assert rejected.code == "write_clause"
    assert keyword in str(rejected)


def test_a_write_keyword_inside_a_string_literal_is_not_a_write() -> None:
    """A workbook called 'Create new report' must not look like a write."""
    accepted = accept("MATCH (w:Workbook) WHERE w.name = 'Create new report' RETURN w")
    assert accepted.columns == ("w",)


def test_a_write_keyword_inside_a_comment_is_not_a_write() -> None:
    accepted = accept("MATCH (w:Workbook) // we do not CREATE here\nRETURN w")
    assert accepted.columns == ("w",)


def test_block_comments_are_stripped() -> None:
    accepted = accept("MATCH (w:Workbook) /* DELETE nothing */ RETURN w AS wb")
    assert accepted.columns == ("wb",)


def test_strip_preserves_length() -> None:
    """Boundaries computed on the scrubbed text are used to slice the original."""
    query = "MATCH (n) WHERE n.name = 'a, b' RETURN n"
    assert len(strip_literals_and_comments(query)) == len(query)


# --------------------------------------------------------------- result columns


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("MATCH (w:Workbook) RETURN w", ("w",)),
        ("MATCH (w:Workbook) RETURN w AS workbook", ("workbook",)),
        ("MATCH (w:Workbook) RETURN w.name AS name, w.luid AS luid", ("name", "luid")),
        ("MATCH (w:Workbook) RETURN w ORDER BY w.name LIMIT 10", ("w",)),
        ("MATCH (w:Workbook) RETURN count(w) AS total", ("total",)),
        ("MATCH (w:Workbook) WITH w RETURN w AS wb", ("wb",)),
        ("MATCH (w:Workbook) RETURN collect(w.name) AS names", ("names",)),
    ],
)
def test_columns_are_derived_from_the_return_clause(query, expected) -> None:
    assert derive_columns(query) == expected


def test_return_star_is_refused_with_an_explanation() -> None:
    rejected = reason("MATCH (w:Workbook) RETURN *")
    assert rejected.code == "return_star"
    assert "Name each returned item" in str(rejected)


def test_an_expression_without_an_alias_is_refused() -> None:
    rejected = reason("MATCH (w:Workbook) RETURN w.name")
    assert rejected.code == "unaliased_return_item"
    assert "needs an alias" in str(rejected)


def test_a_query_without_a_return_is_refused() -> None:
    assert reason("MATCH (w:Workbook) WHERE w.name = 'x'").code == "no_return_clause"


def test_duplicate_column_names_are_refused() -> None:
    rejected = reason("MATCH (a:Site), (b:Site) RETURN a AS x, b AS x")
    assert rejected.code == "duplicate_return_columns"


def test_a_comma_inside_a_function_call_does_not_split_a_column() -> None:
    assert derive_columns(
        "MATCH (w:Workbook) RETURN coalesce(w.name, w.luid) AS label, w.revision AS rev"
    ) == ("label", "rev")


def test_explicit_columns_override_the_deriver() -> None:
    accepted = accept("MATCH (w:Workbook) RETURN w.name", columns=["name"])
    assert accepted.columns == ("name",)


def test_explicit_columns_must_be_identifiers() -> None:
    rejected = reason("MATCH (w:Workbook) RETURN w.name", columns=["drop table x"])
    assert rejected.code == "invalid_column_name"


def test_column_definition_is_schema_qualified() -> None:
    accepted = accept("MATCH (w:Workbook) RETURN w AS wb")
    assert accepted.column_definition == '"wb" ag_catalog.agtype'


# ---------------------------------------------------------------------- limits


def test_an_empty_query_is_refused() -> None:
    assert reason("   ").code == "empty_query"


def test_an_oversized_query_is_refused() -> None:
    assert reason("MATCH (w) RETURN w " + "// pad\n" * 4000).code == "query_too_long"
