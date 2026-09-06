"""Node types of the Estate Graph — a transcription of specification §4.1.1.

Required vs optional is a judgement the specification's "key properties" column does not
make. The rule applied here, and recorded in ADR 0001:

* **required** — identity or classification that the producing component always has at the
  moment it first writes the node, and that a downstream reader cannot function without.
* **optional** — values derived by a later stage (a Transpiler class label, a parity
  verdict reference), values the specification marks nullable, and values that depend on a
  source capability that may be absent (usage, ownership, licence figures).

Adding a property here, changing its type, or making it required is an ontology change and
is governed by ``ontology.lock.json`` and the migration guard (S1.1.1 criterion 4).
"""

from __future__ import annotations

from .properties import PropertyType as T
from .types import NodeType, Side, SpecDeviation, _p

# Closed sets that appear in more than one place.
_CALC_CLASSES = ("C1", "C2", "C3", "C4")

#: Model family lifecycle, spec §12.2, plus SINGLETON. §12.2's table is the *post-proposal*
#: lifecycle; SINGLETON is the clustering step's own outcome (§12.1: "held as SINGLETON for
#: engineer review") for a family under the minimum size with nothing to merge into. Not a
#: state §12.2 transitions out of by itself — an engineer resolves it the same way a
#: PROPOSED family is resolved (split, merge, move — S3.1.2), at which point normal §12.2
#: transitions apply.
_FAMILY_STATES = (
    "PROPOSED",
    "SINGLETON",
    "DRAFT",
    "IN_REVIEW",
    "APPROVED",
    "BUILT",
    "PUBLISHED",
    "DEPRECATED",
)

#: Failure taxonomy, spec §11.1.
_FAILURE_CLASSES = (
    "FILTER_CONTEXT",
    "NULL_HANDLING",
    "DATE_GRAIN",
    "AGGREGATION",
    "TYPE_COERCION",
    "LOD_SCOPE",
    "TABLE_CALC",
    "SORT_LIMIT",
    "KEY_MISSING",
    "SOURCE_DRIFT",
    "UNKNOWN",
    "VISUAL_REDESIGN",
)

_VALIDATION_STATE_NOTE = (
    "Position on the validation ladder (spec §16.1). Left as free text until E5/E7 fix the "
    "closed set; see docs/adr/0001 open questions."
)


