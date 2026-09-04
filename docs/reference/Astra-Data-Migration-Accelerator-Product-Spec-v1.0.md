ASTRA DATA
Migration Accelerator
Product Specification v1.0
Version	1.0 — draft for engineering
Status	For build
Owner	Swarup D., Product Engineering, Data & AI
Date	September 2026
Audience	Engineering, product, architecture, QA

# How to read this document
This is a build specification. It is written for the engineers who will implement the product, and it assumes they will not have been in the room for the sales conversations that produced it. Every section states what the component does, what it consumes, what it produces, and what has to be true for it to be considered working. Where a design choice was made for a reason that is not obvious, the reason is stated.
Two themes run through every section and are called out explicitly where they apply: acceleration — how the platform removes human time from a migration — and accuracy — how the platform proves that what it generated is correct before anyone relies on it. The product is only worth building if both hold. A fast migration that produces wrong reports is worse than a slow one.
- Sections 1–3 say what the product is and how work flows through it.
- Sections 4–7 define the primitives and the platform architecture, including the adapter contracts.
- Sections 8–14 specify the agents and engines in build detail.
- Section 15 is the console UX specification, screen by screen.
- Sections 16–17 specify how accuracy and acceleration are measured and enforced.
- Sections 18–26 cover security, integration, APIs, data model, NFRs, testing, roadmap, metrics and glossary.
- A companion document, the Product Backlog, decomposes this specification into epics, features and user stories with acceptance criteria.

# Contents
How to read this document
1. Executive summary
1.1 What this product is
1.2 Acceleration and accuracy — the two design commitments
1.3 What release 1 delivers
2. Product overview
2.1 What the Migration Accelerator is
2.2 What it is not
2.3 Design principles
2.4 Users and roles
3. The migration lifecycle
3.1 The unit of work — the Migration Unit
3.2 Migration Unit state machine
3.3 Programme structure — waves and release trains
3.4 Worked example — one workbook, end to end
4. Core primitives
4.1 The Estate Graph
4.2 Artefact store and the AI Provenance Record
4.3 The Pattern Library
4.4 The Tolerance Charter
4.5 The Evidence Chain
5. Platform architecture
5.1 Layered view
5.2 Component inventory
5.3 Deployment topologies
5.4 Technology commitments (reference implementation)
5.5 Model gateway and TokenOps
6. The Source Adapter contract
6.1 Responsibilities and interface
6.2 The Tableau adapter (release 1)
6.3 Adapter conformance suite
7. The Target Adapter — Power BI / Fabric
7.1 Artefact formats
7.2 Execution and deployment
8. The Agent Mesh
8.1 Anatomy of an agent
8.2 AI mode taxonomy
8.3 Agent catalog (release 1)
8.4 Harvester
8.5 Cartographer
8.6 Modeller
8.7 Transpiler
8.8 Compositor
8.9 Arbiter
8.10 Mender
8.11 Steward
9. The Transpiler in depth
9.1 Classification
9.2 The deterministic rules engine
9.3 The Pattern Library and the promotion pipeline
9.4 The generation path (Class 3)
9.5 Coverage measurement
10. The Proof Engine in depth
10.1 Parity case derivation
10.2 Execution
10.3 Normalisation and the diff algorithm
10.4 Sampling
10.5 Visual parity (advisory)
10.6 Regression mode
11. The remediation loop
11.1 Failure taxonomy
11.2 Pass structure and bounds
11.3 The Exception Desk
12. The Semantic Model Foundry
12.1 Clustering (Cartographer)
12.2 Model design and the G2 workflow
12.3 Build and conformance rules
13. Governance gates and autonomy
13.1 The four gates
13.2 Autonomy for migration action classes
13.3 Gate decision record
14. The Control Plane
14.1 Orchestration
14.2 The wave scheduler
14.3 The Calibration Wave
14.4 Release and decommission
15. Migration Console — UX specification
15.1 Role model and what each role’s day looks like
15.2 UX principles
15.3 Screen specifications by surface
15.4 The Migration Unit page — anatomy
15.5 The gate card — anatomy of the most important 30 seconds
15.6 Cross-cutting UX requirements
16. Accuracy governance — how AI output earns trust
16.1 The validation ladder
16.2 Provenance on every artefact
16.3 Confidence and calibration
16.4 Evaluation sets and the golden corpus
16.5 Prompt-injection and content trust
16.6 Accuracy metrics
17. Acceleration instrumentation
17.1 Absorption
17.2 Cycle time and throughput
17.3 TokenOps
18. Security, identity and data handling
18.1 Identity
18.2 Execution safety
18.3 The inference boundary
18.4 Evidence Chain
18.5 Compliance mapping
19. Integration architecture
20. APIs and extensibility
21. Data model reference
22. Non-functional requirements
23. Test strategy
24. Release roadmap
25. Product success metrics
26. Glossary
Appendix A — Traceability to the BlackRock proposal
Appendix B — Tableau → Power BI mapping reference (excerpt)
Appendix C — Event catalogue (CloudEvents types)

# 1. Executive summary

## 1.1 What this product is
Astra Data Migration Accelerator is the first product on the Astra Data platform. It migrates a legacy business-intelligence estate to a target platform by parsing the source estate into a structured graph, generating the target artefacts from that graph with a combination of deterministic rules and language-model agents, and proving each generated artefact against its source original by executing both and comparing the results. Release 1 migrates Tableau estates to Power BI on Microsoft Fabric.
The product exists because conventional BI migration scales with report count: every workbook is opened, studied, rebuilt by hand and checked by eye. On a 1,067-workbook estate that method costs roughly 56,000 engineering hours. The accelerator scales with pattern count instead — the first workbook that uses a particular calculation idiom costs engineering time; the four-hundredth costs a lookup. On the same estate the platform absorbs roughly 70% of the work content, and the remaining engineering time concentrates on the decisions that were never mechanical: model design, ambiguous business logic and sign-off.

## 1.2 Acceleration and accuracy — the two design commitments
Acceleration is achieved by removing whole categories of human work rather than by making humans faster at them. Discovery is replaced by parsing. Rebuilding is replaced by generation. Side-by-side review is replaced by executed comparison. First-pass defect repair is replaced by an agentic loop. The product measures its own absorption rate per lifecycle stage and per complexity tier, and those measurements are the basis on which Artizent prices a fixed fee per report.
Accuracy is achieved by refusing to trust generated output until it has been proved. Every artefact an agent produces carries a provenance record — which agent, which model, which prompt hash, which pattern, what confidence. No artefact reaches a business user without either passing a deterministic parity test or being explicitly approved by a named person. Language models draft; the Proof Engine judges; people approve. The product is designed so that an incorrect model output is caught by the platform, not by the client.

## 1.3 What release 1 delivers

[TABLE]
| Capability | Release 1 scope | Measured by |
| Estate harvest | Tableau Server / Cloud sites parsed into the Estate Graph via the Metadata API and .twb/.twbx XML: workbooks, sheets, datasources, fields, calculations, parameters, filters, actions, lineage, usage, ownership | Coverage: % of estate objects parsed without error; time to full parse |
| Model consolidation | Lineage clustering proposes shared semantic models; human-approved model designs built as TMDL | Reports per model; G2 approval cycle time |
| Calculation transpilation | Tableau calculation language → DAX and Power Query M through a four-class pipeline: deterministic map, structural rewrite, LLM-generated-and-proved, human redesign | Class mix; first-pass parity rate by class |
| Report generation | Tableau dashboards and sheets → Power BI report definitions (PBIR) bound to Foundry models, committed to Git | % of visuals generated without redesign flag |
| Parity proof | Source and target executed at the same grain and diffed cell by cell under a versioned Tolerance Charter; verdict and evidence bundle per report | First-pass parity rate; mean passes to pass |
| Agentic remediation | Failing parity cases classified and repaired by an agent within bounded passes; residue escalated with evidence | % of failures closed without a human |
| Governance | Four gates as workflow objects with named approvers; Evidence Chain over every decision and artefact | 100% of releases gate-traced |
| Migration Console | Programme, estate, foundry, delivery, proof, governance and admin surfaces for Artizent and client roles | Console is the system of record for programme status |
| Release & decommission | Fabric deployment pipelines, per-site parallel running, licence-release tracking | Sites decommissioned on plan |
[/TABLE]


# 2. Product overview

## 2.1 What the Migration Accelerator is
A deployable platform, installed in the client’s cloud tenant, that runs a BI migration as a pipeline. It has four layers. The Estate Graph is the parsed model of the source estate and the growing model of the target; everything else reads from it. The Agent Mesh is eight named agents, each with one job, one input contract and one output artefact. The Proof Engine executes source and target and compares them. The Control Plane orchestrates the pipeline, enforces the governance gates, records evidence and serves the Migration Console.
The platform is source- and target-agnostic in its core. Source-specific knowledge lives in a Source Adapter (Tableau in release 1) and target-specific knowledge in a Target Adapter (Power BI / Fabric in release 1). The adapter contracts in sections 6 and 7 are the boundary a second source or target must satisfy.

## 2.2 What it is not
- It is not a Tableau-to-Power-BI converter that runs unattended. The product is designed around human gates; an unattended mode does not exist and is not on the roadmap.
- It is not a BI authoring tool. Engineers finish and redesign in Power BI Desktop and Fabric; the product generates, proves, tracks and releases.
- It is not a data platform. It provisions and populates Fabric artefacts through the target adapter but does not replace the client’s data engineering.
- It is not a general agent platform. Agents are fixed-purpose and configured, not authored by end users. Extensibility is through adapters and patterns, not through new agents.

## 2.3 Design principles

[TABLE]
| # | Principle | Consequence in the design |
| P1 | Nothing generated is trusted until it is proved or approved | Every artefact has a verification state. The release path checks it. There is no configuration that lets an unproved artefact ship. |
| P2 | Deterministic before probabilistic | Any transformation that can be expressed as a rule is a rule. Language models are used only where rules run out, and their output is fed back into the rule library once it has passed proof repeatedly. |
| P3 | The graph is the source of truth | Agents read and write the Estate Graph. No agent holds private state about the estate. The console is a view over the graph. |
| P4 | Human decisions are first-class objects | Gate decisions, adjudications and redesign choices are records with an approver, evidence and a timestamp, not comments on a ticket. |
| P5 | Evidence is a deliverable | The parity record, provenance and gate trail are handed to the client and are designed to be read by an auditor. |
| P6 | The platform measures itself honestly | Absorption, first-pass parity and cycle time are computed from events, not entered by people. The numbers that price the next engagement come from the platform. |
| P7 | The client keeps everything | Generated artefacts go into the client’s repository as they are produced. The Estate Graph, parity suite and console are handed over at close. |
| P8 | Row-level data stays in the tenant | The Proof Engine runs where the data is. Only metadata and calculation expressions reach an inference endpoint; comparison values never do. |
[/TABLE]


## 2.4 Users and roles

[TABLE]
| Role | Organisation | What they do in the product | Primary surfaces |
| Programme Manager | Artizent | Owns the plan, waves and gate scheduling; reports status | Programme Board, Wave Board, Gate Inbox |
| Migration Architect | Artizent | Owns target architecture and conformance rules; approves Class 4 redesign approach | Foundry Workbench, Admin |
| Semantic Model Engineer | Artizent | Turns Cartographer proposals into approved model designs; builds and publishes models | Foundry Workbench, Model Detail |
| Migration Engineer | Artizent | Adjudicates exceptions, finishes generated reports, resolves redesign | Exception Desk, Migration Unit page |
| Parity Engineer | Artizent | Owns the Tolerance Charter and the parity suite; investigates inconclusive verdicts | Parity Dashboard, Charter editor |
| Platform Engineer | Artizent | Runs adapters, patterns, model gateway, pipelines | Admin, Pattern Library, TokenOps |
| Client Data Owner | Client | Approves model designs (G2) for their domain | Gate Inbox, Model Detail |
| Client Report Owner / Business User | Client | Resolves flagged business logic; performs UAT; signs off reports (G3) | Gate Inbox, Migration Unit page (client view) |
| Client Licence Administrator | Client | Authorises and executes Tableau decommission (G4) | Decommission Tracker |
| Client InfoSec Reviewer | Client | Reviews the data-handling position, inference boundary and evidence export | Admin › Data Handling, Evidence Export |
| Client Programme Sponsor | Client | Reads programme status; owns escalations | Programme Board (read) |
[/TABLE]


# 3. The migration lifecycle

## 3.1 The unit of work — the Migration Unit
The Migration Unit (MU) is one source workbook and everything the platform produces for it: its parsed representation in the Estate Graph, its assignment to a semantic model family and a release train, its generated target artefacts, its parity cases and verdicts, its exceptions, its gate decisions and its release record. Every agent acts on MUs. Every console screen that shows progress counts MUs by state. Invoicing under a fixed-price-per-report contract is triggered by an MU reaching ACCEPTED.
MigrationUnit {
id:            mu_01HX7…            // ULID
source:        { adapter: tableau, site: rqa, project: 'Risk Core',
workbook_luid: 8f3e…, name: 'Daily VaR', revision: 14 }
tier:          COMPLEX               // from assessment; re-tiered by joint review
score:         2.87                  // complexity score
model_family:  mf_fixedincome_positions   // set by Cartographer, confirmed at G2
train:         rt_2                  // release train
state:         PROVING               // §3.2
artefacts:     { model_ref, report_ref: pbir_…, measures: [..], queries: [..] }
parity:        { suite: ps_…, cases: 41, pass: 39, fail: 2, run: pr_…, first_pass_rate: 0.95 }
passes:        2                     // Mender passes consumed (bound: 3)
exceptions:    [ex_…]
gates:         { G2: approved(model), G3: pending, G4: n/a }
owners:        { engineer: u_…, client_owner: u_…, bu: 'Global Fixed Income' }
provenance:    prov_…                // §4.2
timing:        { harvested, generated, first_verdict, accepted, released }
}

