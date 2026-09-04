"""Turning a workbook's structure into graph fragments — S2.2.2's first criterion.

    "Published datasources are captured as Datasource nodes with published: true and their
    connection graph."

Nodes reach the Estate Graph through §6.1's `parse`, so this is where S2.2.2 lands. What it
emits is deliberately **partial and honestly scored**:

- `Workbook`, `Datasource`, `Connection`, `Table`, `Field` from `<datasources>`;
  `Worksheet`, `Dashboard`, `Filter`, `Action` and `Parameter` from `<worksheets>`,
  `<dashboards>` and `<actions>` (S2.3.2), with row-level security on the Workbook.
- `CalculatedField`, with its AST (S2.3.1) — §4.1.1 requires ``formula_ast`` because
  classification, pattern matching and context assembly all key off it, and a CalculatedField
  without one would be half a node.

**Parse quality is now genuine rather than structural.** A workbook whose calculations all
parse reports 1.0; one containing a construct the grammar cannot read reports less, names the
construct, and is held by §4.1.4. Before S2.3.1 every workbook was held because *no*
calculation was read — correct then, and the difference now is that the number measures the
grammar rather than the absence of one.
"""

from __future__ import annotations

import logging
from typing import Any

from astra_adapter import AssetRef, EdgeFragment, NodeFragment, ParseResult, Unrecognised

from .datasource import Connection, Datasource, WorkbookStructure
from .metadata import MetadataWorkbook

logger = logging.getLogger(__name__)


def build(
    ref: AssetRef,
    structure: WorkbookStructure,
    *,
    metadata: MetadataWorkbook | None = None,
    archive_extracts: tuple[str, ...] = (),
    revision: str = "",
    size_bytes: int = 0,
    grammar: Any = None,
    sheets: Any = None,
) -> ParseResult:
    """The graph fragment for one workbook."""
    nodes: list[NodeFragment] = []
    edges: list[EdgeFragment] = []

    site_key = f"site:{ref.site}"
    project_key = f"project:{ref.site}/{ref.project}"
    workbook_key = f"workbook:{ref.luid}"

    nodes.append(NodeFragment(site_key, "Site", {"luid": f"site-{ref.site}", "name": ref.site}))
    nodes.append(
        NodeFragment(
            project_key,
            "Project",
            {"luid": f"proj-{ref.site}-{ref.project}", "name": ref.project},
        )
    )
    edges.append(EdgeFragment("CONTAINS", site_key, project_key))

    any_extract = any(source.extract.present for source in structure.datasources) or bool(
        archive_extracts
    )
    rls = getattr(sheets, "rls", None)
    workbook_properties: dict[str, Any] = {
        "luid": ref.luid,
        "name": ref.name,
        "revision": revision or ref.revision,
        "size": size_bytes,
        "extract_flag": any_extract,
        "last_published": ref.updated_at,
    }
    if rls is not None:
        # S2.3.2's third criterion. Written whenever the adapter *looked*, true or false —
        # absent means "not examined", which is a different thing from "no restriction" and
        # the distinction matters to whoever reads the estate before a Calibration Wave.
        workbook_properties["rls"] = bool(rls.present)
        workbook_properties["rls_expression"] = rls.expression or None

    nodes.append(NodeFragment(workbook_key, "Workbook", workbook_properties))
    edges.append(EdgeFragment("CONTAINS", project_key, workbook_key))

    sheet_keys = _sheets(nodes, edges, structure, sheets, workbook_key=workbook_key)
    _dashboards(nodes, edges, structure, sheets, workbook_key=workbook_key, sheet_keys=sheet_keys)
    parameter_keys = _parameters(nodes, sheets, workbook_key=workbook_key)
    _actions(nodes, sheets, workbook_key=workbook_key)

    published_luids = _published_luids(metadata)
    refreshes = _refresh_schedules(metadata)

    parsed: dict[str, Any] = {}
    for index, source in enumerate(structure.datasources):
        _datasource(
            nodes,
            edges,
            source,
            index=index,
            workbook_key=workbook_key,
            sheet_keys=sheet_keys,
            structure=structure,
            published_luids=published_luids,
            refreshes=refreshes,
            grammar=grammar,
            parsed=parsed,
            parameter_keys=parameter_keys,
        )

    unrecognised, total, recognised = _constructs(structure, workbook_key, parsed, sheets)

    return ParseResult(
        nodes=tuple(nodes),
        edges=tuple(edges),
        parse_quality=(recognised / total) if total else 1.0,
        unrecognised=tuple(unrecognised),
        constructs_total=total,
        constructs_recognised=recognised,
    )


