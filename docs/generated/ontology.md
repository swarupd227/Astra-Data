<!-- Generated from services/graph-svc/src/astra_graph/ontology by `make ontology`. Do not edit by hand; CI fails if this file and the schema differ. -->

# Estate Graph ontology

Schema version **23**. 28 node types, 15 edge types.

Transcribed from Product Specification v1.0 §4.1.1 (nodes) and §4.1.2 (edges) and enforced at write time by `graph-svc`. Where this file and the specification differ, the difference is declared under [Declared deviations](#declared-deviations) — an undeclared difference fails CI.

## Base properties

Every node carries these regardless of type:

| Property | Type | Required | Notes |
|---|---|---|---|
| `id` | ulid | yes | Platform identifier. Server-issued when the writer does not supply one; adapters supply deterministic ULIDs so a re-harvest is idempotent. |
| `side` | enum(source|target|platform) | yes | Fixed by the node type, except for User which exists on both sides and must declare it. |
| `created_by` | string | yes (server-set) | The agent or user principal that made the write. |
| `created_at` | timestamp | yes (server-set) | Server clock at the write, UTC. |
| `created_in_run` | string | no (server-set) | The agent run that made the write, where the caller declared one. |
| `updated_by` | string | no (server-set) | The principal that last changed the node. Absent until something does: an upsert preserves created_by and sets this instead, so creation attribution survives a re-harvest or a re-score. |
| `updated_at` | timestamp | no (server-set) | Server clock at the last change, UTC. Absent on a node never changed. |
| `retired_at` | timestamp | no (server-set) | Set when the node is retired. A node is never deleted, so this is how a node leaves the working estate while staying in the record (S1.1.3). |
| `retired_by` | string | no (server-set) | The principal that retired the node. |
| `retirement_reason` | text | no (server-set) | Why it was retired. Required at the point of retirement: a retirement with no stated reason is not a decision an auditor can read (spec P4). |

Every edge carries these regardless of type:

| Property | Type | Required | Notes |
|---|---|---|---|
| `id` | ulid | yes | Platform identifier, so an edge can be addressed and superseded. |
| `written_by` | string | yes (server-set) | The agent or user principal that wrote the edge (spec §4.1.2 'Written by'). |
| `created_at` | timestamp | yes (server-set) | Server clock at the write, UTC. |
| `created_in_run` | string | no (server-set) | The agent run that made the write, where the caller declared one. |
| `retired_at` | timestamp | no (server-set) | Set when the edge is retired — 'superseded' in this property's own note above, finally used (story S3.1.2). An edge's endpoints cannot change once created, so replacing one relationship with another is retire-and-recreate, the same shape S1.1.3 gives nodes. |
| `retired_by` | string | no (server-set) | The principal that retired the edge. |
| `retirement_reason` | text | no (server-set) | Why. Required at the point of retirement, the same as a node's (spec P4). |

## Node types

| Node | Side | Spec | Properties |
|---|---|---|---|
| **Site** | source | §4.1.1 | `luid` <br> luid, required <br> `name` <br> string, required <br> `owner` <br> string <br> `user_count` <br> int <br> `licence_cost_annual` <br> float <br> `licence_tier` <br> string |
| **Project** | source | §4.1.1 | `luid` <br> luid, required <br> `name` <br> string, required <br> `parent` <br> luid |
| **Workbook** | source | §4.1.1 | `luid` <br> luid, required <br> `name` <br> string, required <br> `revision` <br> string, required <br> `size` <br> int <br> `extract_flag` <br> bool <br> `last_published` <br> timestamp <br> `views_90d` <br> int <br> `distinct_viewers_90d` <br> int <br> `rls` <br> bool <br> `rls_expression` <br> text <br> `parse_quality` <br> float |
| **Dashboard** | source | §4.1.1 | `name` <br> string, required <br> `size` <br> json <br> `layout_json` <br> json, required <br> `contained_sheets` <br> string[], required <br> `views_90d` <br> int <br> `distinct_viewers_90d` <br> int <br> `last_view` <br> timestamp |
| **Worksheet** | source | §4.1.1 | `name` <br> string, required <br> `mark_type` <br> string <br> `rows_shelf` <br> string[], required <br> `cols_shelf` <br> string[], required <br> `marks_shelf` <br> string[], required <br> `sort` <br> json <br> `filters` <br> json <br> `reference_lines` <br> json <br> `views_90d` <br> int <br> `distinct_viewers_90d` <br> int <br> `last_view` <br> timestamp |
| **Datasource** | source | §4.1.1 | `luid` <br> luid <br> `name` <br> string, required <br> `type` <br> enum(embedded|published), required <br> `connection_ref` <br> string <br> `extract_flag` <br> bool <br> `refresh_schedule` <br> string |
| **Connection** | source | §4.1.1 | `class` <br> enum(sybase|sqlserver|snowflake|postgres|hive|excel|text|odbc|hyper), required <br> `server` <br> string <br> `db` <br> string <br> `schema` <br> string <br> `auth_mode` <br> string |
| **Table** | source | §4.1.1 | `name` <br> string, required <br> `schema` <br> string <br> `custom_sql` <br> text <br> `row_estimate` <br> int |
| **Field** | source | §4.1.1 | `name` <br> string, required <br> `datatype` <br> string, required <br> `role` <br> enum(dimension|measure), required <br> `default_agg` <br> string <br> `hidden` <br> bool |
| **CalculatedField** | source | §4.1.1 | `name` <br> string, required <br> `formula` <br> text, required <br> `formula_ast` <br> json, required <br> `class` <br> enum(C1|C2|C3|C4) <br> `lod_type` <br> enum(FIXED|INCLUDE|EXCLUDE) <br> `table_calc_flag` <br> bool <br> `depends_on` <br> string[] <br> `pattern_ref` <br> string <br> `reason` <br> text <br> `classifier_version` <br> int <br> `appendix_b_guidance` <br> text <br> `redesign_suggestion` <br> text <br> `redesign_suggestion_provenance_ref` <br> string <br> `redesign_decision` <br> enum(IMPLEMENT_AS_SUGGESTED|ALTERNATIVE|DROP) <br> `redesign_decision_reason` <br> text <br> `redesign_decision_by` <br> string <br> `redesign_decision_at` <br> timestamp |
| **Parameter** | source | §4.1.1 | `name` <br> string, required <br> `datatype` <br> string, required <br> `domain` <br> enum(list|range|any), required <br> `default` <br> json <br> `current_values_seen` <br> string[] |
| **Filter** | source | §4.1.1 | `field_ref` <br> string, required <br> `type` <br> enum(categorical|range|relative_date|top_n|condition), required <br> `values` <br> json <br> `context_flag` <br> bool |
| **Action** | source | §4.1.1 | `type` <br> enum(filter|highlight|url|parameter|set), required <br> `source_sheets` <br> string[], required <br> `target_sheets` <br> string[] |
| **User** | source \| target (declared per node) | §4.1.1 | `upn` <br> string, required <br> `display` <br> string <br> `licence_tier` <br> string <br> `site_roles` <br> string[] <br> `directory_id` <br> string <br> `directory_resolved_at` <br> timestamp |
| **ModelFamily** | target | §4.1.1 | `name` <br> string, required <br> `domain` <br> string <br> `grain` <br> string <br> `state` <br> enum(PROPOSED|SINGLETON|DRAFT|IN_REVIEW|APPROVED|BUILT|PUBLISHED|DEPRECATED), required <br> `owner` <br> string <br> `conformed_dims` <br> string[] <br> `reason` <br> string <br> `evidence_shared_tables` <br> string[] <br> `evidence_shared_fields` <br> string[] <br> `evidence_shared_calc_shapes` <br> int <br> `overridden` <br> bool <br> `override_action` <br> enum(SPLIT|MERGE|MOVE) <br> `override_reason` <br> string <br> `g2_cycle_count` <br> int <br> `conformance_ruleset_version` <br> int |
| **SemanticModel** | target | §4.1.1 | `family_ref` <br> string, required <br> `tmdl_ref` <br> string <br> `workspace` <br> string <br> `state` <br> string <br> `version` <br> string <br> `rls_roles` <br> string[] <br> `grain_statement` <br> string <br> `design_generated_at` <br> timestamp <br> `design_provenance_ref` <br> string <br> `design_document` <br> json <br> `version_number` <br> int <br> `published_at` <br> timestamp <br> `deprecated_at` <br> timestamp |
| **ModelTable** | target | §4.1.1 | `name` <br> string, required <br> `source_table_refs` <br> string[] <br> `mode` <br> enum(import|directlake|directquery), required <br> `family_ref` <br> string, required <br> `semantic_model_ref` <br> string <br> `schema` <br> string <br> `mode_reason` <br> string <br> `row_estimate` <br> int <br> `custom_sql` <br> bool |
| **Measure** | target | §4.1.1 | `name` <br> string, required <br> `dax` <br> text, required <br> `m_query` <br> text <br> `source_calc_ref` <br> string <br> `class` <br> enum(C1|C2|C3|C4) <br> `pattern_ref` <br> string <br> `provenance_ref` <br> string, required <br> `validation_state` <br> string |
| **ReportDefinition** | target | §4.1.1 | `mu_ref` <br> string, required <br> `pbir_ref` <br> string <br> `pages` <br> string[] <br> `model_ref` <br> string, required <br> `version` <br> string <br> `validation_state` <br> string <br> `deploy_state` <br> string <br> `deploy_error` <br> string |
| **Visual** | target | §4.1.1 | `page` <br> string, required <br> `type` <br> string, required <br> `source_sheet_ref` <br> string <br> `encodings` <br> json <br> `redesign_flag` <br> bool <br> `redesign_reason` <br> string <br> `layout` <br> json |
| **ParityCase** | target | §4.1.1, §10 | `mu_ref` <br> string, required <br> `sheet_ref` <br> string, required <br> `grain` <br> string[], required <br> `measures` <br> string[], required <br> `filter_ctx` <br> json <br> `param_values` <br> json <br> `expected_ref` <br> string <br> `candidate_ref` <br> string <br> `state` <br> string |
| **ParityRun** | target | §4.1.1, §10 | `suite_ref` <br> string, required <br> `charter_version` <br> string, required <br> `started` <br> timestamp <br> `finished` <br> timestamp <br> `verdicts` <br> string[] |
| **Verdict** | target | §4.1.1, §10.3 | `case_ref` <br> string, required <br> `result` <br> enum(PASS|FAIL|INCONCLUSIVE), required <br> `failing_cells` <br> json <br> `evidence_ref` <br> string |
| **ExceptionCase** | target | §4.1.1, §11.3 | `mu_ref` <br> string, required <br> `class` <br> enum(FILTER_CONTEXT|NULL_HANDLING|DATE_GRAIN|AGGREGATION|TYPE_COERCION|LOD_SCOPE|TABLE_CALC|SORT_LIMIT|KEY_MISSING|SOURCE_DRIFT|UNKNOWN), required <br> `evidence_ref` <br> string <br> `state` <br> string <br> `assignee` <br> string <br> `decision` <br> string <br> `pattern_ref` <br> string |
| **Pattern** | platform | §4.1.1, §4.3 | `name` <br> string, required <br> `class` <br> enum(C1|C2|C3|C4), required <br> `source_signature` <br> json, required <br> `target_template` <br> text, required <br> `guards` <br> string[] <br> `provenance` <br> json <br> `promotion_state` <br> enum(CANDIDATE|ACTIVE|RETIRED), required <br> `pass_count` <br> int <br> `failure_count` <br> int <br> `version` <br> int <br> `supersedes_id` <br> string |
| **GateDecision** | platform | §4.1.1, §13.3 | `gate` <br> enum(G1|G2|G3|G4), required <br> `subject_ref` <br> string, required <br> `decision` <br> enum(APPROVED|REJECTED|CHANGES_REQUESTED|WAIVED), required <br> `approver` <br> string, required <br> `rationale` <br> text <br> `evidence_ref` <br> string <br> `timestamp` <br> timestamp, required <br> `approver_role` <br> string <br> `version_hash` <br> string <br> `countersigner` <br> string <br> `countersigner_role` <br> string |
| **ReleaseTrain** | platform | §4.1.1, §3.3 | `name` <br> string, required <br> `mu_refs` <br> string[] <br> `planned_start` <br> date <br> `planned_end` <br> date <br> `actual_start` <br> date <br> `actual_end` <br> date <br> `gate_schedule` <br> json <br> `wip_limits` <br> json <br> `overridden` <br> bool <br> `override_action` <br> enum(MOVE|RESEQUENCE|WIP_LIMITS) <br> `override_reason` <br> string |
| **Wave** | platform | §4.1.1, §3.3 | `name` <br> string, required <br> `mu_refs` <br> string[] <br> `planned_start` <br> date <br> `planned_end` <br> date <br> `actual_start` <br> date <br> `actual_end` <br> date |

### Site

From Metadata API plus the licence export.

| Property | Type | Required | Notes |
|---|---|---|---|
| `luid` | luid | yes |  |
| `name` | string | yes |  |
| `owner` | string | no | Reference to a User node. |
| `user_count` | int | no |  |
| `licence_cost_annual` | float | no | From the client's licence export; drives the decommission business case (spec §21 site_record). |
| `licence_tier` | string | no | The site's licensing model, where the source exposes it. Absent when the adapter has no ownership capability (story S1.2.3). |

### Project

Hierarchy preserved.

| Property | Type | Required | Notes |
|---|---|---|---|
| `luid` | luid | yes |  |
| `name` | string | yes |  |
| `parent` | luid | no | Parent project LUID; absent on a top-level project. |

### Workbook

One Migration Unit per Workbook.

| Property | Type | Required | Notes |
|---|---|---|---|
| `luid` | luid | yes |  |
| `name` | string | yes |  |
| `revision` | string | yes | Source revision identifier. String because the Tableau REST API returns revisionNumber as a string; the Harvester is idempotent on it. |
| `size` | int | no | Bytes. |
| `extract_flag` | bool | no |  |
| `last_published` | timestamp | no |  |
| `views_90d` | int | no | Trailing-window usage; absent where the source does not expose usage. |
| `distinct_viewers_90d` | int | no |  |
| `rls` | bool | no | Row-level security detected in the workbook (story S2.3.2). Absent is not the same as false: a workbook harvested before the adapter looked for it has neither. |
| `rls_expression` | text | no | The user-filter expression, verbatim. Kept because the target has to reproduce the access model, and 'this workbook restricts rows' without saying how is not something a Modeller can act on. |
| `parse_quality` | float | no | Fraction of this workbook's source constructs the adapter grammar could read, counting constructs an engineer has accepted as ignorable (spec §4.1.4, story S1.2.2). Absent until harvested. |

### Dashboard

Layout retained for the Compositor.

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `size` | json | no | Source dashboard size specification (type and, for fixed sizes, width and height). |
| `layout_json` | json | yes | The zone tree. Required because the Compositor derives page layout from it and has no fallback (spec §8.8). |
| `contained_sheets` | string[] | yes | May be empty; absence and emptiness are different facts. |
| `views_90d` | int | no | Trailing-window usage for this view. Tableau reports usage per published view, not only per workbook, and S1.2.3 needs both: a wave is ordered by business impact, and a workbook whose usage sits in one dashboard is a different proposition from one spread across six sheets. |
| `distinct_viewers_90d` | int | no |  |
| `last_view` | timestamp | no |  |

### Worksheet

The visual specification. Parity cases are derived from these fields, so the shelves are required (spec §10.1).

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `mark_type` | string | no | Source mark type, including 'Automatic'. |
| `rows_shelf` | string[] | yes |  |
| `cols_shelf` | string[] | yes |  |
| `marks_shelf` | string[] | yes | Colour, size, shape, detail, label, tooltip and path encodings. |
| `sort` | json | no | List of sort specifications. |
| `filters` | json | no | List of filter specifications; also materialised as FILTERED_BY edges. |
| `reference_lines` | json | no | List of reference line and band specifications. |
| `views_90d` | int | no | Trailing-window usage for this view. Tableau reports usage per published view, not only per workbook, and S1.2.3 needs both: a wave is ordered by business impact, and a workbook whose usage sits in one dashboard is a different proposition from one spread across six sheets. |
| `distinct_viewers_90d` | int | no |  |
| `last_view` | timestamp | no |  |

### Datasource

| Property | Type | Required | Notes |
|---|---|---|---|
| `luid` | luid | no | Published datasources carry a LUID; embedded ones do not. |
| `name` | string | yes |  |
| `type` | enum(embedded|published) | yes |  |
| `connection_ref` | string | no |  |
| `extract_flag` | bool | no |  |
| `refresh_schedule` | string | no |  |

### Connection

Drives platform-side ingestion planning.

| Property | Type | Required | Notes |
|---|---|---|---|
| `class` | enum(sybase|sqlserver|snowflake|postgres|hive|excel|text|odbc|hyper) | yes | Closed set per the specification. A source estate using a class outside it is rejected at write time; see docs/adr/0001 open questions. |
| `server` | string | no |  |
| `db` | string | no |  |
| `schema` | string | no |  |
| `auth_mode` | string | no |  |

### Table

custom_sql retained verbatim.

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `schema` | string | no |  |
| `custom_sql` | text | no | Nullable. Retained byte-for-byte: the live-replay proof strategy re-executes it (spec §6.2). |
| `row_estimate` | int | no |  |

### Field

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `datatype` | string | yes |  |
| `role` | enum(dimension|measure) | yes |  |
| `default_agg` | string | no |  |
| `hidden` | bool | no |  |

### CalculatedField

AST from the adapter parser.

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `formula` | text | yes | Verbatim source expression. |
| `formula_ast` | json | yes | Grammar-backed AST. Required: classification, pattern matching and context assembly all key off it (spec §9.1, §9.3). |
| `class` | enum(C1|C2|C3|C4) | no | Set by the Transpiler, absent at harvest. |
| `lod_type` | enum(FIXED|INCLUDE|EXCLUDE) | no | Absent when the expression is not a level-of-detail expression. |
| `table_calc_flag` | bool | no |  |
| `depends_on` | string[] | no | Also materialised as DEPENDS_ON edges; retained here as the parser recorded it. |
| `pattern_ref` | string | no | The classifier rule or Pattern Library id that decided `class` (story S5.1.1). Absent alongside `class`, at harvest. |
| `reason` | text | no | Why `class` is what it is — the specific construct the classifier found, in Appendix B.1's own terms (story S5.1.1). |
| `classifier_version` | int | no | Which classifier ruleset version last classified this field — the same 'stamped on every attempt' footing conformance_ruleset_version already has (story S5.1.1). |
| `appendix_b_guidance` | text | no | Appendix B's own target/notes text for the rule that produced a C4 verdict (story S5.4.1). Absent unless `class` is C4. |
| `redesign_suggestion` | text | no | A real, deterministic ASSISTED-mode next-step suggestion (story S5.4.1) — never a model call, the same footing the Modeller's own grain-statement draft already established for this mode. Absent unless `class` is C4. |
| `redesign_suggestion_provenance_ref` | string | no | The ProvenanceRecord (mode ASSISTED) for `redesign_suggestion` (story S5.4.1). Absent alongside it. |
| `redesign_decision` | enum(IMPLEMENT_AS_SUGGESTED|ALTERNATIVE|DROP) | no | A Migration Engineer's own recorded decision (story S5.4.1). Absent is the disclosed proxy for a C4 field's own Migration Unit being BLOCKED (§3.2) — no real MU record exists anywhere in this codebase to hold that state (§4.1.1 declares none; confirmed by research, not assumed). |
| `redesign_decision_reason` | text | no | The engineer's own rationale (story S5.4.1) — for a DROP decision, where the report-owner agreement the acceptance criteria requires is recorded, since this platform has no separate co-sign workflow (that is G2's own dedicated mechanism, not rebuilt here). |
| `redesign_decision_by` | string | no | The principal who recorded the decision. |
| `redesign_decision_at` | timestamp | no | When the decision was recorded. |