## 3.2 Migration Unit state machine
States are exclusive; transitions are emitted as events on the platform bus and written to the Evidence Chain. The console never derives state from anything but the graph.

[TABLE]
| State | Meaning | Entered by | Exits to |
| HARVESTED | Workbook parsed; graph nodes and edges written; parse quality recorded | Harvester | CLUSTERED |
| CLUSTERED | Assigned to a model family and a release train | Cartographer | MODEL_READY, BLOCKED |
| BLOCKED | Waiting on a dependency: model family not yet approved (G2), missing source access, or an open Class 4 redesign decision | Control Plane | MODEL_READY |
| MODEL_READY | Its model family is APPROVED and BUILT; generation may start | Control Plane | GENERATED |
| GENERATED | Measures, queries and report definition produced and committed; schema, parse and compile checks passed | Transpiler + Compositor | PROVING |
| PROVING | Parity suite executing | Arbiter | PASSED, FAILED |
| FAILED | At least one parity case failed or was inconclusive | Arbiter | MENDING, ESCALATED |
| MENDING | Mender pass in progress (bounded) | Mender | PROVING, ESCALATED |
| ESCALATED | Residual failures routed to the Exception Desk with evidence | Mender / Control Plane | ADJUDICATED |
| ADJUDICATED | Engineer decision recorded; artefact patched or redesign accepted | Migration Engineer | PROVING, PASSED (waiver) |
| PASSED | All parity cases pass at charter tolerance; visual review pending | Arbiter | ACCEPTED |
| ACCEPTED | G3 approved by client owner; invoicing trigger | Gate workflow | RELEASED |
| RELEASED | Deployed to production workspace; parallel running started | Steward | DECOMMISSIONED |
| DECOMMISSIONED | Source workbook retired under G4; licence release recorded | Steward | — (terminal) |
| WITHDRAWN | Removed from scope by change control | Programme Manager | — (terminal) |
[/TABLE]

A PASSED (waiver) transition from ADJUDICATED requires a written justification and is visible on the Parity Dashboard as a waived case forever; it is the only path to acceptance that is not a passing test, and it exists for the Class 4 case where the target is a deliberate redesign rather than a reproduction.

## 3.3 Programme structure — waves and release trains
A release train is an ordered group of MUs that share model families and move through generation, proof, acceptance and release together. Trains are proposed by the Cartographer from graph clustering and confirmed by the Programme Manager. A wave is a calendar window in which one or more trains execute. The platform holds the plan as data: train membership, planned start and end, gate schedule and per-site sign-off dates. The Programme Board renders it; the wave scheduler drives it.
Sequencing by shared model rather than by site is a deliberate choice: it means each model family is designed and approved once, and later MUs in a train benefit from the patterns and adjudications recorded on earlier ones. Site-level sign-off (G3) and licence release (G4) are unaffected — they are per site regardless of train.

## 3.4 Worked example — one workbook, end to end
- Harvest. The Tableau adapter pulls workbook Daily VaR (site RQA) via the Metadata API and downloads the .twbx. The Harvester parses 6 sheets, 2 dashboards, 3 datasources, 118 fields, 27 calculated fields, 4 parameters, 9 filters and 2 actions into the graph, and records 412 views in the last 90 days across 31 users. Parse quality is 100%; state → HARVESTED.
- Cluster. The Cartographer finds that its two primary datasources share 71% of their table-and-field lineage with 38 other workbooks across RQA and GTAA, and assigns it to model family mf_risk_positions and release train 1. State → CLUSTERED, then BLOCKED because mf_risk_positions is still IN_REVIEW at G2.
- Model approval. The Modeller’s proposal for mf_risk_positions (grain, conformed dimensions, 41 candidate measures, RLS scaffold) is reviewed by the Semantic Model Engineer and approved by the RQA data owner at G2. The model is built as TMDL, published to the dev workspace, and the 39 MUs in the family move to MODEL_READY.
- Generate. The Transpiler classifies the 27 calculated fields: 15 Class 1, 8 Class 2, 3 Class 3, 1 Class 4. It emits 26 DAX measures (schema-valid, parse-valid, compile-valid against the model) and flags one table calculation with addressing that has no DAX equivalent. The Compositor generates a two-page PBIR report bound to the model, with the flagged visual carrying a redesign marker. Artefacts are committed; state → GENERATED.
- Prove. The Arbiter derives 41 parity cases (one per sheet × parameter combination within the enumeration bound), executes expected result sets via the Tableau adapter and candidate result sets via XMLA, and diffs them under Tolerance Charter v3. 39 pass. Two fail: one on a null-handling difference in a ratio measure, one on a date-grain mismatch. State → FAILED.
- Mend. The Mender classifies both failures, applies the null_ratio_divide pattern to the first (a known Class 2 pattern), re-runs, passes. For the second it invokes the model gateway with the failing evidence and the model definition, receives a candidate measure with DATESYTD scoping corrected, validates it, re-runs, passes. Two passes consumed of three. State → PASSED.
- Accept. The Class 4 visual is adjudicated by a Migration Engineer with the RQA report owner: they agree on a redesign, the engineer finishes it in Desktop, commits, and the Arbiter re-proves the affected sheet. The report owner reviews the parity record and the visual, and approves G3. State → ACCEPTED; invoice line raised.
- Release. The Steward promotes through the deployment pipeline to production, starts parallel running for the site, and generates the report documentation from the graph. After the agreed parallel period the RQA licence administrator authorises G4; state → DECOMMISSIONED.

# 4. Core primitives

## 4.1 The Estate Graph
A property graph holding the parsed source estate, the target artefacts as they are produced, and the relationships between them. It is written by the Harvester and by every downstream agent, and read by everything. It is the only place estate knowledge lives.

### 4.1.1 Ontology — node types

[TABLE]
| Node | Side | Key properties | Notes |
| Site | source | luid, name, owner, user_count, licence_cost_annual | From Metadata API + licence export |
| Project | source | luid, name, parent | Hierarchy preserved |
| Workbook | source | luid, name, revision, size, extract_flag, last_published, views_90d, distinct_viewers_90d | One MU per Workbook |
| Dashboard | source | name, size, layout_json, contained_sheets[] | Layout retained for Compositor |
| Worksheet | source | name, mark_type, rows_shelf[], cols_shelf[], marks_shelf[], sort[], filters[], reference_lines[] | The visual specification |
| Datasource | source | luid, name, type (embedded|published), connection_ref, extract_flag, refresh_schedule |  |
| Connection | source | class (sybase|sqlserver|snowflake|postgres|hive|excel|text|odbc|hyper), server, db, schema, auth_mode | Drives platform-side ingestion planning |
| Table | source | name, schema, custom_sql (nullable), row_estimate | custom_sql retained verbatim |
| Field | source | name, datatype, role (dimension|measure), default_agg, hidden |  |
| CalculatedField | source | name, formula, formula_ast, class (C1..C4, set by Transpiler), lod_type, table_calc_flag, depends_on[] | AST from adapter parser |
| Parameter | source | name, datatype, domain (list|range|any), default, current_values_seen[] | Domain bounds Arbiter enumeration |
| Filter | source | field_ref, type (categorical|range|relative_date|top_n|condition), values, context_flag |  |
| Action | source | type (filter|highlight|url|parameter|set), source_sheets[], target_sheets[] | Interactivity mapping |
| User | both | upn, display, licence_tier, site_roles[] | Entra-linked |
| ModelFamily | target | id, name, domain, grain, state (§12.2), owner, conformed_dims[] | Proposed by Cartographer |
| SemanticModel | target | id, family_ref, tmdl_ref, workspace, state, version, rls_roles[] | One per family per environment |
| ModelTable | target | name, source_table_refs[], mode (import|directlake|directquery) |  |
| Measure | target | name, dax, m_query (nullable), source_calc_ref, class, pattern_ref, provenance_ref, validation_state | The Transpiler’s product |
| ReportDefinition | target | id, mu_ref, pbir_ref, pages[], model_ref, version, validation_state | The Compositor’s product |
| Visual | target | page, type, source_sheet_ref, encodings, redesign_flag, redesign_reason |  |
| ParityCase | target | id, mu_ref, sheet_ref, grain[], measures[], filter_ctx, param_values, expected_ref, candidate_ref, state | §10 |
| ParityRun | target | id, suite_ref, charter_version, started, finished, verdicts[] |  |
| Verdict | target | case_ref, result (PASS|FAIL|INCONCLUSIVE), failing_cells, evidence_ref |  |
| ExceptionCase | target | id, mu_ref, class, evidence_ref, state, assignee, decision, pattern_ref | §11.3 |
| Pattern | platform | id, name, class, source_signature, target_template, provenance, promotion_state, pass_count | §4.3 |
| GateDecision | platform | gate (G1..G4), subject_ref, decision, approver, rationale, evidence_ref, timestamp | §13 |
| ReleaseTrain / Wave | platform | id, name, mu_refs[], planned_start, planned_end, actual_* | §3.3 |
[/TABLE]


### 4.1.2 Ontology — edge types

[TABLE]
| Edge | From → To | Properties | Written by |
| CONTAINS | Site→Project→Workbook→Dashboard/Worksheet | — | Harvester |
| USES_DATASOURCE | Worksheet→Datasource | — | Harvester |
| CONNECTS_TO | Datasource→Connection→Table | join_clause on Table edges | Harvester |
| HAS_FIELD | Table/Datasource→Field | — | Harvester |
| DEPENDS_ON | CalculatedField→Field/CalculatedField/Parameter | position_in_ast | Harvester (from AST) |
| ENCODES | Worksheet→Field/CalculatedField | shelf, aggregation, sort | Harvester |
| FILTERED_BY | Worksheet/Dashboard→Filter | — | Harvester |
| OWNED_BY / VIEWED_BY | Workbook→User | views_90d, last_view | Harvester |
| SHARES_LINEAGE | Workbook↔Workbook | jaccard_tables, jaccard_fields, shared_calc_count | Cartographer (derived) |
| IN_FAMILY | Workbook→ModelFamily | confidence | Cartographer |
| IN_TRAIN | Workbook→ReleaseTrain | sequence | Cartographer / PM |
| MAPS_TO | Field→ModelTable.column; CalculatedField→Measure; Worksheet→Visual | class, pattern_ref | Modeller / Transpiler / Compositor |
| PROVED_BY | ReportDefinition→ParityRun | charter_version | Arbiter |
| DECIDED_BY | any→GateDecision | — | Gate workflow |
[/TABLE]


### 4.1.3 Retrieval — how agents consume the graph
Agents do not receive raw graph dumps. Each agent declares a context contract: the sub-graph shape it needs for one unit of work (for example, the Transpiler’s contract is one CalculatedField, its transitive DEPENDS_ON closure, the Parameter domains it references, the target ModelTable columns those fields MAPS_TO, and the Pattern records whose source_signature matches its AST shape). The context assembler materialises exactly that shape, applies the data-class rules in §18, and hands the agent a typed object. This is what keeps prompts small, deterministic and auditable — the prompt hash in a provenance record is reproducible because the context is.

### 4.1.4 Graph quality
Every Harvester run records a parse quality score per workbook: the fraction of source constructs recognised by the adapter grammar, with unrecognised constructs stored verbatim and flagged. A workbook below the configurable threshold (default 0.98) cannot leave HARVESTED until a Platform Engineer has reviewed the unrecognised constructs and either extended the grammar or marked them as ignorable. Graph quality is reported on the Estate Explorer and is a release-readiness check for the Calibration Wave.

## 4.2 Artefact store and the AI Provenance Record
Generated artefacts — TMDL, DAX, M, PBIR, documentation — are files. They are committed to the client’s Git repository through the target adapter and mirrored in the platform’s object store with content-addressed identifiers. Every artefact is linked from the graph and every artefact carries a provenance record. The provenance record is the accuracy theme made concrete: it is what lets an auditor, or a Mender pass, see exactly how an artefact came to be.
ProvenanceRecord {
id:            prov_01HX…
artefact:      { kind: MEASURE, ref: msr_…, content_hash: sha256:… }
produced_by:   { agent: transpiler, agent_version: 1.4.2, run: run_… }
mode:          GENERATED_PROVED     // DETERMINISTIC | ASSISTED | GENERATED_PROVED | HUMAN
inputs:        { source_ref: calc_…, context_hash: sha256:…, pattern_ref: pat_lod_fixed_v3 }
model_call:    { gateway_request: mg_…, provider: anthropic, model: claude-…,
prompt_hash: sha256:…, temperature: 0, tokens_in: 2140, tokens_out: 310 }   // null if DETERMINISTIC
confidence:    0.91                  // agent-declared, calibrated per §16.3
validation:    { schema: PASS, parse: PASS, compile: PASS, proof: PASS(pr_…), human: n/a }
supersedes:    prov_…                // previous version, if a Mender pass rewrote it
created:       2027-01-14T09:12:07Z
}

