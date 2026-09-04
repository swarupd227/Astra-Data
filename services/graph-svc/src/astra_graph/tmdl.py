"""TMDL emission — story S4.3.1.

    "Emission is deterministic from the approved design version; the same version always
    produces byte-identical TMDL."

**A pure function of the frozen design document, and nothing else.** ``emit_tmdl`` reads
only what ``modeller.read_design_document`` returns for an ``APPROVED`` family — no graph
read, no clock, no random id — so "the same version always produces byte-identical TMDL" is
true by construction rather than by care: there is no second input that could vary between
two calls with the same document. ``design_generated_at`` and ``version`` are the two keys
``model_lifecycle.hashable_document`` already excludes from the version hash for the same
reason (they describe *when*, not *what*); this module simply never reads them either.

**The folder shape follows §7.1's own table**: ``model.tmdl``, ``tables/<name>.tmdl``,
``relationships.tmdl``, ``roles/<name>.tmdl``, ``expressions.tmdl``. Only files with real
content are emitted — an empty ``roles/`` folder because no family has RLS, or an empty
``expressions.tmdl`` because nothing populates it yet, would be ceremony, not TMDL.

**Column-level detail is a disclosed gap.** ``design_document["tables"]`` carries the shape
S4.1.1's Modeller produces — name, schema, mode, row estimate — not a per-table column
list; no story has threaded ``Field``/``MAPS_TO`` detail into the frozen design yet. Each
table's own TMDL says so in a comment rather than fabricating columns, the same honesty the
console's Measures tab already gives "class"/"pattern" (both "pending" until the
Transpiler, E5).

**Measures carry no DAX.** ``candidate_measures`` names a measure and the source
calculations it was deduplicated from (``source_calc_refs``, ids only) — real DAX requires
the Transpiler (E5, not built). Each measure emits as a syntactically valid DAX expression —
a string literal — naming what it stands in for, so the bundle is a real, loadable TMDL
artefact today and a slot the Transpiler fills in later, not a broken placeholder.

**Every measure carries a ``displayFolder`` named for its family** (§12.3, story S4.3.2:
"measures in display folders by source family") — ``document["family_name"]``, threaded in
by the caller (``build.py`` already reads it; ``emit_tmdl`` itself still touches nothing
this build's own version does not already determine) alongside ``family_id`` as a fallback.
Assigning the folder is unconditional, so it cannot itself fail a conformance check; a name
colliding with another measure inside that one folder is what actually can, and is what
``conformance_rules.check_measures_display_folder`` checks for.

**Relationships parse ``join_clause``** (e.g. ``"positions.desk_id = desk.id"``, a plain
string the Modeller already produces) for the two column names TMDL's own ``relationship``
block needs; an unparseable or absent clause falls back to each table's own ``id`` column,
disclosed with a comment rather than guessed at silently.

**RLS roles carry no table assignment.** ``RlsRoleCandidate`` (S4.1.1) names a role and its
filter expression but not which table it filters — real future scope (the RLS scaffold is
disclosed as a scaffold, not a finished assignment, per ADR 0028). Each role's TMDL states
the expression as an annotation and says so.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from astra_adapter import TmdlBundle

_JOIN_CLAUSE_RE = re.compile(
    r"^\s*(?P<from_table>[\w.]+)\s*\.\s*(?P<from_col>\w+)\s*=\s*"
    r"(?P<to_table>[\w.]+)\s*\.\s*(?P<to_col>\w+)\s*$"
)


def emit_tmdl(document: Mapping[str, Any]) -> TmdlBundle:
    """The design's TMDL, as a ``{relative path: bytes}`` bundle."""
    tables = sorted(document.get("tables") or [], key=lambda t: str(t.get("name") or t.get("id")))
    relationships = sorted(
        document.get("relationships") or [],
        key=lambda r: (str(r.get("from_table")), str(r.get("to_table"))),
    )
    measures = sorted(document.get("candidate_measures") or [], key=lambda m: str(m.get("name")))
    roles = sorted(document.get("rls_role_detail") or [], key=lambda r: str(r.get("name")))

    files: dict[str, bytes] = {"model.tmdl": _model_file(document, tables).encode("utf-8")}

    for table in tables:
        name = str(table.get("name"))
        files[f"tables/{safe_name(name)}.tmdl"] = _table_file(table).encode("utf-8")

    if measures:
        family_name = str(document.get("family_name") or document.get("family_id") or "Model")
        files["tables/_Measures.tmdl"] = _measures_file(measures, family_name).encode("utf-8")

    if relationships:
        by_id = {str(t.get("id")): str(t.get("name")) for t in tables}
        files["relationships.tmdl"] = _relationships_file(relationships, by_id).encode("utf-8")

    for role in roles:
        name = str(role.get("name"))
        files[f"roles/{safe_name(name)}.tmdl"] = _role_file(role).encode("utf-8")

    return TmdlBundle(files=files)


def safe_name(name: str) -> str:
    """A filesystem- and Git-safe stand-in for a table/role name — real TMDL tooling
    escapes the same way; this is not a general-purpose slugifier, just enough for the
    names this platform's own harvesting produces."""
    cleaned = re.sub(r"[^\w.\- ]", "_", name).strip() or "unnamed"
    return cleaned