### Parameter

Domain bounds the Arbiter's enumeration (spec §10.1).

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `datatype` | string | yes |  |
| `domain` | enum(list|range|any) | yes |  |
| `default` | json | no | Scalar of the parameter's own datatype. |
| `current_values_seen` | string[] | no | Observed values from usage, where the adapter provides them. |

### Filter

| Property | Type | Required | Notes |
|---|---|---|---|
| `field_ref` | string | yes |  |
| `type` | enum(categorical|range|relative_date|top_n|condition) | yes |  |
| `values` | json | no |  |
| `context_flag` | bool | no | Tableau context filter. |

### Action

Interactivity mapping.

| Property | Type | Required | Notes |
|---|---|---|---|
| `type` | enum(filter|highlight|url|parameter|set) | yes |  |
| `source_sheets` | string[] | yes |  |
| `target_sheets` | string[] | no | Absent for a URL action. |

### User

Entra-linked. The only type the specification marks as existing on both sides, so the writer declares 'side' explicitly.

| Property | Type | Required | Notes |
|---|---|---|---|
| `upn` | string | yes |  |
| `display` | string | no |  |
| `licence_tier` | string | no | Creator / Explorer / Viewer, where exposed. |
| `site_roles` | string[] | no |  |
| `directory_id` | string | no | The directory object id this source user resolved to. Its absence is the fact the unresolved-owner list is built from: a workbook whose owner did not resolve cannot be sent a gate request (story S1.2.3). |
| `directory_resolved_at` | timestamp | no | When the link was made, and by extension whether it was made by the resolver or by a person assigning it. |