## 4.3 The Pattern Library
A pattern is a reusable transformation from a source construct signature to a target template. Patterns are the mechanism by which the platform gets faster and more accurate as it runs. They come from three places: the deterministic rule set shipped with the adapter (Class 1 and most Class 2), LLM-produced transformations that have passed proof repeatedly and been promoted (§9.3), and engineer adjudications that were generalised (§11.3).
Pattern {
id:               pat_lod_fixed_ratio_v3
class:            C2
source_signature: { ast_shape: 'DIV(SUM(a), LOD_FIXED(dims, SUM(b)))', adapter: tableau }
target_template:  'DIVIDE(SUM({a}), CALCULATE(SUM({b}), ALLEXCEPT({table}, {dims})))'
guards:           [ 'dims ⊆ model.dimensions', 'a,b numeric' ]
provenance:       { origin: PROMOTED_FROM_LLM, first_seen: mu_…, promoted_at: … }
promotion_state:  ACTIVE            // CANDIDATE | ACTIVE | RETIRED
stats:            { applications: 212, proof_pass: 209, proof_fail: 3, last_fail: … }
}

## 4.4 The Tolerance Charter
A versioned configuration document, agreed with the client at gate G1, that defines what “the same result” means. It is the contract the Proof Engine enforces and the reason parity can be a test rather than an opinion. The charter is edited in the console, stored in Git, and every ParityRun records the charter version it ran under.
ToleranceCharter v3 {
numeric:   { abs_epsilon: 0.005, rel_epsilon: 1e-6, rounding: HALF_EVEN, currency_scale: 2 }
nulls:     { source_null_vs_target_zero: FAIL, source_null_vs_target_blank: PASS, empty_string_is_null: true }
dates:     { grain_alignment: TRUNCATE_TO_SOURCE_GRAIN, timezone: 'America/New_York', fiscal_year_start: 1 }
strings:   { trim: true, case_sensitive: false, collation: 'en-US' }
ordering:  { sort_sensitive: false, top_n_tie_break: SOURCE_ORDER }
rows:      { missing_key: FAIL, extra_key: FAIL, row_count_tolerance: 0 }
sampling:  { full_compare_max_rows: 200000, sample_rows: 50000, stratify_by: grain }
params:    { enumerate_max_values: 12, enumerate_strategy: DEFAULT_PLUS_OBSERVED }
waiver:    { allowed_classes: [C4], requires: [engineer, client_owner], justification_min_chars: 120 }
}

## 4.5 The Evidence Chain
An append-only, hash-linked record of every state transition, gate decision, agent run, model call and parity verdict, with daily roots that can be anchored externally at the client’s option. It is the same construct as in Astra Operate and shares its implementation. For the accelerator it answers three questions an auditor will ask: who approved this report and on what evidence; what did the model see when it generated this measure; and was this artefact ever released without a passing test. The Evidence Export screen (§15.3) produces a signed bundle per site or per programme.

# 5. Platform architecture

## 5.1 Layered view

[TABLE]
| Layer | Responsibility | Principal components |
| Control Plane | Orchestration, scheduling, gates, evidence, console, APIs, tenancy, TokenOps | Workflow engine (Temporal), wave scheduler, gate service, evidence service, console API (GraphQL/REST), event bus, model gateway, metering |
| Proof Engine | Parity case derivation, dual execution, normalisation, diff, verdicts, regression | Case deriver, source executor (via adapter), target executor (XMLA), differ, evidence bundler, scheduler |
| Agent Mesh | Eight fixed-purpose agents operating on Migration Units | Agent runtime (sandboxed workers), context assembler, pattern service, validation ladder |
| Estate Graph | Source and target model of the estate, provenance, patterns, plan | Graph store, artefact/object store, Git mirror, search index |
| Adapters | Source-specific parsing and execution; target-specific generation and deployment | Tableau Source Adapter; Power BI / Fabric Target Adapter; adapter SDK |
[/TABLE]


## 5.2 Component inventory

[TABLE]
| Component | Type | Responsibility | Scale unit |
| graph-svc | service | Estate Graph read/write, context assembly, schema enforcement | per tenant |
| artefact-svc | service | Object store + Git mirror; content addressing; provenance linkage | per tenant |
| adapter-tableau | worker | Metadata API client, .twb/.twbx parser, calc AST parser, source query executor | per site parallelism |
| adapter-fabric | worker | TMDL/PBIR writers, Git integration, deployment pipelines, XMLA executor | per workspace |
| agent-runtime | worker pool | Executes agent runs in sandboxes; enforces context contracts and validation ladder | horizontal, per agent type |
| pattern-svc | service | Pattern matching, promotion pipeline, statistics | per tenant |
| proof-svc | service + workers | Case derivation, execution orchestration, diff, verdicts | horizontal, per parity run |
| orchestrator | workflow engine | MU lifecycle workflows, wave scheduling, retries, bounds | cluster |
| gate-svc | service | Gate objects, approver routing, decision records | per tenant |
| evidence-svc | service | Append-only chain, hashing, anchoring, export | per tenant |
| model-gateway | service | Provider routing, prompt/response logging (hashes), budgets, redaction, retries | per tenant |
| console-api | service | GraphQL + REST for the console and external clients | per tenant |
| console-web | SPA | Migration Console (React/TypeScript) | static |
| metering | service | TokenOps: cost per artefact, per MU, per agent | per tenant |
| event-bus | infrastructure | CloudEvents for all state transitions | cluster |
[/TABLE]


## 5.3 Deployment topologies
In-tenant (default). The platform is deployed into the client’s Azure subscription: AKS for services and workers, Azure Database for PostgreSQL, Azure Blob for artefacts, Azure Event Hubs (Kafka protocol) for the bus, Key Vault for secrets, Entra ID for identity. The Tableau adapter reaches Tableau Server through the client’s network; the Fabric adapter reaches the tenant’s workspaces through service principals. The model gateway calls either an Anthropic endpoint through an egress-controlled route or an Azure OpenAI deployment inside the subscription, per the client’s data-handling decision. Nothing else leaves the tenant.
Artizent-hosted. For clients whose estates are small or whose policy permits it, the same deployment runs in an Artizent-operated subscription with the client’s Tableau and Fabric reached over private connectivity. Single-tenant per client; no shared control plane across clients.

## 5.4 Technology commitments (reference implementation)
- Runtime: Kubernetes (AKS). Agent and adapter workers run as isolated pods with per-run ephemeral filesystems and default-deny egress; only broker-approved endpoints are reachable.
- Languages: Python 3.12 for services, workers, adapters and the Transpiler; TypeScript/React for the console; the calc-language parsers are generated from grammars (Lark) and versioned with the adapter.
- Data: PostgreSQL 16 as the system of record, with the Estate Graph held as a property graph in Postgres (Apache AGE) for release 1 and a migration path to a dedicated graph store if scale demands; Azure Blob for artefacts; OpenSearch for full-text and AST-shape search; Git (Azure Repos or GitHub) as the client-facing artefact mirror.
- Orchestration: Temporal for MU workflows, Mender bounds, retries and long-running parity runs.
- Eventing: CloudEvents over Event Hubs (Kafka protocol); consoles are event-sourced views.
- Models: model-agnostic gateway. Anthropic (Claude) is the default reasoning tier for Class 3 generation and Mender diagnosis; Azure OpenAI is the in-tenant alternative; a small model tier (client-hosted OSS or provider small models) handles classification and extraction. Temperature 0 for all generation paths. No client data is used for shared-model training.
- Interfaces: GraphQL for the graph and console; REST for adapters, gates and evidence; MCP for tools exposed to agents; CloudEvents for integration.

## 5.5 Model gateway and TokenOps
All model calls go through the gateway. It enforces the redaction rules in §18.3, records a hash of prompt and response (never the response body outside the tenant), applies per-agent and per-programme token budgets, routes by task class to the cheapest model tier that meets the calibrated accuracy bar (§16.3), caches identical context hashes, and retries with backoff. Metering attributes every token to an MU, an agent and a pattern so that cost per report is a reported number, not an estimate.

# 6. The Source Adapter contract

## 6.1 Responsibilities and interface
A source adapter is the only component that knows the source platform. It has four responsibilities: enumerate the estate, parse assets into the graph ontology, execute source queries for the Proof Engine, and expose usage and ownership. It is packaged as a versioned worker image with a manifest and must pass the conformance suite in §6.3 before it can be enabled on a tenant.
interface SourceAdapter {
manifest():                  AdapterManifest      // name, version, grammar_version, capabilities[]
enumerate(scope):            AsyncIterable<AssetRef>   // sites → projects → workbooks
fetch(asset: AssetRef):      RawAsset             // bytes + metadata, content-hashed
parse(raw: RawAsset):        ParseResult          // graph fragment + parse_quality + unrecognised[]
parseCalc(expr: string):     CalcAST              // grammar-backed AST, versioned
usage(scope, window):        UsageRecord[]        // views, viewers, last_view per asset
owners(scope):               OwnershipRecord[]
executeCase(c: ParityCase):  ResultSet            // expected result set at the case grain
capabilities:                { live_query, extract_read, usage, ownership, screenshot }
}

## 6.2 The Tableau adapter (release 1)

[TABLE]
| Concern | Implementation |
| Enumeration | Tableau REST API (sites, projects, workbooks, datasources, users) plus the Metadata API (GraphQL) for lineage: workbooks { sheets { upstreamFields, upstreamTables } }, calculatedFields { formula, upstreamFields }, publishedDatasources, databaseTables, parameters. |
| Fetch | REST download of .twbx (or .twb); the .twbx is unpacked, the embedded .twb XML parsed, and any packaged .hyper extract retained for the executor. |
| Parse — structure | XML parse of <workbook>: <datasources> (connections, relations, custom SQL, columns), <worksheets> (<table> with <rows>, <cols>, <encodings>, <filter>, <sort>), <dashboards> (zones with layout), <actions>, <parameters> (as datasource columns with param-domain-type). |
| Parse — calculations | Grammar-backed parser for the Tableau calculation language producing an AST with node types for function calls, operators, LOD expressions (FIXED|INCLUDE|EXCLUDE), table calculations (with addressing/partitioning taken from the sheet), IF/CASE, type casts, date functions, string functions and parameter references. Unrecognised constructs are retained verbatim as UNKNOWN(text) nodes and lower parse quality. |
| Usage | Metadata API views and, where available, the historical_events admin views; window configurable (default 90 days). |
| Ownership | Workbook owner, project leaders and site admins from REST; mapped to Entra users where a match exists. |
| Execute (Proof) | Three strategies in preference order: (1) extract read — query the packaged or published .hyper with the Hyper API at the case grain; (2) view data — REST queryViewData for the sheet with filter and parameter values applied via vf_ parameters; (3) live replay — reconstruct the datasource SQL (with custom SQL verbatim) and execute against the source connection under the client’s service account. The strategy used is recorded on the ParityCase. |
| Screenshot | REST queryViewImage for the advisory visual comparison in §10.6. |
| Auth | Personal access token or Connected App (JWT) held in Key Vault; site-scoped. |
| Rate limits | Adaptive concurrency per site; Metadata API paging; backoff on 429. |
[/TABLE]


## 6.3 Adapter conformance suite
An adapter ships with a corpus of source assets and expected graph fragments. The suite checks: enumeration completeness against a known site; parse quality ≥ 0.98 on the corpus; AST round-trip (AST → canonical text → AST) stability; executor result-set determinism (same case, same result across three runs); and usage and ownership mapping. The suite runs in CI and on tenant enablement against a client-provided sample.

# 7. The Target Adapter — Power BI / Fabric

## 7.1 Artefact formats

