"""Edge types of the Estate Graph — a transcription of specification §4.1.2.

Each edge declares the endpoint pairs it permits. The specification writes these
compactly: a chain (``Site→Project→Workbook→Dashboard/Worksheet``) is every consecutive
pair, and an alternation (``Table/Datasource→Field``) is every combination. They are
expanded here so the endpoint check is a lookup rather than a parse.

The ``written_by`` field records the component the specification names as the writer. It
is documentation at this story: enforcing *which* principal may write *which* edge needs
agent identity, which arrives with E11.
"""

from __future__ import annotations

from .properties import PropertyType as T
from .types import ANY_LABEL, EdgeType, SpecDeviation, _p

EDGE_TYPES: tuple[EdgeType, ...] = (
    EdgeType(
        label="CONTAINS",
        pairs=(
            ("Site", "Project"),
            ("Project", "Project"),
            ("Project", "Workbook"),
            ("Workbook", "Dashboard"),
            ("Workbook", "Worksheet"),
        ),
        written_by="Harvester",
        spec_ref="§4.1.2",
        note="Project→Project is the nested-project case; §4.1.1 states the Project "
             "hierarchy is preserved.",
    ),
    EdgeType(
        label="USES_DATASOURCE",
        pairs=(("Worksheet", "Datasource"),),
        written_by="Harvester",
        spec_ref="§4.1.2",
    ),
    EdgeType(
        label="CONNECTS_TO",
        pairs=(("Datasource", "Connection"), ("Connection", "Table")),
        written_by="Harvester",
        spec_ref="§4.1.2",
        properties=(
            _p("join_clause", T.TEXT,
               note="Carried on the Connection→Table edges, per the specification."),
        ),
    ),
    EdgeType(
        label="HAS_FIELD",
        pairs=(("Table", "Field"), ("Datasource", "Field"), ("Datasource", "CalculatedField")),
        written_by="Harvester",
        spec_ref="§4.1.2",
        note="Datasource→CalculatedField: see EDGE_SPEC_DEVIATIONS below. Not new behaviour "
             "— the adapter has written this edge since S2.3.1; the ontology just never "
             "permitted it, so it could only be written by a harvest nothing had yet run "
             "against the real ontology.",
    ),
    EdgeType(
        label="DEPENDS_ON",
        pairs=(
            ("CalculatedField", "Field"),
            ("CalculatedField", "CalculatedField"),
            ("CalculatedField", "Parameter"),
        ),
        written_by="Harvester (from AST)",
        spec_ref="§4.1.2",
        properties=(
            _p("position_in_ast", T.STRING,
               note="Path to the referencing node within the AST, so the transitive closure "
                    "the Transpiler assembles is ordered and reproducible (spec §4.1.3)."),
        ),
    ),
    EdgeType(
        label="ENCODES",
        pairs=(("Worksheet", "Field"), ("Worksheet", "CalculatedField")),
        written_by="Harvester",
        spec_ref="§4.1.2",
        properties=(
            _p("shelf", T.STRING, required=True,
               note="Which shelf the field sits on. Required: parity case grain is derived "
                    "from shelf placement (spec §10.1), so an unplaced encoding is unusable."),
            _p("aggregation", T.STRING),
            _p("sort", T.JSON),
        ),
    ),
    EdgeType(
        label="FILTERED_BY",
        pairs=(("Worksheet", "Filter"), ("Dashboard", "Filter")),
        written_by="Harvester",
        spec_ref="§4.1.2",
    ),
    EdgeType(
        label="OWNED_BY",
        pairs=(("Workbook", "User"),),
        written_by="Harvester",
        spec_ref="§4.1.2",
        note="Ownership routes gate requests to a named person (spec §15.1).",
    ),
    EdgeType(
        label="VIEWED_BY",
        pairs=(("Workbook", "User"),),
        written_by="Harvester",
        spec_ref="§4.1.2",
        properties=(
            _p("views_90d", T.INT),
            _p("last_view", T.TIMESTAMP),
        ),
    ),
    EdgeType(
        label="SHARES_LINEAGE",
        pairs=(("Workbook", "Workbook"),),
        written_by="Cartographer (derived)",
        spec_ref="§4.1.2",
        note="The specification writes this as undirected (Workbook↔Workbook). It is stored "
             "as a directed edge; readers treat it as symmetric.",
        properties=(
            _p("jaccard_tables", T.FLOAT, required=True),
            _p("jaccard_fields", T.FLOAT, required=True),
            _p("shared_calc_count", T.INT, required=True,
               note="All three are required: the similarity score in §12.1 is a weighted sum "
                    "of exactly these, so a partial edge cannot be scored."),
        ),
    ),
    EdgeType(
        label="IN_FAMILY",
        pairs=(("Workbook", "ModelFamily"),),
        written_by="Cartographer",
        spec_ref="§4.1.2",
        properties=(
            _p("confidence", T.FLOAT, required=True,
               note="Clustering confidence; the Foundry Workbench orders review by it."),
        ),
    ),
    EdgeType(
        label="IN_TRAIN",
        pairs=(("Workbook", "ReleaseTrain"),),
        written_by="Cartographer / Programme Manager",
        spec_ref="§4.1.2",
        properties=(
            _p("sequence", T.INT, required=True,
               note="Position within the train; a train is defined as ordered (spec §3.3)."),
            _p("state", T.STRING,
               note="The MU's §3.2 state, for the Wave Board's kanban columns (story "
                    "S3.2.2). A string, not a closed enum: migration_units.py's own "
                    "MU_STATES is deliberately held as strings rather than an enum "
                    "because 'the state machine belongs to the control plane, and this "
                    "service should not be the place it is defined' — this property "
                    "keeps that boundary rather than quietly becoming the enforcement "
                    "point for a state machine graph-svc was never meant to own."),
            _p("wip_override_reason", T.STRING,
               note="Set when this member was moved into a train that was already at or "
                    "over its configured WIP limit (story S3.2.2); the reason a Programme "
                    "Manager gave for proceeding anyway."),
        ),
    ),
    EdgeType(
        label="MAPS_TO",
        pairs=(
            ("Field", "ModelTable"),
            ("CalculatedField", "Measure"),
            ("Worksheet", "Visual"),
        ),
        written_by="Modeller / Transpiler / Compositor",
        spec_ref="§4.1.2",
        note="The source-to-target correspondence the Proof Engine normalises result-set "
             "column names through (spec §10.3).",
        properties=(
            _p("class", T.ENUM, enum=("C1", "C2", "C3", "C4")),
            _p("pattern_ref", T.STRING),
            _p("target_column", T.STRING,
               note="Column within the ModelTable. The specification writes this endpoint as "
                    "'Field→ModelTable.column'; the column is carried on the edge because "
                    "columns are not nodes in this ontology."),
        ),
    ),
    EdgeType(
        label="PROVED_BY",
        pairs=(("ReportDefinition", "ParityRun"),),
        written_by="Arbiter",
        spec_ref="§4.1.2",
        properties=(
            _p("charter_version", T.STRING, required=True,
               note="A proof without the tolerance definition it ran under is not evidence "
                    "(spec §4.4)."),
        ),
    ),
    EdgeType(
        label="DECIDED_BY",
        pairs=((ANY_LABEL, "GateDecision"),),
        written_by="Gate workflow",
        spec_ref="§4.1.2",
        note="Any node may be the subject of a gate decision.",
    ),
)