### ModelFamily

Proposed by the Cartographer.

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `domain` | string | no |  |
| `grain` | string | no | Candidate grain inferred from member sheets; confirmed at G2. |
| `state` | enum(PROPOSED|SINGLETON|DRAFT|IN_REVIEW|APPROVED|BUILT|PUBLISHED|DEPRECATED) | yes | Spec §12.2. |
| `owner` | string | no |  |
| `conformed_dims` | string[] | no |  |
| `reason` | string | no | Why this family is SINGLETON rather than clustered or merged (story S3.1.1). Absent otherwise. |
| `evidence_shared_tables` | string[] | no | Tables reached by two or more members — the evidence the clustering was made from (story S3.1.1), not the union of everything every member reaches. |
| `evidence_shared_fields` | string[] | no | Fields encoded by two or more members. |
| `evidence_shared_calc_shapes` | int | no | Count of distinct calculation AST shapes (context.signature.ast_shape) defined by two or more members. |
| `overridden` | bool | no | A human split, merged or moved a member into or out of this family (story S3.1.2). Pinned: a Cartographer re-run reports what it would change here and does not apply it without confirmation. |
| `override_action` | enum(SPLIT|MERGE|MOVE) | no | Which kind of override last touched this family. Absent on a family no human has touched. |
| `override_reason` | string | no | The stated reason for the most recent override — who and when are already carried by the base properties updated_by/updated_at. |
| `g2_cycle_count` | int | no | How many times this family has been sent back to DRAFT from G2 review (story S4.2.1's 'the cycle count is stored'). Absent means zero — no request-changes has happened yet. |
| `conformance_ruleset_version` | int | no | Which version of the §12.3 conformance ruleset this family's most recent build was checked against (story S4.3.2's 'recorded on the ModelFamily at build'). Absent means never built. |

### SemanticModel

One per family per environment.