[TABLE]
| Artefact | Format | Written by | Validation before commit |
| Semantic model | TMDL (Tabular Model Definition Language) folder: model.tmdl, tables/*.tmdl, relationships.tmdl, roles/*.tmdl, expressions.tmdl | Modeller | TMDL parse; tabular schema check; deploy to dev workspace and read back |
| Measures | TMDL measure entries with formatString, displayFolder, lineage tag | Transpiler | DAX parse (tokeniser + grammar); compile check via XMLA EVALUATE of a trivial row; dependency resolution against the model |
| Power Query | M expressions in expressions.tmdl / partition sources | Transpiler (custom SQL, Class 2 date/string prep) | M parse; dry-run evaluate with row limit |
| Report | PBIR (Power BI enhanced report format): definition/report.json, pages/*/page.json, visuals/*/visual.json, theme | Compositor | JSON schema validation against PBIR schema; visual-type whitelist; binding check that every field reference resolves in the model |
| Deployment | Fabric Git integration (workspace ↔ repo branch) and deployment pipelines dev → test → prod | Steward | Pipeline run status; post-deploy smoke query |
| Documentation | Markdown per report and per model, generated from the graph | Steward | Link check |
[/TABLE]


## 7.2 Execution and deployment
The target executor runs DAX over XMLA (read-write endpoint enabled on the capacity) against the dev or test workspace copy of the model. Queries are generated by the Proof Engine at the case grain — EVALUATE SUMMARIZECOLUMNS(...) with the case’s filter context expressed as FILTER/TREATAS arguments and parameters bound through what-if tables or query-scoped variables. Deployment uses Fabric deployment pipelines; the Steward promotes a report and its model together, records the pipeline run in evidence, and never promotes an artefact whose validation state is not PROVED or WAIVED.

# 8. The Agent Mesh

## 8.1 Anatomy of an agent
An agent is a versioned worker with a fixed charter: what it consumes, what it produces, which AI mode it operates in, which validation steps its output must pass, and what autonomy it has. Agents do not talk to each other; they read and write the graph and the orchestrator sequences them. This is deliberate — it keeps every hand-off inspectable.
AgentRecord {
id:            transpiler            version: 1.4.2
charter:       { consumes: [CalculatedField + context contract],
produces: [Measure | MQuery | RedesignFlag],
prohibited: [ 'write outside MU scope', 'call executor', 'modify Pattern.promotion_state' ] }
ai_mode:       { C1: DETERMINISTIC, C2: DETERMINISTIC, C3: GENERATED_PROVED, C4: HUMAN }
validation:    [ SCHEMA, PARSE, COMPILE ]        // before the artefact may enter PROVING
autonomy:      L3                                // §13.2
model_policy:  { tier: reasoning, provider: anthropic|azure_openai, temperature: 0, max_tokens: 2000 }
budgets:       { tokens_per_run: 20000, wall_clock_s: 120 }
evaluation:    { suite: es_transpiler_2027_01, first_pass_parity: 0.93, class3_promotion_rate: 0.61 }
owner:         platform_eng
}

## 8.2 AI mode taxonomy
Every unit of agent output is produced in exactly one of four modes, recorded on its provenance. The mode determines what has to happen before the output can be used. This taxonomy is the spine of the accuracy commitment.

[TABLE]
| Mode | How the output is produced | What must be true before use | Where it applies |
| DETERMINISTIC | A rule or pattern with no model call. Reproducible byte-for-byte from the same input. | Schema, parse and compile checks; proof still runs but a failure is treated as a pattern defect and blocks the pattern | Harvest, Class 1 and 2 transpilation, PBIR generation from a mapped visual, case derivation, diff |
| ASSISTED | A model proposes; a rule or a person decides. The model output is advisory. | The deciding rule or person is recorded; model output is retained as evidence only | Cartographer family naming, Modeller measure naming and folder placement, documentation drafting |
| GENERATED_PROVED | A model produces the artefact; it is validated structurally and then proved by execution. | Schema, parse, compile and a passing parity verdict, or a human adjudication | Class 3 transpilation, Mender repairs |
| HUMAN | A person produces or decides; the platform records and checks. | Decision record with rationale; proof still runs on any artefact produced | Class 4 redesign, G2/G3/G4 decisions, waivers |
[/TABLE]


## 8.3 Agent catalog (release 1)

[TABLE]
| Agent | Consumes | Produces | AI mode | Autonomy |
| Harvester | Source assets via adapter | Graph fragments; parse quality; usage and ownership | DETERMINISTIC | L4 |
| Cartographer | Graph lineage across workbooks | SHARES_LINEAGE edges; ModelFamily proposals; release-train proposal | DETERMINISTIC clustering; ASSISTED naming | L2 (PM confirms) |
| Modeller | ModelFamily + member workbooks’ datasources, fields, calcs | Model design proposal; TMDL after G2 | ASSISTED design; DETERMINISTIC TMDL emit | L2 (G2) |
| Transpiler | CalculatedField + context contract; Pattern Library | Measures, M queries, class labels, redesign flags | Per class (§8.2) | L3 |
| Compositor | Worksheet/Dashboard specs + MAPS_TO edges | PBIR report definition; visual redesign flags | DETERMINISTIC; ASSISTED layout on collisions | L3 |
| Arbiter | ReportDefinition + model + charter | ParityCases, ParityRuns, Verdicts, evidence bundles | DETERMINISTIC | L4 |
| Mender | FAIL verdicts + evidence + artefact + pattern hits | Patched artefacts; escalations; pattern candidates | DETERMINISTIC pattern fix; GENERATED_PROVED repair | L3, bounded |
| Steward | ACCEPTED MUs; pipelines; site plan | Deployments; documentation; decommission records | DETERMINISTIC; ASSISTED documentation | L2 (G4) |
[/TABLE]


## 8.4 Harvester
Runs the adapter’s enumerate → fetch → parse loop for a scope (a site, a project or the whole estate), writes graph fragments transactionally per workbook, records parse quality and unrecognised constructs, and pulls usage and ownership. Idempotent on content hash: a re-run on an unchanged workbook is a no-op; a changed workbook produces a new revision node and re-opens the MU if it had progressed past HARVESTED (a change-control event visible on the Programme Board). Emits mu.harvested with parse quality. Throughput target: 500 workbooks per hour per site worker.

## 8.5 Cartographer
Computes pairwise lineage similarity between workbooks (Jaccard over source tables, over fields, and a weighted count of shared calculated-field AST shapes), clusters them (agglomerative on the combined similarity, threshold configurable, default 0.55), and proposes one ModelFamily per cluster with a grain inferred from the most common row-level dimensions across member sheets. It also proposes release trains by ordering clusters on a cost function that prefers high reuse, high usage and early-renewal sites. A small model names families and drafts their one-paragraph scope (ASSISTED); the Programme Manager confirms membership and sequence on the Wave Board. Details in §12.1.

## 8.6 Modeller
For an approved-for-review ModelFamily, produces a model design proposal: candidate tables (from the union of member datasources, deduplicated by connection + table), candidate relationships (from Tableau joins and relationships), candidate conformed dimensions, the union of measures (from member calculated fields, deduplicated by normalised AST), storage mode recommendation per table, RLS scaffold from Tableau user filters, and a list of open design questions (grain conflicts, ambiguous keys, duplicate measures with different definitions). The proposal is a document rendered in the Foundry Workbench for the G2 workflow. After approval it emits TMDL deterministically from the approved design and deploys it to the dev workspace. The model draft is ASSISTED: a model proposes naming, folders and descriptions; the Semantic Model Engineer edits; the client data owner approves.

## 8.7 Transpiler
Consumes one CalculatedField with its context contract and produces a Measure or M expression, a class label and, for Class 4, a redesign flag with a reason. Its pipeline — classify, match pattern, apply or generate, validate — is specified in §9. It never calls the executor and never writes outside its MU. Its output enters PROVING only after schema, parse and compile checks pass; a compile failure on a Class 3 output triggers one regeneration with the compiler error in context, then a Class 4 flag.

## 8.8 Compositor
Maps each Worksheet to a Visual using the visual-type mapping in Appendix B, binds encodings to model columns and measures through MAPS_TO edges, translates filters and parameters to slicers and report-level filters, translates actions to drill-through and cross-filter settings, lays out dashboards from the Tableau zone tree into PBIR page layouts, and applies the client theme. Visuals with no mapping (Appendix B marks them) receive a redesign flag and a placeholder card so the report still generates and proves for its other visuals. Layout collisions after mapping are resolved by a small model proposing a grid (ASSISTED); the proposal is applied deterministically and is visible on the Migration Unit page for the engineer to override.

## 8.9 Arbiter
Owns the Proof Engine run for an MU: derives parity cases, schedules execution on both sides through the adapters, normalises, diffs, writes verdicts and evidence bundles, and sets MU state. Specified in §10. Fully deterministic; the only agent at autonomy L4 besides the Harvester, because it produces no artefact — it produces a judgement about one.

## 8.10 Mender
Consumes FAIL verdicts. For each failing case it classifies the failure (§11.1), looks for an applicable pattern, and if found applies it deterministically; if not, it assembles a repair context (the failing cells, filter context, both result sets’ headers and a bounded sample, the current measure and its source calc, the model definition excerpt) and asks the reasoning model for a corrected artefact, which then goes back through the validation ladder and the Arbiter. It runs a bounded number of passes per MU (default 3) and escalates with the full evidence on exhaustion. Every repair it makes is a Pattern candidate. Specified in §11.

## 8.11 Steward
Runs the release path for ACCEPTED MUs: promotes report and model through the deployment pipeline, starts parallel running for the site, generates report and model documentation from the graph (ASSISTED drafting, deterministic facts), tracks per-site decommission readiness, and records G4. It also produces the handover bundle at programme close: graph export, parity suite, patterns, documentation, evidence.

# 9. The Transpiler in depth

## 9.1 Classification
Every CalculatedField AST is classified into one of four classes before anything is generated. Classification is deterministic and is the first thing the Calibration Wave measures, because the class mix drives both the acceleration figure and the price.

[TABLE]
| Class | Definition | Detection rule | Path |
| C1 — Direct map | Every node in the AST has a one-to-one target equivalent with the same semantics | All function/operator nodes ∈ C1 map table (Appendix B); no LOD, no table calc, no parameter of type range-with-step | Rule engine; DETERMINISTIC |
| C2 — Structural rewrite | Semantics preserved but the expression shape changes: LOD expressions, table calculations with simple addressing, parameters, date arithmetic idioms, string idioms, IF/CASE with type coercion | AST contains LOD, table calc with addressing resolvable from the sheet, parameter, or an idiom in the C2 pattern set | Pattern Library; DETERMINISTIC |
| C3 — Context-dependent | Meaning depends on the sheet’s grain, filter shelf, quick-filter behaviour or dashboard actions; requires reasoning about the target model | AST contains constructs whose result depends on visual context (e.g. ATTR, TOTAL, table calcs with RUNNING_*/WINDOW_* across restarting partitions, nested LOD with filter interaction), or a C2 match with failed guards | Reasoning model → validation ladder → proof; GENERATED_PROVED |
| C4 — No equivalent | No faithful target construct exists; a redesign decision is required | AST contains constructs on the C4 list (e.g. table-calc addressing across pane with INDEX() arithmetic, page-shelf animation, certain reference-line semantics, RAWSQL_* against unsupported dialects), or C3 generation fails twice | Redesign flag; HUMAN |
[/TABLE]

Classification is not tier-graded and is not a prediction of difficulty; a Very Complex workbook may be entirely C1 and C2. The class mix is reported per MU, per train and per programme, and the C4 rate is the number Artizent watches most closely because it is the work no machine removes.

## 9.2 The deterministic rules engine
The rule engine walks the AST bottom-up. Each node is matched against the C1 map (function → DAX function, operator → operator, type cast → CONVERT/VALUE/FORMAT, aggregation → aggregation) and rewritten. The output is a DAX AST which is then printed with the target adapter’s pretty-printer so that generated DAX is consistently formatted and diffable. Guards on every rule check the operand types and the model context (e.g. a SUM over a field that maps to a model column of type string is rejected, not coerced). A rule failure downgrades the node to C2 matching; a C2 match failure downgrades to C3.
Aggregation context is the part of the rule engine that most often decides between C1 and C2. Tableau measures are aggregated at the visual’s level of detail; DAX measures are evaluated in filter context. For a field aggregated with a single aggregation across all of its uses the mapping is a measure with that aggregation. For a field used with different aggregations in different sheets the rule engine emits one measure per aggregation with a naming convention (Field (SUM), Field (AVG)), records the choice on provenance, and the Compositor binds each visual to the right one.

## 9.3 The Pattern Library and the promotion pipeline
Patterns are matched by AST shape: a signature is the AST with leaf identifiers abstracted to typed placeholders. Matching is exact on shape and guarded on types and model context. When a pattern is applied, the provenance record cites it and the pattern’s application count increments. When an artefact produced by a pattern fails proof, the pattern’s failure count increments and, above a threshold (default 3 failures or a pass rate below 0.97 over 30 applications), the pattern is automatically moved to RETIRED and every MU that used it is flagged on the Parity Dashboard for re-proof.
Promotion. Every Class 3 generation and every Mender repair produces a Pattern candidate: the source signature and the generated target, with placeholders inferred. A candidate becomes ACTIVE when it has been applied to at least five distinct MUs with a passing verdict each time and a Platform Engineer has approved it in the Pattern Library screen. Promotion is the mechanism by which the platform’s deterministic coverage rises through an engagement and across engagements — an ACTIVE pattern promoted on one client is shipped in the next adapter release for all.

## 9.4 The generation path (Class 3)
For a Class 3 field the Transpiler assembles a repair-grade context and calls the reasoning tier through the gateway with a fixed prompt contract. The contract is versioned and its hash is on the provenance record.
GenerationRequest {
task:        TRANSLATE_CALC
source:      { language: tableau_calc, formula: '…', ast: {...}, class: C3, reason: 'table calc RUNNING_SUM restarting on [Region]' }
sheet_ctx:   { rows: [...], cols: [...], marks: [...], filters: [...], sort: [...], partitioning: [...], addressing: [...] }
model_ctx:   { tables: [...], columns: [{name, type, table}], relationships: [...], existing_measures: [{name, dax}] }
params:      [{ name, type, domain }]
constraints: [ 'output DAX only', 'no new tables', 'use existing measures where semantics match',
'declare assumptions as JSON', 'if not expressible say NOT_EXPRESSIBLE with reason' ]
output_schema: { dax: string, m: string|null, assumptions: [string], confidence: number, notes: string }
}
The response is parsed against the output schema (a schema failure is a hard failure, not a retry), then the DAX is parsed, then compiled against the dev model, then handed to the Arbiter. A NOT_EXPRESSIBLE response, or a second compile failure, produces a Class 4 flag with the model’s stated reason attached for the engineer. Temperature is 0; the same context hash produces a cached response; the model never sees row-level data.

## 9.5 Coverage measurement
The Transpiler reports, per MU and in aggregate: class mix; rule-engine coverage (fraction of AST nodes matched by C1 rules); pattern coverage (fraction of C2 fields matched by an ACTIVE pattern); Class 3 first-pass parity rate; promotion rate (Class 3 generations that became patterns); and C4 rate with reasons histogram. These are the Calibration Wave’s primary outputs and they are on the Calibration Report screen.

