"""Datasources, connections and extracts, read from the workbook XML — S2.2.2.

    "Published datasources are captured as Datasource nodes with published: true and their
    connection graph. Embedded Hyper extracts are detected; the adapter records the extract
    schema and refresh schedule; extract data is not copied."

§6.2's "Parse — structure" row lists `<datasources>` first: connections, relations, custom SQL
and columns. This module reads exactly that much of the workbook — sheets, filters, dashboards
and actions are S2.3.2's, and calculations are S2.3.1's.

Custom SQL is handed to `sql.py`, which reads it in the *connection's own dialect* (S2.3.3).
The verbatim text stays here either way: §6.2's live-replay strategy re-executes it.

**Why XML and not the Metadata API.** The Metadata API knows which *published* datasources a
workbook uses and can be asked for upstream tables, but it does not describe an **embedded**
datasource's connection, and embedded is the common case in a real estate. The workbook is
the only place that says what an embedded datasource actually connects to. The Metadata API
supplies what the file cannot — the published datasource's LUID, its extract refresh times —
and the two are joined by name.

**The extract's data is not read, and cannot be from here.** This module sees XML. The
``.hyper`` file was already excluded by `archive.py` and by the download itself; what is
recorded is the extract's *schema* — the columns Tableau materialised — and its refresh
schedule, both of which are metadata the Modeller needs and neither of which is client data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from astra_adapter import AdapterError

from .secrets import secret_reference, strip_secrets
from .sql import CustomSql, parse_custom_sql

logger = logging.getLogger(__name__)

#: §4.1.1 fixes the Connection class enum, and the platform **rejects a write outside it**
#: (see the ontology's note and ADR 0001). Tableau's own class names are many more than nine,
#: so they are mapped — and a class with no mapping is reported rather than guessed at, since
#: guessing would put a wrong class on a node the Modeller plans ingestion from.
CONNECTION_CLASSES: dict[str, str] = {
    "sqlserver": "sqlserver",
    "azure-sql-database": "sqlserver",
    "sybase": "sybase",
    "sybasease": "sybase",
    "sybaseiq": "sybase",
    "snowflake": "snowflake",
    "postgres": "postgres",
    "redshift": "postgres",
    "greenplum": "postgres",
    "hadoophive": "hive",
    "hive": "hive",
    "spark": "hive",
    "excel-direct": "excel",
    "excel": "excel",
    "textscan": "text",
    "csv": "text",
    "genericodbc": "odbc",
    "odbc": "odbc",
    "hyper": "hyper",
    "dataengine": "hyper",
    "tde": "hyper",
    "federated": "hyper",
}

#: Connection classes that are the *extract engine* rather than a data source. Tableau writes
#: one inside a datasource that has an extract, pointing at the .hyper in the package — it is
#: the extract, and `_extract` below detects it as one. Modelling it as a Connection would put
#: a node on the graph for a file inside the workbook, and would derive a Key Vault reference
#: for a credential that does not exist.
EXTRACT_ENGINE_CLASSES = frozenset({"dataengine", "hyper", "tde"})


@dataclass(frozen=True, slots=True)
class Column:
    """One column of a datasource or a table — the extract schema, when there is one."""

    name: str
    datatype: str = "string"
    role: str = "dimension"
    default_agg: str = ""
    hidden: bool = False
    calculation: str = ""
    """The formula, verbatim, when this column is a calculated field.

    Kept but **not parsed**: S2.3.1 builds the grammar. Retaining it is §6.2's "unrecognised
    constructs are retained verbatim", and it is what lets the Parse Quality Queue show an
    engineer the calculation itself rather than a count of calculations.
    """

    @property
    def is_calculated(self) -> bool:
        return bool(self.calculation)


@dataclass(frozen=True, slots=True)
class Table:
    """A relation the connection reads. Custom SQL is kept byte-for-byte (§4.1.1)."""

    name: str
    schema: str = ""
    custom_sql: str = ""
    join_clause: str = ""
    columns: tuple[Column, ...] = ()
    sql: CustomSql | None = None
    """What was made of the custom SQL (S2.3.3). ``None`` for an ordinary table relation.

    The verbatim text stays in ``custom_sql`` whether or not this parsed: §6.2's live-replay
    strategy re-executes it, so the original is the artefact and the extraction is additive.
    """


@dataclass(frozen=True, slots=True)
class Connection:
    """Where a datasource gets its data, with nothing that could authenticate to it."""

    tableau_class: str
    connection_class: str
    """Mapped into §4.1.1's closed enum. Empty when Tableau's class has no mapping."""

    server: str = ""
    database: str = ""
    schema: str = ""
    auth_mode: str = ""
    secret_reference: str = ""
    """The Key Vault secret *name* the executor would ask for (S2.2.2). Never a credential."""

    stripped_attributes: tuple[str, ...] = ()
    """Names of credential attributes found embedded in the workbook and removed. A real
    finding for a programme: the client will have to rotate them."""

    tables: tuple[Table, ...] = ()

    @property
    def recognised(self) -> bool:
        return bool(self.connection_class)


@dataclass(frozen=True, slots=True)
class Extract:
    """An extract the datasource materialises. Its *schema*, never its data."""

    present: bool = False
    kind: str = ""
    """``hyper`` or ``tde``. Tableau replaced TDE with Hyper in 2018; a TDE in a live estate
    is a migration finding in itself."""

    columns: tuple[Column, ...] = ()
    refresh_schedule: str = ""
    last_refresh: str = ""
    files: tuple[str, ...] = ()
    """Names of the extract files in the archive. Names only — `archive.py` never reads them."""

    def as_properties(self) -> dict[str, Any]:
        return {
            "extract_flag": self.present,
            "extract_kind": self.kind or None,
            "extract_columns": len(self.columns),
            "refresh_schedule": self.refresh_schedule or None,
            "extract_last_refresh": self.last_refresh or None,
        }


@dataclass(frozen=True, slots=True)
class Datasource:
    """One datasource of a workbook, embedded or published."""

    name: str
    caption: str = ""
    published: bool = False
    luid: str = ""
    """Published datasources carry a LUID; embedded ones do not (§4.1.1's own note)."""

    connections: tuple[Connection, ...] = ()
    columns: tuple[Column, ...] = ()
    extract: Extract = field(default_factory=Extract)

    @property
    def kind(self) -> str:
        """§4.1.1 spells this ``type: embedded | published``.

        S2.2.2 asks for "published: true"; the ontology — which is machine-checked against
        §4.1.1's own table — carries the same fact as an enum. The specification wins (the
        backlog's own rule), so this is `type`, and `published` is the boolean it is derived
        from. Recorded in ADR 0016.
        """
        return "published" if self.published else "embedded"

    @property
    def calculated_columns(self) -> tuple[Column, ...]:
        return tuple(column for column in self.columns if column.is_calculated)


@dataclass(frozen=True, slots=True)
class WorkbookStructure:
    """What this story reads out of a workbook: its datasources, and the names of its sheets.

    Sheet and dashboard **names** are here because a Datasource has to hang off something —
    §4.1.2's `USES_DATASOURCE` runs Worksheet→Datasource, so a datasource with no worksheet is
    an orphan the platform cannot attach. Their *contents* — filters, encodings, layout,
    actions — are S2.3.2's, and none of it is read here.
    """

    datasources: tuple[Datasource, ...] = ()
    worksheets: tuple[str, ...] = ()
    dashboards: tuple[str, ...] = ()
    parameters: tuple[Column, ...] = ()
    sheet_datasources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Which datasources each worksheet uses, by name. Tableau records this on the sheet."""

    @property
    def calculated_columns(self) -> tuple[Column, ...]:
        return tuple(column for source in self.datasources for column in source.calculated_columns)

    @property
    def embedded_credentials(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    name
                    for source in self.datasources
                    for connection in source.connections
                    for name in connection.stripped_attributes
                }
            )
        )


def read_structure(xml: bytes, *, site: str = "", name: str = "") -> WorkbookStructure:
    """Read a workbook's datasources and sheet names from its XML.

    Untrusted input (§16.5): a workbook comes from a client system and may be malformed,
    enormous, or hostile. `ElementTree` with no entity resolution is used deliberately —
    Python's default parser does not expand external entities, which is the attack this file
    format is exposed to.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise AdapterError(
            f"the workbook XML for {name or 'this workbook'} could not be parsed: {exc}. "
            f"The download completed, so this is the file's content rather than the transfer.",
            retryable=False,
        ) from exc

    datasources = tuple(
        _datasource(element, site=site) for element in root.findall("./datasources/datasource")
    )
    worksheets = tuple(
        str(element.get("name", ""))
        for element in root.findall("./worksheets/worksheet")
        if element.get("name")
    )
    dashboards = tuple(
        str(element.get("name", ""))
        for element in root.findall("./dashboards/dashboard")
        if element.get("name")
    )
    sheet_sources = {
        str(element.get("name", "")): _sheet_datasources(element)
        for element in root.findall("./worksheets/worksheet")
        if element.get("name")
    }

    # Tableau models a parameter as a column on a reserved datasource. Pulled out here so the
    # Parameter nodes S2.3.2 will need are already identified, and so parameters are not
    # miscounted as ordinary fields.
    parameters = tuple(
        column
        for source in datasources
        if source.name.lower() in {"parameters", "parameters "}
        for column in source.columns
    )

    logger.debug(
        "%s: %d datasource(s), %d worksheet(s), %d dashboard(s)",
        name or "workbook",
        len(datasources),
        len(worksheets),
        len(dashboards),
    )
    return WorkbookStructure(
        datasources=datasources,
        worksheets=worksheets,
        dashboards=dashboards,
        parameters=parameters,
        sheet_datasources=sheet_sources,
    )


# ------------------------------------------------------------------------ internals


def _datasource(element: ElementTree.Element, *, site: str) -> Datasource:
    name = str(element.get("name", "") or element.get("caption", "") or "unnamed")
    #: Tableau marks a published datasource by carrying a `<repository-location>` with the
    #: datasource's id on the server. An embedded one has none — which is the distinction
    #: S2.2.2's first criterion turns on, and it is in the file rather than inferred.
    repository = element.find("./repository-location")
    published = repository is not None
    luid = str(repository.get("id", "")) if repository is not None else ""

    connections = tuple(
        _connection(child, site=site)
        for child in element.findall(".//connection")
        if _is_data_connection(child)
    )
    columns = tuple(_column(child) for child in element.findall("./column"))
    extract = _extract(element, columns)

    return Datasource(
        name=name,
        caption=str(element.get("caption", "")),
        published=published,
        luid=luid,
        connections=connections,
        columns=columns,
        extract=extract,
    )


def _is_data_connection(element: ElementTree.Element) -> bool:
    """A connection to *data*, not to Tableau's own plumbing.

    ``federated`` is the wrapper Tableau puts around a datasource's real connections, and
    ``dataengine`` is the extract. Neither is somewhere data comes from, and both would
    otherwise become Connection nodes with derived secret references for credentials that do
    not exist.
    """
    connection_class = str(element.get("class", "")).lower()
    return bool(connection_class) and connection_class not in (
        EXTRACT_ENGINE_CLASSES | {"federated", "sqlproxy"}
    )


def _connection(element: ElementTree.Element, *, site: str) -> Connection:
    # Stripped *first*, before anything is built from the attributes. Filtering on the way out
    # would leave a window in which a credential is in a live object, and a later code path
    # that read the element directly would miss the filter entirely.
    attributes, stripped = strip_secrets(dict(element.attrib))

    tableau_class = str(attributes.get("class", ""))
    mapped = CONNECTION_CLASSES.get(tableau_class.lower(), "")
    if not mapped and tableau_class:
        # Reported, not guessed. §4.1.1's enum is closed and the platform rejects a write
        # outside it, so a wrong guess would be rejected at the graph — with an error about
        # the graph rather than about the connection this actually came from.
        logger.warning(
            "connection class %r has no mapping into §4.1.1's enum; the Datasource will "
            "record it unmapped and the Modeller will have to be told what it is",
            tableau_class,
        )

    server = str(attributes.get("server", ""))
    database = str(attributes.get("dbname", "") or attributes.get("database", ""))

    return Connection(
        tableau_class=tableau_class,
        connection_class=mapped,
        server=server,
        database=database,
        schema=str(attributes.get("schema", "")),
        auth_mode=str(attributes.get("authentication", "") or attributes.get("auth", "")),
        secret_reference=(
            secret_reference(
                site=site, connection_class=tableau_class, server=server, database=database
            )
            if tableau_class
            else ""
        ),
        stripped_attributes=stripped,
        tables=_tables(element, connection_class=mapped or tableau_class),
    )


def _tables(connection: ElementTree.Element, *, connection_class: str = "") -> tuple[Table, ...]:
    """The relations a connection reads, including custom SQL.

    Custom SQL is kept byte-for-byte because §4.1.1 says so and because §6.2's live-replay
    executor reconstructs the datasource SQL "with custom SQL verbatim" — a normalised copy
    would execute differently from what the client's report actually ran.
    """
    tables: list[Table] = []
    for relation in connection.findall(".//relation"):
        kind = str(relation.get("type", "table"))
        name = str(relation.get("name", "") or relation.get("table", "") or "")
        if kind == "text":
            sql = (relation.text or "").strip()
            tables.append(
                Table(
                    name=name or "custom sql",
                    custom_sql=sql,
                    columns=_relation_columns(relation),
                    sql=parse_custom_sql(sql, connection_class=connection_class),
                )
            )
        elif kind == "join":
            # A join has no table of its own; its children are the tables and the clause
            # belongs on the edges the fragment builder writes.
            continue
        elif name:
            schema, bare = _split_table_name(name)
            tables.append(
                Table(name=bare or name, schema=schema, columns=_relation_columns(relation))
            )
    return tuple(tables)


def _split_table_name(name: str) -> tuple[str, str]:
    """``[dbo].[fx_rates]`` → ``("dbo", "fx_rates")``.

    Each part is unbracketed separately. Stripping the brackets from the whole string first
    and then splitting leaves ``dbo]`` and ``[fx_rates``, which is the kind of wrong that
    looks right in a log line and produces a Table node nothing can be matched against.
    """
    parts = [part.strip().strip("[]") for part in name.strip().split("].[")]
    if len(parts) == 1:
        schema, _, bare = parts[0].partition(".")
        return (schema, bare) if bare else ("", parts[0])
    return ".".join(parts[:-1]), parts[-1]


def _relation_columns(relation: ElementTree.Element) -> tuple[Column, ...]:
    return tuple(
        Column(
            name=str(child.get("name", "")),
            datatype=str(child.get("datatype", "string")),
            role=str(child.get("role", "dimension")),
        )
        for child in relation.findall("./columns/column")
        if child.get("name")
    )


def _column(element: ElementTree.Element) -> Column:
    calculation = element.find("./calculation")
    return Column(
        name=str(element.get("name", "")).strip("[]"),
        datatype=str(element.get("datatype", "string")),
        role=str(element.get("role", "dimension")),
        default_agg=str(element.get("default-aggregation", "")),
        hidden=str(element.get("hidden", "false")).lower() == "true",
        calculation=str(calculation.get("formula", "")) if calculation is not None else "",
    )


def _extract(element: ElementTree.Element, columns: tuple[Column, ...]) -> Extract:
    """Detect an extract and record its schema — never its data (S2.2.2, §16).

    The columns are the datasource's own: an extract materialises the datasource's schema,
    and Tableau does not repeat it inside the `<extract>` element. Recording the count and the
    columns is metadata the Modeller needs to plan a Fabric table; the rows are the client's.
    """
    extract = element.find("./extract")
    engine = element.find(".//connection[@class='dataengine']")
    if extract is None and engine is None:
        return Extract()

    kind = "hyper"
    if extract is not None:
        connection = extract.find(".//connection")
        if connection is not None and str(connection.get("dbname", "")).endswith(".tde"):
            kind = "tde"

    return Extract(
        present=True,
        kind=kind,
        columns=tuple(column for column in columns if not column.is_calculated),
        last_refresh=str(extract.get("refreshed-at", "")) if extract is not None else "",
    )


def _sheet_datasources(worksheet: ElementTree.Element) -> tuple[str, ...]:
    """Which datasources a sheet uses, by name, from its `<datasource-dependencies>`."""
    return tuple(
        str(child.get("datasource", ""))
        for child in worksheet.findall(".//datasource-dependencies")
        if child.get("datasource")
    )