def _datasource(
    nodes: list[NodeFragment],
    edges: list[EdgeFragment],
    source: Datasource,
    *,
    index: int,
    workbook_key: str,
    sheet_keys: dict[str, str],
    structure: WorkbookStructure,
    published_luids: dict[str, str],
    refreshes: dict[str, str],
    grammar: Any = None,
    parsed: dict[str, Any] | None = None,
    parameter_keys: dict[str, str] | None = None,
) -> None:
    key = f"{workbook_key}/datasource:{index}"

    # A published datasource's LUID comes from the file where Tableau wrote it, and from the
    # Metadata API where it did not — the two disagree often enough that preferring the file
    # and falling back is worth doing explicitly.
    luid = source.luid or published_luids.get(source.name, "")
    schedule = source.extract.refresh_schedule or refreshes.get(source.name, "")
    extract = source.extract

    nodes.append(
        NodeFragment(
            key,
            "Datasource",
            {
                "name": source.caption or source.name,
                # §4.1.1 spells S2.2.2's "published: true" as this enum, and the ontology is
                # machine-checked against that table. See ADR 0016.
                "type": source.kind,
                "luid": luid or None,
                # S2.2.2's third criterion: a *name*, never a credential. One per connection;
                # the first is on the node and the rest are on the Connection nodes below,
                # because §4.1.1 gives the Datasource one connection_ref and a datasource can
                # federate several.
                "connection_ref": _connection_ref(source),
                # The datasource's *own* extract, not the workbook's. A .hyper in the
                # archive belongs to one datasource; folding the archive fact in here told
                # the Modeller that every datasource had an extract — including the
                # Parameters pseudo-datasource, which has no data at all.
                "extract_flag": extract.present,
                "refresh_schedule": schedule or None,
            },
        )
    )

    for sheet in structure.worksheets:
        used = structure.sheet_datasources.get(sheet, ())
        # A sheet that names no datasource uses all of them — Tableau omits the dependency
        # when there is only one, and a fragment that attached nothing would orphan it.
        if not used or source.name in used:
            edges.append(EdgeFragment("USES_DATASOURCE", sheet_keys[sheet], key))

    for position, connection in enumerate(source.connections):
        _connection(nodes, edges, connection, key=f"{key}/connection:{position}", parent=key)

    # The extract's schema, on the datasource. Columns rather than rows: what Tableau
    # materialised, not what it materialised *from*.
    field_keys: dict[str, str] = {}
    for position, column in enumerate(extract.columns if extract.present else source.columns):
        if column.is_calculated:
            continue
        field_key = f"{key}/field:{position}"
        field_keys[column.name] = field_key
        nodes.append(
            NodeFragment(
                field_key,
                "Field",
                {
                    "name": column.name,
                    "datatype": column.datatype,
                    "role": "measure" if column.role == "measure" else "dimension",
                    "default_agg": column.default_agg or None,
                    "hidden": column.hidden,
                },
            )
        )
        edges.append(EdgeFragment("HAS_FIELD", key, field_key))

    _calculated_fields(
        nodes,
        edges,
        source,
        key=key,
        field_keys=field_keys,
        grammar=grammar,
        parsed=parsed,
        parameter_keys=parameter_keys or {},
    )