# 10. The Proof Engine in depth
The Proof Engine is what makes the accuracy commitment enforceable and the fixed price possible. Its job is to answer one question per report, deterministically: does the generated report return the same numbers as the Tableau original, under rules both parties agreed in advance?

## 10.1 Parity case derivation
A parity case is one executable comparison. Cases are derived from the source, not the target, so that a target that silently drops a dimension or a filter fails rather than passing trivially. For each Worksheet the Arbiter derives:
- Grain: the set of dimension fields on rows, columns, marks (colour, size, shape, detail, path) and pages, after resolving the sheet’s level-of-detail rules — this is the key of the result set.
- Measures: every measure field on the shelves with its aggregation, plus every calculated field the sheet encodes.
- Filter context: the sheet’s filters (categorical, range, relative-date, top-N, condition), the dashboard filters that apply to it, and context filters, resolved to concrete values from the source’s current state.
- Parameter values: the default value plus observed values from usage where the adapter provides them, bounded by params.enumerate_max_values in the charter; one case per parameter combination up to the bound.
- Sort and limit: retained for top-N evaluation; ignored for comparison unless the charter says otherwise.
A sheet with three parameters of domain sizes 4, 3 and 2 produces up to 24 cases; the charter bound caps enumeration at 12, prioritising default plus most-observed combinations. The remaining combinations are recorded as NOT_ENUMERATED on the suite so the coverage is explicit.

## 10.2 Execution
The expected side is produced by the source adapter’s executeCase — extract read, view data or live replay — and the candidate side by the target executor as a DAX query over XMLA. Both return a ResultSet: an ordered list of column descriptors (name, role, type) and rows. Both executions are scheduled by the orchestrator with retry and timeout; a timeout on either side yields INCONCLUSIVE, not FAIL, and is retried once with a longer budget before being surfaced.
-- candidate query for a case with grain (Region, Product) and measures (Net Revenue, Margin %)
EVALUATE
SUMMARIZECOLUMNS(
'Geography'[Region], 'Product'[Product],
TREATAS({"FY2026"}, 'Date'[Fiscal Year]),                -- sheet filter
FILTER(ALL('Product'[Category]), 'Product'[Category] IN {"Bonds","Equities"}),
"Net Revenue", [Net Revenue],
"Margin %",    [Margin %]
)
ORDER BY 'Geography'[Region], 'Product'[Product]

## 10.3 Normalisation and the diff algorithm
Both result sets are normalised under the charter before comparison: column names mapped through MAPS_TO edges; types coerced to a common lattice (integer ⊂ decimal ⊂ double; date ⊂ datetime; everything ⊂ string); dates truncated to the source grain; strings trimmed and case-folded if the charter says so; nulls canonicalised per the charter’s null rules. Rows are keyed by the grain tuple.
- Key set comparison. Missing keys (in expected, not candidate) and extra keys (in candidate, not expected) are collected. Either is a FAIL under the default charter, with the keys listed in evidence.
- Cell comparison. For each shared key and each measure: numeric cells pass if |e − c| ≤ abs_epsilon or |e − c| / max(|e|, |c|) ≤ rel_epsilon; string and date cells pass on normalised equality; null pairs pass or fail per the charter’s null matrix.
- Row-count and total check. Row counts compared under row_count_tolerance; grand totals per measure recomputed on both sides and compared under the numeric rule as a cheap early signal.
- Verdict. PASS if no key differences and no failing cells; FAIL otherwise; INCONCLUSIVE if either execution did not complete or the sample could not be stratified.
- Evidence bundle. For a FAIL: the first N failing cells (default 50) with key, measure, expected, candidate and delta; the key differences; the filter context and parameter values; the candidate DAX; the source strategy used; the charter version; and the result-set headers. Comparison values stay in the tenant; the bundle is stored in the artefact store and referenced from the Verdict.

## 10.4 Sampling
Result sets up to full_compare_max_rows are compared in full. Larger sets are compared on a stratified sample keyed by the grain (every distinct value of the first grain dimension is represented) plus the top-N rows by each measure’s absolute value, so that the rows that matter most to a reader are always in the sample. Sampling is recorded on the ParityCase and shown on the Parity Dashboard; a sampled PASS is labelled as such.

## 10.5 Visual parity (advisory)
Structural comparison of the visual specification — mark type, encodings, axis fields, sort, reference lines — produces a visual parity score per sheet. A screenshot of the source view (via the adapter) and a rendered image of the target visual (via the Power BI export API) are compared perceptually and the score is shown next to the structural score. Neither gates acceptance: G3 requires a passing data-parity verdict and a human visual review, and the advisory scores exist to direct that review to the visuals most likely to have drifted.

## 10.6 Regression mode
Every parity suite is retained after acceptance. The Steward re-runs a site’s suites on a schedule (default: after every model publish and weekly during parallel running) and on demand from the Parity Dashboard. A regression FAIL on a released report raises an ExceptionCase tagged REGRESSION and notifies the report owner; it does not change the MU’s state. At handover the suites are exported with a runner so the client can keep them running without the platform.

# 11. The remediation loop

## 11.1 Failure taxonomy
The Mender classifies each failing case before deciding what to do with it. Classification is by evidence-bundle features and is deterministic; the class decides whether a pattern can be applied and what context a model repair receives.

[TABLE]
| Class | Signal in the evidence | Typical cause | Usual fix path |
| FILTER_CONTEXT | Same keys, measures off by a consistent factor or a subset of rows | Filter not applied, or applied at the wrong scope; ALL/ALLEXCEPT scope wrong | Pattern: scope correction; else model repair |
| NULL_HANDLING | Cells where one side is null/blank and the other is 0 or a value | Division by zero, ZN, IFNULL semantics | Pattern: DIVIDE, COALESCE idioms |
| DATE_GRAIN | Differences concentrated on date dimensions; row multiplicity differs | Date truncation, fiscal calendar, week start, DATETRUNC vs DATEPART | Pattern: date table alignment; charter check |
| AGGREGATION | Totals match, rows do not, or vice versa | Aggregation at the wrong grain; SUM of a ratio | Pattern: measure-of-measures; else repair |
| TYPE_COERCION | String/number mismatches or formatting differences | Implicit casts, format strings | Pattern: FORMAT/VALUE |
| LOD_SCOPE | Differences on rows where the LOD dimensions differ from the sheet grain | FIXED vs INCLUDE semantics; context filters | Pattern: CALCULATE scope; else repair |
| TABLE_CALC | Differences following a partition boundary pattern | Restart/addressing not reproduced | Repair with sheet context; C4 if addressing is unrepresentable |
| SORT_LIMIT | Row set differs only by membership under a top-N | Tie-break order | Charter tie-break rule; pattern |
| KEY_MISSING | Whole keys absent on one side | Relationship direction, inactive relationship, missing dimension member | Model repair via Foundry (not per report) |
| SOURCE_DRIFT | Expected side changed between runs | Source data refreshed mid-run | Re-execute both sides in a pinned window |
| UNKNOWN | No signature matched | — | Model diagnosis, then escalate |
[/TABLE]


## 11.2 Pass structure and bounds
- Classify every failing case; group by artefact (a measure used by several sheets is repaired once).
- Pattern first. If an ACTIVE pattern matches the failure class and the artefact’s AST shape, apply it deterministically and re-run only the affected cases.
- Model repair otherwise: assemble the repair context (§8.10), request a corrected artefact under the same output schema as generation, run the validation ladder, re-run affected cases.
- Bound. Each MU has a pass budget (default 3). Each pass may touch any number of artefacts but re-runs only affected cases. A pass that produces no change in the failing set ends the loop early.
- Escalate on exhaustion, on an UNKNOWN class after one model diagnosis, on any KEY_MISSING (which is a model defect, not a report defect), or on a repair that makes a previously passing case fail (the artefact is reverted first).
- Patternise. Every successful repair is written as a Pattern candidate with the failure class as part of its signature.
The bound is the reason the loop is safe to run unattended at L3: it cannot spin, it cannot silently accept, and every pass is in evidence. The loop closes the majority of failures on real estates because most parity failures are mechanical — a scope, a null, a grain — and those are exactly the classes patterns capture.

## 11.3 The Exception Desk
Escalated cases become ExceptionCases and appear in the Exception Desk, which is the Migration Engineer’s work queue. There is no separate defect tracker. Each ExceptionCase carries the full evidence bundle, the Mender’s pass history, the current artefact and its source calc, and the model’s diagnosis where one was made. The engineer’s decision is one of: patch (edit the artefact; re-prove), redesign (Class 4; agree with the report owner; finish in Desktop; re-prove the rest of the report; waiver the redesigned visual’s case with justification), model defect (route to the Foundry as a change to the family; the MU returns to BLOCKED), or source defect (the Tableau report was wrong; record, inform the owner, and either reproduce the defect faithfully or fix it with the owner’s written agreement — the choice is a G3 matter). Every decision is a record and every patch is a Pattern candidate.

# 12. The Semantic Model Foundry

## 12.1 Clustering (Cartographer)
Inputs: for every workbook, the set of source tables it reaches (through datasources and joins), the set of fields it encodes, and the multiset of calculated-field AST shapes it defines. Similarity between two workbooks is 0.5·J(tables) + 0.3·J(fields) + 0.2·shared_calc_shapes / max_calc_shapes, where J is Jaccard. Agglomerative clustering (average linkage) at the configured threshold produces families. Each family is annotated with its member count, its total 90-day views, its distinct source connections, its candidate grain (the most frequent minimal dimension set across member sheets) and its early-renewal weight (sites in the early-renewal tranche). Families under a minimum size (default 3) are merged into the nearest family or held as SINGLETON for engineer review.
The output is a proposal, not a decision. The Foundry Workbench shows the family graph, lets the Semantic Model Engineer split or merge families and move workbooks, and records every manual change as an override with a reason. The number of families is the number that sets the Foundry cost; it is measured here in Month 1 and confirmed at the Calibration Wave.

## 12.2 Model design and the G2 workflow

[TABLE]
| ModelFamily state | Meaning | Transition |
| PROPOSED | Cartographer output; membership editable | Engineer accepts → DRAFT |
| DRAFT | Modeller proposal generated; engineer editing tables, keys, grain, measures, RLS | Engineer submits → IN_REVIEW |
| IN_REVIEW | G2 open with the client data owner; questions and changes tracked on the model record | Approve → APPROVED; Request changes → DRAFT |
| APPROVED | Design frozen at a version; TMDL emitted | Deploy to dev succeeds → BUILT |
| BUILT | Deployed to dev workspace; smoke queries pass; member MUs → MODEL_READY | Promote → PUBLISHED |
| PUBLISHED | In test/prod; regression suites attached | Change request → DRAFT (new version); DEPRECATED at retirement |
[/TABLE]

A model design proposal contains: tables with source mapping and storage mode; relationships with cardinality and direction; the grain statement; conformed dimensions and which other families share them; measures with source calc references and deduplication decisions; RLS roles derived from Tableau user filters and site membership; refresh policy; and a list of open questions, each of which must be answered or explicitly deferred before G2 can be approved. The proposal document is generated from the graph and the approver signs the version hash.

## 12.3 Build and conformance rules
TMDL emission is deterministic from the approved design. Conformance rules are enforced at emit time and reported on the model record: star schema (no many-to-many without a bridge), single active relationship path between any two tables, conformed dimensions shared by reference not copied, measures in display folders by source family, every column with a description (drafted, ASSISTED), and RLS roles tested with a fixture user per role. A conformance failure blocks BUILT.

# 13. Governance gates and autonomy

## 13.1 The four gates

[TABLE]
| Gate | Subject | Approver | Evidence required | Effect |
| G1 Tolerance Charter | Programme (once; re-versioned on change) | Client analytics lead + Artizent Parity Engineer | Charter document; Calibration Wave results if a revision | No parity run without an approved charter |
| G2 Model design approval | ModelFamily version | Client data owner for the domain; countersigned by Semantic Model Engineer | Design proposal at version hash; open questions closed | Member MUs may generate |
| G3 Parity acceptance | Migration Unit | Client report owner; countersigned by Migration Engineer | Passing ParityRun (or waived cases with justification); visual review record | Invoice trigger; release permitted |
| G4 Decommission authorisation | Site | Client licence administrator; countersigned by Programme Manager | All site MUs RELEASED; parallel-run period elapsed; regression suites green | Licence release; source workbooks archived |
[/TABLE]


## 13.2 Autonomy for migration action classes
The platform adopts the Astra L0–L4 ladder. Autonomy is set per action class and is not a property of an agent. Release 1 ceilings:

[TABLE]
| Action class | Ceiling | Rationale |
| MA-01 Parse and write graph | L4 | Read-only on source; reversible; fully deterministic |
| MA-02 Propose families and trains | L2 | Sets programme structure; PM confirms |
| MA-03 Emit model design proposal | L2 | Client-facing decision at G2 |
| MA-04 Deploy model to dev workspace | L3 | Non-production; observable; reversible |
| MA-05 Generate measures/report definition | L3 | Enters proof; cannot reach a user |
| MA-06 Execute parity cases | L4 | Read-only on both sides; deterministic |
| MA-07 Repair artefact (Mender) | L3, bounded | Every repair re-proved; pass budget |
| MA-08 Promote to test workspace | L3 | Post-G3 only |
| MA-09 Promote to production | L2 | Explicit release approval by PM |
| MA-10 Decommission source | L2 | G4 |
| MA-11 Promote pattern to ACTIVE | L2 | Platform Engineer approves |
| MA-12 Retire pattern | L4 | Safety action; automatic on failure threshold |
[/TABLE]


