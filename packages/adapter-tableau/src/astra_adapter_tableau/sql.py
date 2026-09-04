"""Custom SQL, read where it can be — story S2.3.3.

    "I want custom SQL captured verbatim and parsed where possible, so that custom SQL becomes
    a Modeller input rather than a surprise."

**The surprise is the thing being designed away.** A Tableau custom-SQL relation is an opaque
string as far as the estate is concerned: the Modeller sees a Table node called "Custom SQL
Query" and has no idea which warehouse tables it reads, so the Fabric model is planned around
a hole. Worse, it is planned *confidently* — nothing about the estate says a hole is there.
Parsing turns the string into lineage; failing to parse it, loudly, turns it into a work item.

**Dialect awareness comes from the connection**, not from configuration. §4.1.1 already records
`Connection.class`, so the adapter knows whether this SQL is Snowflake, T-SQL or Postgres
before it reads a character. The story names those three; the mapping below covers what the
connection enum can hold and falls back to a permissive dialect rather than refusing.

**Verbatim first, parsed second.** §4.1.1 requires `custom_sql` byte-for-byte because §6.2's
live-replay strategy re-executes it, and a normalised copy would execute differently from the
client's report. Nothing here rewrites the SQL; the extraction is additive, and the original
survives whether or not it parsed.

`sqlglot` does the parsing — a real SQL parser with real dialects, and the wrong thing in the
world to hand-roll.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError, TokenError

logger = logging.getLogger(__name__)

#: §4.1.1's `Connection.class` enum → sqlglot's dialect. The story names Snowflake, SQL Server
#: and Postgres; the rest of the enum is mapped where sqlglot has a dialect and left to the
#: permissive default where it does not.
#:
#: The default matters more than it looks. A dialect sqlglot does not know is not a reason to
#: refuse to read the SQL — most custom SQL is ordinary `SELECT … FROM … JOIN`, and reading it
#: with a generic dialect extracts the same table names. What the dialect buys is the awkward
#: 10%: T-SQL's `TOP`, Snowflake's `QUALIFY`, Postgres's `::` casts.
DIALECTS: dict[str, str | None] = {
    "snowflake": "snowflake",
    "sqlserver": "tsql",
    "postgres": "postgres",
    "hive": "hive",
    "sybase": "tsql",
    "odbc": None,
    "excel": None,
    "text": None,
    "hyper": None,
}


@dataclass(frozen=True, slots=True)
class TableReference:
    """One table the SQL reads."""

    name: str
    schema: str = ""
    catalog: str = ""

    @property
    def qualified(self) -> str:
        return ".".join(part for part in (self.catalog, self.schema, self.name) if part)


@dataclass(frozen=True, slots=True)
class CustomSql:
    """What was made of one custom-SQL relation."""

    sql: str
    dialect: str = ""
    parsed: bool = False
    tables: tuple[TableReference, ...] = ()
    columns: tuple[str, ...] = ()
    error: str = ""
    is_select_star: bool = False
    """``SELECT *`` — parsed, but the columns are not knowable from the text.

    Distinct from "could not parse": the lineage is complete and the *column* list is not, and
    a Modeller planning a Fabric table needs to know which of those they are looking at."""

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(table.qualified for table in self.tables)

    def as_properties(self) -> dict[str, Any]:
        return {
            "custom_sql_dialect": self.dialect or None,
            "custom_sql_parsed": self.parsed,
            "custom_sql_tables": list(self.table_names),
            "custom_sql_columns": list(self.columns),
            "custom_sql_error": self.error or None,
        }


def dialect_for(connection_class: str) -> str | None:
    """The sqlglot dialect for a §4.1.1 connection class."""
    return DIALECTS.get(connection_class.lower())


def parse_custom_sql(sql: str, *, connection_class: str = "") -> CustomSql:
    """Read a custom-SQL relation, and say plainly when it could not be read.

    Never raises. A client's custom SQL is fifteen years of accumulated ideas, including
    stored-procedure calls, vendor extensions and SQL that was already broken when Tableau
    cached its results — and an adapter that raised on the first of them would fail a harvest
    over a string it was only ever going to record.
    """
    text = (sql or "").strip()
    if not text:
        return CustomSql(sql=sql or "")

    dialect = dialect_for(connection_class)
    label = dialect or "generic"

    try:
        statements = sqlglot.parse(text, dialect=dialect)
    except (ParseError, TokenError) as exc:
        return _unparsed(sql, label, f"{type(exc).__name__}: {_first_line(str(exc))}")
    except Exception as exc:
        # Deliberately broad. sqlglot is a dependency reading untrusted client input (§16.5);
        # a shape it mishandles must degrade to "unparsed, here is why" rather than take the
        # harvest down.
        return _unparsed(sql, label, f"{type(exc).__name__}: {_first_line(str(exc))}")

    # sqlglot returns None for an empty statement between semicolons; narrowed here so the
    # loop below has an Expression and not an Expression | None.
    parsed_statements: list[exp.Expression] = [
        statement for statement in statements if statement is not None
    ]
    if not parsed_statements:
        return _unparsed(sql, label, "the SQL parsed to nothing")

    tables: list[TableReference] = []
    columns: list[str] = []
    star = False

    for statement in parsed_statements:
        for table in statement.find_all(exp.Table):
            # A CTE name is not a table: it is defined in the same statement, and recording it
            # as lineage would tell the Modeller to look for a warehouse table that does not
            # exist.
            if _is_cte_reference(statement, table):
                continue
            reference = TableReference(
                name=str(table.name or ""),
                schema=str(table.db or ""),
                catalog=str(table.catalog or ""),
            )
            if reference.name and reference not in tables:
                tables.append(reference)

        for projection in _projections(statement):
            if isinstance(projection, exp.Star):
                star = True
                continue
            name = projection.alias_or_name
            if name and name != "*" and name not in columns:
                columns.append(str(name))

    if not tables:
        # Parsed, but reads nothing this adapter can name — a table-valued function, a bare
        # `SELECT 1`, a stored-procedure call sqlglot accepted. The Modeller still has a hole,
        # so it is reported as one rather than as a success with an empty list.
        return _unparsed(
            sql,
            label,
            "parsed, but no source table could be identified — a function call, a procedure, "
            "or a construct outside what this parser can attribute",
        )

    logger.debug(
        "custom SQL (%s) reads %d table(s): %s",
        label,
        len(tables),
        ", ".join(reference.qualified for reference in tables),
    )
    return CustomSql(
        sql=sql,
        dialect=label,
        parsed=True,
        tables=tuple(tables),
        columns=tuple(columns),
        is_select_star=star,
    )


def _unparsed(sql: str, dialect: str, reason: str) -> CustomSql:
    logger.info("custom SQL could not be read as %s: %s", dialect, reason)
    return CustomSql(sql=sql, dialect=dialect, parsed=False, error=reason)


def _projections(statement: exp.Expression) -> list[exp.Expression]:
    """The selected expressions of the outermost SELECT.

    Outermost only. A subquery's projections are intermediate — they are not columns the
    Tableau datasource exposes, and listing them would give the Modeller a field list that
    does not match the one Tableau shows.
    """
    select = statement.find(exp.Select)
    return list(select.expressions) if select is not None else []


def _is_cte_reference(statement: exp.Expression, table: exp.Table) -> bool:
    names = {str(cte.alias_or_name) for cte in statement.find_all(exp.CTE) if cte.alias_or_name}
    return str(table.name) in names


def _first_line(message: str) -> str:
    """sqlglot's parse errors carry a caret diagram; the first line is the sentence."""
    return message.strip().splitlines()[0][:200] if message.strip() else "unparseable"