def _calculated_fields(
    nodes: list[NodeFragment],
    edges: list[EdgeFragment],
    source: Datasource,
    *,
    key: str,
    field_keys: dict[str, str],
    grammar: Any,
    parsed: dict[str, Any] | None,
    parameter_keys: dict[str, str],
) -> None:
    """CalculatedField nodes, with the AST §4.1.1 requires (S2.3.1).

    ``formula_ast`` is required by the ontology because §9.1's classification, §9.3's pattern
    matching and S1.3.1's context assembly all key off it — a CalculatedField carrying only
    its text would be a node every one of them had to re-parse.

    A calculation the grammar could not fully read still gets a node: the formula is there
    verbatim, the AST contains UNKNOWN where the gap is, and the *workbook* is held by parse
    quality. Omitting the node would lose the field from the estate entirely, which is a
    worse answer than an honest partial one.
    """
    if grammar is None:
        return

    calculated = source.calculated_columns
    calc_keys = {
        column.name: f"{key}/calculation:{position}" for position, column in enumerate(calculated)
    }

    for column in calculated:
        ast = grammar.parse(column.calculation)
        if parsed is not None:
            parsed[f"{source.name}/{column.name}"] = ast

        depends = _dependencies(ast)
        calc_key = calc_keys[column.name]
        nodes.append(
            NodeFragment(
                calc_key,
                "CalculatedField",
                {
                    "name": column.name,
                    "formula": column.calculation,
                    "formula_ast": _ast_json(ast.root),
                    "lod_type": _lod_type(ast),
                    "table_calc_flag": _has_table_calc(ast),
                    "depends_on": sorted(depends),
                    # Not `class`: §4.1.1 notes it is "set by the Transpiler, absent at
                    # harvest". An adapter guessing it would be guessing E5's answer.
                },
            )
        )
        edges.append(EdgeFragment("HAS_FIELD", key, calc_key))

        # DEPENDS_ON is materialised only where the target exists in this fragment. A
        # reference to something the workbook does not define — a parameter (S2.3.2), a field
        # on another datasource — stays in `depends_on` and becomes an edge when that node
        # does; writing an edge to a key nothing defines would fail at the graph.
        for name in sorted(depends):
            # Parameters are in the map now that S2.3.2 reads them — §4.1.2's
            # DEPENDS_ON(CalculatedField → Parameter). Before it did, a calculation using a
            # parameter had the name in `depends_on` and no edge, which made "what breaks if
            # this parameter changes" unanswerable by traversal.
            target = calc_keys.get(name) or field_keys.get(name) or parameter_keys.get(name)
            if target and target != calc_key:
                edges.append(EdgeFragment("DEPENDS_ON", calc_key, target))


def _sheets(
    nodes: list[NodeFragment],
    edges: list[EdgeFragment],
    structure: WorkbookStructure,
    sheets: Any,
    *,
    workbook_key: str,
) -> dict[str, str]:
    """Worksheet nodes, with their shelves, marks and filters (S2.3.2).

    Falls back to names alone when no sheet structure was supplied — the caller can ask for
    the datasource graph without the visual specification, and a Worksheet with a name is
    still what USES_DATASOURCE hangs off.
    """
    parsed_sheets = {sheet.name: sheet for sheet in getattr(sheets, "sheets", ())}
    sheet_keys: dict[str, str] = {}

    for name in structure.worksheets:
        key = f"{workbook_key}/worksheet:{name}"
        sheet_keys[name] = key
        sheet = parsed_sheets.get(name)
        nodes.append(
            NodeFragment(key, "Worksheet", sheet.as_properties() if sheet else {"name": name})
        )
        edges.append(EdgeFragment("CONTAINS", workbook_key, key))

        for position, item in enumerate(sheet.filters if sheet else ()):
            filter_key = f"{key}/filter:{position}"
            nodes.append(NodeFragment(filter_key, "Filter", item.as_properties()))
            edges.append(EdgeFragment("FILTERED_BY", key, filter_key))

    return sheet_keys


def _dashboards(
    nodes: list[NodeFragment],
    edges: list[EdgeFragment],
    structure: WorkbookStructure,
    sheets: Any,
    *,
    workbook_key: str,
    sheet_keys: dict[str, str],
) -> None:
    """Dashboard nodes with their zone tree (§4.1.1: "Layout retained for Compositor")."""
    parsed_dashboards = {item.name: item for item in getattr(sheets, "dashboards", ())}

    for name in structure.dashboards:
        key = f"{workbook_key}/dashboard:{name}"
        dashboard = parsed_dashboards.get(name)
        nodes.append(
            NodeFragment(
                key, "Dashboard", dashboard.as_properties() if dashboard else {"name": name}
            )
        )
        edges.append(EdgeFragment("CONTAINS", workbook_key, key))