## 13.3 Gate decision record
GateDecision {
id: gd_…   gate: G3   subject: mu_01HX…   version_hash: sha256:…
decision: APPROVED | REJECTED | CHANGES_REQUESTED | WAIVED
approver: { user: u_…, role: client_report_owner, identity: entra:… }
countersign: { user: u_…, role: migration_engineer }
evidence: [ pr_… (ParityRun PASS 41/41, charter v3), vr_… (visual review), ex_… (waiver, C4, justification) ]
rationale: '…'   decided_at: …   sla: { opened: …, due: …, breached: false }
}

# 14. The Control Plane

## 14.1 Orchestration
Each MU is a Temporal workflow whose activities are agent runs, adapter calls and gate waits. The workflow encodes the state machine in §3.2, the Mender bound, retries with backoff for adapter and executor calls, timeouts that yield INCONCLUSIVE rather than FAIL, and compensation (revert the artefact commit) when a repair makes a passing case fail. Long waits — a G2 or G3 that takes days — are durable; the workflow resumes on the gate event. Every activity start and finish is an event on the bus and a record in evidence.

## 14.2 The wave scheduler
Takes the confirmed release-train plan and the per-family gate schedule and drives MUs through generation and proof in train order, subject to: family state (no generation before BUILT), executor concurrency limits per source site and per Fabric workspace, model-gateway budgets, and a configurable work-in-progress limit per train so that the Exception Desk queue stays reviewable. It re-plans when a family slips and surfaces the effect on the Programme Board as a projected-versus-planned date per train.

## 14.3 The Calibration Wave
A first-class programme object. The Programme Manager selects a calibration set (default: 40 workbooks stratified across the four tiers and at least two sites, weighted toward the highest-usage workbooks). The wave runs the full pipeline on the set with all gates active and produces the Calibration Report: class mix per tier, rule and pattern coverage, first-pass parity rate, mean Mender passes to pass, C4 rate with reasons, family count and reports-per-family, adapter parse quality, executor strategy mix, cost per report by tier, and the elapsed time per stage. The report is signed by both parties and its figures are written to the programme record as the calibrated baseline against which absorption is later reported.

## 14.4 Release and decommission
The Steward promotes ACCEPTED MUs per train through the deployment pipeline, opens a parallel-running window per site (default 4 weeks, configurable), schedules regression runs, and tracks decommission readiness: all MUs released, regression green, adoption sessions held, owner confirmation received. When readiness is met the G4 request opens automatically for the licence administrator. On approval the Steward archives the source workbooks (adapter capability) and records the licence-release date and value from the site record.

# 15. Migration Console — UX specification

## 15.1 Role model and what each role’s day looks like

[TABLE]
| Role | Opens the console to… | Lands on |
| Programme Manager | See where every train is against plan, what is blocked and why, which gates are due, and what the calibrated absorption is running at | Programme Board |
| Semantic Model Engineer | Work the family queue: accept proposals, edit designs, submit for G2, watch build status | Foundry Workbench |
| Migration Engineer | Work the Exception Desk; open the Migration Unit page for anything assigned; finish and re-prove | Exception Desk |
| Parity Engineer | Watch first-pass rates and failure classes; investigate inconclusives; edit and version the charter | Parity Dashboard |
| Platform Engineer | Adapter health, parse quality, pattern promotions, gateway budgets and spend | Admin › Platform Health |
| Client data owner | Approve or query model designs for their domain | Gate Inbox (filtered to G2) |
| Client report owner | Review a report’s parity record and visual, resolve a flagged logic question, sign off | Gate Inbox (filtered to G3) / Migration Unit page |
| Client licence admin | Authorise decommission when a site is ready | Decommission Tracker |
| Client InfoSec | Confirm the data-handling position; export evidence | Admin › Data Handling |
[/TABLE]


## 15.2 UX principles
- The graph is the screen. Every number on every screen is a query over the Estate Graph and the Evidence Chain; there is no status that a person types in.
- One page per Migration Unit. Everything about a report — source, artefacts, parity, exceptions, gates, provenance — is one page with one URL, shared between Artizent and client roles with role-based sections.
- Evidence one click away. Any verdict, decision or artefact opens its evidence bundle inline. Nothing is a summary of something you cannot see.
- Queues, not dashboards, for workers. Engineers land on a work queue ordered by the scheduler; dashboards are for managers and clients.
- Client surfaces are calm. Client roles see plain language, their own domain, and the actions they own. Platform detail (passes, patterns, tokens) is Artizent-only by default.
- Every action is a record. Approve, waive, override, split, merge — all produce a decision or override record with a reason field that is required, not optional.

## 15.3 Screen specifications by surface

### 15.3.1 Programme surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Programme Board (PM default) | Top: KPI strip — MUs by state, first-pass parity, absorption vs calibrated baseline, gates due this week, spend vs budget. Middle: train swimlanes with planned vs projected bars, MU counts by state per train, blocked reasons. Bottom: milestone rail and gate calendar. | Open train; re-plan; open gate; export status pack |
| Wave Board | Kanban of trains × states with MU cards; drag to re-sequence within scheduler constraints; family dependencies shown as lines; WIP limit indicator per column. | Confirm train plan; move MU; set WIP limit; hold/release train |
| Calibration Report | The §14.3 report rendered: class-mix by tier, coverage gauges, parity rates, C4 reasons histogram, family count, cost per report, stage timings; comparison panel to the pre-calibration assumptions. | Sign report; open any MU in the set; export |
| Status Pack | Generated weekly pack: progress, gates, exceptions aging, risks; editable narrative | Generate; edit; publish to client |
[/TABLE]


### 15.3.2 Estate surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Estate Explorer | Left: site/project tree with MU counts and parse quality. Centre: workbook table (tier, score, usage, family, train, state, C4 count) with faceted filters. Right: selected workbook summary with lineage mini-graph. | Open MU; re-harvest; re-tier (joint review flow); withdraw from scope |
| Lineage View | Force-directed graph of workbooks ↔ tables ↔ fields for a family or a selection; edge weight = shared lineage; colour = state. | Select for family; export lineage |
| Parse Quality Queue | Workbooks below threshold with their unrecognised constructs grouped by construct text; frequency across the estate. | Mark ignorable; open grammar issue; re-parse |
| Usage & Ownership | Per site: users, licence tiers, 90-day views, owners; unowned workbooks flagged. | Assign owner; export for licence planning |
[/TABLE]


### 15.3.3 Foundry surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Foundry Workbench | Family list with state, members, views, grain, open questions, G2 status; family graph panel; queue ordered by train sequence. | Accept/split/merge family; move workbook; generate proposal; submit for G2 |
| Model Detail | Tabs: Design (tables, relationships diagram, grain, conformed dims), Measures (source calc ↔ measure, class, pattern, dedup decisions), RLS, Open Questions (each with owner and state), Versions (hash, approver, diff), Build (TMDL, deploy log, conformance results). | Edit design; answer question; submit; approve (client role); deploy; open TMDL in Git |
| Model Proposal (client view) | The proposal as a readable document: what this model is, what reports use it, what it changes for the business unit, the open questions that need an answer, an approve/request-changes card. | Approve (G2); request changes; ask a question |
[/TABLE]


### 15.3.4 Delivery surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Exception Desk (engineer default) | Queue of ExceptionCases ordered by train sequence then age; card shows MU, failure class, passes consumed, sheet, one-line diagnosis; filters by class, train, site, assignee. | Claim; open case; bulk-assign |
| Exception Case | Three-pane: (1) evidence — failing cells table with expected/candidate/delta, key diffs, filter context, parameter values; (2) artefact — current DAX/M with source calc alongside, Mender pass history with diffs, model diagnosis; (3) decision — patch editor with validate-and-reprove button, redesign flow, route-to-Foundry, source-defect flow; reason required. | Patch and re-prove; mark redesign; route to Foundry; record source defect; waive (with client co-sign) |
| Migration Unit page | §15.4 | — |
| Release Board | Per train: MUs ACCEPTED → RELEASED with pipeline status; per site: parallel-run window, regression status, adoption sessions, decommission readiness checklist. | Promote (PM approval for prod); open pipeline run; schedule adoption session |
| Decommission Tracker (client licence admin) | Per site: readiness checklist, MUs released, regression green, owner confirmations, licence value; G4 card when ready. | Authorise decommission (G4); defer with reason |
[/TABLE]


### 15.3.5 Proof surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Parity Dashboard (parity engineer default) | KPI strip: first-pass rate, mean passes to pass, inconclusive rate, waived count, regression status. Heat grid: MUs × sheets coloured by verdict; filters by train, class, executor strategy. Failure-class histogram over time. Pattern retirements feed. | Open case; re-run; open charter; open pattern |
| Parity Run | One run: cases table with verdicts, executor strategies, timings, sampling flags; charter version; evidence bundle links. | Re-run subset; compare to previous run |
| Tolerance Charter | Structured editor for §4.4 with inline explanation of each rule’s effect; version history; simulate — re-diff the last run under the edited rules without re-executing. | Edit; simulate; submit new version for G1 |
| Regression Monitor | Released reports with last regression result, schedule, and drift alerts. | Run now; open regression exception |
[/TABLE]


### 15.3.6 Governance surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Gate Inbox (client default) | Card stack of open gate requests for the user’s role and domain, ordered by due date; each card in the §15.5 anatomy; filters by gate type and site. | Approve; request changes; reject; delegate; ask a question |
| Decision Register | All GateDecisions and adjudications with approver, evidence, rationale; search and export. | Open evidence; export |
| Evidence Export | Select programme/site/date range; produce a signed bundle (decisions, provenance, verdicts, artefact hashes, daily roots) with a verification tool. | Generate; download; verify |
[/TABLE]


### 15.3.7 Admin surface

[TABLE]
| Screen | Layout and key components | Primary actions |
| Platform Health | Adapter status per site/workspace, queue depths, executor latencies, workflow failures, gateway error rates. | Pause/resume adapter; drain queue; open workflow |
| Pattern Library | Patterns by class and state with stats; candidates awaiting promotion with their proof history; retirements with cause. | Promote; retire; edit guards; export pack |
| Model Gateway & TokenOps | Providers, routing rules by task class, budgets, spend by agent/MU/pattern, cache hit rate, cost per report by tier. | Set budget; change routing; export |
| Data Handling | The data-handling position as a document: what crosses the inference boundary, redaction rules, provider, region, retention; InfoSec sign-off card; boundary test results. | Sign; run boundary test; export |
| Tenant & Access | Roles, users (Entra groups), site/domain scoping, service principals, secrets references. | Assign role; scope; rotate |
[/TABLE]


## 15.4 The Migration Unit page — anatomy
One URL per report. The header carries the state, tier, family, train, owners and the gate status strip. Below it, sections that expand: Source (sheets, dashboards, datasources, calcs with class labels, usage, screenshot); Artefacts (model reference, measures with source alongside and provenance badges, report definition with page thumbnails, Git links); Parity (latest run summary, case table, per-sheet verdict grid, evidence links, visual parity scores); Exceptions (open and closed, with decisions); Gates (G2 inherited from family, G3 card, G4 site status); Timeline (every event, from harvest to release, with who and what); Provenance (every artefact’s record, filterable by mode). Client roles see Source, Artefacts (thumbnails and documentation only), Parity (summary and verdict grid), Gates and Timeline.

## 15.5 The gate card — anatomy of the most important 30 seconds
┌ G3 · PARITY ACCEPTANCE · Daily VaR (RQA) ────────────────── due in 2d 06h ┐
│ WHAT    Accept migrated report 'Daily VaR' (2 pages, 6 visuals)              │
│ PROOF   41/41 parity cases PASS · charter v3 · full compare (no sampling)    │
│         1 visual redesigned (table-calc addressing) · waiver signed by you   │
│ VISUAL  structural 0.96 · perceptual 0.91 · reviewed by S. Iyer 14 Jan       │
│ CHANGES none to source data · model mf_risk_positions v2 (approved 9 Jan)   │
│ NEXT    on approval: promote to test → parallel run 4 weeks → your sign-off  │
│   [ APPROVE ]   [ REQUEST CHANGES… ]   [ ASK A QUESTION ]   [ OPEN REPORT ] │
└ countersigned by Migration Engineer A. Mehta · escalates to PM in 3d ───────┘
Identical anatomy on desktop and mobile, and mirrored into Teams as an adaptive card. Approve and Request Changes require a reason of at least one sentence. Open Report opens the target report in the test workspace in a new tab and the source view alongside.

## 15.6 Cross-cutting UX requirements
- Accessibility: WCAG 2.2 AA; full keyboard operation of the Exception Desk and Gate Inbox; screen-reader-tested gate cards and evidence tables.
- Responsiveness: Gate Inbox, gate cards and the Migration Unit page fully functional on mobile; Programme Board and Parity Dashboard tablet-first.
- Tenancy and branding: client branding on client-facing surfaces; prod/test/dev chrome visibly distinct.
- Latency budgets: board and queue updates ≤ 2 s from event; Migration Unit page open ≤ 500 ms; evidence bundle open ≤ 1 s.
- Localisation: en-GB and en-US in release 1; all strings externalised.
- Notifications: Teams and email for gate requests, exceptions assigned, regression failures and train re-plans; per-user preferences; digest mode.

# 16. Accuracy governance — how AI output earns trust
This section exists because a migration platform that uses language models has to answer a harder question than a platform that does not: how do you know the model was right? The answer in this product is structural. No model output is ever the last word; it is one step in a ladder that ends in execution or in a person.