def _model_file(document: Mapping[str, Any], tables: list[dict[str, Any]]) -> str:
    lines = [
        f"model '{document.get('semantic_model_id')}'",
        "\tculture: en-US",
        "",
        f"\tannotation astra_family_id = {document.get('family_id')}",
        f"\tannotation astra_semantic_model_id = {document.get('semantic_model_id')}",
        f"\tannotation astra_grain_statement = {_quote(document.get('grain_statement') or '')}",
        "",
    ]
    for table in tables:
        lines.append(f"ref table {_quote(str(table.get('name')))}")
    return "\n".join(lines) + "\n"


def _table_file(table: Mapping[str, Any]) -> str:
    name = str(table.get("name"))
    schema = table.get("schema")
    mode = table.get("mode") or "directquery"
    source_refs = table.get("source_table_refs") or []
    qualified = f"{schema}.{name}" if schema else name

    lines = [
        f"table {_quote(name)}",
        f"\tlineageTag: {table.get('id')}",
        "",
        f"\tpartition {_quote(name)} = m",
        f"\t\tmode: {mode}",
        "\t\tsource =",
        "\t\t\tlet",
        f"\t\t\t\t// {table.get('mode_reason') or 'no storage-mode reason recorded'}",
        f'\t\t\t\tSource = Value.NativeQuery("{qualified}"'
        + (", custom_sql = true" if table.get("custom_sql") else "")
        + ")",
        "\t\t\tin",
        "\t\t\t\tSource",
        "",
        "\t/* column-level detail requires source Field data not yet captured on the design",
        "\t   document (S4.1.1's own scope) — a disclosed gap, not a fabricated schema. */",
    ]
    if len(source_refs) > 1:
        lines.append(f"\t// merged from {len(source_refs)} source tables via custom SQL")
    return "\n".join(lines) + "\n"


def _measures_file(measures: list[dict[str, Any]], family_name: str) -> str:
    """Every measure lands in one display folder, named for the source family (§12.3,
    story S4.3.2: "measures in display folders by source family") — assigning it is
    unconditional; a name colliding with another measure's inside that one folder is what
    `conformance_rules.check_measures_display_folder` actually checks for."""
    lines = ["table '_Measures'", "\tlineageTag: astra-measures", "\tisHidden", ""]
    for measure in measures:
        name = str(measure.get("name"))
        refs = ", ".join(str(r) for r in (measure.get("source_calc_refs") or []))
        stand_in = (
            f"NOT YET TRANSPILED — source calculations: {refs or '(none)'} "
            f"(dedup: {measure.get('dedup_decision') or 'n/a'})"
        )
        lines += [
            f"\tmeasure {_quote(name)} = {_quote(stand_in)}",
            '\t\tformatString: "General"',
            f"\t\tdisplayFolder: {_quote(family_name)}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def _relationships_file(relationships: list[dict[str, Any]], table_name_of: Mapping[str, str]) -> str:
    blocks: list[str] = []
    for rel in relationships:
        from_id = str(rel.get("from_table"))
        to_id = str(rel.get("to_table"))
        from_table = table_name_of.get(from_id, from_id)
        to_table = table_name_of.get(to_id, to_id)
        from_col, to_col, parsed = _parse_join_clause(rel.get("join_clause"), from_table, to_table)
        blocks.append(
            "\n".join(
                [
                    f"relationship {from_id}-{to_id}",
                    f"\tfromColumn: {from_table}.{from_col}",
                    f"\ttoColumn: {to_table}.{to_col}",
                    f"\t// cardinality: {rel.get('cardinality') or 'ambiguous'} ({rel.get('confidence')})",
                ]
                + ([] if parsed else ["\t// join_clause could not be parsed; column names are placeholders"])
            )
        )
    return "\n\n".join(blocks) + "\n"


def _parse_join_clause(
    join_clause: str | None, from_table: str, to_table: str
) -> tuple[str, str, bool]:
    """``join_clause`` is free text the Modeller wrote from whichever side of the join it
    walked first — its own left/right order carries no relationship to which table this
    ``RelationshipCandidate`` calls "from" or "to". The two sides are matched by table
    *name* instead of position, so ``"positions.desk_id = desk.id"`` resolves correctly
    whichever of ``from_table``/``to_table`` "positions" turns out to be."""
    if join_clause:
        match = _JOIN_CLAUSE_RE.match(join_clause)
        if match:
            left_table = match.group("from_table").rsplit(".", 1)[-1]
            right_table = match.group("to_table").rsplit(".", 1)[-1]
            if left_table == from_table and right_table == to_table:
                return match.group("from_col"), match.group("to_col"), True
            if left_table == to_table and right_table == from_table:
                return match.group("to_col"), match.group("from_col"), True
    return "id", "id", False


def _role_file(role: Mapping[str, Any]) -> str:
    name = str(role.get("name"))
    expression = str(role.get("expression") or "")
    source_workbooks = role.get("source_workbook_ids") or []
    lines = [
        f"role {_quote(name)}",
        "\tmodelPermission: read",
        "",
        f"\tannotation astra_source_expression = {_quote(expression)}",
        f"\tannotation astra_source_workbook_count = {len(source_workbooks)}",
        "\t// which table(s) this role filters is not yet assigned — a disclosed gap, see",
        "\t// RlsRoleCandidate (modeller.py); real future scope, not guessed at here.",
    ]
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


__all__ = ["emit_tmdl", "safe_name"]