def _parameters(nodes: list[NodeFragment], sheets: Any, *, workbook_key: str) -> dict[str, str]:
    """Parameter nodes, with the domain that bounds the Arbiter's enumeration (§10.1).

    No containing edge: §4.1.2 gives Parameter only ``DEPENDS_ON(CalculatedField →
    Parameter)``, and the platform's fixture adapter emits them the same way. An edge the
    ontology does not permit would be rejected at the graph.
    """
    keys: dict[str, str] = {}
    for parameter in getattr(sheets, "parameters", ()):
        key = f"{workbook_key}/parameter:{parameter.name}"
        keys[parameter.name] = key
        nodes.append(NodeFragment(key, "Parameter", parameter.as_properties()))
    return keys


def _actions(nodes: list[NodeFragment], sheets: Any, *, workbook_key: str) -> None:
    """Action nodes (§4.1.1: "Interactivity mapping").

    Also without a containing edge: §4.1.1 models the linkage as ``source_sheets`` and
    ``target_sheets`` properties rather than as edges, and following the specification here
    keeps the Tableau adapter and the platform's fixture producing the same shape.
    """
    for position, action in enumerate(getattr(sheets, "actions", ())):
        nodes.append(
            NodeFragment(f"{workbook_key}/action:{position}", "Action", action.as_properties())
        )


def _connection(
    nodes: list[NodeFragment],
    edges: list[EdgeFragment],
    connection: Connection,
    *,
    key: str,
    parent: str,
) -> None:
    if not connection.recognised:
        # §4.1.1's enum is closed and the platform rejects a write outside it. Emitting an
        # unmapped class would fail at the graph with an error about the graph; skipping it
        # and saying so leaves a Datasource whose missing connection is visible and
        # attributable.
        logger.warning(
            "connection class %r is outside §4.1.1's enum; no Connection node was emitted "
            "for it, and the Datasource records the gap",
            connection.tableau_class,
        )
        return

    nodes.append(
        NodeFragment(
            key,
            "Connection",
            {
                "class": connection.connection_class,
                "server": connection.server or None,
                "db": connection.database or None,
                "schema": connection.schema or None,
                # How it authenticates, never what with. An embedded credential that was
                # stripped shows here as the mode it implied, so a programme can see that the
                # connection needs a secret provisioning.
                "auth_mode": connection.auth_mode
                or ("embedded_credential" if connection.stripped_attributes else None),
            },
        )
    )
    edges.append(EdgeFragment("CONNECTS_TO", parent, key))

    for position, table in enumerate(connection.tables):
        table_key = f"{key}/table:{position}"
        properties: dict[str, Any] = {
            "name": table.name,
            "schema": table.schema or None,
            # Byte-for-byte (§4.1.1). §6.2's live-replay executor runs this verbatim,
            # and a normalised copy would execute differently from the client's report.
            "custom_sql": table.custom_sql or None,
        }
        if table.sql is not None:
            properties.update(table.sql.as_properties())
        nodes.append(NodeFragment(table_key, "Table", properties))
        edges.append(
            EdgeFragment(
                "CONNECTS_TO",
                key,
                table_key,
                properties={"join_clause": table.join_clause} if table.join_clause else {},
            )
        )
        for position_column, column in enumerate(table.columns):
            field_key = f"{table_key}/field:{position_column}"
            nodes.append(
                NodeFragment(
                    field_key,
                    "Field",
                    {
                        "name": column.name,
                        "datatype": column.datatype,
                        "role": "measure" if column.role == "measure" else "dimension",
                        "hidden": column.hidden,
                    },
                )
            )
            edges.append(EdgeFragment("HAS_FIELD", table_key, field_key))

        _referenced_tables(nodes, edges, table, connection_key=key, position=position)