## 16.1 The validation ladder

[TABLE]
| Rung | Check | Applies to | On failure |
| 1 Schema | Model response conforms to the declared output schema (JSON schema, strict) | Every model call | Hard failure; logged; no retry on schema — the prompt contract is at fault |
| 2 Parse | DAX / M / TMDL / PBIR parses under the target grammar | Every generated artefact | One regeneration with the parser error in context; then C4 flag |
| 3 Compile | Artefact deploys or evaluates against the dev model (XMLA trivial evaluate; TMDL deploy; PBIR binding check) | Every generated artefact | One regeneration with the compiler error; then C4 flag |
| 4 Proof | Parity verdict PASS under the charter | Every report | Mender loop (§11); then Exception Desk |
| 5 Human | Named approver at G2/G3/G4; adjudication at the Exception Desk | Every release; every waiver | Changes requested; MU returns to the relevant state |
[/TABLE]

A DETERMINISTIC artefact enters at rung 2. A GENERATED_PROVED artefact enters at rung 1 and must reach rung 4. No configuration flag skips a rung; the release path checks the provenance record’s validation block, not a status field.

## 16.2 Provenance on every artefact
The ProvenanceRecord in §4.2 is mandatory. The artefact store rejects a write without one, and the target adapter refuses to commit an artefact whose provenance does not reference it by content hash. Provenance is what makes three things possible: a Mender pass can see how the current artefact was produced before changing it; a pattern can be traced to the MU where it was first generated; and an auditor can ask “what did the model see?” and be shown the context hash and the prompt hash, with the context reproducible from the graph at that version.

## 16.3 Confidence and calibration
Agents that call a model declare a confidence with each output (the schema requires it). Declared confidence is worthless until it is calibrated: the platform records, per agent, per class and per model, the proof outcome for every declared confidence bucket, and reports the calibration curve on the Pattern Library screen. Routing uses calibrated confidence: a task class whose small-model outputs pass proof at the same rate as the reasoning model’s is routed to the small model. A class whose calibration drifts (declared 0.9 passing at 0.7) raises a platform alert and pins routing to the reasoning tier until reviewed.

## 16.4 Evaluation sets and the golden corpus
The platform ships with a golden corpus: Tableau workbooks with known-correct Power BI equivalents and parity suites, covering every C1 rule, every shipped C2 pattern, a set of C3 idioms and the C4 list. Every adapter, rule-engine, pattern-library and prompt-contract change runs the corpus in CI; a regression on the corpus blocks the release. Client engagements add to the corpus only with the client’s written agreement and only as anonymised structure (AST shapes and synthetic data), never as client workbooks.

## 16.5 Prompt-injection and content trust
Source workbook content — field names, calc comments, custom SQL, descriptions — is untrusted. It reaches a model only inside typed fields of the context contract, never in the instruction position; the gateway screens it with an injection classifier; and model outputs are validated against schema before any use. A model cannot cause an action: the only things a model output can become are an artefact that enters the ladder or a proposal that a person or rule decides on.

## 16.6 Accuracy metrics

[TABLE]
| Metric | Definition | Target (R1) | Where shown |
| First-pass parity rate | MUs whose first ParityRun is all-PASS ÷ MUs proved | ≥ 0.75 after calibration | Parity Dashboard, Calibration Report |
| Mean passes to pass | Average Mender passes consumed by MUs that reach PASSED | ≤ 1.4 | Parity Dashboard |
| Mender close rate | Failing MUs closed without an ExceptionCase ÷ failing MUs | ≥ 0.70 | Parity Dashboard |
| Class 3 proof rate | GENERATED_PROVED artefacts passing proof first time ÷ produced | ≥ 0.80 | Pattern Library |
| Pattern retirement rate | Patterns retired per 1,000 applications | ≤ 2 | Pattern Library |
| Waiver rate | Waived cases ÷ total cases | ≤ 0.03 | Parity Dashboard, Decision Register |
| Regression escape rate | Regression FAILs on released reports per 100 released | ≤ 1 | Regression Monitor |
| Calibration error | Mean |declared − observed| across confidence buckets | ≤ 0.08 | Pattern Library |
[/TABLE]


# 17. Acceleration instrumentation
Acceleration is reported, not asserted. Every figure below is computed from platform events and, where human time is involved, from time recorded against ExceptionCases, adjudications and gate reviews in the console. These are the numbers that price the next engagement.

## 17.1 Absorption
For each lifecycle stage (discovery, model, calculation, visual, validation) and each MU, absorption is 1 − human_minutes / work_content_minutes, where work content is the calibrated per-tier stage allowance carried on the programme record and human minutes are recorded against the MU. Reported per stage, per tier, per train and per programme, against the calibrated baseline from the Calibration Wave.

## 17.2 Cycle time and throughput

[TABLE]
| Measure | Definition | Where shown |
| Stage cycle time | Elapsed time per MU between state transitions (HARVESTED→GENERATED→PASSED→ACCEPTED→RELEASED), p50/p90 | Programme Board |
| Gate wait | Time an MU or family spends waiting at G2/G3/G4 — the client-side component of cycle time | Programme Board, Gate Inbox aging |
| Throughput | MUs reaching ACCEPTED per week per train | Programme Board |
| Engineer time per MU | Minutes recorded by engineers per MU, by tier | Calibration Report; internal |
| Queue health | Exception Desk depth, age distribution, claim-to-decision time | Exception Desk |
[/TABLE]


## 17.3 TokenOps
Metering attributes every model call to an MU, an agent, a class and a pattern. Reported: tokens and cost per MU by tier; cost per stage; cache hit rate; routing mix by tier; and cost per report against the programme budget. Cost engineering loops run automatically: identical-context caching, routing demotion for calibrated classes, and a distillation-candidate list (Class 3 idioms that have become patterns and no longer need a model at all).

# 18. Security, identity and data handling

## 18.1 Identity
Human users authenticate through the client’s Entra ID; roles map from Entra groups and are scoped by site and domain. Agents and adapters are non-human identities (SPIFFE/SVID workload identity) with short-lived credentials issued per run by a credential broker; the Tableau and Fabric service principals are held in Key Vault and never enter agent context. Every action in Tableau or Fabric is attributable in the client’s own audit logs to the platform identity plus run plus MU.

## 18.2 Execution safety
Agent and adapter workers run in sandboxes with default-deny egress; only the source, the target, the graph, the artefact store and the gateway are reachable. Per-run ephemeral filesystems. Tool-call arguments validated against schemas. Source content treated as untrusted per §16.5.

## 18.3 The inference boundary
This is the data-handling position the client’s InfoSec signs, rendered as a screen and enforced by the gateway:

[TABLE]
| Crosses the boundary to an inference endpoint | Never crosses |
| Calculation expressions and their ASTs; field, table, datasource and workbook names; data types; visual specifications (shelves, encodings, layout); parameter definitions and domains; model definitions (TMDL); compiler and parser error text; parity evidence headers and deltas (key values are redacted to hashes; measure values are redacted to sign-and-magnitude buckets) | Row-level data of any kind; extract contents; source or candidate result sets; parity comparison values; user identities; credentials; custom SQL literals matching the client’s secret patterns (redacted by the gateway); anything tagged by the client’s classification as restricted |
[/TABLE]

The Proof Engine runs entirely inside the tenant. Mender repair context is assembled from evidence bundles after redaction, and the redaction is tested by the boundary test on the Data Handling screen, which sends canary values through the pipeline and asserts they never reach the gateway’s outbound log. Provider, region, retention and logging are configured per tenant and shown on that screen. Model providers are contractually prevented from training on client traffic.

## 18.4 Evidence Chain
As §4.5. Append-only, hash-linked per MU and per day; daily roots exportable and optionally anchored externally. Records capture inputs (context hash, prompt hash, pattern refs), agent and model identity, validation results, verdicts with evidence hashes, gate decisions with approver identity, and every state transition.

## 18.5 Compliance mapping

[TABLE]
| Regime | How the platform supports it |
| ISO 27001 / SOC 2 | Control set mapped; evidence generated continuously from the chain; in-tenant deployment keeps data residency with the client |
| Model risk management (SR 11-7 style) | Golden corpus as validation dataset; calibration reporting; provenance as model inventory; human gates as effective challenge |
| EU AI Act (deployer duties) | Agent records as AI-system registry; human oversight by construction (gates, ladder); logging and traceability via provenance and evidence |
| SOX / segregation of duties | An agent that generates an artefact cannot approve it; countersign required at G3; PM approval for production promotion |
| Data protection | No personal data crosses the inference boundary; user identities pseudonymised in evidence exports on request |
[/TABLE]


# 19. Integration architecture

[TABLE]
| Domain | Release-1 integrations | Depth |
| Source BI | Tableau Server 2022.1+ and Tableau Cloud (REST, Metadata API, Hyper API) | Enumerate, fetch, parse, execute, usage, ownership, screenshot, archive |
| Target BI / data | Microsoft Fabric (Git integration, deployment pipelines, XMLA read-write, Power BI REST export), Power BI Desktop project format | Emit, deploy, execute, export |
| Source connections (Proof live replay) | SQL Server, Snowflake, PostgreSQL, Sybase (ODBC), Hive (ODBC), files | Read-only query execution under client service account |
| Repository | Azure Repos, GitHub | Artefact mirror; PR-based promotion of patterns |
| Identity | Entra ID | SSO, group-to-role mapping, service principals |
| Collaboration | Microsoft Teams, email | Gate cards as adaptive cards; notifications; status pack distribution |
| Work tracking (optional) | Azure DevOps Boards, Jira | One-way mirror of ExceptionCases and gate requests for clients who require it |
| Observability | Azure Monitor / OpenTelemetry | Traces per MU workflow; metrics; alerting |
| Model providers | Anthropic API, Azure OpenAI | Via gateway only |
[/TABLE]

Integrations are separated into sensing (read from source and target) and acting (write to target, archive source). Acting integrations run only through the Steward and the target adapter under the autonomy ceilings in §13.2; no other component can write to a client system.

# 20. APIs and extensibility
- Graph API (GraphQL): typed queries over the Estate Graph with filters on state, tier, family, train, class and verdict; subscriptions on MU state changes and gate events. This is the console’s API and is exposed to the client for their own reporting.
- Programme API (REST): trains, waves, MUs, gates — create, inspect, transition where a human action is permitted; idempotent; webhook subscriptions on lifecycle transitions.
- Proof API (REST): run or re-run a suite; fetch verdicts and evidence bundles; charter versions; regression schedules.
- Evidence API (REST): query and export with verification helpers; streaming to SIEM.
- Adapter SDK (Python): the §6/§7 interfaces, manifest schema, conformance harness, grammar tooling for calc-language parsers, and a packaging pipeline. A new source adapter is a repository that passes the harness.
- Pattern SDK: signature and template language, guard expression language, local matcher, and the promotion pipeline hooks; patterns are shipped as versioned packs per adapter.
- Events: CloudEvents for every state transition and decision (catalogue in Appendix C).

# 21. Data model reference
Relational tables backing the graph and the platform records. Graph nodes and edges are stored in Apache AGE within the same PostgreSQL instance; the tables below hold platform records that are not graph-shaped or that require relational integrity.

[TABLE]
| Table | Key columns | Notes |
| tenant | id, name, deployment_mode, region, provider_policy | One row per client deployment |
| programme | id, tenant_id, name, start, charter_version, calibration_baseline_json, scope_json | Scope: sites, workbooks, tiers |
| release_train / wave | id, programme_id, name, sequence, planned_start, planned_end, actual_start, actual_end, wip_limit |  |
| migration_unit | id, programme_id, workbook_node_id, tier, score, family_id, train_id, state, passes, owners_json, timing_json | State machine §3.2 |
| model_family / semantic_model | id, programme_id, name, state, version, design_hash, tmdl_ref, workspace_id, conformance_json | §12.2 |
| artefact | id, kind, content_hash, mu_id, family_id, storage_ref, git_ref, validation_json, provenance_id | Content-addressed |
| provenance | id, artefact_id, agent, agent_version, mode, context_hash, prompt_hash, model, tokens_in, tokens_out, confidence, supersedes_id | §4.2 |
| pattern / pattern_stats | id, adapter, class, signature_json, template, guards_json, state, origin_json; applications, pass, fail, last_fail | §4.3 |
| parity_suite / parity_case / parity_run / verdict | suite: mu_id, sheet_refs; case: grain_json, measures_json, filter_ctx_json, params_json, strategy, sampled; run: charter_version, started, finished; verdict: case_id, run_id, result, failing_cells_ref, evidence_ref | §10 |
| exception_case | id, mu_id, class, evidence_ref, state, assignee, decision, decision_reason, pattern_candidate_id, opened, decided | §11.3 |
| gate_request / gate_decision | id, gate, subject_type, subject_id, version_hash, approver, countersign, decision, rationale, evidence_refs_json, opened, due, decided | §13 |
| evidence_record | seq, ts, kind, subject_id, payload_hash, prev_hash, daily_root_id | Append-only; §4.5 |
| model_call | id, gateway_request_id, agent, mu_id, class, provider, model, prompt_hash, response_hash, tokens_in, tokens_out, cost, cache_hit, latency_ms | TokenOps |
| user / role_binding | user: upn, display, org (artizent|client); binding: user_id, role, scope_json | Entra-linked |
| site_record | id, programme_id, site_node_id, licence_cost_annual, users, early_renewal_flag, parallel_run_start, decommissioned_at, licence_released_value | Business case |
[/TABLE]


