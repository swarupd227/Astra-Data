"""S2.3.3 — custom SQL captured verbatim and parsed where possible.

    "Custom SQL is stored on the Table node; a dialect-aware parser (Snowflake, SQL Server,
    Postgres) extracts referenced tables and columns where it can. Custom SQL that cannot be
    parsed is flagged as an unrecognised construct and counts against parse quality."

The story's purpose is the standard these are written to: *"so that custom SQL becomes a
Modeller input rather than a surprise."* Without this, the Modeller sees a Table called
"Custom SQL Query" and plans a Fabric model around a hole — **confidently**, because nothing
in the estate says a hole is there. Parsing turns the string into lineage; failing to parse
it, loudly, turns it into a work item. Both are better than silence.
"""

from __future__ import annotations

import pytest
from astra_adapter import Scope

from astra_adapter_tableau.sql import DIALECTS, dialect_for, parse_custom_sql

from .conftest import adapter_for
from .fake_tableau import FakeTableau, FakeWorkbook

SCOPE = Scope(site="golden")


def nodes_of(result, kind: str) -> list:
    return [node for node in result.nodes if node.type == kind]


async def parsed(adapter):
    ref = await anext(adapter.enumerate(SCOPE))
    return await adapter.parse(await adapter.fetch(ref))


# ------------------------------------------------------------ dialect awareness


def test_the_dialect_comes_from_the_connection_class() -> None:
    """ "Dialect-aware" without asking anybody: §4.1.1 already records `Connection.class`, so
    the adapter knows whether this is Snowflake, T-SQL or Postgres before it reads a
    character."""
    assert dialect_for("snowflake") == "snowflake"
    assert dialect_for("sqlserver") == "tsql"
    assert dialect_for("postgres") == "postgres"


def test_every_connection_class_has_a_decision() -> None:
    """A class missing from the table would silently fall to the permissive default, which is
    the right *behaviour* and the wrong way to arrive at it."""
    from astra_adapter_tableau.datasource import CONNECTION_CLASSES

    for mapped in set(CONNECTION_CLASSES.values()):
        assert mapped in DIALECTS, mapped


def test_t_sql_top_is_read_in_its_own_dialect() -> None:
    """`SELECT TOP 10` is a syntax error in Postgres. Reading every client's SQL with one
    dialect would fail the awkward 10% — which is the part with the money in it."""
    result = parse_custom_sql(
        "SELECT TOP 10 d.desk, p.notional FROM dbo.positions p "
        "JOIN dbo.desks d ON d.id = p.desk_id",
        connection_class="sqlserver",
    )

    assert result.parsed
    assert result.dialect == "tsql"
    assert set(result.table_names) == {"dbo.positions", "dbo.desks"}


def test_snowflake_qualify_is_read_in_its_own_dialect() -> None:
    result = parse_custom_sql(
        "select desk, notional from positions "
        "qualify row_number() over (partition by desk order by dt desc) = 1",
        connection_class="snowflake",
    )

    assert result.parsed
    assert result.dialect == "snowflake"
    assert result.table_names == ("positions",)


def test_postgres_is_read_in_its_own_dialect() -> None:
    result = parse_custom_sql(
        "select desk, sum(notional)::numeric as total from risk.positions group by desk",
        connection_class="postgres",
    )

    assert result.parsed
    assert result.table_names == ("risk.positions",)
    assert result.columns == ("desk", "total")


# --------------------------------------------------- tables and columns extracted


def test_referenced_tables_and_columns_are_extracted() -> None:
    """S2.3.3's first criterion, and the thing that makes custom SQL an input."""
    result = parse_custom_sql(
        "select p.desk, p.notional, d.region from risk.positions p "
        "join ref.desks d on d.id = p.desk_id where p.as_of > current_date - 30",
        connection_class="postgres",
    )

    assert set(result.table_names) == {"risk.positions", "ref.desks"}
    assert set(result.columns) == {"desk", "notional", "region"}


def test_a_cte_name_is_not_reported_as_a_source_table() -> None:
    """A CTE is defined in the same statement. Recording it as lineage would send the Modeller
    looking for a warehouse table that does not exist."""
    result = parse_custom_sql(
        "with recent as (select * from risk.positions) select desk from recent",
        connection_class="postgres",
    )

    assert result.table_names == ("risk.positions",)
    assert "recent" not in result.table_names


def test_a_subquerys_projections_are_not_reported_as_columns() -> None:
    """They are intermediate — not columns the Tableau datasource exposes — and listing them
    would give the Modeller a field list that does not match what Tableau shows."""
    result = parse_custom_sql(
        "select outer_desk from (select desk as outer_desk, notional from risk.positions) t",
        connection_class="postgres",
    )

    assert result.columns == ("outer_desk",)


def test_select_star_is_parsed_but_says_its_columns_are_unknown() -> None:
    """Distinct from "could not parse": the lineage is complete and the column list is not,
    and a Modeller planning a Fabric table needs to know which of those they are looking at."""
    result = parse_custom_sql("select * from risk.positions", connection_class="postgres")

    assert result.parsed
    assert result.is_select_star
    assert result.columns == ()
    assert result.table_names == ("risk.positions",)