def _referenced_tables(
    nodes: list[NodeFragment],
    edges: list[EdgeFragment],
    table: Any,
    *,
    connection_key: str,
    position: int,
) -> None:
    """The warehouse tables a custom-SQL relation reads (S2.3.3).

    Emitted as Table nodes under the same Connection, because that is what they are: tables
    this connection reads. The custom-SQL Table keeps their names in ``custom_sql_tables`` as
    well, so the linkage is readable without a traversal — §4.1.2 has no Table→Table edge, and
    inventing one for this would be an ontology change for one adapter's convenience.

    Without this, the Modeller sees a Table called "Custom SQL Query" and plans a Fabric model
    around a hole — confidently, because nothing in the estate says a hole is there. That is
    the surprise the story exists to remove.
    """
    parsed = getattr(table, "sql", None)
    if parsed is None or not parsed.parsed:
        return

    for index, reference in enumerate(parsed.tables):
        key = f"{connection_key}/table:{position}/source:{index}"
        nodes.append(
            NodeFragment(
                key,
                "Table",
                {
                    "name": reference.name,
                    "schema": reference.schema or None,
                    # No custom_sql: this is a real table the SQL reads, not the relation.
                    "custom_sql": None,
                },
            )
        )
        edges.append(EdgeFragment("CONNECTS_TO", connection_key, key))


def _constructs(
    structure: WorkbookStructure,
    workbook_key: str,
    parsed: dict[str, Any] | None = None,
    sheets: Any = None,
) -> tuple[list[Unrecognised], int, int]:
    """Parse quality, counted over what the adapter attempted (§4.1.4).

    Recognised: the structural elements, plus every *construct inside* each calculation the
    grammar read. Unrecognised: the constructs it could not, retained verbatim (§6.2) with the
    field they came from.

    Counting a calculation as one construct would make a workbook with one unreadable
    function in a fifty-node formula score the same as one whose calculation is entirely
    unreadable — and §4.1.4's threshold is meant to distinguish them.
    """
    recognised = (
        len(structure.worksheets)
        + len(structure.dashboards)
        # S2.3.2's own constructs: a filter, an action or a parameter the adapter read is a
        # construct it read, and leaving them out would let a workbook full of filters score
        # on its datasources alone.
        + sum(len(sheet.filters) for sheet in getattr(sheets, "sheets", ()))
        + len(getattr(sheets, "actions", ()))
        + len(getattr(sheets, "parameters", ()))
        + len(structure.datasources)
        + sum(len(source.connections) for source in structure.datasources)
        + sum(
            len(connection.tables)
            for source in structure.datasources
            for connection in source.connections
        )
        + sum(
            1
            for source in structure.datasources
            for column in source.columns
            if not column.is_calculated
        )
    )

    unrecognised: list[Unrecognised] = []
    calc_total = 0
    calc_recognised = 0

    # S2.3.3's second criterion. A custom-SQL relation is a construct like any other: read, it
    # counts as recognised; unread, it is retained verbatim and holds the workbook. Silence
    # here would let a workbook whose entire datasource is an unreadable query score 1.0.
    for source in structure.datasources:
        for connection in source.connections:
            for table in connection.tables:
                parsed_sql = getattr(table, "sql", None)
                if parsed_sql is None:
                    continue
                calc_total += 1
                if parsed_sql.parsed:
                    calc_recognised += 1
                    continue
                unrecognised.append(
                    Unrecognised(
                        construct=parsed_sql.sql,
                        location=(
                            f"{workbook_key}/datasource:{source.name}/connection:"
                            f"{connection.connection_class}/table:{table.name}"
                        ),
                        detail=(
                            f"custom SQL could not be read as {parsed_sql.dialect}: "
                            f"{parsed_sql.error}. It is stored verbatim on the Table node; the "
                            f"Modeller will have to be told what it reads."
                        ),
                    )
                )

    for source in structure.datasources:
        for column in source.calculated_columns:
            ast = (parsed or {}).get(f"{source.name}/{column.name}")
            if ast is None:
                # No grammar was supplied — the caller asked for structure only. The
                # calculation is then one unread construct, which is what it is.
                unrecognised.append(
                    Unrecognised(
                        construct=column.calculation,
                        location=f"{workbook_key}/datasource:{source.name}/column:{column.name}",
                        detail="no calculation grammar was supplied to this parse",
                    )
                )
                calc_total += 1
                continue

            calc_total += ast.total
            calc_recognised += ast.recognised
            for node in ast.root.walk():
                if node.kind.value != "UNKNOWN" and not _is_unrecognised_call(node):
                    continue
                unrecognised.append(
                    Unrecognised(
                        construct=_construct_text(node, column.calculation),
                        location=(
                            f"{workbook_key}/datasource:{source.name}/column:{column.name}"
                            + (f"@{node.span[0]}:{node.span[1]}" if node.span else "")
                        ),
                        detail=(
                            f"outside grammar {ast.grammar_version}; retained verbatim and "
                            f"flagged (S2.3.1). The Parse Quality Queue is where it is worked "
                            f"down."
                        ),
                    )
                )

    return unrecognised, recognised + calc_total, recognised + calc_recognised