# 22. Non-functional requirements

[TABLE]
| Dimension | Requirement (release 1 targets) |
| Scale | 10,000 workbooks, 250,000 fields, 50,000 calculated fields, 2 million graph edges per tenant; 500 concurrent parity cases in execution |
| Throughput | Harvest ≥ 500 workbooks/hour/site worker; generation ≥ 60 MUs/hour; proof ≥ 200 cases/minute on a 200k-row cap with executors healthy |
| Latency | Graph queries for a Migration Unit page ≤ 300 ms p95; gate card open ≤ 300 ms; board refresh ≤ 2 s from event |
| Availability | Control plane 99.5% during programme hours; workflows durable across restarts; no data loss on worker failure |
| Durability & DR | PostgreSQL with PITR; artefact store geo-redundant; Evidence Chain RPO 0 (synchronous); RTO 4 hours |
| Determinism | Same input, same adapter/rule/pattern versions → byte-identical DETERMINISTIC output; same context hash → identical model response via cache |
| Auditability | 100% of artefacts with provenance; 100% of transitions and decisions in evidence; export verifiable offline |
| Security | In-tenant deployment; default-deny egress; NHI per run; boundary test automated; no secrets in prompts |
| Observability | OpenTelemetry traces per MU workflow; metrics for every §16.6 and §17 measure; alerting on calibration drift, pattern retirement, executor failure |
| Cost | Model spend per report ≤ 5% of the per-report price at calibrated routing; reported daily |
[/TABLE]


# 23. Test strategy

[TABLE]
| Level | What is tested | How | Gate |
| Unit | Rule engine, pattern matcher, normaliser, differ, state machine, context assembler | pytest; property-based tests for the differ (commutativity of key sets, tolerance monotonicity) | CI |
| Grammar | Calc-language parser | Corpus of 5,000 real-world expressions (anonymised); round-trip; unrecognised-rate tracking | CI |
| Adapter conformance | §6.3 suite against a reference Tableau site and a reference Fabric workspace | Nightly against live sandboxes | Adapter release |
| Golden corpus | End-to-end: harvest → generate → prove on known-correct pairs | CI on every change to adapter, rules, patterns, prompts, gateway | Release |
| Proof self-test | The differ against synthetic result sets with known injected differences per failure class | CI | Release |
| Prompt contracts | Schema conformance and proof rate per contract version on the corpus | CI; calibration drift alert in production | Release |
| Boundary | Canary values never reach the gateway outbound log | CI and on-demand in tenant | Tenant enablement |
| Load | NFR throughput and latency on a synthetic 10k-workbook estate | Pre-release | Release |
| UX | Screen flows for every role; accessibility audit | Playwright; axe | Release |
| Calibration Wave | The product on the client’s real estate before prices are fixed | Per engagement | Commercial |
[/TABLE]


# 24. Release roadmap

[TABLE]
| Release | Window | Scope |
| R1 — Tableau to Power BI | GA Q1 2027 | Everything in this specification for the Tableau source adapter and the Power BI / Fabric target adapter; in-tenant Azure deployment; console for all roles; golden corpus v1; first client programme (BlackRock) from October 2026 on the R1 candidate |
| R1.1 — Hardening | Q2 2027 | Pattern pack v2 from the first programme; Artizent-hosted topology; ADO/Jira mirror; regression runner handover kit |
| R2 — Second source | H2 2027 | Cognos adapter (report specs, expressions, Framework Manager models) against the same contract; MicroStrategy adapter scoped; cross-adapter pattern sharing |
| R2.1 — Estate continuity | Q4 2027 | Continuous mode: harvester watches the source estate, re-opens MUs on change, keeps the Estate Graph current after migration for governance use |
| R3 — Second target | 2028 | Target adapter abstraction hardened; Looker or Tableau-as-target adapter; multi-target programmes |
[/TABLE]

Sequencing logic: R1 proves the accuracy machinery on one source and one target where the client’s stakes are highest; R2 proves the adapter contract by adding a source whose expression language is structurally different; R3 proves the target contract. The Proof Engine, Pattern Library and Control Plane do not change shape across releases.

# 25. Product success metrics

[TABLE]
| Metric | Target by end of first programme | Why it matters |
| Absorption (programme) | ≥ 0.65 across stages, against calibrated work content | The number the fixed price rests on |
| First-pass parity | ≥ 0.75 | Accuracy of generation before any repair |
| Mender close rate | ≥ 0.70 | Accuracy of repair; size of the human residue |
| Engineer minutes per MU (Complex) | ≤ 800 | Direct check on the effort model |
| Gate wait share of cycle time | Reported; client-facing | Shows where the schedule actually goes |
| Pattern library growth | ≥ 150 ACTIVE patterns at close | The asset carried to the next engagement |
| Waiver rate | ≤ 0.03 | Keeps the fixed price honest |
| Cost per report (model spend) | ≤ 5% of price | TokenOps discipline |
| Handover completeness | 100% of artefacts, suites, patterns and evidence transferred | P7 |
[/TABLE]


# 26. Glossary

[TABLE]
| Term | Meaning |
| Migration Unit (MU) | One source workbook and everything the platform produces for it; the unit of work, state and invoicing |
| Estate Graph | The property graph of the source estate, the target artefacts and their relationships |
| Model family | A cluster of workbooks that share lineage and will bind to one semantic model |
| Foundry | The workflow that turns a family into an approved, built semantic model |
| Class C1–C4 | The Transpiler’s classification of a calculated field: direct map, structural rewrite, context-dependent, no equivalent |
| Pattern | A reusable source-signature → target-template transformation with guards and statistics |
| Parity case / run / verdict | One executable comparison; a set of cases executed under one charter version; the result of one case |
| Tolerance Charter | The versioned definition of “the same result”, approved at G1 |
| Evidence bundle | The failing cells, context and artefacts attached to a FAIL verdict |
| Mender pass | One bounded iteration of classify → fix → re-prove |
| Exception Case | A failure the loop could not close, with evidence, on the engineer’s queue |
| Gate G1–G4 | Tolerance charter; model design approval; parity acceptance; decommission authorisation |
| AI mode | DETERMINISTIC, ASSISTED, GENERATED_PROVED or HUMAN — how an output was produced and what must be true before use |
| Provenance record | The mandatory record of how an artefact was produced and validated |
| Calibration Wave | The first-month run on a stratified sample that measures class mix, parity rates and family count before prices are fixed |
| Absorption | 1 − human minutes ÷ calibrated work content, per stage and tier |
| Autonomy L0–L4 | Manual, advise, approve-first, supervised, autonomous — set per action class |
[/TABLE]


# Appendix A — Traceability to the BlackRock proposal

[TABLE]
| Proposal claim | Specification section | Mechanism |
| All 1,067 workbooks parsed by machine; no per-workbook study | §6.2, §8.4 | Tableau adapter + Harvester; parse quality gate |
| ~150 shared governed models (planning assumption, measured in Month 1) | §8.5, §12.1, §14.3 | Cartographer clustering; Calibration Report family count |
| Transpiler four-class model; class mix measured before price is fixed | §9.1, §9.5, §14.3 | Deterministic classification; Calibration Report |
| Parity proved cell by cell under an agreed tolerance | §4.4, §10 | Tolerance Charter; Proof Engine |
| Remediation loop closes mechanical failures before an engineer sees them | §11 | Mender with bounded passes; failure taxonomy; patterns |
| Four gates; no agent approves anything | §13 | Gate objects; autonomy ceilings; decision records |
| Migration Console visible from month one | §15 | Programme, estate, foundry, delivery, proof, governance, admin surfaces |
| Metadata only crosses the inference boundary | §18.3 | Gateway redaction; boundary test; in-tenant Proof Engine |
| Absorption rises with complexity; absorption measured, not asserted | §17.1 | Event-derived absorption vs calibrated baseline |
| Fixed price per report invoiced on parity pass + sign-off | §3.2 ACCEPTED, §13.1 G3 | State transition as invoice trigger |
| Everything handed over | §8.11, §10.6, §20 | Steward handover bundle; regression runner; APIs |
[/TABLE]


# Appendix B — Tableau → Power BI mapping reference (excerpt)

### B.1 Calculation function families

[TABLE]
| Tableau family | Examples | DAX / M target | Default class |
| Aggregate | SUM, AVG, MIN, MAX, COUNT, COUNTD, MEDIAN, PERCENTILE | SUM, AVERAGE, MIN, MAX, COUNT/COUNTROWS, DISTINCTCOUNT, MEDIAN, PERCENTILE.INC | C1 |
| Arithmetic / logical | + − × ÷, AND, OR, NOT, IF/ELSEIF, CASE, IIF, ZN, IFNULL, ISNULL | Operators, IF, SWITCH, DIVIDE, COALESCE, ISBLANK | C1 (ZN/IFNULL → C2 null idiom) |
| String | LEFT, RIGHT, MID, LEN, CONTAINS, REPLACE, SPLIT, TRIM, UPPER, LOWER, REGEXP_* | LEFT, RIGHT, MID, LEN, CONTAINSSTRING, SUBSTITUTE, PATHITEM (limited), TRIM, UPPER, LOWER; REGEXP → M or C4 | C1 / C2 / C4 (regex) |
| Date | DATEPART, DATETRUNC, DATEADD, DATEDIFF, DATENAME, TODAY, NOW, MAKEDATE | YEAR/MONTH/DAY, date-table columns, DATEADD (date table), DATEDIFF, FORMAT, TODAY, NOW, DATE | C2 (date table alignment) |
| Type | INT, FLOAT, STR, DATE, DATETIME, BOOL | INT, VALUE, FORMAT, DATEVALUE, CONVERT | C1 |
| LOD | {FIXED …}, {INCLUDE …}, {EXCLUDE …} | CALCULATE with ALLEXCEPT / ALL / VALUES / REMOVEFILTERS patterns | C2; C3 when interacting with context filters |
| Table calc — simple | RUNNING_SUM/AVG, TOTAL, WINDOW_SUM with table-down/across addressing | Window functions (WINDOW/OFFSET/INDEX) or CALCULATE with visual-grain filters | C2 when addressing resolves from the sheet; C3 otherwise |
| Table calc — complex | LOOKUP with offsets, INDEX/FIRST/LAST arithmetic, RANK_* with restarting, nested table calcs | OFFSET/INDEX/RANK where the grain is fixed; otherwise C4 | C3 / C4 |
| Parameters | Parameter references in calcs and filters | What-if parameter tables; SELECTEDVALUE | C2 |
| Sets / groups / bins | IN set, group members, BIN | Calculated columns or dimension tables; grouping tables | C2 |
| RAWSQL | RAWSQL_*, RAWSQLAGG_* | M pass-through where dialect supported; else C4 | C4 by default |
| ATTR | ATTR(field) | SELECTEDVALUE / HASONEVALUE guard | C3 (context-dependent) |
[/TABLE]


### B.2 Visual types

[TABLE]
| Tableau mark / sheet | Power BI visual | Notes |
| Bar (horizontal/vertical, stacked, side-by-side) | Clustered/stacked bar and column | Sort and colour legend carried |
| Line, area, dual axis | Line, area, line-and-column combo | Dual axis → combo; synchronised axes flagged for review |
| Text table / crosstab | Matrix or table | Subtotals mapped; row banding by theme |
| Highlight table | Matrix with conditional formatting |  |
| Scatter, bubble | Scatter | Size and colour carried |
| Map (filled, symbol) | Filled map, map | Requires geography role on the model column; ArcGIS layers → C4 |
| Treemap, pie, donut | Treemap, pie, donut |  |
| Gantt | Gantt (custom visual) or C4 | Flagged for redesign unless the client approves the custom visual |
| Bullet, box plot, histogram | Custom visual or C4 | Flagged for redesign |
| KPI / BAN | Card / KPI |  |
| Reference lines / bands | Analytics pane constant/average lines | Distribution bands → C4 |
| Dashboard actions (filter, highlight, URL) | Cross-filter/highlight settings, drill-through, URL via conditional formatting | Parameter and set actions → C3 or C4 |
| Story points, page shelf animation | — | C4 |
[/TABLE]


# Appendix C — Event catalogue (CloudEvents types)

[TABLE]
| Type | Emitted when | Key attributes |
| astra.data.mu.state.changed | Any MU transition | mu_id, from, to, cause |
| astra.data.harvest.completed | Harvester finishes a scope | scope, workbooks, parse_quality_p50 |
| astra.data.family.proposed / .state.changed | Cartographer / Foundry | family_id, state, version |
| astra.data.artefact.written | Artefact + provenance stored | artefact_id, kind, mode, validation |
| astra.data.parity.run.completed | Arbiter finishes a run | run_id, mu_id, pass, fail, inconclusive, charter_version |
| astra.data.mender.pass.completed | Mender pass ends | mu_id, pass_no, fixed, remaining, patterns_applied |
| astra.data.exception.opened / .decided | Exception Desk | case_id, class, decision |
| astra.data.gate.opened / .decided | Gate workflow | gate, subject, decision, approver |
| astra.data.pattern.promoted / .retired | Pattern service | pattern_id, cause, stats |
| astra.data.release.promoted / .decommissioned | Steward | mu_id | site_id, environment, pipeline_run |
| astra.data.calibration.completed | Calibration Wave | programme_id, report_ref |
| astra.data.alert.calibration_drift / .boundary_violation | Platform safety | agent, class, detail |
[/TABLE]