| Property | Type | Required | Notes |
|---|---|---|---|
| `family_ref` | string | yes |  |
| `tmdl_ref` | string | no | Artefact reference to the TMDL folder. |
| `workspace` | string | no |  |
| `state` | string | no | Deployment state within an environment; the family lifecycle is on ModelFamily.state. |
| `version` | string | no |  |
| `rls_roles` | string[] | no |  |
| `grain_statement` | string | no | A prose rendering of ModelFamily.grain, drafted at proposal time (story S4.1.1). 'One row per X, Y, Z' rather than the terse candidate grain tuple the Cartographer clusters on — the Semantic Model Engineer edits this in the Model Detail screen, S4.1.2. |
| `design_generated_at` | timestamp | no | When the Modeller last generated a design proposal for this model. Absent until the first proposal (story S4.1.1). |
| `design_provenance_ref` | string | no | The ProvenanceRecord for this proposal's ASSISTED-mode drafting step (naming, grain_statement) — spec §16.2's 'provenance recorded'. |
| `design_document` | json | no | The rest of the design proposal (story S4.1.1): relationships (with cardinality), candidate measures (source calc refs, dedup decisions), conformed dimensions (which other families share them), an RLS role detail per name in rls_roles, refresh policy, and open questions. Not split into first-class properties or nodes because none of it is a first-class graph concept yet — a candidate measure has no dax, and is not a Measure node, until the Transpiler (E5) produces one. |
| `version_number` | int | no | 1, 2, 3... — which version of this family's model this is (story S4.3.3's own v(n)/v(n+1)). Absent means 1: every family had exactly one SemanticModel before this story, so an absent number is unambiguous, never a second version to confuse it with. |
| `published_at` | timestamp | no | When this version's state last became PUBLISHED (story S4.3.3). Absent until then. |
| `deprecated_at` | timestamp | no | When this version's state became DEPRECATED — set the moment its successor is promoted (story S4.3.3's 'marks v(n) DEPRECATED with the date'). Absent for a version that has never been superseded. |

### ModelTable

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `source_table_refs` | string[] | no |  |
| `mode` | enum(import|directlake|directquery) | yes |  |
| `family_ref` | string | yes | Which ModelFamily's design this table is a candidate for. §4.1.1 declares no edge from a ModelTable back to its family or SemanticModel (MAPS_TO only carries a source Field to a ModelTable's column) — a plain reference, the same shape as ReportDefinition.mu_ref (S2.4.2). |
| `semantic_model_ref` | string | no | Which SemanticModel *version* this table belongs to (story S4.3.3) — family_ref alone stopped being enough to find 'the' design the moment a family could have two live SemanticModel nodes at once (a published v(n) and a draft v(n+1)). Absent on tables written before this story; read_design_document falls back to family_ref for those, safe only because a pre-S4.3.3 family never had a second version to confuse it with. |
| `schema` | string | no | The source table's own schema, carried onto its ModelTable candidate at proposal time (`TableCandidate.schema`, computed since S4.1.1) — declared and finally persisted by story S4.3.3, which found it silently dropped at write time while adding semantic_model_ref to the same NodeWrite: every build since S4.1.1 emitted TMDL with an unqualified table name because this was never on the node to read back. |
| `mode_reason` | string | no | The disclosed heuristic behind `mode` (ADR 0028) — computed at proposal time, same gap and same fix as `schema`. |
| `row_estimate` | int | no | Carried from the source Table's own row_estimate — same gap and fix as `schema`; the console's Design tab has shown '—' here on every read after the first, since generation's own immediate response was the only place this ever appeared. |
| `custom_sql` | bool | no | Whether this table's source is custom SQL (merged from more than one underlying table) — same gap and fix as `schema`. |

### Measure

The Transpiler's product.

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `dax` | text | yes |  |
| `m_query` | text | no | Nullable; present where the transformation lands in Power Query rather than DAX. |
| `source_calc_ref` | string | no |  |
| `class` | enum(C1|C2|C3|C4) | no |  |
| `pattern_ref` | string | no |  |
| `provenance_ref` | string | yes | Mandatory: the artefact store rejects a write without a provenance record (spec §16.2). |
| `validation_state` | string | no | Position on the validation ladder (spec §16.1). Left as free text until E5/E7 fix the closed set; see docs/adr/0001 open questions. |

### ReportDefinition

The Compositor's product.

| Property | Type | Required | Notes |
|---|---|---|---|
| `mu_ref` | string | yes |  |
| `pbir_ref` | string | no | The Git commit this report last deployed from (story S6.1.2) -- absent until the first successful commit; the Fabric item path itself is not recorded here, since it is derived from the workbook's own name and never stored as an independent fact. |
| `pages` | string[] | no |  |
| `model_ref` | string | yes | A report definition that binds to no model cannot be proved. |
| `version` | string | no |  |
| `validation_state` | string | no | Position on the validation ladder (spec §16.1). Left as free text until E5/E7 fix the closed set; see docs/adr/0001 open questions. |
| `deploy_state` | string | no | The disclosed proxy for this report's own place in §3.2's MU state machine (story S6.1.2) -- no real Migration Unit node exists anywhere in this codebase to hold GENERATED/PROVING/etc for real (confirmed a sixth time, the identical gap S5.4.1/S5.5.1/S5.5.2/S5.5.3/S6.1.1 each already found). 'GENERATED' after a successful commit and deploy; 'DEPLOY_FAILED' after every retry is exhausted -- the AC's own 'deployment failure returns the MU to GENERATED' read as 'never claims the deploy succeeded', since nothing here can roll back a state that was never really entered by a real MU record to begin with. Absent before this report has ever been deployed at all. |
| `deploy_error` | string | no | The failing step's own detail (story S6.1.2) -- 'the error on the MU page' (F10.3, not yet built); absent whenever deploy_state is absent or 'GENERATED'. |

### Visual