# ------------------------------------------------ what cannot be read, said plainly


def test_unparseable_sql_is_flagged_rather_than_raised() -> None:
    """A client's custom SQL is fifteen years of accumulated ideas, including SQL that was
    already broken when Tableau cached its results. An adapter that raised would fail a
    harvest over a string it was only ever going to record."""
    result = parse_custom_sql("this is not sql at all ((", connection_class="postgres")

    assert not result.parsed
    assert result.error
    assert result.sql == "this is not sql at all ((", "and the text survives"


def test_a_stored_procedure_call_is_reported_as_unattributable() -> None:
    """It may parse, and it still leaves the Modeller with a hole. Reporting it as a success
    with an empty table list would hide that."""
    result = parse_custom_sql("EXEC sp_get_positions @desk = 'Rates'", connection_class="sqlserver")

    assert not result.parsed
    assert "no source table could be identified" in result.error


def test_sql_that_reads_nothing_is_not_a_success() -> None:
    result = parse_custom_sql("select 1", connection_class="postgres")

    assert not result.parsed


def test_empty_sql_is_not_an_error() -> None:
    """A relation with no text is not a custom-SQL relation; it is an ordinary one."""
    result = parse_custom_sql("", connection_class="postgres")

    assert not result.parsed
    assert not result.error


@pytest.mark.parametrize(
    "sql",
    [
        "select * from t where x = ",
        "sel ect from",
        "\x00\x01\x02",
        "select " + "a," * 500 + "b from t",
    ],
)
def test_no_input_makes_the_parser_raise(sql: str) -> None:
    """§16.5 treats source content as untrusted. sqlglot is a dependency reading a client's
    file; a shape it mishandles must degrade to "unparsed, here is why" rather than take the
    harvest down."""
    result = parse_custom_sql(sql, connection_class="postgres")

    assert isinstance(result.parsed, bool)


# --------------------------------------------------------------- in the fragment


async def test_the_custom_sql_table_carries_what_was_read(adapter) -> None:
    """S2.3.3's first criterion, on the node §4.1.1 puts it on."""
    result = await parsed(adapter)

    custom = [node for node in nodes_of(result, "Table") if node.properties.get("custom_sql")]
    assert custom
    properties = custom[0].properties

    assert "select * from risk.positions" in properties["custom_sql"]
    assert properties["custom_sql_parsed"] is True
    assert properties["custom_sql_dialect"] == "postgres"
    assert properties["custom_sql_tables"] == ["risk.positions"]


async def test_the_verbatim_text_survives_whether_or_not_it_parsed(adapter) -> None:
    """§4.1.1 requires byte-for-byte because §6.2's live-replay strategy re-executes it. The
    extraction is additive; nothing rewrites the SQL."""
    result = await parsed(adapter)

    custom = next(n for n in nodes_of(result, "Table") if n.properties.get("custom_sql"))
    assert "current_date - 30" in custom.properties["custom_sql"]


async def test_referenced_tables_become_nodes_the_modeller_can_see(adapter) -> None:
    """The point of the story. Without them the estate shows a Table called "Custom SQL
    Query" and nothing about where its data comes from."""
    result = await parsed(adapter)

    names = {
        node.properties["name"]
        for node in nodes_of(result, "Table")
        if not node.properties.get("custom_sql")
    }

    assert "positions" in names, "the table the custom SQL reads is in the estate"


async def test_unparseable_custom_sql_holds_the_workbook(server: FakeTableau) -> None:
    """S2.3.3's second criterion. Silence here would let a workbook whose entire datasource is
    an unreadable query score 1.0."""
    server.workbooks = [
        FakeWorkbook(luid="wb-1", name="Opaque", project="Risk", broken_custom_sql=True)
    ]
    adapter = adapter_for(server)
    try:
        result = await parsed(adapter)
    finally:
        await adapter.aclose()

    assert result.parse_quality < 1.0
    flagged = [item for item in result.unrecognised if "custom SQL" in item.detail]
    assert flagged
    assert "EXEC" in flagged[0].construct, "retained verbatim"
    assert "tsql" in flagged[0].detail or "postgres" in flagged[0].detail


async def test_readable_custom_sql_does_not_hold_the_workbook(adapter) -> None:
    """The other side of the same rule: quality falls because SQL was unreadable, not because
    the workbook contains SQL."""
    result = await parsed(adapter)

    assert result.parse_quality == 1.0
    assert not result.unrecognised


async def test_a_plain_table_relation_has_no_sql_properties(adapter) -> None:
    """The published datasource reads `[dbo].[fx_rates]` directly. Recording a null dialect
    and an empty table list on it would say the parser looked and found nothing."""
    result = await parsed(adapter)

    plain = [node for node in nodes_of(result, "Table") if node.properties["name"] == "fx_rates"]
    assert plain
    assert "custom_sql_parsed" not in plain[0].properties