NODE_TYPES: tuple[NodeType, ...] = (
    # ---------------------------------------------------------------- source estate
    NodeType(
        label="Site",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="From Metadata API plus the licence export.",
        properties=(
            _p("luid", T.LUID, required=True),
            _p("name", T.STRING, required=True),
            _p("owner", T.STRING, note="Reference to a User node."),
            _p("user_count", T.INT),
            _p("licence_cost_annual", T.FLOAT, note="From the client's licence export; "
               "drives the decommission business case (spec §21 site_record)."),
            _p("licence_tier", T.STRING,
               note="The site's licensing model, where the source exposes it. Absent "
                    "when the adapter has no ownership capability (story S1.2.3)."),
        ),
    ),
    NodeType(
        label="Project",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="Hierarchy preserved.",
        properties=(
            _p("luid", T.LUID, required=True),
            _p("name", T.STRING, required=True),
            _p("parent", T.LUID, note="Parent project LUID; absent on a top-level project."),
        ),
    ),
    NodeType(
        label="Workbook",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="One Migration Unit per Workbook.",
        properties=(
            _p("luid", T.LUID, required=True),
            _p("name", T.STRING, required=True),
            _p("revision", T.STRING, required=True,
               note="Source revision identifier. String because the Tableau REST API returns "
                    "revisionNumber as a string; the Harvester is idempotent on it."),
            _p("size", T.INT, note="Bytes."),
            _p("extract_flag", T.BOOL),
            _p("last_published", T.TIMESTAMP),
            _p("views_90d", T.INT, note="Trailing-window usage; absent where the source does "
               "not expose usage."),
            _p("distinct_viewers_90d", T.INT),
            _p("rls", T.BOOL,
               note="Row-level security detected in the workbook (story S2.3.2). Absent is "
                    "not the same as false: a workbook harvested before the adapter looked "
                    "for it has neither."),
            _p("rls_expression", T.TEXT,
               note="The user-filter expression, verbatim. Kept because the target has to "
                    "reproduce the access model, and 'this workbook restricts rows' without "
                    "saying how is not something a Modeller can act on."),
            _p("parse_quality", T.FLOAT,
               note="Fraction of this workbook's source constructs the adapter grammar "
                    "could read, counting constructs an engineer has accepted as "
                    "ignorable (spec §4.1.4, story S1.2.2). Absent until harvested."),
        ),
    ),
    NodeType(
        label="Dashboard",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="Layout retained for the Compositor.",
        properties=(
            _p("name", T.STRING, required=True),
            _p("size", T.JSON, note="Source dashboard size specification (type and, for fixed "
               "sizes, width and height)."),
            _p("layout_json", T.JSON, required=True,
               note="The zone tree. Required because the Compositor derives page layout from "
                    "it and has no fallback (spec §8.8)."),
            _p("contained_sheets", T.STRING_LIST, required=True,
               note="May be empty; absence and emptiness are different facts."),
            _p("views_90d", T.INT, note="Trailing-window usage for this view. Tableau reports usage per published view, not only per workbook, and S1.2.3 needs both: a wave is ordered by business impact, and a workbook whose usage sits in one dashboard is a different proposition from one spread across six sheets."),
            _p("distinct_viewers_90d", T.INT),
            _p("last_view", T.TIMESTAMP),
        ),
    ),
    NodeType(
        label="Worksheet",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="The visual specification. Parity cases are derived from these fields, so the "
             "shelves are required (spec §10.1).",
        properties=(
            _p("name", T.STRING, required=True),
            _p("mark_type", T.STRING, note="Source mark type, including 'Automatic'."),
            _p("rows_shelf", T.STRING_LIST, required=True),
            _p("cols_shelf", T.STRING_LIST, required=True),
            _p("marks_shelf", T.STRING_LIST, required=True,
               note="Colour, size, shape, detail, label, tooltip and path encodings."),
            _p("sort", T.JSON, note="List of sort specifications."),
            _p("filters", T.JSON, note="List of filter specifications; also materialised as "
               "FILTERED_BY edges."),
            _p("reference_lines", T.JSON, note="List of reference line and band specifications."),
            _p("views_90d", T.INT, note="Trailing-window usage for this view. Tableau reports usage per published view, not only per workbook, and S1.2.3 needs both: a wave is ordered by business impact, and a workbook whose usage sits in one dashboard is a different proposition from one spread across six sheets."),
            _p("distinct_viewers_90d", T.INT),
            _p("last_view", T.TIMESTAMP),
        ),
    ),
    NodeType(
        label="Datasource",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        properties=(
            _p("luid", T.LUID, note="Published datasources carry a LUID; embedded ones do not."),
            _p("name", T.STRING, required=True),
            _p("type", T.ENUM, required=True, enum=("embedded", "published")),
            _p("connection_ref", T.STRING),
            _p("extract_flag", T.BOOL),
            _p("refresh_schedule", T.STRING),
        ),
    ),
    NodeType(
        label="Connection",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="Drives platform-side ingestion planning.",
        properties=(
            _p("class", T.ENUM, required=True,
               enum=("sybase", "sqlserver", "snowflake", "postgres", "hive", "excel", "text",
                     "odbc", "hyper"),
               note="Closed set per the specification. A source estate using a class outside "
                    "it is rejected at write time; see docs/adr/0001 open questions."),
            _p("server", T.STRING),
            _p("db", T.STRING),
            _p("schema", T.STRING),
            _p("auth_mode", T.STRING),
        ),
    ),
    NodeType(
        label="Table",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="custom_sql retained verbatim.",
        properties=(
            _p("name", T.STRING, required=True),
            _p("schema", T.STRING),
            _p("custom_sql", T.TEXT, note="Nullable. Retained byte-for-byte: the live-replay "
               "proof strategy re-executes it (spec §6.2)."),
            _p("row_estimate", T.INT),
        ),
    ),
    NodeType(
        label="Field",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        properties=(
            _p("name", T.STRING, required=True),
            _p("datatype", T.STRING, required=True),
            _p("role", T.ENUM, required=True, enum=("dimension", "measure")),
            _p("default_agg", T.STRING),
            _p("hidden", T.BOOL),
        ),
    ),
    NodeType(
        label="CalculatedField",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="AST from the adapter parser.",
        properties=(
            _p("name", T.STRING, required=True),
            _p("formula", T.TEXT, required=True, note="Verbatim source expression."),
            _p("formula_ast", T.JSON, required=True,
               note="Grammar-backed AST. Required: classification, pattern matching and "
                    "context assembly all key off it (spec §9.1, §9.3)."),
            _p("class", T.ENUM, enum=_CALC_CLASSES,
               note="Set by the Transpiler, absent at harvest."),
            _p("lod_type", T.ENUM, enum=("FIXED", "INCLUDE", "EXCLUDE"),
               note="Absent when the expression is not a level-of-detail expression."),
            _p("table_calc_flag", T.BOOL),
            _p("depends_on", T.STRING_LIST,
               note="Also materialised as DEPENDS_ON edges; retained here as the parser "
                    "recorded it."),
            _p("pattern_ref", T.STRING,
               note="The classifier rule or Pattern Library id that decided `class` "
                    "(story S5.1.1). Absent alongside `class`, at harvest."),
            _p("reason", T.TEXT,
               note="Why `class` is what it is — the specific construct the classifier "
                    "found, in Appendix B.1's own terms (story S5.1.1)."),
            _p("classifier_version", T.INT,
               note="Which classifier ruleset version last classified this field — the "
                    "same 'stamped on every attempt' footing conformance_ruleset_version "
                    "already has (story S5.1.1)."),
            _p("appendix_b_guidance", T.TEXT,
               note="Appendix B's own target/notes text for the rule that produced a C4 "
                    "verdict (story S5.4.1). Absent unless `class` is C4."),
            _p("redesign_suggestion", T.TEXT,
               note="A real, deterministic ASSISTED-mode next-step suggestion (story "
                    "S5.4.1) — never a model call, the same footing the Modeller's own "
                    "grain-statement draft already established for this mode. Absent "
                    "unless `class` is C4."),
            _p("redesign_suggestion_provenance_ref", T.STRING,
               note="The ProvenanceRecord (mode ASSISTED) for `redesign_suggestion` "
                    "(story S5.4.1). Absent alongside it."),
            _p("redesign_decision", T.ENUM,
               enum=("IMPLEMENT_AS_SUGGESTED", "ALTERNATIVE", "DROP"),
               note="A Migration Engineer's own recorded decision (story S5.4.1). Absent "
                    "is the disclosed proxy for a C4 field's own Migration Unit being "
                    "BLOCKED (§3.2) — no real MU record exists anywhere in this codebase "
                    "to hold that state (§4.1.1 declares none; confirmed by research, not "
                    "assumed)."),
            _p("redesign_decision_reason", T.TEXT,
               note="The engineer's own rationale (story S5.4.1) — for a DROP decision, "
                    "where the report-owner agreement the acceptance criteria requires is "
                    "recorded, since this platform has no separate co-sign workflow "
                    "(that is G2's own dedicated mechanism, not rebuilt here)."),
            _p("redesign_decision_by", T.STRING, note="The principal who recorded the decision."),
            _p("redesign_decision_at", T.TIMESTAMP, note="When the decision was recorded."),
        ),
    ),
    NodeType(
        label="Parameter",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="Domain bounds the Arbiter's enumeration (spec §10.1).",
        properties=(
            _p("name", T.STRING, required=True),
            _p("datatype", T.STRING, required=True),
            _p("domain", T.ENUM, required=True, enum=("list", "range", "any")),
            _p("default", T.JSON, note="Scalar of the parameter's own datatype."),
            _p("current_values_seen", T.STRING_LIST,
               note="Observed values from usage, where the adapter provides them."),
        ),
    ),
    NodeType(
        label="Filter",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        properties=(
            _p("field_ref", T.STRING, required=True),
            _p("type", T.ENUM, required=True,
               enum=("categorical", "range", "relative_date", "top_n", "condition")),
            _p("values", T.JSON),
            _p("context_flag", T.BOOL, note="Tableau context filter."),
        ),
    ),
    NodeType(
        label="Action",
        side=Side.SOURCE,
        spec_ref="§4.1.1",
        note="Interactivity mapping.",
        properties=(
            _p("type", T.ENUM, required=True,
               enum=("filter", "highlight", "url", "parameter", "set")),
            _p("source_sheets", T.STRING_LIST, required=True),
            _p("target_sheets", T.STRING_LIST, note="Absent for a URL action."),
        ),
    ),
    # ------------------------------------------------------------------- both sides
    NodeType(
        label="User",
        side=None,
        spec_ref="§4.1.1",
        note="Entra-linked. The only type the specification marks as existing on both sides, "
             "so the writer declares 'side' explicitly.",
        properties=(
            _p("upn", T.STRING, required=True),
            _p("display", T.STRING),
            _p("licence_tier", T.STRING, note="Creator / Explorer / Viewer, where exposed."),
            _p("site_roles", T.STRING_LIST),
            _p("directory_id", T.STRING,
               note="The directory object id this source user resolved to. Its absence is "
                    "the fact the unresolved-owner list is built from: a workbook whose "
                    "owner did not resolve cannot be sent a gate request (story S1.2.3)."),
            _p("directory_resolved_at", T.TIMESTAMP,
               note="When the link was made, and by extension whether it was made by the "
                    "resolver or by a person assigning it."),
        ),
    ),
    # ---------------------------------------------------------------- target estate
    NodeType(
        label="ModelFamily",
        side=Side.TARGET,
        spec_ref="§4.1.1",
        note="Proposed by the Cartographer.",
        properties=(
            _p("name", T.STRING, required=True),
            _p("domain", T.STRING),
            _p("grain", T.STRING, note="Candidate grain inferred from member sheets; confirmed "
               "at G2."),
            _p("state", T.ENUM, required=True, enum=_FAMILY_STATES, note="Spec §12.2."),
            _p("owner", T.STRING),
            _p("conformed_dims", T.STRING_LIST),
            _p("reason", T.STRING,
               note="Why this family is SINGLETON rather than clustered or merged (story "
                    "S3.1.1). Absent otherwise."),
            _p("evidence_shared_tables", T.STRING_LIST,
               note="Tables reached by two or more members — the evidence the clustering "
                    "was made from (story S3.1.1), not the union of everything every member "
                    "reaches."),
            _p("evidence_shared_fields", T.STRING_LIST,
               note="Fields encoded by two or more members."),
            _p("evidence_shared_calc_shapes", T.INT,
               note="Count of distinct calculation AST shapes (context.signature.ast_shape) "
                    "defined by two or more members."),
            _p("overridden", T.BOOL,
               note="A human split, merged or moved a member into or out of this family "
                    "(story S3.1.2). Pinned: a Cartographer re-run reports what it would "
                    "change here and does not apply it without confirmation."),
            _p("override_action", T.ENUM, enum=("SPLIT", "MERGE", "MOVE"),
               note="Which kind of override last touched this family. Absent on a family "
                    "no human has touched."),
            _p("override_reason", T.STRING,
               note="The stated reason for the most recent override — who and when are "
                    "already carried by the base properties updated_by/updated_at."),
            _p("g2_cycle_count", T.INT,
               note="How many times this family has been sent back to DRAFT from G2 "
                    "review (story S4.2.1's 'the cycle count is stored'). Absent means "
                    "zero — no request-changes has happened yet."),
            _p("conformance_ruleset_version", T.INT,
               note="Which version of the §12.3 conformance ruleset this family's most "
                    "recent build was checked against (story S4.3.2's 'recorded on the "
                    "ModelFamily at build'). Absent means never built."),
        ),
    ),
    NodeType(
        label="SemanticModel",
        side=Side.TARGET,
        spec_ref="§4.1.1",
        note="One per family per environment.",
        properties=(
            _p("family_ref", T.STRING, required=True),
            _p("tmdl_ref", T.STRING, note="Artefact reference to the TMDL folder."),
            _p("workspace", T.STRING),
            _p("state", T.STRING, note="Deployment state within an environment; the family "
               "lifecycle is on ModelFamily.state."),
            _p("version", T.STRING),
            _p("rls_roles", T.STRING_LIST),
            _p("grain_statement", T.STRING,
               note="A prose rendering of ModelFamily.grain, drafted at proposal time "
                    "(story S4.1.1). 'One row per X, Y, Z' rather than the terse candidate "
                    "grain tuple the Cartographer clusters on — the Semantic Model Engineer "
                    "edits this in the Model Detail screen, S4.1.2."),
            _p("design_generated_at", T.TIMESTAMP,
               note="When the Modeller last generated a design proposal for this model. "
                    "Absent until the first proposal (story S4.1.1)."),
            _p("design_provenance_ref", T.STRING,
               note="The ProvenanceRecord for this proposal's ASSISTED-mode drafting step "
                    "(naming, grain_statement) — spec §16.2's 'provenance recorded'."),
            _p("design_document", T.JSON,
               note="The rest of the design proposal (story S4.1.1): relationships (with "
                    "cardinality), candidate measures (source calc refs, dedup decisions), "
                    "conformed dimensions (which other families share them), an RLS role "
                    "detail per name in rls_roles, refresh policy, and open questions. Not "
                    "split into first-class properties or nodes because none of it is a "
                    "first-class graph concept yet — a candidate measure has no dax, and "
                    "is not a Measure node, until the Transpiler (E5) produces one."),
            _p("version_number", T.INT,
               note="1, 2, 3... — which version of this family's model this is (story "
                    "S4.3.3's own v(n)/v(n+1)). Absent means 1: every family had exactly "
                    "one SemanticModel before this story, so an absent number is "
                    "unambiguous, never a second version to confuse it with."),
            _p("published_at", T.TIMESTAMP,
               note="When this version's state last became PUBLISHED (story S4.3.3). "
                    "Absent until then."),
            _p("deprecated_at", T.TIMESTAMP,
               note="When this version's state became DEPRECATED — set the moment its "
                    "successor is promoted (story S4.3.3's 'marks v(n) DEPRECATED with the "
                    "date'). Absent for a version that has never been superseded."),
        ),
    ),
    NodeType(
        label="ModelTable",
        side=Side.TARGET,
        spec_ref="§4.1.1",
        properties=(
            _p("name", T.STRING, required=True),
            _p("source_table_refs", T.STRING_LIST),
            _p("mode", T.ENUM, required=True, enum=("import", "directlake", "directquery")),
            _p("family_ref", T.STRING, required=True,
               note="Which ModelFamily's design this table is a candidate for. §4.1.1 "
                    "declares no edge from a ModelTable back to its family or SemanticModel "
                    "(MAPS_TO only carries a source Field to a ModelTable's column) — a "
                    "plain reference, the same shape as ReportDefinition.mu_ref (S2.4.2)."),
            _p("semantic_model_ref", T.STRING,
               note="Which SemanticModel *version* this table belongs to (story S4.3.3) — "
                    "family_ref alone stopped being enough to find 'the' design the moment "
                    "a family could have two live SemanticModel nodes at once (a published "
                    "v(n) and a draft v(n+1)). Absent on tables written before this story; "
                    "read_design_document falls back to family_ref for those, safe only "
                    "because a pre-S4.3.3 family never had a second version to confuse it "
                    "with."),
            _p("schema", T.STRING,
               note="The source table's own schema, carried onto its ModelTable candidate "
                    "at proposal time (`TableCandidate.schema`, computed since S4.1.1) — "
                    "declared and finally persisted by story S4.3.3, which found it "
                    "silently dropped at write time while adding semantic_model_ref to the "
                    "same NodeWrite: every build since S4.1.1 emitted TMDL with an "
                    "unqualified table name because this was never on the node to read "
                    "back."),
            _p("mode_reason", T.STRING,
               note="The disclosed heuristic behind `mode` (ADR 0028) — computed at "
                    "proposal time, same gap and same fix as `schema`."),
            _p("row_estimate", T.INT,
               note="Carried from the source Table's own row_estimate — same gap and fix "
                    "as `schema`; the console's Design tab has shown '—' here on every read "
                    "after the first, since generation's own immediate response was the "
                    "only place this ever appeared."),
            _p("custom_sql", T.BOOL,
               note="Whether this table's source is custom SQL (merged from more than one "
                    "underlying table) — same gap and fix as `schema`."),
        ),
    ),
    NodeType(
        label="Measure",
        side=Side.TARGET,
        spec_ref="§4.1.1",
        note="The Transpiler's product.",
        properties=(
            _p("name", T.STRING, required=True),
            _p("dax", T.TEXT, required=True),
            _p("m_query", T.TEXT, note="Nullable; present where the transformation lands in "
               "Power Query rather than DAX."),
            _p("source_calc_ref", T.STRING),
            _p("class", T.ENUM, enum=_CALC_CLASSES),
            _p("pattern_ref", T.STRING),
            _p("provenance_ref", T.STRING, required=True,
               note="Mandatory: the artefact store rejects a write without a provenance "
                    "record (spec §16.2)."),
            _p("validation_state", T.STRING, note=_VALIDATION_STATE_NOTE),
        ),
    ),
    NodeType(
        label="ReportDefinition",
        side=Side.TARGET,
        spec_ref="§4.1.1",
        note="The Compositor's product.",
        properties=(
            _p("mu_ref", T.STRING, required=True),
            _p("pbir_ref", T.STRING,
               note="The Git commit this report last deployed from (story S6.1.2) -- "
                    "absent until the first successful commit; the Fabric item path "
                    "itself is not recorded here, since it is derived from the "
                    "workbook's own name and never stored as an independent fact."),
            _p("pages", T.STRING_LIST),
            _p("model_ref", T.STRING, required=True,
               note="A report definition that binds to no model cannot be proved."),
            _p("version", T.STRING),
            _p("validation_state", T.STRING, note=_VALIDATION_STATE_NOTE),
            _p("deploy_state", T.STRING,
               note="The disclosed proxy for this report's own place in §3.2's MU state "
                    "machine (story S6.1.2) -- no real Migration Unit node exists anywhere "
                    "in this codebase to hold GENERATED/PROVING/etc for real (confirmed a "
                    "sixth time, the identical gap S5.4.1/S5.5.1/S5.5.2/S5.5.3/S6.1.1 each "
                    "already found). 'GENERATED' after a successful commit and deploy; "
                    "'DEPLOY_FAILED' after every retry is exhausted -- the AC's own "
                    "'deployment failure returns the MU to GENERATED' read as 'never claims "
                    "the deploy succeeded', since nothing here can roll back a state that "
                    "was never really entered by a real MU record to begin with. Absent "
                    "before this report has ever been deployed at all."),
            _p("deploy_error", T.STRING,
               note="The failing step's own detail (story S6.1.2) -- 'the error on the MU "
                    "page' (F10.3, not yet built); absent whenever deploy_state is absent "
                    "or 'GENERATED'."),
            _p("documentation_artefact_ref", T.STRING,
               note="The generated markdown documentation page for this report (story "
                    "S6.2.2) -- an ArtefactRecord id (kind 'report_documentation'). "
                    "Absent until documentation has been generated at least once."),
            _p("documentation_provenance_ref", T.STRING,
               note="The ProvenanceRecord (mode ASSISTED) for the documentation-drafting "
                    "step that produced `documentation_artefact_ref` (story S6.2.2). "
                    "Absent alongside it."),
        ),
    ),
    NodeType(
        label="Visual",
        side=Side.TARGET,
        spec_ref="§4.1.1",
        properties=(
            _p("page", T.STRING, required=True),
            _p("type", T.STRING, required=True),
            _p("source_sheet_ref", T.STRING, note="Absent on a visual with no source sheet, "
               "such as a placeholder card for a redesigned visual."),
            _p("encodings", T.JSON),
            _p("redesign_flag", T.BOOL),
            _p("redesign_reason", T.STRING),
            _p("layout", T.JSON,
               note="x/y/width/height (story S6.1.1's own 'layout preserved at the "
                    "container level') — the geometry of the dashboard zone this visual's "
                    "source sheet occupied, read straight off Dashboard.layout_json's own "
                    "zone tree. Absent on a standalone sheet with no containing dashboard: "
                    "its own page has just the one visual, filling it, so there is no "
                    "container geometry to preserve."),
            _p("interactivity", T.JSON,
               note="Parameters this visual's own calculated-field wells depend on "
                    "(DEPENDS_ON), classified into a what-if parameter or a slicer by "
                    "domain, and every Action naming this visual's source sheet as a "
                    "source or target, classified into a cross-filter/highlight setting, "
                    "a URL field, or left unsupported with Appendix B.2's own guidance "
                    "(story S6.1.3). Always present once composed (an empty list either "
                    "way means 'none found', not 'not looked for')."),
        ),
    ),
    NodeType(
        label="ParityCase",
        side=Side.TARGET,
        spec_ref="§4.1.1, §10",
        properties=(
            _p("mu_ref", T.STRING, required=True),
            _p("sheet_ref", T.STRING, required=True),
            _p("grain", T.STRING_LIST, required=True,
               note="The key of the result set; a case without a grain is not executable."),
            _p("measures", T.STRING_LIST, required=True),
            _p("filter_ctx", T.JSON),
            _p("param_values", T.JSON),
            _p("expected_ref", T.STRING, note="Result set from the source side."),
            _p("candidate_ref", T.STRING, note="Result set from the target side."),
            _p("state", T.STRING),
            _p("case_key", T.STRING, required=True,
               note="The AC's own 'stable id' (story S7.2.1) -- a sha256 digest of "
                    "(sheet_ref, grain, measures, filter_ctx, param_values), stable "
                    "across a re-derivation that finds the same case again. Not `id` "
                    "itself: the base `id` property is a validated ULID (26 Crockford-"
                    "base32 characters), which a content hash cannot be shaped into "
                    "without inventing a fake encoding nobody asked for -- the same "
                    "'id stays a server-issued identity; a separate property carries the "
                    "content-derived key' split `ArtefactRecord.id`/`.content_hash` "
                    "already established (S2.4.2)."),
        ),
    ),
    NodeType(
        label="ParityRun",
        side=Side.TARGET,
        spec_ref="§4.1.1, §10",
        properties=(
            _p("suite_ref", T.STRING, required=True),
            _p("charter_version", T.STRING, required=True,
               note="Every run records the Tolerance Charter version it ran under (spec §4.4)."),
            _p("started", T.TIMESTAMP),
            _p("finished", T.TIMESTAMP),
            _p("verdicts", T.STRING_LIST),
        ),
    ),
    NodeType(
        label="Verdict",
        side=Side.TARGET,
        spec_ref="§4.1.1, §10.3",
        properties=(
            _p("case_ref", T.STRING, required=True),
            _p("result", T.ENUM, required=True, enum=("PASS", "FAIL", "INCONCLUSIVE")),
            _p("failing_cells", T.JSON, note="Bounded sample; the full bundle is an artefact."),
            _p("evidence_ref", T.STRING),
        ),
    ),
    NodeType(
        label="ExceptionCase",
        side=Side.TARGET,
        spec_ref="§4.1.1, §11.3",
        properties=(
            _p("mu_ref", T.STRING, required=True),
            _p("class", T.ENUM, required=True, enum=_FAILURE_CLASSES, note="Spec §11.1."),
            _p("evidence_ref", T.STRING),
            _p("state", T.STRING),
            _p("assignee", T.STRING),
            _p("decision", T.STRING),
            _p("pattern_ref", T.STRING, note="The pattern candidate the decision produced."),
            _p("visual_ref", T.STRING,
               note="Which Visual this case concerns (story S6.2.1, class "
                    "VISUAL_REDESIGN). Absent for every other failure class -- those "
                    "concern a CalculatedField/Measure, which `mu_ref` and `evidence_ref` "
                    "already identify without a second reference property."),
            _p("mapping_reason", T.TEXT,
               note="A snapshot of Visual.redesign_reason at the moment this case opened "
                    "(story S6.2.1) -- copied, not read live, the same 'evidence copied "
                    "onto a record is a snapshot' discipline S1.4.3's own grammar-issue "
                    "records already established, so a later mapping-table edit cannot "
                    "quietly rewrite what a still-open case says it is about."),
            _p("placeholder_location", T.JSON,
               note="A snapshot of {page, layout, source_sheet_ref} at open time (story "
                    "S6.2.1) -- 'the placeholder location' the AC names. No separate "
                    "Page/Location node exists (§4.1.1 declares none); composed from "
                    "Visual's own properties, the same three facts §8.8's own page "
                    "layout already keeps."),
            _p("screenshot_ref", T.STRING,
               note="An ArtefactRecord id (kind 'visual_capture', story S2.4.2) whose "
                    "case_id names this case's own source worksheet, if one has ever "
                    "been uploaded for it (story S6.2.1). Absent today on every real "
                    "estate this platform has ever composed: nothing in this codebase's "
                    "own local/demo environment captures a Tableau screenshot "
                    "automatically (no live Tableau connection exists to take one from) "
                    "-- a real, disclosed gap, not a broken reference."),
            _p("closed_by", T.STRING, note="The engineer who closed this case (story "
               "S6.2.1). Absent while state is OPEN."),
            _p("closed_at", T.TIMESTAMP, note="When this case was closed (story S6.2.1). "
               "Absent while state is OPEN."),
            _p("desktop_commit_hash", T.STRING,
               note="The Power BI Desktop commit the AC itself names (story S6.2.1) -- "
                    "recorded as the engineer states it, the same 'a real fact, not "
                    "verified against a system this platform cannot reach' footing "
                    "`ArtefactRecord.content_hash` already has for a caller-supplied "
                    "artefact. Absent while state is OPEN."),
        ),
    ),
    # --------------------------------------------------------------------- platform
    NodeType(
        label="Pattern",
        side=Side.PLATFORM,
        spec_ref="§4.1.1, §4.3",
        properties=(
            _p("name", T.STRING, required=True),
            _p("class", T.ENUM, required=True, enum=_CALC_CLASSES),
            _p("source_signature", T.JSON, required=True,
               note="AST shape with leaf identifiers abstracted to typed placeholders."),
            _p("target_template", T.TEXT, required=True),
            _p("guards", T.STRING_LIST,
               note="Human-readable preconditions the captured placeholders must satisfy "
                    "(§4.3's own worked example: ['dims ⊆ model.dimensions', 'a,b numeric']) "
                    "-- story S5.5.1. Descriptive, not machine-evaluated, the identical "
                    "footing `RuleMeta.guards` already has (consulted by a coverage report, "
                    "never enforced by a renderer); matching stays exact-shape only."),
            _p("provenance", T.JSON, note="Origin, first sighting, promotion timestamp."),
            _p("promotion_state", T.ENUM, required=True,
               enum=("CANDIDATE", "ACTIVE", "RETIRED"), note="Spec §4.3."),
            _p("pass_count", T.INT,
               note="A point-in-time snapshot (written at creation and at promotion), not a "
                    "live-maintained counter -- story S5.5.1's own `pattern_observation` "
                    "table (append-only, one row per real proof pass or failure) is the "
                    "authoritative source promotion eligibility is actually checked "
                    "against, the identical 'computed from the raw table, never a "
                    "maintained running total' footing `calibration_observation` already "
                    "set (S5.3.3)."),
            _p("failure_count", T.INT,
               note="Story S5.5.2's own 'a proof failure ... increments its failure count' "
                    "-- incremented on every recorded failure, for any promotion_state, "
                    "the same 'proof_fail' fact spec §4.3's own worked `stats` example "
                    "names. A point-in-time snapshot, like `pass_count`: the retirement "
                    "threshold itself is always checked live against `pattern_observation`, "
                    "never against this counter, so a missed increment could never cause a "
                    "wrong retirement decision."),
            _p("version", T.INT,
               note="Story S5.5.3's own 'edit guards (creates a new version)' -- absent "
                    "means 1, the same 'additive, no backfill' reading every optional "
                    "counter in this codebase already gets. Editing never mutates a "
                    "Pattern in place: it writes a new node with `version` incremented "
                    "and `supersedes_id` naming this one, then retires this one -- the "
                    "identical 'an edit is a new version, the old row is never touched' "
                    "discipline `SemanticModel`'s own per-version lifecycle (S4.3.3) "
                    "already set for a graph node specifically."),
            _p("supersedes_id", T.STRING,
               note="The Pattern this version replaced (story S5.5.3) -- absent on a "
                    "pattern that has never been edited. The identical field name "
                    "`ProvenanceRecord.supersedes_id` already uses for the same "
                    "'this new record replaces that one' relationship, applied here to "
                    "a graph node instead of a provenance row."),
        ),
    ),
    NodeType(
        label="GateDecision",
        side=Side.PLATFORM,
        spec_ref="§4.1.1, §13.3",
        properties=(
            _p("gate", T.ENUM, required=True, enum=("G1", "G2", "G3", "G4")),
            _p("subject_ref", T.STRING, required=True),
            _p("decision", T.ENUM, required=True,
               enum=("APPROVED", "REJECTED", "CHANGES_REQUESTED", "WAIVED")),
            _p("approver", T.STRING, required=True,
               note="A gate decision without a named approver is not a decision (spec P4)."),
            _p("rationale", T.TEXT),
            _p("evidence_ref", T.STRING),
            _p("timestamp", T.TIMESTAMP, required=True,
               note="When the approver decided, which is not the same instant as the write."),
            _p("approver_role", T.STRING,
               note="§13.1's own approver column names a role, not just a person — "
                    "'client data owner for the domain'. Story S4.2.1."),
            _p("version_hash", T.STRING,
               note="The frozen artefact version this decision is about — §13.3's own "
                    "'version_hash: sha256:…'. For G2, SemanticModel.version (S4.1.2)."),
            _p("countersigner", T.STRING,
               note="§13.1's approver/countersign pairs (e.g. G2: data owner approves, "
                    "Semantic Model Engineer countersigns). Absent when a gate has none."),
            _p("countersigner_role", T.STRING),
        ),
    ),
    NodeType(
        label="ReleaseTrain",
        side=Side.PLATFORM,
        spec_ref="§4.1.1, §3.3",
        properties=(
            _p("name", T.STRING, required=True),
            _p("mu_refs", T.STRING_LIST,
               note="Not populated by story S3.2.1 — IN_TRAIN edges are the single source "
                    "of truth for membership, the same way IN_FAMILY edges are for "
                    "ModelFamily, which carries no members-list property either."),
            _p("planned_start", T.DATE),
            _p("planned_end", T.DATE),
            _p("actual_start", T.DATE),
            _p("actual_end", T.DATE),
            _p("gate_schedule", T.JSON,
               note="Planned G2/G3 windows for this train's members (story S3.2.1). A "
                    "first-cut plan, not a throughput projection — that is the wave "
                    "scheduler's job (§14.2, backlog S3.2.3), not built yet."),
            _p("wip_limits", T.JSON,
               note="Configured work-in-progress caps for the Wave Board (story S3.2.2): "
                    "{'train': <int|null>, 'states': {<state>: <int>, ...}}. Absent means "
                    "no limit is configured — a train starts open, the same way a family "
                    "starts un-overridden."),
            _p("overridden", T.BOOL,
               note="A Programme Manager moved, resequenced or WIP-limited this train on "
                    "the Wave Board (story S3.2.2). Pinned the same way S3.1.2's "
                    "ModelFamily.overridden is: a train proposal re-run leaves this train "
                    "and its members alone unless its id is named to confirm."),
            _p("override_action", T.ENUM, enum=("MOVE", "RESEQUENCE", "WIP_LIMITS"),
               note="Which kind of Wave Board edit last touched this train."),
            _p("override_reason", T.STRING,
               note="The stated reason for the most recent override — who and when are "
                    "the base properties updated_by/updated_at every node already "
                    "carries."),
        ),
    ),
    NodeType(
        label="Wave",
        side=Side.PLATFORM,
        spec_ref="§4.1.1, §3.3",
        note="A calendar window in which one or more trains execute.",
        properties=(
            _p("name", T.STRING, required=True),
            _p("mu_refs", T.STRING_LIST),
            _p("planned_start", T.DATE),
            _p("planned_end", T.DATE),
            _p("actual_start", T.DATE),
            _p("actual_end", T.DATE),
        ),
    ),
)