#: Differences between the specification's §4.1.2 table and this schema.
EDGE_SPEC_DEVIATIONS: tuple[SpecDeviation, ...] = (
    SpecDeviation(
        element="OWNED_BY, VIEWED_BY, OWNED_BY.views_90d, OWNED_BY.last_view",
        reason="The specification renders these as one table row, 'OWNED_BY / VIEWED_BY', "
               "so its properties column lists views_90d and last_view against both.",
        detail="Split into two edge types: they carry different properties. Ownership is a "
               "single relationship with no properties; usage counts belong to VIEWED_BY, "
               "which is the edge the Harvester writes per viewer.",
    ),
    SpecDeviation(
        element="MAPS_TO.target_column",
        reason="The specification writes the endpoint as 'Field→ModelTable.column'.",
        detail="Columns are not nodes in the §4.1.1 ontology, so the column name is carried "
               "as a property on the edge rather than modelled as a node type the "
               "specification does not declare.",
    ),
    SpecDeviation(
        element="CONTAINS: Project→Project",
        reason="The specification writes the chain as 'Site→Project→Workbook→Dashboard/"
               "Worksheet', which does not show the nested-project case.",
        detail="§4.1.1 states the Project hierarchy is preserved and Project carries a "
               "'parent' property, so a Project→Project containment pair must be permitted.",
    ),
    SpecDeviation(
        element="HAS_FIELD: Datasource→CalculatedField",
        reason="§4.1.2 writes the endpoint as 'Table/Datasource→Field', naming only the "
               "Field node type.",
        detail="§4.1.1 splits Field and CalculatedField into two node types with different "
               "properties, but gives a CalculatedField no other edge from the Datasource "
               "that defines it — and the Tableau adapter has written "
               "Datasource→HAS_FIELD→CalculatedField since S2.3.1's fragments.py. Read "
               "generically ('a field the datasource has'), a calculated field is a field; "
               "the fix is to the ontology's endpoint pair, not to the edge the adapter "
               "already writes. Found by story S3.1.1, which reads this edge to compute "
               "§12.1's 'calculated-field AST shapes a workbook defines' — the first thing "
               "in this codebase to actually harvest a real (non-fixture) workbook's "
               "calculated fields through the write path rather than only parsing them.",
    ),
    SpecDeviation(
        element="IN_TRAIN.state, IN_TRAIN.wip_override_reason",
        reason="§4.1.2 does not list them; backlog story S3.2.2 requires a kanban of "
               "\"trains → states with MU cards\" and a WIP limit that, when exceeded, "
               "\"requires a reason\".",
        detail="state carries the §3.2 state a card's column groups by — a string, not an "
               "enum, matching migration_units.py's own choice to hold MU_STATES as "
               "strings because the state machine's definition belongs to the control "
               "plane, not to this ontology. wip_override_reason is set only when a move "
               "proceeds past a configured WIP limit; who and when are the base "
               "updated_by/updated_at every edge already carries.",
    ),
)