| Property | Type | Required | Notes |
|---|---|---|---|
| `page` | string | yes |  |
| `type` | string | yes |  |
| `source_sheet_ref` | string | no | Absent on a visual with no source sheet, such as a placeholder card for a redesigned visual. |
| `encodings` | json | no |  |
| `redesign_flag` | bool | no |  |
| `redesign_reason` | string | no |  |
| `layout` | json | no | x/y/width/height (story S6.1.1's own 'layout preserved at the container level') — the geometry of the dashboard zone this visual's source sheet occupied, read straight off Dashboard.layout_json's own zone tree. Absent on a standalone sheet with no containing dashboard: its own page has just the one visual, filling it, so there is no container geometry to preserve. |

### ParityCase

| Property | Type | Required | Notes |
|---|---|---|---|
| `mu_ref` | string | yes |  |
| `sheet_ref` | string | yes |  |
| `grain` | string[] | yes | The key of the result set; a case without a grain is not executable. |
| `measures` | string[] | yes |  |
| `filter_ctx` | json | no |  |
| `param_values` | json | no |  |
| `expected_ref` | string | no | Result set from the source side. |
| `candidate_ref` | string | no | Result set from the target side. |
| `state` | string | no |  |

### ParityRun

| Property | Type | Required | Notes |
|---|---|---|---|
| `suite_ref` | string | yes |  |
| `charter_version` | string | yes | Every run records the Tolerance Charter version it ran under (spec §4.4). |
| `started` | timestamp | no |  |
| `finished` | timestamp | no |  |
| `verdicts` | string[] | no |  |

### Verdict

| Property | Type | Required | Notes |
|---|---|---|---|
| `case_ref` | string | yes |  |
| `result` | enum(PASS|FAIL|INCONCLUSIVE) | yes |  |
| `failing_cells` | json | no | Bounded sample; the full bundle is an artefact. |
| `evidence_ref` | string | no |  |

### ExceptionCase

| Property | Type | Required | Notes |
|---|---|---|---|
| `mu_ref` | string | yes |  |
| `class` | enum(FILTER_CONTEXT|NULL_HANDLING|DATE_GRAIN|AGGREGATION|TYPE_COERCION|LOD_SCOPE|TABLE_CALC|SORT_LIMIT|KEY_MISSING|SOURCE_DRIFT|UNKNOWN) | yes | Spec §11.1. |
| `evidence_ref` | string | no |  |
| `state` | string | no |  |
| `assignee` | string | no |  |
| `decision` | string | no |  |
| `pattern_ref` | string | no | The pattern candidate the decision produced. |

### Pattern

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `class` | enum(C1|C2|C3|C4) | yes |  |
| `source_signature` | json | yes | AST shape with leaf identifiers abstracted to typed placeholders. |
| `target_template` | text | yes |  |
| `guards` | string[] | no | Human-readable preconditions the captured placeholders must satisfy (§4.3's own worked example: ['dims ⊆ model.dimensions', 'a,b numeric']) -- story S5.5.1. Descriptive, not machine-evaluated, the identical footing `RuleMeta.guards` already has (consulted by a coverage report, never enforced by a renderer); matching stays exact-shape only. |
| `provenance` | json | no | Origin, first sighting, promotion timestamp. |
| `promotion_state` | enum(CANDIDATE|ACTIVE|RETIRED) | yes | Spec §4.3. |
| `pass_count` | int | no | A point-in-time snapshot (written at creation and at promotion), not a live-maintained counter -- story S5.5.1's own `pattern_observation` table (append-only, one row per real proof pass or failure) is the authoritative source promotion eligibility is actually checked against, the identical 'computed from the raw table, never a maintained running total' footing `calibration_observation` already set (S5.3.3). |
| `failure_count` | int | no | Story S5.5.2's own 'a proof failure ... increments its failure count' -- incremented on every recorded failure, for any promotion_state, the same 'proof_fail' fact spec §4.3's own worked `stats` example names. A point-in-time snapshot, like `pass_count`: the retirement threshold itself is always checked live against `pattern_observation`, never against this counter, so a missed increment could never cause a wrong retirement decision. |
| `version` | int | no | Story S5.5.3's own 'edit guards (creates a new version)' -- absent means 1, the same 'additive, no backfill' reading every optional counter in this codebase already gets. Editing never mutates a Pattern in place: it writes a new node with `version` incremented and `supersedes_id` naming this one, then retires this one -- the identical 'an edit is a new version, the old row is never touched' discipline `SemanticModel`'s own per-version lifecycle (S4.3.3) already set for a graph node specifically. |
| `supersedes_id` | string | no | The Pattern this version replaced (story S5.5.3) -- absent on a pattern that has never been edited. The identical field name `ProvenanceRecord.supersedes_id` already uses for the same 'this new record replaces that one' relationship, applied here to a graph node instead of a provenance row. |

### GateDecision

| Property | Type | Required | Notes |
|---|---|---|---|
| `gate` | enum(G1|G2|G3|G4) | yes |  |
| `subject_ref` | string | yes |  |
| `decision` | enum(APPROVED|REJECTED|CHANGES_REQUESTED|WAIVED) | yes |  |
| `approver` | string | yes | A gate decision without a named approver is not a decision (spec P4). |
| `rationale` | text | no |  |
| `evidence_ref` | string | no |  |
| `timestamp` | timestamp | yes | When the approver decided, which is not the same instant as the write. |
| `approver_role` | string | no | §13.1's own approver column names a role, not just a person — 'client data owner for the domain'. Story S4.2.1. |
| `version_hash` | string | no | The frozen artefact version this decision is about — §13.3's own 'version_hash: sha256:…'. For G2, SemanticModel.version (S4.1.2). |
| `countersigner` | string | no | §13.1's approver/countersign pairs (e.g. G2: data owner approves, Semantic Model Engineer countersigns). Absent when a gate has none. |
| `countersigner_role` | string | no |  |

### ReleaseTrain

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `mu_refs` | string[] | no | Not populated by story S3.2.1 — IN_TRAIN edges are the single source of truth for membership, the same way IN_FAMILY edges are for ModelFamily, which carries no members-list property either. |
| `planned_start` | date | no |  |
| `planned_end` | date | no |  |
| `actual_start` | date | no |  |
| `actual_end` | date | no |  |
| `gate_schedule` | json | no | Planned G2/G3 windows for this train's members (story S3.2.1). A first-cut plan, not a throughput projection — that is the wave scheduler's job (§14.2, backlog S3.2.3), not built yet. |
| `wip_limits` | json | no | Configured work-in-progress caps for the Wave Board (story S3.2.2): {'train': <int\|null>, 'states': {<state>: <int>, ...}}. Absent means no limit is configured — a train starts open, the same way a family starts un-overridden. |
| `overridden` | bool | no | A Programme Manager moved, resequenced or WIP-limited this train on the Wave Board (story S3.2.2). Pinned the same way S3.1.2's ModelFamily.overridden is: a train proposal re-run leaves this train and its members alone unless its id is named to confirm. |
| `override_action` | enum(MOVE|RESEQUENCE|WIP_LIMITS) | no | Which kind of Wave Board edit last touched this train. |
| `override_reason` | string | no | The stated reason for the most recent override — who and when are the base properties updated_by/updated_at every node already carries. |

### Wave

A calendar window in which one or more trains execute.

| Property | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes |  |
| `mu_refs` | string[] | no |  |
| `planned_start` | date | no |  |
| `planned_end` | date | no |  |
| `actual_start` | date | no |  |
| `actual_end` | date | no |  |

## Edge types

| Edge | Permitted endpoints | Written by | Spec | Properties |
|---|---|---|---|---|
| **CONTAINS** | Site→Project; Project→Project; Project→Workbook; Workbook→Dashboard; Workbook→Worksheet | Harvester | §4.1.2 | — |
| **USES_DATASOURCE** | Worksheet→Datasource | Harvester | §4.1.2 | — |
| **CONNECTS_TO** | Datasource→Connection; Connection→Table | Harvester | §4.1.2 | `join_clause` <br> text |
| **HAS_FIELD** | Table→Field; Datasource→Field; Datasource→CalculatedField | Harvester | §4.1.2 | — |
| **DEPENDS_ON** | CalculatedField→Field; CalculatedField→CalculatedField; CalculatedField→Parameter | Harvester (from AST) | §4.1.2 | `position_in_ast` <br> string |
| **ENCODES** | Worksheet→Field; Worksheet→CalculatedField | Harvester | §4.1.2 | `shelf` <br> string, required <br> `aggregation` <br> string <br> `sort` <br> json |
| **FILTERED_BY** | Worksheet→Filter; Dashboard→Filter | Harvester | §4.1.2 | — |
| **OWNED_BY** | Workbook→User | Harvester | §4.1.2 | — |
| **VIEWED_BY** | Workbook→User | Harvester | §4.1.2 | `views_90d` <br> int <br> `last_view` <br> timestamp |
| **SHARES_LINEAGE** | Workbook→Workbook | Cartographer (derived) | §4.1.2 | `jaccard_tables` <br> float, required <br> `jaccard_fields` <br> float, required <br> `shared_calc_count` <br> int, required |
| **IN_FAMILY** | Workbook→ModelFamily | Cartographer | §4.1.2 | `confidence` <br> float, required |
| **IN_TRAIN** | Workbook→ReleaseTrain | Cartographer / Programme Manager | §4.1.2 | `sequence` <br> int, required <br> `state` <br> string <br> `wip_override_reason` <br> string |
| **MAPS_TO** | Field→ModelTable; CalculatedField→Measure; Worksheet→Visual | Modeller / Transpiler / Compositor | §4.1.2 | `class` <br> enum(C1|C2|C3|C4) <br> `pattern_ref` <br> string <br> `target_column` <br> string |
| **PROVED_BY** | ReportDefinition→ParityRun | Arbiter | §4.1.2 | `charter_version` <br> string, required |
| **DECIDED_BY** | *→GateDecision | Gate workflow | §4.1.2 | — |

### CONTAINS

Endpoints: Site→Project; Project→Project; Project→Workbook; Workbook→Dashboard; Workbook→Worksheet. Written by Harvester.

Project→Project is the nested-project case; §4.1.1 states the Project hierarchy is preserved.

No type-specific properties.

### USES_DATASOURCE

Endpoints: Worksheet→Datasource. Written by Harvester.

No type-specific properties.

### CONNECTS_TO

Endpoints: Datasource→Connection; Connection→Table. Written by Harvester.

| Property | Type | Required | Notes |
|---|---|---|---|
| `join_clause` | text | no | Carried on the Connection→Table edges, per the specification. |

### HAS_FIELD

Endpoints: Table→Field; Datasource→Field; Datasource→CalculatedField. Written by Harvester.

Datasource→CalculatedField: see EDGE_SPEC_DEVIATIONS below. Not new behaviour — the adapter has written this edge since S2.3.1; the ontology just never permitted it, so it could only be written by a harvest nothing had yet run against the real ontology.

No type-specific properties.

### DEPENDS_ON

Endpoints: CalculatedField→Field; CalculatedField→CalculatedField; CalculatedField→Parameter. Written by Harvester (from AST).

| Property | Type | Required | Notes |
|---|---|---|---|
| `position_in_ast` | string | no | Path to the referencing node within the AST, so the transitive closure the Transpiler assembles is ordered and reproducible (spec §4.1.3). |

### ENCODES

Endpoints: Worksheet→Field; Worksheet→CalculatedField. Written by Harvester.

| Property | Type | Required | Notes |
|---|---|---|---|
| `shelf` | string | yes | Which shelf the field sits on. Required: parity case grain is derived from shelf placement (spec §10.1), so an unplaced encoding is unusable. |
| `aggregation` | string | no |  |
| `sort` | json | no |  |

### FILTERED_BY

Endpoints: Worksheet→Filter; Dashboard→Filter. Written by Harvester.

No type-specific properties.

### OWNED_BY

Endpoints: Workbook→User. Written by Harvester.

Ownership routes gate requests to a named person (spec §15.1).

No type-specific properties.

### VIEWED_BY

Endpoints: Workbook→User. Written by Harvester.

| Property | Type | Required | Notes |
|---|---|---|---|
| `views_90d` | int | no |  |
| `last_view` | timestamp | no |  |

### SHARES_LINEAGE

Endpoints: Workbook→Workbook. Written by Cartographer (derived).

The specification writes this as undirected (Workbook↔Workbook). It is stored as a directed edge; readers treat it as symmetric.

| Property | Type | Required | Notes |
|---|---|---|---|
| `jaccard_tables` | float | yes |  |
| `jaccard_fields` | float | yes |  |
| `shared_calc_count` | int | yes | All three are required: the similarity score in §12.1 is a weighted sum of exactly these, so a partial edge cannot be scored. |

### IN_FAMILY

Endpoints: Workbook→ModelFamily. Written by Cartographer.

| Property | Type | Required | Notes |
|---|---|---|---|
| `confidence` | float | yes | Clustering confidence; the Foundry Workbench orders review by it. |

### IN_TRAIN

Endpoints: Workbook→ReleaseTrain. Written by Cartographer / Programme Manager.

| Property | Type | Required | Notes |
|---|---|---|---|
| `sequence` | int | yes | Position within the train; a train is defined as ordered (spec §3.3). |
| `state` | string | no | The MU's §3.2 state, for the Wave Board's kanban columns (story S3.2.2). A string, not a closed enum: migration_units.py's own MU_STATES is deliberately held as strings rather than an enum because 'the state machine belongs to the control plane, and this service should not be the place it is defined' — this property keeps that boundary rather than quietly becoming the enforcement point for a state machine graph-svc was never meant to own. |
| `wip_override_reason` | string | no | Set when this member was moved into a train that was already at or over its configured WIP limit (story S3.2.2); the reason a Programme Manager gave for proceeding anyway. |

### MAPS_TO

Endpoints: Field→ModelTable; CalculatedField→Measure; Worksheet→Visual. Written by Modeller / Transpiler / Compositor.

The source-to-target correspondence the Proof Engine normalises result-set column names through (spec §10.3).

| Property | Type | Required | Notes |
|---|---|---|---|
| `class` | enum(C1|C2|C3|C4) | no |  |
| `pattern_ref` | string | no |  |
| `target_column` | string | no | Column within the ModelTable. The specification writes this endpoint as 'Field→ModelTable.column'; the column is carried on the edge because columns are not nodes in this ontology. |

### PROVED_BY

Endpoints: ReportDefinition→ParityRun. Written by Arbiter.

| Property | Type | Required | Notes |
|---|---|---|---|
| `charter_version` | string | yes | A proof without the tolerance definition it ran under is not evidence (spec §4.4). |

### DECIDED_BY

Endpoints: *→GateDecision. Written by Gate workflow.

Any node may be the subject of a gate decision.

No type-specific properties.

## Declared deviations

Differences between the specification's tables and this schema, each with its reason. `tools/ontology_check.py --spec` fails on any difference not listed here.

| Element | Why the specification differs | Decision |
|---|---|---|
| ReleaseTrain, Wave | The specification renders these as one table row, 'ReleaseTrain / Wave'. | Split into two node types: §3.3 defines them as different objects — a train is an ordered group of Migration Units, a wave is a calendar window. |
| Workbook.rls, Workbook.rls_expression | §4.1.1 does not list them; backlog story S2.3.2 requires them by name — "recorded on the Workbook node as rls: true with the expression". | The backlog adds rather than contradicts here, so the property is carried with the story as its warrant. Row-level security is a fact about who may see which rows, and §10's parity cases have to be derived under the same restriction or they compare a report the client cannot see. Recording it on the Workbook rather than on a node of its own follows the story's wording and keeps it visible wherever the Migration Unit is. |
| Worksheet.views_90d, Worksheet.distinct_viewers_90d, Worksheet.last_view, Dashboard.views_90d, Dashboard.distinct_viewers_90d, Dashboard.last_view | §4.1.1 lists usage only on Workbook, but §6.2 sources usage from the Metadata API's views, which Tableau reports per published view. | Story S1.2.3 requires views and distinct viewers 'per workbook and per view'. A view is a Worksheet or a Dashboard in this ontology, so the same three properties sit there too. |
| User.directory_id, User.directory_resolved_at | §4.1.1 annotates User as 'Entra-linked' and §6.2 maps owners to 'Entra users where a match exists', but neither names a property to hold the link or to record its absence. | Story S1.2.3 needs unresolved owners listed for assignment, which is a query for users with no directory link. |
| Site.licence_tier | §4.1.1's Site row carries licence_cost_annual and user_count but not the tier. | Story S1.2.3 asks for the licence tier of the site alongside the per-user tier that User already declares. |
| Workbook.parse_quality | §4.1.1's Workbook row does not list it, but §4.1.4 requires that "every Harvester run records a parse quality score per workbook" and makes it a release-readiness check for the Calibration Wave. | Story S1.2.2 requires it on the Workbook node specifically, so the estate can be filtered by it without joining to harvest history. |
| ReleaseTrain.actual_start, ReleaseTrain.actual_end, Wave.actual_start, Wave.actual_end | The specification abbreviates these as 'actual_*'. | Expanded to the two properties the wildcard stands for, matching the planned_start / planned_end pair on the same row. |
| ModelFamily.reason, ModelFamily.evidence_shared_tables, ModelFamily.evidence_shared_fields, ModelFamily.evidence_shared_calc_shapes | §4.1.1 does not list them; backlog story S3.1.1 requires a family held as SINGLETON to carry "the reason", and every PROPOSED family to carry "the evidence (shared tables, shared fields, shared calc shapes)". | The backlog adds rather than contradicts here, so the properties are carried with the story as their warrant — the same pattern as Workbook.rls above. Recording the evidence on the node is what lets the Foundry Workbench show a proposal's reasoning (E3's own goal) without re-deriving it from SHARES_LINEAGE edges every time a family is opened, and without it silently drifting from what the clustering run actually used when membership is later edited (S3.1.2). |
| ModelFamily.overridden, ModelFamily.override_action, ModelFamily.override_reason | §4.1.1 does not list them; backlog story S3.1.2 requires every split, merge and move to be "preserved across re-clustering runs" and to record "who, when and why". | Who and when are already the base properties updated_by/updated_at every node carries (S1.1.1); overridden and override_reason are what a re-cluster reads to decide whether it may replace this family at all, and override_action is what a reviewer reads to know which kind of change was made without opening the event log. |
| ReleaseTrain.gate_schedule | §4.1.1 does not list it; backlog story S3.2.1 requires "gate schedule" to be "stored as ReleaseTrain nodes" alongside membership and planned start/end. | §13.1 gates a family at G2 and a Migration Unit at G3 — a train has no gate of its own, so this is a train-level roll-up: G2 windows cluster near the train's planned start (families are confirmed before generation begins) and G3 near its planned end (units are accepted as they finish). A first-cut plan a Programme Manager edits, not the throughput-based projection §14.2's wave scheduler produces (backlog S3.2.3, not built). |
| ReleaseTrain.wip_limits, ReleaseTrain.overridden, ReleaseTrain.override_action, ReleaseTrain.override_reason | §4.1.1 does not list them; backlog story S3.2.2 requires a configurable WIP limit "per train and per state", and every Wave Board change to "be refused" or "require a reason" — which only means something if a later train proposal run (S3.2.1) cannot silently overwrite it. | wip_limits is config a Programme Manager sets, not evidence from a run — the same distinction ModelFamily.evidence_* draws against .overridden. overridden/override_action/override_reason are S3.1.2's ModelFamily pinning mechanism, reused verbatim for trains: TrainPlanner.run() reads overridden the same way Cartographer.run() does, so a re-propose leaves a Programme Manager's move, resequence or WIP configuration alone unless the train's id is named to confirm. |
| ModelTable.family_ref | §4.1.1 declares no edge or property linking a ModelTable back to the ModelFamily (or SemanticModel) it was proposed for; MAPS_TO only carries a source Field to a ModelTable's own column (§4.1.2), which does not help a reader ask 'this family's candidate tables'. | A plain reference property, not an edge — the same shape ReportDefinition.mu_ref already uses to link back to an MU that (like a ModelFamily's design before G2) is not yet a settled first-class record. Backlog story S4.1.1 needs to list one family's candidate tables without a graph-wide scan. |
| SemanticModel.grain_statement, SemanticModel.design_generated_at, SemanticModel.design_provenance_ref, SemanticModel.design_document | §4.1.1 does not list them; backlog story S4.1.1 requires a model design proposal containing a grain statement possibly drafted by a model "with provenance recorded", relationships with cardinality, conformed dimensions shared with other families, candidate measures with dedup decisions, RLS roles, a refresh policy and open questions for the data owner. | grain_statement, design_generated_at and design_provenance_ref are each a plain scalar an engineer or a filter would want directly; design_document holds everything else this story's proposal needs that has no first-class graph shape yet (relationships have no edge type — §4.1.2 declares none between ModelTables; a candidate measure is not a Measure node until the Transpiler gives it dax, per Measure.dax being required). One JSON property for 'the rest of the document', the same reasoning ReleaseTrain.gate_schedule and ModelFamily's own evidence_* properties already established for a structure not yet worth first-class graph citizenship. |
| ModelFamily.g2_cycle_count | §4.1.1 does not list it; backlog story S4.2.1 requires that when a data owner sends a design back to DRAFT, "the cycle count is stored". | A plain counter on the family being cycled, incremented once per request-changes decision — the same footing as ModelFamily's other S3.1.x/S4.x counters and flags that the base spec table never enumerated because it predates the story that needed them. |
| ModelFamily.conformance_ruleset_version | §4.1.1 does not list it; backlog story S4.3.2 requires that conformance rule checks at build time are "recorded on the ModelFamily at build". | §14's own relational sketch puts a `conformance_json` column on a generic model_family/semantic_model table — this ontology has neither a generic table nor a place to hold a whole results blob on a graph node it does not already have one for; the individual violations already have a home (`build_run.steps`, story S4.3.1). What belongs on the family itself is the one fact that outlives any single build: which ruleset version it was last measured against. |
| GateDecision.approver_role, GateDecision.version_hash, GateDecision.countersigner, GateDecision.countersigner_role | §13.3's own worked example shows approver and countersign as nested objects ({user, role, identity}) and a top-level version_hash — §4.1.1's node table compresses GateDecision to a flat approver/rationale/evidence_ref/timestamp row with no place for either. | AGE node properties are flat everywhere in this ontology (no nested objects anywhere else either), so §13.3's structure is flattened the same way: approver_role sits beside approver, countersigner/countersigner_role sit beside it, and version_hash is promoted to a named property rather than folded into evidence_ref, which already means something else (an artefact reference). Story S4.2.1 is the first write of this node type. |
| SemanticModel.version_number, SemanticModel.published_at, SemanticModel.deprecated_at | §4.1.1 does not list them; backlog story S4.3.3 requires a change request on a PUBLISHED family to produce a second, independently-versioned model "without breaking released reports", with promotion marking the prior version "DEPRECATED with the date". | §12.2's own PUBLISHED row ("regression suites attached; Change request → DRAFT (new version); DEPRECATED at retirement") names the mechanic but not its data shape; §21's relational sketch gives a `model_family/semantic_model` table one `version` column, singular — no guidance for two versions coexisting. version_number is what lets `read_design_document` (and the console's own Versions list) find 'the current one' deterministically once a family can have more than one live SemanticModel node at a time; published_at/deprecated_at are the dated record §12.2 and this story's own acceptance criteria both ask for. `SemanticModel.state` was declared since S1.1.1 for exactly this ("deployment state within an environment") and is driven for the first time by this story. |
| ModelTable.semantic_model_ref | Backlog story S4.3.3 requires two SemanticModel versions of one family to coexist; `family_ref` alone can no longer say which version a ModelTable belongs to. | Every ModelTable read up to this story assumed exactly one live SemanticModel per family_ref — true by construction, since nothing before this story ever created a second one. A version-specific reference is what makes v(n)'s and v(n+1)'s own tables independently editable without either mutating the other's history; absent on tables written before this story, where family_ref alone is still unambiguous. |
| ModelTable.schema, ModelTable.mode_reason, ModelTable.row_estimate, ModelTable.custom_sql | §4.1.1 does not list them; the Modeller (story S4.1.1) computes all four on every `TableCandidate` it proposes, but the write path never carried them onto the graph node — found while story S4.3.3 touched the same NodeWrite to add `semantic_model_ref`. | A real, pre-existing gap, not new scope: `propose-design`'s own immediate HTTP response (built from `TableCandidate.as_dict()` in memory) always showed these correctly, but every *subsequent* read of the same table — a reload, a G2 review, TMDL emission itself — hydrated the graph node instead and got nothing, since these four were never in the properties dict `Modeller._write` sent to `NodeWrite`. Concretely: every build since S4.3.1 emitted `Value.NativeQuery("positions")` rather than `Value.NativeQuery("risk.positions")` (no schema qualifier), and the console's Design tab has shown '—' for row estimate on every table on every read after the first. Fixed at the source (`Modeller._write`) rather than deferred, since this story's own version-copy logic must read these same fields faithfully to be correct. |
| CalculatedField.pattern_ref, CalculatedField.reason, CalculatedField.classifier_version | Section 4.1.1 lists only `class (C1..C4, set by Transpiler)` on CalculatedField; backlog story S5.1.1 requires 'the matched rule or pattern id, and reason' to be written alongside it, and re-classification to report 'what moved class' - which needs the previous run's own ruleset version on the node to compare against. | Section 4.1.1's own `pattern_ref` already exists, but only on the MAPS_TO edge (CalculatedField to Measure) - the pattern a *generated* Measure came from, written by a Transpiler generation story that does not exist yet (F5.2/F5.3). This story's own pattern_ref/reason answer a different, earlier question - why the classifier put this field in this class, before any generation happens - so a second, node-level pair is added rather than reusing the edge property for a fact the edge cannot carry yet. classifier_version is `conformance_ruleset_version`'s own precedent (S4.3.2): stamped on every classification attempt so a field's class is always checkable against exactly the rule set that produced it. |
| CalculatedField.appendix_b_guidance, CalculatedField.redesign_suggestion, CalculatedField.redesign_suggestion_provenance_ref, CalculatedField.redesign_decision, CalculatedField.redesign_decision_reason, CalculatedField.redesign_decision_by, CalculatedField.redesign_decision_at | Backlog story S5.4.1 requires 'the Transpiler writes the reason, the Appendix B guidance, and an ASSISTED-mode redesign suggestion' for every C4 construct, and 'the MU is BLOCKED until a Migration Engineer records the redesign decision' — Section 4.1.1 lists only `class`/`pattern_ref`/`reason` on CalculatedField (the last two already a declared deviation, S5.1.1) and defines no Migration Unit node at all to hold a BLOCKED state on (Section 4.1.1's own node table has no `MigrationUnit` row; confirmed directly against the spec, not assumed from this codebase's own prior claims about the gap). | No Migration Unit record exists anywhere in this codebase to set to BLOCKED (§3.2) — it is a control-plane concept spanning several nodes (§3.1), not itself a graph node, and no story before this one has ever created one. `redesign_decision` absent on the one real, existing per-construct record (`CalculatedField`) is the disclosed proxy for that state: a C4 field with no decision yet is exactly a field this platform would otherwise call BLOCKED. `appendix_b_guidance`/`redesign_suggestion` are real, deterministic data (Appendix B's own text; a template-composed suggestion, `AgentMode.ASSISTED` — never a model call, the identical footing the Modeller's own grain-statement draft already established for this mode since S3.1.1/S4.1.2). The fuller generic decision-recording mechanism (a `GateDecision`-shaped record, visible to the report owner by construction) is S8.3.1's own later, explicit scope (Exception Desk, milestone I4); a real G3 gate that references these decisions is S9.1.1/S9.1.2's own later, explicit scope (milestone I5) — this story adds only what its own acceptance criteria asks for now. |
| Pattern.guards | Section 4.1.1's own node table lists Pattern with no `guards` property at all — §4.3's worked example (a narrative section, not the §4.1.1 table) already shows one (`guards: [ 'dims ⊆ model.dimensions', 'a,b numeric' ]`), and backlog story S5.5.1's own acceptance criteria requires the platform to store 'its (source AST shape, target template, guards) tuple' for every candidate pattern — the ontology simply never declared the third element of that tuple until this story needed to write one. | Descriptive text, not a machine-evaluated precondition — the identical footing `rules.RuleMeta.guards` already has (consulted by a coverage report and by tests, never enforced by the renderer itself). §9.3 also says matching is 'guarded on types and model context'; building a real guard-evaluation engine is a future refinement this story does not attempt — matching stays exact-shape only, as it already was. |
| Pattern.failure_count | Section 4.1.1's own node table lists Pattern with no `failure_count` property, and backlog story S5.5.2's own acceptance criteria requires 'a proof failure attributed to an ACTIVE pattern increments its failure count' — §4.3's own worked example already carries the identical fact as `stats.proof_fail`, so this is the same gap `Pattern.guards` (S5.5.1) already found in the same table, for the sibling fact. | A point-in-time snapshot, the identical footing `pass_count` already has (S5.5.1): incremented for visibility on every recorded failure, but never itself the authority a retirement decision is checked against — that check always reads `pattern_observation` (append-only) live, so this counter drifting could never cause a wrong retirement. |
| Pattern.version, Pattern.supersedes_id | Section 4.1.1's own node table declares neither property, and backlog story S5.5.3's own acceptance criteria requires a Pattern Library screen action to 'edit guards (creates a new version)' — the ontology never needed a version concept on Pattern until a story asked to edit one without silently rewriting its own history. | The identical 'an edit is a new version, the old row stays exactly what it said' discipline `SemanticModel`'s own per-version lifecycle (S4.3.3) already set for a graph node, and the identical field name `ProvenanceRecord.supersedes_id` already uses for a provenance row's own 'this replaces that' relationship — reused here rather than inventing a second name for the same idea. Editing retires the prior version's own node (`GraphWriter.retire_node`) so `find_matching_pattern`'s own 'one live pattern per shape' invariant never sees two candidates for one AST shape at once. |
| Visual.layout | Section 4.1.1's own node table gives Visual no property carrying position or size, and declares no Page or Container node at all -- yet backlog story S6.1.1's own acceptance criteria requires 'dashboard containers become report pages with layout preserved at the container level'. Section 8.8 names the source of that layout directly ('lays out dashboards from the Tableau zone tree into PBIR page layouts'), and Dashboard.layout_json (already declared, S2.3.2) is exactly that zone tree -- this property is where one visual's own share of it lands. | x/y/width/height, copied from the zone in Dashboard.layout_json whose own name matches this visual's source sheet -- a plain read, not a second layout engine. Absent on a visual with no containing dashboard (a standalone sheet gets its own single-visual page, which needs no preserved geometry) and on a placeholder visual for an unmapped mark type, where section 8.8's own 'small model proposing a grid' collision resolution is future, unbuilt scope this story does not attempt. |
| ReportDefinition.deploy_state, ReportDefinition.deploy_error | Section 4.1.1's own node table declares neither property, and backlog story S6.1.2's own acceptance criteria requires 'deployment failure returns the MU to GENERATED with the error on the MU page' -- section 3.2's own state machine names GENERATED as a real Migration Unit state, but no MU node exists anywhere in this codebase to hold it (confirmed a sixth time, the identical gap S5.4.1/S5.5.1/S5.5.2/S5.5.3/S6.1.1 each already found). | A disclosed proxy on the one real node this story's own deploy action touches, not a fabricated MU record. 'GENERATED' after a successful commit and deploy, 'DEPLOY_FAILED' with the failing step's own detail in deploy_error after every retry is exhausted; both absent before this report has ever been deployed. |
| OWNED_BY, VIEWED_BY, OWNED_BY.views_90d, OWNED_BY.last_view | The specification renders these as one table row, 'OWNED_BY / VIEWED_BY', so its properties column lists views_90d and last_view against both. | Split into two edge types: they carry different properties. Ownership is a single relationship with no properties; usage counts belong to VIEWED_BY, which is the edge the Harvester writes per viewer. |
| MAPS_TO.target_column | The specification writes the endpoint as 'Field→ModelTable.column'. | Columns are not nodes in the §4.1.1 ontology, so the column name is carried as a property on the edge rather than modelled as a node type the specification does not declare. |
| CONTAINS: Project→Project | The specification writes the chain as 'Site→Project→Workbook→Dashboard/Worksheet', which does not show the nested-project case. | §4.1.1 states the Project hierarchy is preserved and Project carries a 'parent' property, so a Project→Project containment pair must be permitted. |
| HAS_FIELD: Datasource→CalculatedField | §4.1.2 writes the endpoint as 'Table/Datasource→Field', naming only the Field node type. | §4.1.1 splits Field and CalculatedField into two node types with different properties, but gives a CalculatedField no other edge from the Datasource that defines it — and the Tableau adapter has written Datasource→HAS_FIELD→CalculatedField since S2.3.1's fragments.py. Read generically ('a field the datasource has'), a calculated field is a field; the fix is to the ontology's endpoint pair, not to the edge the adapter already writes. Found by story S3.1.1, which reads this edge to compute §12.1's 'calculated-field AST shapes a workbook defines' — the first thing in this codebase to actually harvest a real (non-fixture) workbook's calculated fields through the write path rather than only parsing them. |
| IN_TRAIN.state, IN_TRAIN.wip_override_reason | §4.1.2 does not list them; backlog story S3.2.2 requires a kanban of "trains → states with MU cards" and a WIP limit that, when exceeded, "requires a reason". | state carries the §3.2 state a card's column groups by — a string, not an enum, matching migration_units.py's own choice to hold MU_STATES as strings because the state machine's definition belongs to the control plane, not to this ontology. wip_override_reason is set only when a move proceeds past a configured WIP limit; who and when are the base updated_by/updated_at every edge already carries. |