#: Differences between the specification's §4.1.1 table and this schema, each with its
#: reason. ``tools/ontology_check.py`` fails on any difference that is not declared here.
NODE_SPEC_DEVIATIONS: tuple[SpecDeviation, ...] = (
    SpecDeviation(
        element="ReleaseTrain, Wave",
        reason="The specification renders these as one table row, 'ReleaseTrain / Wave'.",
        detail="Split into two node types: §3.3 defines them as different objects — a train "
               "is an ordered group of Migration Units, a wave is a calendar window.",
    ),
    SpecDeviation(
        element="Workbook.rls, Workbook.rls_expression",
        reason="§4.1.1 does not list them; backlog story S2.3.2 requires them by name — "
               "\"recorded on the Workbook node as rls: true with the expression\".",
        detail="The backlog adds rather than contradicts here, so the property is carried "
               "with the story as its warrant. Row-level security is a fact about who may "
               "see which rows, and §10's parity cases have to be derived under the same "
               "restriction or they compare a report the client cannot see. Recording it on "
               "the Workbook rather than on a node of its own follows the story's wording "
               "and keeps it visible wherever the Migration Unit is.",
    ),
    SpecDeviation(
        element="Worksheet.views_90d, Worksheet.distinct_viewers_90d, Worksheet.last_view, "
                "Dashboard.views_90d, Dashboard.distinct_viewers_90d, Dashboard.last_view",
        reason="§4.1.1 lists usage only on Workbook, but §6.2 sources usage from the "
               "Metadata API's views, which Tableau reports per published view.",
        detail="Story S1.2.3 requires views and distinct viewers 'per workbook and per "
               "view'. A view is a Worksheet or a Dashboard in this ontology, so the "
               "same three properties sit there too.",
    ),
    SpecDeviation(
        element="User.directory_id, User.directory_resolved_at",
        reason="§4.1.1 annotates User as 'Entra-linked' and §6.2 maps owners to 'Entra "
               "users where a match exists', but neither names a property to hold the "
               "link or to record its absence.",
        detail="Story S1.2.3 needs unresolved owners listed for assignment, which is a "
               "query for users with no directory link.",
    ),
    SpecDeviation(
        element="Site.licence_tier",
        reason="§4.1.1's Site row carries licence_cost_annual and user_count but not the "
               "tier.",
        detail="Story S1.2.3 asks for the licence tier of the site alongside the "
               "per-user tier that User already declares.",
    ),
    SpecDeviation(
        element="Workbook.parse_quality",
        reason="§4.1.1's Workbook row does not list it, but §4.1.4 requires that "
               "\"every Harvester run records a parse quality score per workbook\" and "
               "makes it a release-readiness check for the Calibration Wave.",
        detail="Story S1.2.2 requires it on the Workbook node specifically, so the estate "
               "can be filtered by it without joining to harvest history.",
    ),
    SpecDeviation(
        element="ReleaseTrain.actual_start, ReleaseTrain.actual_end, Wave.actual_start, "
                "Wave.actual_end",
        reason="The specification abbreviates these as 'actual_*'.",
        detail="Expanded to the two properties the wildcard stands for, matching the "
               "planned_start / planned_end pair on the same row.",
    ),
    SpecDeviation(
        element="ModelFamily.reason, ModelFamily.evidence_shared_tables, "
                "ModelFamily.evidence_shared_fields, ModelFamily.evidence_shared_calc_shapes",
        reason="§4.1.1 does not list them; backlog story S3.1.1 requires a family held as "
               "SINGLETON to carry \"the reason\", and every PROPOSED family to carry "
               "\"the evidence (shared tables, shared fields, shared calc shapes)\".",
        detail="The backlog adds rather than contradicts here, so the properties are "
               "carried with the story as their warrant — the same pattern as "
               "Workbook.rls above. Recording the evidence on the node is what lets the "
               "Foundry Workbench show a proposal's reasoning (E3's own goal) without "
               "re-deriving it from SHARES_LINEAGE edges every time a family is opened, "
               "and without it silently drifting from what the clustering run actually "
               "used when membership is later edited (S3.1.2).",
    ),
    SpecDeviation(
        element="ModelFamily.overridden, ModelFamily.override_action, "
                "ModelFamily.override_reason",
        reason="§4.1.1 does not list them; backlog story S3.1.2 requires every split, "
               "merge and move to be \"preserved across re-clustering runs\" and to "
               "record \"who, when and why\".",
        detail="Who and when are already the base properties updated_by/updated_at every "
               "node carries (S1.1.1); overridden and override_reason are what a "
               "re-cluster reads to decide whether it may replace this family at all, and "
               "override_action is what a reviewer reads to know which kind of change was "
               "made without opening the event log.",
    ),
    SpecDeviation(
        element="ReleaseTrain.gate_schedule",
        reason="§4.1.1 does not list it; backlog story S3.2.1 requires \"gate schedule\" "
               "to be \"stored as ReleaseTrain nodes\" alongside membership and planned "
               "start/end.",
        detail="§13.1 gates a family at G2 and a Migration Unit at G3 — a train has no "
               "gate of its own, so this is a train-level roll-up: G2 windows cluster near "
               "the train's planned start (families are confirmed before generation "
               "begins) and G3 near its planned end (units are accepted as they finish). "
               "A first-cut plan a Programme Manager edits, not the throughput-based "
               "projection §14.2's wave scheduler produces (backlog S3.2.3, not built).",
    ),
    SpecDeviation(
        element="ReleaseTrain.wip_limits, ReleaseTrain.overridden, "
                "ReleaseTrain.override_action, ReleaseTrain.override_reason",
        reason="§4.1.1 does not list them; backlog story S3.2.2 requires a configurable "
               "WIP limit \"per train and per state\", and every Wave Board change to "
               "\"be refused\" or \"require a reason\" — which only means something if a "
               "later train proposal run (S3.2.1) cannot silently overwrite it.",
        detail="wip_limits is config a Programme Manager sets, not evidence from a run — "
               "the same distinction ModelFamily.evidence_* draws against .overridden. "
               "overridden/override_action/override_reason are S3.1.2's ModelFamily "
               "pinning mechanism, reused verbatim for trains: TrainPlanner.run() reads "
               "overridden the same way Cartographer.run() does, so a re-propose leaves "
               "a Programme Manager's move, resequence or WIP configuration alone unless "
               "the train's id is named to confirm.",
    ),
    SpecDeviation(
        element="ModelTable.family_ref",
        reason="§4.1.1 declares no edge or property linking a ModelTable back to the "
               "ModelFamily (or SemanticModel) it was proposed for; MAPS_TO only carries a "
               "source Field to a ModelTable's own column (§4.1.2), which does not help a "
               "reader ask 'this family's candidate tables'.",
        detail="A plain reference property, not an edge — the same shape ReportDefinition."
               "mu_ref already uses to link back to an MU that (like a ModelFamily's design "
               "before G2) is not yet a settled first-class record. Backlog story S4.1.1 "
               "needs to list one family's candidate tables without a graph-wide scan.",
    ),
    SpecDeviation(
        element="SemanticModel.grain_statement, SemanticModel.design_generated_at, "
                "SemanticModel.design_provenance_ref, SemanticModel.design_document",
        reason="§4.1.1 does not list them; backlog story S4.1.1 requires a model design "
               "proposal containing a grain statement possibly drafted by a model \"with "
               "provenance recorded\", relationships with cardinality, conformed dimensions "
               "shared with other families, candidate measures with dedup decisions, RLS "
               "roles, a refresh policy and open questions for the data owner.",
        detail="grain_statement, design_generated_at and design_provenance_ref are each a "
               "plain scalar an engineer or a filter would want directly; design_document "
               "holds everything else this story's proposal needs that has no first-class "
               "graph shape yet (relationships have no edge type — §4.1.2 declares none "
               "between ModelTables; a candidate measure is not a Measure node until the "
               "Transpiler gives it dax, per Measure.dax being required). One JSON property "
               "for 'the rest of the document', the same reasoning ReleaseTrain.gate_schedule "
               "and ModelFamily's own evidence_* properties already established for a "
               "structure not yet worth first-class graph citizenship.",
    ),
    SpecDeviation(
        element="ModelFamily.g2_cycle_count",
        reason="§4.1.1 does not list it; backlog story S4.2.1 requires that when a data "
               "owner sends a design back to DRAFT, \"the cycle count is stored\".",
        detail="A plain counter on the family being cycled, incremented once per "
               "request-changes decision — the same footing as ModelFamily's other "
               "S3.1.x/S4.x counters and flags that the base spec table never enumerated "
               "because it predates the story that needed them.",
    ),
    SpecDeviation(
        element="ModelFamily.conformance_ruleset_version",
        reason="§4.1.1 does not list it; backlog story S4.3.2 requires that conformance "
               "rule checks at build time are \"recorded on the ModelFamily at build\".",
        detail="§14's own relational sketch puts a `conformance_json` column on a generic "
               "model_family/semantic_model table — this ontology has neither a generic "
               "table nor a place to hold a whole results blob on a graph node it does not "
               "already have one for; the individual violations already have a home "
               "(`build_run.steps`, story S4.3.1). What belongs on the family itself is the "
               "one fact that outlives any single build: which ruleset version it was last "
               "measured against.",
    ),
    SpecDeviation(
        element="GateDecision.approver_role, GateDecision.version_hash, "
                "GateDecision.countersigner, GateDecision.countersigner_role",
        reason="§13.3's own worked example shows approver and countersign as nested "
               "objects ({user, role, identity}) and a top-level version_hash — §4.1.1's "
               "node table compresses GateDecision to a flat approver/rationale/"
               "evidence_ref/timestamp row with no place for either.",
        detail="AGE node properties are flat everywhere in this ontology (no nested "
               "objects anywhere else either), so §13.3's structure is flattened the same "
               "way: approver_role sits beside approver, countersigner/countersigner_role "
               "sit beside it, and version_hash is promoted to a named property rather "
               "than folded into evidence_ref, which already means something else (an "
               "artefact reference). Story S4.2.1 is the first write of this node type.",
    ),
    SpecDeviation(
        element="SemanticModel.version_number, SemanticModel.published_at, "
                "SemanticModel.deprecated_at",
        reason="§4.1.1 does not list them; backlog story S4.3.3 requires a change request "
               "on a PUBLISHED family to produce a second, independently-versioned model "
               "\"without breaking released reports\", with promotion marking the prior "
               "version \"DEPRECATED with the date\".",
        detail="§12.2's own PUBLISHED row (\"regression suites attached; Change request → "
               "DRAFT (new version); DEPRECATED at retirement\") names the mechanic but not "
               "its data shape; §21's relational sketch gives a `model_family/semantic_"
               "model` table one `version` column, singular — no guidance for two versions "
               "coexisting. version_number is what lets `read_design_document` (and the "
               "console's own Versions list) find 'the current one' deterministically "
               "once a family can have more than one live SemanticModel node at a time; "
               "published_at/deprecated_at are the dated record §12.2 and this story's own "
               "acceptance criteria both ask for. `SemanticModel.state` was declared since "
               "S1.1.1 for exactly this (\"deployment state within an environment\") and is "
               "driven for the first time by this story.",
    ),
    SpecDeviation(
        element="ModelTable.semantic_model_ref",
        reason="Backlog story S4.3.3 requires two SemanticModel versions of one family to "
               "coexist; `family_ref` alone can no longer say which version a ModelTable "
               "belongs to.",
        detail="Every ModelTable read up to this story assumed exactly one live "
               "SemanticModel per family_ref — true by construction, since nothing before "
               "this story ever created a second one. A version-specific reference is what "
               "makes v(n)'s and v(n+1)'s own tables independently editable without either "
               "mutating the other's history; absent on tables written before this story, "
               "where family_ref alone is still unambiguous.",
    ),
    SpecDeviation(
        element="ModelTable.schema, ModelTable.mode_reason, ModelTable.row_estimate, "
                "ModelTable.custom_sql",
        reason="§4.1.1 does not list them; the Modeller (story S4.1.1) computes all four "
               "on every `TableCandidate` it proposes, but the write path never carried "
               "them onto the graph node — found while story S4.3.3 touched the same "
               "NodeWrite to add `semantic_model_ref`.",
        detail="A real, pre-existing gap, not new scope: `propose-design`'s own immediate "
               "HTTP response (built from `TableCandidate.as_dict()` in memory) always "
               "showed these correctly, but every *subsequent* read of the same table — a "
               "reload, a G2 review, TMDL emission itself — hydrated the graph node "
               "instead and got nothing, since these four were never in the properties "
               "dict `Modeller._write` sent to `NodeWrite`. Concretely: every build since "
               "S4.3.1 emitted `Value.NativeQuery(\"positions\")` rather than "
               "`Value.NativeQuery(\"risk.positions\")` (no schema qualifier), and the "
               "console's Design tab has shown '—' for row estimate on every table on "
               "every read after the first. Fixed at the source (`Modeller._write`) rather "
               "than deferred, since this story's own version-copy logic must read these "
               "same fields faithfully to be correct.",
    ),
    SpecDeviation(
        element="CalculatedField.pattern_ref, CalculatedField.reason, "
                "CalculatedField.classifier_version",
        reason="Section 4.1.1 lists only `class (C1..C4, set by Transpiler)` on "
               "CalculatedField; backlog story S5.1.1 requires 'the matched rule or "
               "pattern id, and reason' to be written alongside it, and re-classification "
               "to report 'what moved class' - which needs the previous run's own "
               "ruleset version on the node to compare against.",
        detail="Section 4.1.1's own `pattern_ref` already exists, but only on the "
               "MAPS_TO edge (CalculatedField to Measure) - the pattern a *generated* "
               "Measure came from, written by a Transpiler generation story that does "
               "not exist yet (F5.2/F5.3). This story's own pattern_ref/reason answer a "
               "different, earlier question - why the classifier put this field in this "
               "class, before any generation happens - so a second, node-level pair is "
               "added rather than reusing the edge property for a fact the edge cannot "
               "carry yet. classifier_version is `conformance_ruleset_version`'s own "
               "precedent (S4.3.2): stamped on every classification attempt so a field's "
               "class is always checkable against exactly the rule set that produced it.",
    ),
    SpecDeviation(
        element="CalculatedField.appendix_b_guidance, CalculatedField.redesign_suggestion, "
                "CalculatedField.redesign_suggestion_provenance_ref, "
                "CalculatedField.redesign_decision, CalculatedField.redesign_decision_reason, "
                "CalculatedField.redesign_decision_by, CalculatedField.redesign_decision_at",
        reason="Backlog story S5.4.1 requires 'the Transpiler writes the reason, the "
               "Appendix B guidance, and an ASSISTED-mode redesign suggestion' for every "
               "C4 construct, and 'the MU is BLOCKED until a Migration Engineer records "
               "the redesign decision' — Section 4.1.1 lists only `class`/`pattern_ref`/"
               "`reason` on CalculatedField (the last two already a declared deviation, "
               "S5.1.1) and defines no Migration Unit node at all to hold a BLOCKED state "
               "on (Section 4.1.1's own node table has no `MigrationUnit` row; confirmed "
               "directly against the spec, not assumed from this codebase's own prior "
               "claims about the gap).",
        detail="No Migration Unit record exists anywhere in this codebase to set to "
               "BLOCKED (§3.2) — it is a control-plane concept spanning several nodes "
               "(§3.1), not itself a graph node, and no story before this one has ever "
               "created one. `redesign_decision` absent on the one real, existing "
               "per-construct record (`CalculatedField`) is the disclosed proxy for that "
               "state: a C4 field with no decision yet is exactly a field this platform "
               "would otherwise call BLOCKED. `appendix_b_guidance`/`redesign_suggestion` "
               "are real, deterministic data (Appendix B's own text; a template-composed "
               "suggestion, `AgentMode.ASSISTED` — never a model call, the identical "
               "footing the Modeller's own grain-statement draft already established for "
               "this mode since S3.1.1/S4.1.2). The fuller generic decision-recording "
               "mechanism (a `GateDecision`-shaped record, visible to the report owner by "
               "construction) is S8.3.1's own later, explicit scope (Exception Desk, "
               "milestone I4); a real G3 gate that references these decisions is "
               "S9.1.1/S9.1.2's own later, explicit scope (milestone I5) — this story adds "
               "only what its own acceptance criteria asks for now.",
    ),
    SpecDeviation(
        element="Pattern.guards",
        reason="Section 4.1.1's own node table lists Pattern with no `guards` property at "
               "all — §4.3's worked example (a narrative section, not the §4.1.1 table) "
               "already shows one (`guards: [ 'dims ⊆ model.dimensions', 'a,b numeric' ]`), "
               "and backlog story S5.5.1's own acceptance criteria requires the platform "
               "to store 'its (source AST shape, target template, guards) tuple' for every "
               "candidate pattern — the ontology simply never declared the third element "
               "of that tuple until this story needed to write one.",
        detail="Descriptive text, not a machine-evaluated precondition — the identical "
               "footing `rules.RuleMeta.guards` already has (consulted by a coverage "
               "report and by tests, never enforced by the renderer itself). §9.3 also "
               "says matching is 'guarded on types and model context'; building a real "
               "guard-evaluation engine is a future refinement this story does not "
               "attempt — matching stays exact-shape only, as it already was.",
    ),
    SpecDeviation(
        element="Pattern.failure_count",
        reason="Section 4.1.1's own node table lists Pattern with no `failure_count` "
               "property, and backlog story S5.5.2's own acceptance criteria requires "
               "'a proof failure attributed to an ACTIVE pattern increments its failure "
               "count' — §4.3's own worked example already carries the identical fact "
               "as `stats.proof_fail`, so this is the same gap `Pattern.guards` (S5.5.1) "
               "already found in the same table, for the sibling fact.",
        detail="A point-in-time snapshot, the identical footing `pass_count` already has "
               "(S5.5.1): incremented for visibility on every recorded failure, but never "
               "itself the authority a retirement decision is checked against — that "
               "check always reads `pattern_observation` (append-only) live, so this "
               "counter drifting could never cause a wrong retirement.",
    ),
    SpecDeviation(
        element="Pattern.version, Pattern.supersedes_id",
        reason="Section 4.1.1's own node table declares neither property, and backlog "
               "story S5.5.3's own acceptance criteria requires a Pattern Library screen "
               "action to 'edit guards (creates a new version)' — the ontology never "
               "needed a version concept on Pattern until a story asked to edit one "
               "without silently rewriting its own history.",
        detail="The identical 'an edit is a new version, the old row stays exactly what "
               "it said' discipline `SemanticModel`'s own per-version lifecycle (S4.3.3) "
               "already set for a graph node, and the identical field name "
               "`ProvenanceRecord.supersedes_id` already uses for a provenance row's own "
               "'this replaces that' relationship — reused here rather than inventing a "
               "second name for the same idea. Editing retires the prior version's own "
               "node (`GraphWriter.retire_node`) so `find_matching_pattern`'s own "
               "'one live pattern per shape' invariant never sees two candidates for one "
               "AST shape at once.",
    ),
    SpecDeviation(
        element="Visual.layout",
        reason="Section 4.1.1's own node table gives Visual no property carrying position "
               "or size, and declares no Page or Container node at all -- yet backlog "
               "story S6.1.1's own acceptance criteria requires 'dashboard containers "
               "become report pages with layout preserved at the container level'. Section "
               "8.8 names the source of that layout directly ('lays out dashboards from "
               "the Tableau zone tree into PBIR page layouts'), and Dashboard.layout_json "
               "(already declared, S2.3.2) is exactly that zone tree -- this property is "
               "where one visual's own share of it lands.",
        detail="x/y/width/height, copied from the zone in Dashboard.layout_json whose own "
               "name matches this visual's source sheet -- a plain read, not a second "
               "layout engine. Absent on a visual with no containing dashboard (a "
               "standalone sheet gets its own single-visual page, which needs no preserved "
               "geometry) and on a placeholder visual for an unmapped mark type, where "
               "section 8.8's own 'small model proposing a grid' collision resolution is "
               "future, unbuilt scope this story does not attempt.",
    ),
    SpecDeviation(
        element="ReportDefinition.deploy_state, ReportDefinition.deploy_error",
        reason="Section 4.1.1's own node table declares neither property, and backlog "
               "story S6.1.2's own acceptance criteria requires 'deployment failure "
               "returns the MU to GENERATED with the error on the MU page' -- section 3.2's "
               "own state machine names GENERATED as a real Migration Unit state, but no MU "
               "node exists anywhere in this codebase to hold it (confirmed a sixth time, "
               "the identical gap S5.4.1/S5.5.1/S5.5.2/S5.5.3/S6.1.1 each already found).",
        detail="A disclosed proxy on the one real node this story's own deploy action "
               "touches, not a fabricated MU record. 'GENERATED' after a successful commit "
               "and deploy, 'DEPLOY_FAILED' with the failing step's own detail in "
               "deploy_error after every retry is exhausted; both absent before this "
               "report has ever been deployed.",
    ),
    SpecDeviation(
        element="Visual.interactivity",
        reason="Section 4.1.1's own node table gives Visual no property carrying "
               "parameter or action mappings, and backlog story S6.1.3's own acceptance "
               "criteria requires exactly that: 'interactivity mapping is recorded on the "
               "Visual node'. Section 8.8 already names the source ('translates filters "
               "and parameters to slicers and report-level filters, translates actions to "
               "drill-through and cross-filter settings'); this property is where that "
               "translation for one visual lands.",
        detail="Two lists, computed from real edges/properties this platform already "
               "harvests -- never fabricated. Parameters: found via DEPENDS_ON from a "
               "calculated field this visual's own field wells already reference, "
               "classified by Parameter.domain (list -> slicer, range -> what-if "
               "parameter, any -> unsupported, no bounded Power BI equivalent exists). "
               "Actions: every live Action naming this visual's own source sheet in "
               "source_sheets or target_sheets, classified by Action.type (filter/"
               "highlight -> a cross-filter/highlight setting, url -> a URL field, "
               "parameter/set -> unsupported, Appendix B.2's own 'Parameter and set "
               "actions -> C3 or C4'). A parameter reachable only through a filter or an "
               "action nothing computes -- never through a calculated field -- is a real, "
               "disclosed gap this property does not claim to close: Parameter/Action "
               "nodes carry no edge back to their own Workbook at all (§4.1.2 declares "
               "none), so DEPENDS_ON-from-a-referenced-calculation is the only real path "
               "this platform has to either.",
    ),
    SpecDeviation(
        element="ExceptionCase.class (VISUAL_REDESIGN)",
        reason="Section 11.1's own failure taxonomy is for a parity verdict the Mender "
               "diagnoses after a real proof attempt (FILTER_CONTEXT, NULL_HANDLING, ...); "
               "a visual redesign flag (story S6.1.1) is a pre-proof, structural fact about "
               "a mark type with no Power BI mapping, the identical 'different moment' gap "
               "`generation.py`'s own UNKNOWN-class ExceptionCase already discloses for "
               "pre-proof generation failures. Backlog story S6.2.1's own acceptance "
               "criteria requires exactly this: 'ExceptionCases of class VISUAL_REDESIGN'.",
        detail="Reuses the one real work-item mechanism this platform has (`ExceptionCase`) "
               "for a second, disclosed-different kind of case rather than inventing a "
               "parallel node type -- the same 'one real mechanism, a disclosed second use' "
               "footing `evidence_ref` already has for `generation.py`'s own UNKNOWN case.",
    ),
    SpecDeviation(
        element="ExceptionCase.visual_ref, ExceptionCase.mapping_reason, "
                "ExceptionCase.placeholder_location, ExceptionCase.screenshot_ref, "
                "ExceptionCase.closed_by, ExceptionCase.closed_at, "
                "ExceptionCase.desktop_commit_hash",
        reason="Section 4.1.1's own node table declares none of these -- `ExceptionCase` "
               "as specified carries only what a parity-failure case needs (`mu_ref`, "
               "`class`, `evidence_ref`, `state`, `assignee`, `decision`, `pattern_ref`). "
               "Backlog story S6.2.1's own acceptance criteria requires a VISUAL_REDESIGN "
               "case to carry 'the source screenshot, the mapping reason and the "
               "placeholder location', and closing one to record 'the engineer, the "
               "Desktop commit hash and the date' -- none of the existing properties name "
               "any of these.",
        detail="`visual_ref` names which Visual; `mapping_reason`/`placeholder_location` "
               "are snapshots taken when the case opens, not live reads, so a later "
               "recompose or mapping-table edit cannot quietly rewrite what a still-open "
               "case says it is about. `screenshot_ref` points at an `ArtefactRecord` "
               "(kind 'visual_capture') if one exists -- honestly absent today, since "
               "nothing in this platform's own local/demo environment captures a Tableau "
               "screenshot automatically. `closed_by`/`closed_at`/`desktop_commit_hash` "
               "are the AC's own three closing facts, the identical `*_by`/`*_at` shape "
               "`CalculatedField.redesign_decision_by`/`.redesign_decision_at` (S5.4.1) "
               "already set for a comparable closing action.",
    ),
    SpecDeviation(
        element="ReportDefinition.documentation_artefact_ref, "
                "ReportDefinition.documentation_provenance_ref",
        reason="Section 4.1.1's own node table declares neither -- `ReportDefinition` as "
               "specified carries no reference to a generated documentation page at all. "
               "Backlog story S6.2.2's own acceptance criteria requires the generated "
               "documentation to be 'stored as an artefact and linked from the MU page'; "
               "no MU page exists (F10.3, unbuilt, the identical gap ADRs 0045-0048 each "
               "already found), so the link is made real and queryable from the one real, "
               "existing node this touches instead.",
        detail="`documentation_artefact_ref` is an `ArtefactRecord` id (kind "
               "'report_documentation'); `documentation_provenance_ref` is the "
               "ProvenanceRecord (mode ASSISTED) for the drafting step that produced it -- "
               "the same 'a real fact, additive, on the one node a later story's proxy can "
               "attach to' shape `ReportDefinition.pbir_ref`/`.deploy_state` (S6.1.2) "
               "already set for this exact node.",
    ),
    SpecDeviation(
        element="ParityCase.case_key",
        reason="Section 4.1.1's own node table declares no such property -- `ParityCase` "
               "as specified carries the §10 case schema (grain, measures, filter_ctx, "
               "param_values) with no stable, content-derived identifier of its own. "
               "Backlog story S7.2.1's own acceptance criteria requires 'a stable id' so a "
               "re-derivation finds the same case again rather than minting a fresh one "
               "every time -- the base `id` property cannot serve this: it is a validated "
               "ULID (26 Crockford-base32 characters), which a sha256 digest cannot be "
               "reshaped into without inventing an encoding nobody asked for.",
        detail="A sha256 digest of (sheet_ref, grain, measures, filter_ctx, param_values), "
               "computed the same `context.canonical.context_hash`/`canonical_json` way "
               "every other content-derived key in this codebase already is -- the "
               "identical 'id stays a server-issued identity; a separate property carries "
               "the content-derived key' split `ArtefactRecord.id`/`.content_hash` already "
               "established (S2.4.2), applied to a graph node instead of a stored artefact.",
    ),
)