def _is_unrecognised_call(node: Any) -> bool:
    """A function the grammar parsed but the registry does not know.

    Its structure is real and kept — the arguments are readable and so are the fields it
    depends on — but it counts against parse quality, because the platform cannot say what it
    means and the Transpiler cannot emit it.
    """
    return ("recognised", "false") in tuple(node.detail)


def _construct_text(node: Any, expression: str) -> str:
    """The source text of the construct, for the Parse Quality Queue.

    The span is what makes this exact rather than approximate (S2.3.1's second criterion):
    an engineer sees ``RAWSQL_INT('select 1')`` and not "somewhere in this 40-line formula".
    """
    if node.span is not None:
        start, end = node.span
        return expression[start:end]
    return str(node.value) if node.value is not None else node.name


def _ast_json(node: Any) -> dict[str, Any]:
    """The AST as §4.1.1's ``formula_ast`` JSON.

    Encoded with the SDK's own wire codec rather than a second serialiser, so what the graph
    stores is exactly what crosses the adapter RPC — one shape, one place it can go wrong.
    """
    from astra_adapter.rpc import wire

    return wire.encode_calc_node(node)


def _dependencies(ast: Any) -> set[str]:
    """Field and parameter names the calculation references (§4.1.1's ``depends_on``)."""
    return {node.name for node in ast.root.walk() if node.kind.value == "REFERENCE" and node.name}


def _lod_type(ast: Any) -> str | None:
    """§4.1.1: absent unless the expression is a level-of-detail expression."""
    for node in ast.root.walk():
        if node.kind.value == "AGGREGATE" and node.name in {"FIXED", "INCLUDE", "EXCLUDE"}:
            return str(node.name)
    return None


def _has_table_calc(ast: Any) -> bool:
    return any(node.kind.value == "WINDOW" for node in ast.root.walk())


def _connection_ref(source: Datasource) -> str | None:
    """§4.1.1 gives a Datasource one ``connection_ref``; a federated one has several.

    The first is on the node so the common case is directly readable, and every connection
    keeps its own on the Connection node. A datasource with no recognised connection has
    none, which is a fact rather than an empty string.
    """
    for connection in source.connections:
        if connection.secret_reference:
            return connection.secret_reference
    return None


def _published_luids(metadata: MetadataWorkbook | None) -> dict[str, str]:
    if metadata is None:
        return {}
    return dict(metadata.published_datasource_luids)


def _refresh_schedules(metadata: MetadataWorkbook | None) -> dict[str, str]:
    if metadata is None:
        return {}
    return dict(metadata.extract_refreshes)


def secret_references(structure: WorkbookStructure) -> dict[str, Any]:
    """Every Key Vault secret this workbook's connections would need.

    Reported so an operator has a list to provision against, rather than discovering a
    missing secret when the executor first tries to run a parity case.
    """
    references: dict[str, Any] = {}
    for source in structure.datasources:
        for connection in source.connections:
            if connection.secret_reference:
                references[connection.secret_reference] = {
                    "class": connection.connection_class or connection.tableau_class,
                    "server": connection.server,
                    "database": connection.database,
                    "auth_mode": connection.auth_mode,
                    "embedded_credential_stripped": bool(connection.stripped_attributes),
                }
    return references
