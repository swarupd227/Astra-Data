ASTRA DATA
Migration Accelerator
Product Backlog v1.0 — epics, features and user stories
Version	1.0 — draft for engineering
Status	For increment planning
Owner	Swarup D., Product Engineering, Data & AI
Date	September 2026
Companion	Product Specification v1.0

# How to read this document
This is the build backlog for the Astra Data Migration Accelerator. It decomposes the Product Specification v1.0 into epics, features and user stories that an engineering team can plan, estimate and deliver. Every feature cites the specification section it implements; the specification stays the authority on behaviour, and this document is the authority on sequencing and acceptance.
Stories use the standard form (as a / I want / so that) with acceptance criteria that are testable as written. Priorities are P0 (release cannot ship without it), P1 (release is materially worse without it) and P2 (valuable, may slip). Release tags are R1 (first GA, targeted Q1 2027 and the BlackRock programme), R1.1 (first quarter after GA), R2 (2027 H2) and R3 (2028).

[TABLE]
| Section | Contains |
| 1 | Scope of the backlog and what is deliberately excluded from R1 |
| 2 | Personas used in the stories |
| 3 | Epic map: thirteen epics, their goal, spec traceability and release |
| 4 | Epics, features and user stories with acceptance criteria |
| 5 | Non-functional stories |
| 6 | Release sequencing and the R1 definition of done |
| 7 | Dependencies, risks and open questions |
[/TABLE]


# Contents
How to read this document
1. Scope
1.1 What the backlog covers
1.2 What is excluded
2. Personas
3. Epic map
4. Epics, features and user stories
E1. Estate Graph and Harvest
E2. Source Adapter framework and Tableau adapter
E3. Cartographer: clustering, families and waves
E4. Semantic Model Foundry and G2
E5. Transpiler and Pattern Library
E6. Compositor
E7. Proof Engine and Tolerance Charter
E8. Mender and Exception Desk
E9. Release and Decommission
E10. Migration Console
E11. Security, identity, evidence and inference boundary
E12. Platform: orchestration, TokenOps, observability, APIs
E13. Calibration Wave and accuracy metrics
5. Non-functional stories
6. Release sequencing
6.1 Increments to R1
6.2 R1 definition of done
6.3 After R1
7. Dependencies, risks and open questions
7.1 External dependencies
7.2 Delivery risks
7.3 Open questions
Assumptions and limits

# 1. Scope

## 1.1 What the backlog covers
The backlog covers the platform core, the Tableau source adapter, the Power BI / Fabric target adapter, the eight agents, the Proof Engine, the Migration Console and the security and evidence layer — the full Release 1 product as specified, plus the R1.1–R3 items needed to plan the roadmap. Stories tagged R2 and R3 are written at feature level with fewer stories; they will be refined before their release planning.

## 1.2 What is excluded
- A second source adapter (Cognos, Qlik, MicroStrategy). The adapter contract (E2) is built so these can be added, but no story here builds one.
- A second target (Looker, Sigma). Same position.
- Unattended migration with no gates. Not on the roadmap.
- Data movement. The product provisions Fabric artefacts; it does not migrate warehouse data.
- Visual-level pixel fidelity scoring as a gate. Visual parity is advisory in R1 (F7.6).
Rule  Where a story says the system 'must' do something, that is the acceptance bar. Where the specification and this document disagree, the specification wins and the story is corrected.

# 2. Personas

[TABLE]
| Persona | Short name in stories | What they need from the product |
| Programme Manager (Artizent) | programme manager | Plan, wave and gate status against the fixed-price plan; a status pack they do not have to assemble by hand |
| Migration Architect (Artizent) | architect | Target architecture and conformance rules enforced automatically; Class 4 redesign decisions |
| Semantic Model Engineer (Artizent) | model engineer | Cartographer proposals they can edit and approve; TMDL built from approved designs |
| Migration Engineer (Artizent) | migration engineer | A queue of exceptions with the evidence attached; the ability to patch, re-prove and close |
| Parity Engineer (Artizent) | parity engineer | The Tolerance Charter, parity suites, inconclusive investigation |
| Platform Engineer (Artizent) | platform engineer | Adapter health, pattern promotion, model gateway, budgets, deployment |
| Client data owner | data owner | Approve or question model designs for their domain (G2) |
| Client report owner | report owner | See the parity record and the visual, resolve flagged logic, sign off (G3) |
| Client licence administrator | licence admin | Authorise decommission when readiness is met (G4) |
| Client InfoSec reviewer | InfoSec reviewer | Confirm the data-handling position and inference boundary; export evidence |
| Auditor (internal or external) | auditor | Read the evidence chain and provenance for any released report |
[/TABLE]


# 3. Epic map

[TABLE]
| Epic | Name | Goal | Spec § | Release |
| E1 | Estate Graph and Harvest | Every source object is parsed into a queryable graph with parse quality measured, and the graph is the only shared state | 4.1, 8.4 | R1 |
| E2 | Source Adapter framework and Tableau adapter | One adapter contract; a Tableau adapter that passes the conformance suite against Server and Cloud | 6 | R1 |
| E3 | Cartographer: clustering, families and waves | Model families and release trains proposed from graph evidence; wave plan the programme manager confirms | 8.5, 12.1, 14.2 | R1 |
| E4 | Semantic Model Foundry and G2 | Approved model designs become TMDL in the dev workspace with conformance enforced; G2 runs in the console | 12, 13.1 | R1 |
| E5 | Transpiler and Pattern Library | Four-class calculation pipeline with a rules engine, LLM generation under proof, and pattern promotion | 9, 8.7 | R1 |
| E6 | Compositor | Report definitions (PBIR) generated from dashboards and bound to Foundry models, with visual mapping and redesign flags | 8.8, 7.1 | R1 |
| E7 | Proof Engine and Tolerance Charter | Parity cases derived, executed on both sides, diffed under a versioned charter, with verdicts and evidence bundles | 10, 4.4, 8.9 | R1 |
| E8 | Mender and Exception Desk | Bounded agentic repair of parity failures; residue routed to engineers with the evidence attached | 11, 8.10 | R1 |
| E9 | Release and Decommission | Promotion through pipelines, parallel-run tracking, regression and G4 decommission with licence-release record | 14.4, 8.11, 13.1 | R1 |
| E10 | Migration Console | The seven surfaces, the Migration Unit page and the gate card; role-based, event-sourced, client-safe | 15 | R1 |
| E11 | Security, identity, evidence and inference boundary | Tenant deployment, NHI for agents, sandboxed execution, evidence chain, data-handling controls | 18, 4.5 | R1 |
| E12 | Platform: orchestration, TokenOps, observability, APIs | Temporal workflows, event bus, model gateway with budgets, metrics, SDKs and the public API | 5, 14.1, 17.3, 20 | R1 |
| E13 | Calibration Wave and accuracy metrics | The first-class calibration programme object, calibration report and the accuracy metrics the product publishes about itself | 14.3, 16, 17 | R1 |
[/TABLE]

Epics E1, E2, E5, E7 and E12 form the critical path: nothing proves until the graph exists, the adapter executes and the Proof Engine diffs. E10 (console) is built surface by surface alongside the engine it exposes so that no engine feature ships without a way to see it.

# 4. Epics, features and user stories

## E1. Estate Graph and Harvest
Goal: the source estate is parsed into a property graph that every agent reads and writes, with parse quality measured per workbook and nothing held outside the graph. Spec §4.1, §8.4, §21.

### F1.1  Graph store and ontology
The Estate Graph service (graph-svc) on PostgreSQL 16 with Apache AGE, implementing the node and edge ontology in §4.1.1–4.1.2 with schema enforcement.

[TABLE]
| S1.1.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | the node and edge types in the ontology to be enforced at write time |
| So that | no agent can write a shape the rest of the platform does not understand |
[/TABLE]

Acceptance criteria
- All node types in §4.1.1 and edge types in §4.1.2 are defined with typed properties; a write with an unknown type or a missing required property is rejected with a 422 and a message naming the property
- Every node carries id (ULID), side (source | target | platform), created_by (agent or user principal) and created_at
- Every edge carries written_by; the ontology table in the spec is generated from the schema in CI, so the two cannot drift
- Schema changes are versioned migrations; a migration that removes a property fails CI unless it also supplies a backfill

[TABLE]
| S1.1.2 | P0   ·   R1 |
| As a | migration engineer |
| I want | to query the graph with a typed query API and a raw Cypher endpoint |
| So that | I can answer any lineage question without a new feature |
[/TABLE]

Acceptance criteria
- GraphQL API exposes node lookup by id and luid, neighbourhood traversal to depth 5, and the named contracts in §4.1.3
- A read-only Cypher endpoint is available to Artizent roles with a 30-second timeout and a 10,000-row cap
- p95 latency for a workbook neighbourhood (depth 3) on a 1,000-workbook estate is under 300 ms
- Every query is logged with principal and duration

[TABLE]
| S1.1.3 | P0   ·   R1 |
| As a | auditor |
| I want | every graph mutation to be recorded with who made it and from which run |
| So that | the graph can be reconstructed and any fact traced to its origin |
[/TABLE]

Acceptance criteria
- Mutations are emitted as CloudEvents (estate.node.upserted, estate.edge.upserted, estate.node.retired) with the run id
- A replay of the event stream from empty produces a graph identical to the live graph (verified by a nightly CI job on the test estate)
- Hard deletes are not possible through the API; retirement sets retired_at and keeps the node

### F1.2  Harvester agent
The Harvester pulls the estate through the source adapter and writes it to the graph. Deterministic; no model calls. Spec §8.4.

[TABLE]
| S1.2.1 | P0   ·   R1 |
| As a | programme manager |
| I want | to point the platform at a Tableau site and get the whole site parsed into the graph |
| So that | discovery is a job that runs, not a workshop |
[/TABLE]

Acceptance criteria
- Harvest is started from the Estate Explorer or the API with site credentials from Key Vault; progress is visible per project with counts of workbooks queued, parsed, failed
- For each workbook the Harvester records: sheets, dashboards, datasources, connections, fields, calculated fields (with formula and AST), parameters, filters, actions, owners, views in the last 90 days, distinct viewers
- A site of 1,000 workbooks parses in under 4 hours on the reference deployment; failures do not stop the run and are listed with the error
- Re-harvest of an unchanged workbook (same revision) is a no-op recorded as skipped_unchanged

[TABLE]
| S1.2.2 | P0   ·   R1 |
| As a | parity engineer |
| I want | each workbook to carry a parse-quality score and its unrecognised constructs listed |
| So that | I know before the Calibration Wave which workbooks the grammar cannot yet read |
[/TABLE]

Acceptance criteria
- parse_quality = recognised constructs ÷ total constructs is stored on the Workbook node and shown in the Estate Explorer
- Unrecognised constructs are stored verbatim with location (sheet, field) and flagged unrecognised: true
- Workbooks below the configurable threshold (default 0.98) are held in the Parse Quality Queue and do not advance to CLUSTERED
- A Platform Engineer can mark a construct 'ignorable' with a reason, or extend the grammar; either action re-scores the workbook without a full re-harvest

[TABLE]
| S1.2.3 | P1   ·   R1 |
| As a | programme manager |
| I want | usage and ownership to be captured with the workbook |
| So that | waves can be ordered by business impact and owners can be assigned gate requests |
[/TABLE]

Acceptance criteria
- Views and distinct viewers over the trailing 90 days are stored per workbook and per view
- Owner is linked to a User node resolved against Entra ID where possible; unresolved owners are listed for assignment
- Licence tier of the site and the per-user tier (Creator / Explorer / Viewer) are stored where the Metadata API exposes them

[TABLE]
| S1.2.4 | P1   ·   R1 |
| As a | platform engineer |
| I want | the Harvester to run incrementally on a schedule |
| So that | the graph stays current through a long programme without re-parsing the whole site |
[/TABLE]

Acceptance criteria
- Scheduled run detects new revisions through the Metadata API updatedAt and re-parses only those workbooks
- A changed source workbook that already has an MU in progress raises a SOURCE_DRIFT event and marks the MU for re-proof (see E7)
- Schedule and last run are visible on Platform Health

### F1.3  Context contracts and retrieval
Agents do not receive raw graph dumps. Each agent declares the sub-graph shape it needs; the context assembler materialises exactly that. Spec §4.1.3.

[TABLE]
| S1.3.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | each agent's context contract to be declared in code and materialised by a shared assembler |
| So that | prompts are small, deterministic and reproducible |
[/TABLE]

Acceptance criteria
- A context contract is a named GraphQL fragment plus a serialiser; the assembler returns a canonical JSON document and its sha256 (context_hash)
- Two calls with the same graph state and contract produce the same hash (tested in CI)
- The Transpiler contract includes the CalculatedField, its transitive DEPENDS_ON closure, referenced Parameters, the target ModelTable columns and the matching Pattern records; nothing else
- Context size is reported per call; a contract that exceeds its declared budget fails the call rather than truncating silently

[TABLE]
| S1.3.2 | P1   ·   R1 |
| As a | auditor |
| I want | to reproduce the exact context an agent saw for any artefact |
| So that | a provenance record is verifiable, not just descriptive |
[/TABLE]

Acceptance criteria
- From a ProvenanceRecord the console can re-materialise the context at the recorded graph version and show that the hash matches
- Graph versions are addressable by event offset; retention for versions is the programme lifetime plus 12 months

### F1.4  Estate surface (console)
The Estate Explorer, Lineage View, Parse Quality Queue and Usage & Ownership screens. Spec §15.3.2.

[TABLE]
| S1.4.1 | P0   ·   R1 |
| As a | programme manager |
| I want | to browse the estate by site, project and workbook with parse status and lineage |
| So that | I can see what we are migrating and what is blocking it |
[/TABLE]

Acceptance criteria
- Estate Explorer shows the site → project → workbook tree with counts and parse status; the centre pane lists workbooks with tier, family, train, state, class mix; the right pane shows the selected workbook with a lineage mini-graph
- Faceted filters: tier, state, family, train, owner, parse quality band, usage band
- Actions: open MU, re-harvest, re-tier with reason, withdraw from scope with reason (Programme Manager only)
- Screen loads a 1,067-workbook site in under 2 seconds

[TABLE]
| S1.4.2 | P1   ·   R1 |
| As a | model engineer |
| I want | a lineage view of workbooks, tables and fields |
| So that | I can see why the Cartographer grouped a family and challenge it |
[/TABLE]

Acceptance criteria
- Force-directed graph with node type filter; edge weight shows shared lineage strength; colour shows MU state
- Selecting a family highlights its members; export to PNG and JSON

[TABLE]
| S1.4.3 | P0   ·   R1 |
| As a | platform engineer |
| I want | a Parse Quality Queue |
| So that | the grammar gaps are worked down before the Calibration Wave |
[/TABLE]

Acceptance criteria
- Queue lists workbooks under threshold with the unrecognised constructs grouped by pattern and frequency across the estate
- Actions per construct: mark ignorable with reason (re-scores), open grammar issue (creates a ticket with the construct text and locations), request re-harvest
- Queue shows the estate-wide count of workbooks that would be released by fixing each construct

## E2. Source Adapter framework and Tableau adapter
Goal: one contract that a source adapter implements; a Tableau adapter that passes the conformance suite against Tableau Server and Tableau Cloud. Spec §6.

### F2.1  Source Adapter contract and SDK

[TABLE]
| S2.1.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a versioned adapter interface with an SDK and a conformance suite |
| So that | a second source can be added without changing the platform |
[/TABLE]

Acceptance criteria
- The SourceAdapter interface in §6.1 is published as a Python package with typed methods: discover, fetch_workbook, parse, execute_case, capture_visual, capabilities
- Adapters run out of process and speak to the platform over the adapter RPC; an adapter crash does not take down a worker
- The SDK includes fixtures, a fake source, and the conformance suite runner; astra-adapter conformance --adapter tableau runs the suite end to end
- The interface version is recorded on every harvest and every ParityRun

[TABLE]
| S2.1.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | the conformance suite to be the definition of 'an adapter works' |
| So that | adapter acceptance is a test result, not an opinion |
[/TABLE]

Acceptance criteria
- Suite covers: discovery completeness, parse round-trip (parse → serialise → parse identity), AST coverage on the golden corpus, execution determinism (same case twice, same result), visual capture, error taxonomy, throttling behaviour
- Suite output is a signed report stored in the artefact store and linked from Platform Health
- A failing conformance run blocks adapter promotion to a tenant

### F2.2  Tableau adapter: discovery and fetch

[TABLE]
| S2.2.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | the Tableau adapter to discover and fetch from Server and Cloud |
| So that | both deployment types in the BlackRock estate are covered by one adapter |
[/TABLE]

Acceptance criteria
- Discovery uses the Metadata API (GraphQL) for the object graph and the REST API for downloads, with personal-access-token and connected-app authentication
- Fetch retrieves .twb and .twbx (unpacking the XML from the archive) and records the revision id
- Rate limits and 429s are handled with backoff; a site-level concurrency cap is configurable (default 4)
- Tableau Server 2021.4+ and Tableau Cloud are supported; the version is recorded per site

[TABLE]
| S2.2.2 | P1   ·   R1 |
| As a | platform engineer |
| I want | published datasources and embedded extracts to be captured with their connection details |
| So that | the Modeller knows where data comes from and the executor can read it |
[/TABLE]

Acceptance criteria
- Published datasources are captured as Datasource nodes with published: true and their connection graph
- Embedded Hyper extracts are detected; the adapter records the extract schema and refresh schedule; extract data is not copied
- Connection credentials are never stored; the adapter references a Key Vault secret by name

### F2.3  Tableau adapter: parser and AST

[TABLE]
| S2.3.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | Tableau calculation language parsed into a typed AST |
| So that | the Transpiler works on structure, not text |
[/TABLE]

Acceptance criteria
- Grammar covers the Tableau function set in Appendix B of the spec, LOD expressions (FIXED / INCLUDE / EXCLUDE), table calculations with addressing and partitioning, parameters, type conversions, string, date, logical and aggregation functions
- AST nodes carry source spans so that a failing case can point to the exact text
- Golden corpus parse rate is 100%; a construct outside the grammar is captured verbatim and flagged, never dropped
- Grammar is versioned; parse results record the grammar version

[TABLE]
| S2.3.2 | P0   ·   R1 |
| As a | parity engineer |
| I want | sheets, filters, parameters, actions and dashboards parsed with their context |
| So that | the Proof Engine can derive cases that respect what the user actually sees |
[/TABLE]

Acceptance criteria
- Sheet-level filters (categorical, range, relative date, top-N, condition) are parsed to a typed structure with their values
- Dashboard layout, containers, sheet placements and actions (filter, highlight, URL, parameter, set) are captured
- Row-level security (user filters, ISMEMBEROF, USERNAME()) is detected and recorded on the Workbook node as rls: true with the expression

[TABLE]
| S2.3.3 | P1   ·   R1 |
| As a | platform engineer |
| I want | custom SQL captured verbatim and parsed where possible |
| So that | custom SQL becomes a Modeller input rather than a surprise |
[/TABLE]

Acceptance criteria
- Custom SQL is stored on the Table node; a dialect-aware parser (Snowflake, SQL Server, Postgres) extracts referenced tables and columns where it can
- Custom SQL that cannot be parsed is flagged as an unrecognised construct and counts against parse quality

### F2.4  Tableau adapter: execution and visual capture

[TABLE]
| S2.4.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | the adapter to execute a parity case on the source side and return a typed ResultSet |
| So that | the expected side of every proof comes from Tableau itself, not from a re-implementation |
[/TABLE]

Acceptance criteria
- execute_case supports three strategies: extract read (Hyper API against the workbook extract), view data (REST view data endpoint with filters and parameters applied), live replay (query the underlying connection with the sheet's generated query where obtainable)
- Strategy is chosen per case from the charter and capabilities; the strategy used is recorded on the ParityRun
- ResultSet has ordered typed columns (name, role, type) and rows; nulls are preserved as nulls
- Execution has a timeout (default 120 s); a timeout yields INCONCLUSIVE with the reason

[TABLE]
| S2.4.2 | P1   ·   R1 |
| As a | report owner |
| I want | a screenshot of the source view for each sheet |
| So that | I can compare what I see today with what I will see |
[/TABLE]

Acceptance criteria
- capture_visual returns a PNG per sheet and per dashboard at a configurable size using the REST image endpoint
- Images are stored in the artefact store and linked to the MU; they are never sent to a model endpoint

## E3. Cartographer: clustering, families and waves
Goal: model families and release trains are proposed from graph evidence, shown with their reasoning, and confirmed by people. Spec §8.5, §12.1, §14.2.

### F3.1  Family clustering

[TABLE]
| S3.1.1 | P0   ·   R1 |
| As a | model engineer |
| I want | workbooks clustered into candidate model families by shared lineage |
| So that | the ~150-model planning assumption becomes a measured number in Month 1 |
[/TABLE]

Acceptance criteria
- Similarity = 0.5·J(tables) + 0.3·J(fields) + 0.2·(shared_calc_shapes ÷ max_calc_shapes) computed for every workbook pair sharing at least one table; J is Jaccard
- Agglomerative clustering with the configurable threshold (default 0.35) produces ModelFamily nodes in state PROPOSED with members, candidate grain, candidate dimensions, and the evidence (shared tables, shared fields, shared calc shapes)
- Families with a single member are merged into the nearest family or held as SINGLETON with the reason
- The run on the BlackRock estate completes in under 30 minutes; family count, distribution and the histogram of members per family are written to the programme record

[TABLE]
| S3.1.2 | P0   ·   R1 |
| As a | model engineer |
| I want | to split, merge and move workbooks between families with a reason |
| So that | the proposal is a starting point I control |
[/TABLE]

Acceptance criteria
- Foundry Workbench supports split (select members → new family), merge (two families → one), move (member → other family); each action records who, when and why and re-computes the family's grain and dimensions
- Overrides are preserved across re-clustering runs; a re-run reports what it would change and does not change overridden families without confirmation

[TABLE]
| S3.1.3 | P1   ·   R1 |
| As a | programme manager |
| I want | the family count to be recorded as a calibration input at the end of Month 1 |
| So that | the planning assumption is replaced by a measured value with a date |
[/TABLE]

Acceptance criteria
- A 'Confirm family count' action writes the count, the date and the confirming user to the programme record and the Calibration Report
- Programme Board shows planned (150) against measured with the delta

### F3.2  Release trains and wave plan

[TABLE]
| S3.2.1 | P0   ·   R1 |
| As a | programme manager |
| I want | release trains proposed from families and usage |
| So that | the plan is grouped by what has to be designed together, not by site |
[/TABLE]

Acceptance criteria
- Trains are proposed by ordering families by (shared model readiness, usage, tier mix) and packing MUs to a configurable train size; the proposal explains each train in one paragraph generated from the graph (which families, why this order)
- The BlackRock default is five trains sized 277 / 328 / 184 / 177 / 101; sizes are editable
- Train membership, planned start and end, and gate schedule are stored as ReleaseTrain nodes; an MU is IN_TRAIN exactly one train at a time

[TABLE]
| S3.2.2 | P0   ·   R1 |
| As a | programme manager |
| I want | a Wave Board where I can drag MUs between trains within scheduler constraints |
| So that | re-planning is a board action, not a spreadsheet exercise |
[/TABLE]

Acceptance criteria
- Kanban of trains → states with MU cards; drag to re-sequence within a train or move between trains; a move that breaks a family dependency is refused with the reason
- WIP limit per train and per state is configurable and shown; exceeding it warns and requires a reason
- Every change is an event and appears on the Programme timeline

[TABLE]
| S3.2.3 | P1   ·   R1 |
| As a | programme manager |
| I want | projected versus planned dates per train |
| So that | I see slippage before it becomes a status-meeting surprise |
[/TABLE]

Acceptance criteria
- Projection uses measured throughput per state over the trailing 14 days and the MU counts remaining; shown as a date with a confidence band
- A train projected to miss its planned date by more than 5 working days is flagged on the Programme Board

## E4. Semantic Model Foundry and G2
Goal: approved model designs become TMDL in the dev workspace with conformance enforced at emission, and G2 approval runs in the console with client data owners. Spec §12, §13.1, §8.6.

### F4.1  Modeller agent and design proposal

[TABLE]
| S4.1.1 | P0   ·   R1 |
| As a | model engineer |
| I want | a model design proposal generated for each family from the graph |
| So that | I start from a draft that already knows the sources, grain and measures |
[/TABLE]

Acceptance criteria
- Proposal contains: tables with source mapping and storage mode (Import | DirectQuery | Direct Lake), relationships with cardinality, grain statement, conformed dimensions shared with other families, candidate measures with source calc refs and dedup decisions, RLS roles derived from Tableau user filters, refresh policy
- Proposal is produced in ASSISTED mode: structure is deterministic from the graph; naming and the grain statement may be drafted by the model with provenance recorded
- Proposal lists open questions for the data owner (each with the graph evidence that raised it)
- Generation of a proposal for a 40-workbook family takes under 5 minutes

[TABLE]
| S4.1.2 | P0   ·   R1 |
| As a | model engineer |
| I want | to edit the proposal in the Model Detail screen and submit it for G2 |
| So that | the design the client approves is the design we build |
[/TABLE]

Acceptance criteria
- Tabs: Design (tables, relationships diagram, grain, conformed dims), Measures (source calc → candidate measure with class and pattern), RLS, Open Questions, Build
- State machine PROPOSED → DRAFT → IN_REVIEW → APPROVED → BUILT → PUBLISHED enforced; transitions and their actors recorded
- Submitting to IN_REVIEW freezes a version hash; the G2 request references that hash

### F4.2  G2 workflow

[TABLE]
| S4.2.1 | P0   ·   R1 |
| As a | data owner |
| I want | to review a model design for my domain in plain language and approve it or ask a question |
| So that | I sign off what I understand |
[/TABLE]

Acceptance criteria
- Model Proposal (client view) renders: what the model is, what reports use it, what changes for the business user, open questions with owner and status, approve / request changes / ask a question
- Approval requires the data owner's role and domain scope; the Semantic Model Engineer countersigns; both are recorded on the GateDecision
- Request-changes returns the design to DRAFT with the comment attached; the cycle count is stored
- A question creates a thread visible to both sides; the design cannot be approved with an unanswered question

[TABLE]
| S4.2.2 | P1   ·   R1 |
| As a | programme manager |
| I want | G2 cycle time and open questions per family on the Programme Board |
| So that | I can chase the right person before the train slips |
[/TABLE]

Acceptance criteria
- Board tile shows families awaiting G2, days waiting, and the approver; SLA breach (default 5 working days) is highlighted
- Reminder notifications are sent at 3 and 5 days

### F4.3  Build: TMDL emission and conformance

[TABLE]
| S4.3.1 | P0   ·   R1 |
| As a | model engineer |
| I want | an approved design built as TMDL and deployed to the dev workspace automatically |
| So that | the model exists as code the moment it is approved |
[/TABLE]

Acceptance criteria
- Emission is deterministic from the approved design version; the same version always produces byte-identical TMDL
- TMDL is committed to the client's Git repository through the target adapter with a commit message referencing the family and G2 decision id
- Deployment to the dev workspace uses Fabric Git integration; a smoke query per table (row count, one measure) runs and the result is stored
- BUILT is entered only when deployment and smoke queries pass; failures show on the Build tab with the log

[TABLE]
| S4.3.2 | P0   ·   R1 |
| As a | architect |
| I want | conformance rules enforced at emission |
| So that | no model reaches the client repository that breaks the target architecture |
[/TABLE]

Acceptance criteria
- Rules from §12.3: star schema only (no many-to-many without a bridge), single active relationship path, conformed dimensions shared by reference, measures in display folders by source family, naming convention, RLS roles tested with a fixture user
- A rule failure blocks BUILT and lists the violation with the offending object
- Rules are data, editable by the architect in Admin, versioned, and recorded on the ModelFamily at build

[TABLE]
| S4.3.3 | P1   ·   R1 |
| As a | model engineer |
| I want | a second version of a published model to be produced without breaking released reports |
| So that | a Mender repair or a design change does not regress what is live |
[/TABLE]

Acceptance criteria
- Change request on a PUBLISHED family creates a DRAFT v(n+1); v(n) stays PUBLISHED until v(n+1) passes regression on all released MUs bound to it
- Promoting v(n+1) marks v(n) DEPRECATED with the date; the console shows both

## E5. Transpiler and Pattern Library
Goal: every calculated field is classified, converted through the cheapest path that can be proved, and everything a model drafts is proved before it counts. Spec §9, §8.7, §4.3.

### F5.1  Classification

[TABLE]
| S5.1.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | every calculated field classified C1–C4 from its AST before any generation |
| So that | the class mix is measured on day one and drives cost and routing |
[/TABLE]

Acceptance criteria
- Classifier is deterministic: C1 when a direct-map rule matches the whole AST; C2 when a structural rewrite rule matches; C3 when the AST is within grammar but no rule matches or context (LOD scope, table calc addressing, parameter-driven logic) is required; C4 when a construct has no Power BI equivalent per Appendix B
- Class, matched rule or pattern id, and reason are written to the CalculatedField node
- Estate-wide class mix is reported on the Programme Board against the calibration targets 45 / 30 / 18 / 7
- Re-classification runs when the rule set or pattern library changes and reports what moved class

### F5.2  Deterministic rules engine (C1, C2)

[TABLE]
| S5.2.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a rules engine that maps Tableau AST shapes to DAX and Power Query M templates |
| So that | most of the estate converts without a model call |
[/TABLE]

Acceptance criteria
- Rules are AST-pattern → target-template with guards; shipped set covers the function families in Appendix B and the common LOD and table-calc shapes
- Rule application is DETERMINISTIC mode: no model call; provenance records the rule id and version
- Each rule ships with at least three golden-corpus cases that must pass proof in CI
- Rule coverage report: percentage of estate calcs matched by rule, by rule family

[TABLE]
| S5.2.2 | P1   ·   R1 |
| As a | platform engineer |
| I want | to add or amend a rule through the Pattern Library with CI protection |
| So that | the rule set grows with the estate safely |
[/TABLE]

Acceptance criteria
- New rule is authored as code (pattern + template + guards + cases), reviewed as a pull request, and promoted to the tenant on merge
- Regression: every rule change re-runs the golden corpus and the PASSED artefacts that used the rule; any new failure blocks promotion

### F5.3  Generation under proof (C3)

[TABLE]
| S5.3.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | C3 calculations generated by a model with the context contract and validated up the ladder before proof |
| So that | a model output can never reach the client repository without passing execution |
[/TABLE]

Acceptance criteria
- GenerationRequest (§9.4) carries: calc AST, dependency closure, target table columns, matching patterns, charter excerpt, output schema; the model returns a typed candidate (DAX or M, plus a one-line rationale)
- Ladder: schema (typed JSON), parse (DAX parser), compile (deploy to dev model or EVALUATE syntax check), proof (parity cases), human (only on escalation)
- Mode recorded as GENERATED_PROVED; provenance carries gateway request id, provider, model, prompt hash, context hash, temperature 0, token counts
- Up to two regeneration attempts on parse or compile failure with the error fed back; then the field is routed to the Exception Desk with all attempts attached

[TABLE]
| S5.3.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | generation to be model-agnostic through the gateway |
| So that | the client's data-handling decision (Anthropic or Azure OpenAI in-tenant) does not change the Transpiler |
[/TABLE]

Acceptance criteria
- Transpiler calls gateway.generate(task_class='transpile_c3', ...) and never names a provider
- Routing is by task class and tenant policy; both configured providers pass the Transpiler eval set at ≥ 0.80 first-pass proof before being routable for transpile_c3
- Provider and model are recorded per call in provenance

[TABLE]
| S5.3.3 | P1   ·   R1 |
| As a | parity engineer |
| I want | candidate confidence declared and calibrated |
| So that | confidence means something |
[/TABLE]

Acceptance criteria
- Model declares a confidence in the output schema; the platform records it and, per §16.3, reports calibration (declared vs observed proof rate) in ten buckets
- Below a configurable calibration floor a task class is routed to the small-model-plus-proof path rather than trusted

### F5.4  Class 4 handling

[TABLE]
| S5.4.1 | P0   ·   R1 |
| As a | migration engineer |
| I want | C4 constructs flagged with the closest Power BI approach and routed to a redesign decision |
| So that | no one wastes a proof cycle on something that has no equivalent |
[/TABLE]

Acceptance criteria
- For each C4 the Transpiler writes the reason, the Appendix B guidance, and an ASSISTED-mode redesign suggestion (marked as such)
- The MU is BLOCKED until a Migration Engineer records the redesign decision (implement as suggested / alternative / drop with report-owner agreement)
- Decisions are visible to the report owner and referenced at G3

### F5.5  Pattern Library and promotion

[TABLE]
| S5.5.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a proved C3 transformation to become a candidate pattern automatically |
| So that | the platform gets faster and more deterministic as the programme runs |
[/TABLE]

Acceptance criteria
- When a GENERATED_PROVED artefact passes proof, its (source AST shape, target template, guards) tuple is generalised and stored as a Pattern in CANDIDATE state, keyed by AST shape hash
- Promotion CANDIDATE → ACTIVE requires N distinct proof passes (default 5), zero failures, and a Platform Engineer approval (MA-11, L2)
- ACTIVE patterns are applied deterministically ahead of any model call; the class of the field is re-evaluated to C2 with pattern_ref

[TABLE]
| S5.5.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | patterns retired automatically when they fail |
| So that | a bad pattern cannot keep applying itself |
[/TABLE]

Acceptance criteria
- A proof failure attributed to an ACTIVE pattern increments its failure count; above the threshold (default 2 in 100 applications) it is RETIRED automatically (MA-12, L4) and an event is raised
- Retiring a pattern re-queues the artefacts it produced that have not yet been ACCEPTED for regeneration

[TABLE]
| S5.5.3 | P1   ·   R1 |
| As a | platform engineer |
| I want | a Pattern Library screen |
| So that | I can see what the platform has learned and govern it |
[/TABLE]

Acceptance criteria
- Lists patterns by class and state with applications, pass/fail, first seen, provenance origin; candidates awaiting promotion are a queue
- Actions: promote, retire with reason, edit guards (creates a new version), export

## E6. Compositor
Goal: Tableau dashboards and sheets become Power BI report definitions bound to Foundry models, with visual mapping recorded and anything that needs a human redesign flagged rather than approximated. Spec §8.8, §7.1, Appendix B.

### F6.1  Visual mapping and PBIR emission

[TABLE]
| S6.1.1 | P0   ·   R1 |
| As a | migration engineer |
| I want | each Tableau sheet mapped to a Power BI visual type with encodings and filters translated |
| So that | the generated report is structurally the same report |
[/TABLE]

Acceptance criteria
- Mapping table from Appendix B (mark type × encodings → visual type) is data, versioned, and editable by the architect
- For each sheet the Compositor emits a Visual with type, field wells bound to model columns and measures (through MAPS_TO), sort, and visual-level filters; dashboard containers become report pages with layout preserved at the container level
- Sheets whose mark type has no mapping are emitted as a placeholder visual with redesign_flag: true and the reason
- PBIR output validates against the published PBIR JSON schema before commit

[TABLE]
| S6.1.2 | P0   ·   R1 |
| As a | migration engineer |
| I want | the report definition committed to Git and deployed to the dev workspace bound to the family model |
| So that | I can open the generated report in Fabric within minutes of generation |
[/TABLE]

Acceptance criteria
- Commit through the target adapter with the MU id in the message; deployment through Fabric Git integration to the dev workspace; report bound to the PUBLISHED or BUILT model for its family
- Deployment failure returns the MU to GENERATED with the error on the MU page; three retries with backoff

[TABLE]
| S6.1.3 | P1   ·   R1 |
| As a | report owner |
| I want | parameters, actions and interactivity carried across where Power BI supports them |
| So that | the report behaves the way users expect |
[/TABLE]

Acceptance criteria
- Tableau parameters become what-if parameters or slicers by type; filter actions become cross-filter settings; URL actions become URL fields; unsupported actions are listed on the MU page with the Appendix B guidance
- Interactivity mapping is recorded on the Visual node

### F6.2  Redesign flags and report documentation

[TABLE]
| S6.2.1 | P0   ·   R1 |
| As a | migration engineer |
| I want | every redesign flag to be a work item with its evidence |
| So that | flagged visuals get finished in Desktop and nothing is forgotten |
[/TABLE]

Acceptance criteria
- Redesign flags create ExceptionCases of class VISUAL_REDESIGN routed to the Exception Desk with the source screenshot, the mapping reason and the placeholder location
- An MU with open redesign flags cannot enter PROVING for the affected sheets; other sheets proceed
- Closing the flag records the engineer, the Desktop commit hash and the date

[TABLE]
| S6.2.2 | P1   ·   R1 |
| As a | report owner |
| I want | report documentation generated from the graph |
| So that | users get a page that says what changed and where things moved |
[/TABLE]

Acceptance criteria
- One markdown page per report: purpose (from workbook description), pages and visuals with their Tableau sheet of origin, measures with source calc names, parameters, known differences (C4 decisions, redesigns), model and refresh
- Generated in ASSISTED mode with provenance; stored as an artefact and linked from the MU page

## E7. Proof Engine and Tolerance Charter
Goal: for every migrated report, source and target execute the same cases at the same grain, results are compared under a versioned charter, and the verdict with its evidence is the acceptance record. The Arbiter agent runs the Proof Engine; it is deterministic and never calls a model. Spec §10, §4.4, §8.9.

### F7.1  Tolerance Charter

[TABLE]
| S7.1.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | the Tolerance Charter as a versioned document the platform enforces |
| So that | 'the same result' is defined once, agreed at G1, and applied identically to every report |
[/TABLE]

Acceptance criteria
- Charter schema per §4.4: numeric (abs and rel epsilon, rounding, currency scale), nulls, dates (grain alignment, timezone, fiscal year start), strings (trim, case, collation), ordering, rows (missing key policy, row-count tolerance), sampling, params (enumeration), waiver rules
- Editor in the console with inline explanation of each rule's effect; 'simulate' re-diffs the last run under the edited charter without executing
- Versions are immutable; G1 records the version; every ParityRun records the version it ran under
- Changing the charter after G1 requires the parity engineer and the client analytics lead and re-proves affected MUs

### F7.2  Case derivation

[TABLE]
| S7.2.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | parity cases derived deterministically from each sheet |
| So that | coverage is explicit and reproducible |
[/TABLE]

Acceptance criteria
- Cases = sheet × (parameter combinations from charter enumeration strategy) × (filter contexts: default, and each categorical filter's top-N values); each case has grain, measures, filters, parameter values and a stable id
- Charter bounds cap enumeration; combinations above the bound are recorded NOT_ENUMERATED on the suite
- Case count and coverage are shown on the MU page; a ParityCase is a graph node with a §10 schema

[TABLE]
| S7.2.2 | P1   ·   R1 |
| As a | parity engineer |
| I want | to add a manual case with specific filters and parameters |
| So that | an owner's 'check this one' becomes part of the suite |
[/TABLE]

Acceptance criteria
- Manual cases are authored on the Parity Run screen, tagged MANUAL with the author, and persist across re-runs

### F7.3  Dual execution

[TABLE]
| S7.3.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | each case executed on the source via the adapter and on the target via XMLA and the results stored |
| So that | the comparison is between two executions, never between an execution and a re-implementation |
[/TABLE]

Acceptance criteria
- Target side: DAX EVALUATE over XMLA against the dev or test model, with filters and parameter values applied per §10.2; query text stored
- Source side: adapter execute_case with the chosen strategy; strategy stored
- Both ResultSets stored as Parquet in the artefact store with content hash; retention per charter
- Execution is parallel per MU with a configurable concurrency per Fabric workspace (default 8) and per Tableau site (default 4)

[TABLE]
| S7.3.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | INCONCLUSIVE to be a first-class outcome distinct from FAIL |
| So that | infrastructure problems do not look like migration defects |
[/TABLE]

Acceptance criteria
- Timeout, adapter error, executor error and sampling shortfall produce INCONCLUSIVE with the reason class; the orchestrator retries once with a longer budget
- Inconclusive rate is a Platform Health metric with an alert threshold (default 2%)

### F7.4  Normalisation, diff and verdict

[TABLE]
| S7.4.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | the diff algorithm from §10.3 implemented exactly and tested against a fixture set |
| So that | every verdict is explainable in terms of the charter |
[/TABLE]

Acceptance criteria
- Normalisation: column mapping via MAPS_TO, type coercion to the lattice, date truncation, string folding, null canonicalisation; key by grain tuple
- Key-set comparison, cell comparison with numeric epsilon per charter, row count and totals check; verdict PASS / FAIL / INCONCLUSIVE with failing cells (first N, default 50) and the delta
- Fixture set of 200 hand-verified pairs covering each charter rule; CI runs them on every change
- Evidence bundle per run: charter version, both queries, both result hashes, key differences, failing cells, timings

[TABLE]
| S7.4.2 | P0   ·   R1 |
| As a | report owner |
| I want | a Parity Dashboard and per-run view in plain language |
| So that | I can see whether my report is right without reading a diff |
[/TABLE]

Acceptance criteria
- Per sheet: cases run, pass, fail, inconclusive, first-pass rate, waived count; failing cells shown as a table with expected / candidate / delta and the filter context
- Per MU: pass rate trend across runs and Mender passes
- A single 'this report passes the charter' statement with the charter version when all cases pass

### F7.5  Sampling and scale

[TABLE]
| S7.5.1 | P1   ·   R1 |
| As a | parity engineer |
| I want | large result sets compared by stratified sample with the totals always compared in full |
| So that | proof completes on the biggest workbooks without losing the numbers that matter |
[/TABLE]

Acceptance criteria
- Full compare up to full_compare_max_rows; above that, stratified by grain with the top-N rows by each measure's absolute value always included; sample size and seed recorded
- A sampled PASS is labelled SAMPLED on the verdict and on the G3 card

### F7.6  Visual parity (advisory)

[TABLE]
| S7.6.1 | P2   ·   R1 |
| As a | report owner |
| I want | a structural visual-similarity score and side-by-side images |
| So that | I can spot a report that is numerically right and visually wrong |
[/TABLE]

Acceptance criteria
- Structural score from mark type, encodings, axes, sort, reference lines (0–1); image similarity from source screenshot and Power BI export API render
- Score is shown on the Parity Dashboard and the G3 card; it never gates; the human visual review at G3 is the gate

### F7.7  Regression

[TABLE]
| S7.7.1 | P0   ·   R1 |
| As a | programme manager |
| I want | parity suites re-run on a schedule after acceptance and on demand |
| So that | a source change during parallel run is caught before the report owner notices |
[/TABLE]

Acceptance criteria
- Steward schedules re-runs (default: after model publish, weekly during parallel run, on SOURCE_DRIFT); a regression FAIL creates an ExceptionCase tagged REGRESSION and notifies the report owner
- Regression Monitor screen lists released MUs with last result, schedule and drift alerts
- At handover suites and a runner are exported so the client can keep running them

## E8. Mender and Exception Desk
Goal: parity failures are classified and repaired by a bounded agent loop; what it cannot fix arrives with its evidence in an engineer's queue. Spec §11, §8.10.

### F8.1  Failure classification

[TABLE]
| S8.1.1 | P0   ·   R1 |
| As a | migration engineer |
| I want | every failing case classified into the §11.1 taxonomy from its evidence bundle |
| So that | the fix path is chosen from evidence, not from guessing |
[/TABLE]

Acceptance criteria
- Classes: FILTER_CONTEXT, NULL_HANDLING, DATE_GRAIN, AGGREGATION, TYPE_COERCION, LOD_SCOPE, TABLE_CALC, SORT_LIMIT, KEY_MISSING, SOURCE_DRIFT, UNKNOWN; classification is deterministic from the diff signals
- Classification precision on the labelled fixture set ≥ 0.90; the class and the signals that produced it are recorded on the ExceptionCase or repair record
- Cases are grouped by artefact so one measure used by many sheets is repaired once

### F8.2  Bounded repair loop

[TABLE]
| S8.2.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | the Mender to repair failures in at most three passes with a pattern-first strategy |
| So that | the loop cannot spin, cannot silently accept and every pass is in evidence |
[/TABLE]

Acceptance criteria
- Pass 1: if an ACTIVE pattern matches the failure class and AST shape, apply it deterministically; pass 2: model repair with the failing evidence and the class-specific instructions; pass 3: same with a widened context contract
- Every repair is re-validated up the ladder and re-proved on the affected cases only
- Bound (default 3) is configurable per tenant; exhaustion routes to the Exception Desk with all passes attached; the MU never transitions to PASSED from within the loop without a proof PASS
- Passes consumed per MU is stored and reported (mean passes to pass)

[TABLE]
| S8.2.2 | P0   ·   R1 |
| As a | model engineer |
| I want | a failure diagnosed as a model defect to be routed to the Foundry, not patched in the report |
| So that | the fix lands where the cause is |
[/TABLE]

Acceptance criteria
- KEY_MISSING with graph evidence of a missing dimension member, or AGGREGATION with a grain mismatch at the model, opens a Foundry change request and sets the MU BLOCKED on the family
- The Mender never edits TMDL directly in R1

[TABLE]
| S8.2.3 | P1   ·   R1 |
| As a | platform engineer |
| I want | successful repairs to feed the pattern pipeline |
| So that | a repair made once becomes a rule |
[/TABLE]

Acceptance criteria
- A model repair that passes proof is generalised into a CANDIDATE pattern keyed by (failure class, AST shape) and follows F5.5 promotion

### F8.3  Exception Desk

[TABLE]
| S8.3.1 | P0   ·   R1 |
| As a | migration engineer |
| I want | a queue of ExceptionCases ordered by train sequence, with the evidence bundle in the case |
| So that | I never open Tableau to work out what an exception is |
[/TABLE]

Acceptance criteria
- Queue columns: MU, failure class, passes consumed, train, age, assignee; filters by train, class, site, assignee; bulk assign
- Case page: evidence (failing cells, key diffs, filter context, parameter values), artefact (current DAX / M with source calc alongside, Mender pass history with diffs), decision
- Decisions: patch (edit in place, validate, re-prove), redesign (route to Foundry or open in Desktop with the MU link), model defect (Foundry change request), source defect (record, notify owner, choose reproduce or fix with owner sign-off — G3 matter)
- Every decision is a GateDecision-class record with rationale of at least one sentence and is visible to the report owner

[TABLE]
| S8.3.2 | P1   ·   R1 |
| As a | programme manager |
| I want | exception ageing and close rate on the Programme Board |
| So that | residue does not accumulate unseen |
[/TABLE]

Acceptance criteria
- Tile shows open exceptions by class and age band; Mender close rate (failures closed without an ExceptionCase ÷ failures) with the R1 target ≥ 0.70

## E9. Release and Decommission
Goal: accepted reports are promoted through the client's pipeline, run in parallel for the agreed window, are monitored for regression, and the source is decommissioned with a licence-release record. Spec §14.4, §8.11, §13.1 (G3, G4).

### F9.1  G3 Parity acceptance

[TABLE]
| S9.1.1 | P0   ·   R1 |
| As a | report owner |
| I want | a gate card that tells me in 30 seconds what I am approving |
| So that | acceptance is informed and quick |
[/TABLE]

Acceptance criteria
- Card anatomy per §15.5: what (report, pages, visuals), proof (cases, charter version, sampled flag, waivers), visual (structural score, human review status), changes (C4 decisions, redesigns), next (promotion, parallel window), buttons Approve / Request changes / Ask a question / Open report
- Approve requires the report owner role for that report; countersigned by the Migration Engineer; both recorded
- A PASSED (waiver) MU shows the waivers and their justification on the card; approving records that the owner saw them
- Card renders identically on desktop, mobile and as a Teams adaptive card

[TABLE]
| S9.1.2 | P0   ·   R1 |
| As a | programme manager |
| I want | G3 acceptance to trigger the invoicing event under the fixed-price contract |
| So that | commercial recognition is a platform event, not a spreadsheet |
[/TABLE]

Acceptance criteria
- mu.accepted event with MU, tier and unit price is emitted and exported to the programme's commercial ledger; the Programme Board shows accepted units by tier against plan

### F9.2  Promotion and parallel run

[TABLE]
| S9.2.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | the Steward to promote ACCEPTED MUs per train through the Fabric deployment pipeline |
| So that | release is a pipeline stage with evidence, not a manual copy |
[/TABLE]

Acceptance criteria
- Promotion dev → test → prod via Fabric deployment pipelines with the client's approval rules; MA-08 (L3) to test, MA-09 (L2, explicit PM approval) to production
- Release Board shows per train: MUs by pipeline stage, blockers, and the release evidence bundle
- Parallel-run window (default 4 weeks) starts at production deployment and is visible per MU and per site

[TABLE]
| S9.2.2 | P1   ·   R1 |
| As a | report owner |
| I want | adoption of the released report tracked against the source during parallel run |
| So that | we know users have moved before the source is switched off |
[/TABLE]

Acceptance criteria
- Views on the Power BI report (Fabric activity) and the Tableau view (Metadata API) are both captured weekly; the ratio is shown on the Decommission Tracker
- Configurable adoption threshold contributes to G4 readiness

### F9.3  G4 Decommission

[TABLE]
| S9.3.1 | P0   ·   R1 |
| As a | licence admin |
| I want | a Decommission Tracker per site with a readiness checklist and a G4 card when ready |
| So that | I switch off a site once, safely, with a record |
[/TABLE]

Acceptance criteria
- Readiness = all in-scope MUs RELEASED + parallel window elapsed + regression green + adoption threshold met + owner confirmations received; each item shows its state and evidence
- G4 card lists the MUs, the licence tier and count released, the source workbooks to be archived, and the confirmation text; approver is the licence admin, countersigned by the Programme Manager
- On approval the Steward archives the source workbooks (adapter capability), records the licence-release date and value on the Site node, and emits site.decommissioned
- Deferral records the reason and a new target date

## E10. Migration Console
Goal: the seven surfaces, the Migration Unit page and the gate card, built as event-sourced views over the graph and evidence chain; role-based; calm on the client side. Spec §15.

### F10.1  Console shell, roles and navigation

[TABLE]
| S10.1.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a React / TypeScript console shell with Entra ID sign-in, role-based navigation and tenant branding |
| So that | every role lands on the screen its day starts on |
[/TABLE]

Acceptance criteria
- Roles from §15.1 with landing pages: PM → Programme Board; model engineer → Foundry Workbench; migration engineer → Exception Desk; parity engineer → Parity Dashboard; platform engineer → Platform Health; data owner → Gate Inbox (G2); report owner → Gate Inbox (G3); licence admin → Decommission Tracker; InfoSec → Data Handling
- Client roles see only the client surfaces and their domain scope; Artizent roles see everything; navigation is generated from role
- Prod / test / dev environment is visibly distinct in the chrome
- Every screen is reachable by URL; deep links to an MU, a case, a gate card and a run are stable

[TABLE]
| S10.1.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | screens to be event-sourced projections with a query API |
| So that | the console never shows a state the evidence chain does not have |
[/TABLE]

Acceptance criteria
- Projections are rebuilt from the event stream; a rebuild from empty is a supported operation with a progress indicator
- Live updates over server-sent events for queues and boards; p95 screen update within 2 seconds of the event
- Every number on a screen has an 'explain' affordance that opens the query or the events behind it

### F10.2  Programme surface

[TABLE]
| S10.2.1 | P0   ·   R1 |
| As a | programme manager |
| I want | the Programme Board, Wave Board, Calibration Report and Status Pack |
| So that | the programme's state is one screen and the status pack writes itself |
[/TABLE]

Acceptance criteria
- Programme Board per §15.3.1: KPI strip (MUs by state, first-pass parity, absorption vs calibrated baseline, gates due this week, spend vs budget), train swimlanes with planned vs projected, blocked reasons, exceptions ageing, milestones
- Status Pack: generated weekly as an editable narrative with the numbers and charts of the Board, exportable to PDF and PPTX; edits are stored with the version
- Calibration Report screen per F13.2

### F10.3  Migration Unit page

[TABLE]
| S10.3.1 | P0   ·   R1 |
| As a | migration engineer |
| I want | one page per report with everything about it |
| So that | there is a single URL to send to anyone about any report |
[/TABLE]

Acceptance criteria
- Header: state, tier, family, train, owners, gate status strip; sections per §15.4: Source, Artefacts, Parity, Exceptions, Gates, Provenance, Timeline
- Client roles see Source, Artefacts (thumbnails and documentation only), Parity (summary and verdict), Gates and Timeline
- Page loads in under 500 ms p95; artefact previews lazy-load

### F10.4  Foundry, Delivery, Proof, Governance and Admin surfaces
Surface screens are specified in §15.3.3–15.3.7 and are delivered through the epics that own them: Foundry Workbench, Model Detail and Model Proposal (E4); Exception Desk, Exception Case, Release Board, Decommission Tracker (E8, E9); Parity Dashboard, Parity Run, Tolerance Charter, Regression Monitor (E7); Gate Inbox, Decision Register, Evidence Export (E11, E13); Platform Health, Pattern Library, Model Gateway & TokenOps, Data Handling, Tenant & Access (E12, E11). The stories below cover what is shared.

[TABLE]
| S10.4.1 | P0   ·   R1 |
| As a | data owner |
| I want | a Gate Inbox that shows only the requests waiting for me |
| So that | I do my part in minutes and get out |
[/TABLE]

Acceptance criteria
- Card stack of open gate requests for my role and domain, ordered by due date; each card is the §15.5 anatomy; filters by gate type and site
- Approve / request changes / ask a question in place; an approval that needs a countersign shows who is next
- Email and Teams notification on new request and at SLA thresholds, with a deep link

[TABLE]
| S10.4.2 | P0   ·   R1 |
| As a | auditor |
| I want | a Decision Register of every gate decision and adjudication |
| So that | I can answer 'who approved this and on what evidence' without asking anyone |
[/TABLE]

Acceptance criteria
- Register lists GateDecisions and adjudications with approver, countersigner, evidence references, rationale, timestamps; search and filter; export to CSV and signed PDF
- Each row opens the evidence bundle

### F10.5  Cross-cutting UX

[TABLE]
| S10.5.1 | P1   ·   R1 |
| As a | InfoSec reviewer |
| I want | accessibility, responsiveness and localisation baselines |
| So that | the console meets the client's standards for internal tools |
[/TABLE]

Acceptance criteria
- WCAG 2.2 AA on gate cards, Exception Desk and evidence views (automated axe checks in CI, manual screen-reader test on the three screens)
- Gate Inbox, gate cards and MU page usable on mobile; Programme Board and Parity Dashboard tablet-first
- en-GB and en-US strings externalised; all dates shown in the viewer's timezone with the charter timezone noted where relevant

[TABLE]
| S10.5.2 | P1   ·   R1 |
| As a | report owner |
| I want | notifications I can tune |
| So that | I hear about my reports, not everyone's |
[/TABLE]

Acceptance criteria
- Per-user preferences: channels (email, Teams), events (gate request, exception assigned, regression fail, train re-plan), digest mode (immediate, daily)
- Notification content never includes data values; it links to the console

## E11. Security, identity, evidence and inference boundary
Goal: the platform runs inside the client's tenant, every agent has a non-human identity, generated code executes in a sandbox, every event is in a tamper-evident chain, and the client can see and control what reaches a model endpoint. Spec §18, §4.5, §4.2.

### F11.1  Tenant deployment and identity

[TABLE]
| S11.1.1 | P0   ·   R1 |
| As a | InfoSec reviewer |
| I want | the platform deployed into our Azure subscription with our identity provider |
| So that | nothing about our estate leaves our control |
[/TABLE]

Acceptance criteria
- Helm chart and Terraform module deploy AKS services and workers, Azure Database for PostgreSQL, Blob, Event Hubs, Key Vault, OpenSearch, Temporal; all endpoints private; egress limited to an allow-list
- Users sign in with Entra ID; roles are mapped from Entra groups; service principals for Fabric and Tableau live in Key Vault
- A deployment produces a signed bill of materials (images, versions, chart values) stored as an evidence record

[TABLE]
| S11.1.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | each agent to run under its own non-human identity with least privilege |
| So that | an agent's actions are attributable and its reach is bounded |
[/TABLE]

Acceptance criteria
- Agents receive SPIFFE identities (SVIDs) at start; every adapter call, graph write, gateway call and artefact commit carries the agent's identity
- Permissions per agent are declared in the AgentRecord (§8.1) and enforced at the graph API, artefact store and gateway; an out-of-scope call is refused and logged
- Identity issuance, rotation and revocation are visible in Tenant & Access

### F11.2  Execution safety

[TABLE]
| S11.2.1 | P0   ·   R1 |
| As a | InfoSec reviewer |
| I want | generated DAX, M and adapter queries to execute only in sandboxed, read-only contexts |
| So that | a generated artefact cannot change data or reach anything it should not |
[/TABLE]

Acceptance criteria
- Target execution uses XMLA read-only against dev/test workspaces with a service principal that has no write on data; production execution is limited to the regression runner with the same read-only principal
- Source execution uses the adapter's read-only credential; custom SQL replay is disabled unless the tenant policy enables it and then runs with a statement allow-list (SELECT only)
- Executor workers run with no outbound network except the two data endpoints; resource limits per query

### F11.3  Evidence chain

[TABLE]
| S11.3.1 | P0   ·   R1 |
| As a | auditor |
| I want | an append-only, hash-linked record of every state transition, gate decision, agent run, model call and verdict |
| So that | the migration can be examined years later |
[/TABLE]

Acceptance criteria
- Every CloudEvent is appended with prev_hash and hash; daily roots are computed and can be anchored externally (client-chosen: their own ledger, a timestamping service) as an option
- Verification tool recomputes the chain and reports the first break; runs nightly and on demand
- Retention configurable per tenant (default: programme lifetime + 7 years) with export before deletion

[TABLE]
| S11.3.2 | P0   ·   R1 |
| As a | InfoSec reviewer |
| I want | an Evidence Export that produces a signed bundle for a site or a programme |
| So that | we hold the evidence, not the vendor |
[/TABLE]

Acceptance criteria
- Export selects scope (programme, site, train, MU, date range) and produces a bundle: events, decisions, provenance records, verdicts, artefact hashes, charter versions, chain roots, and a verification tool
- Bundle is signed; the console shows the signature and a verification instruction
- Export of the BlackRock-scale programme completes in under 30 minutes

### F11.4  Inference boundary and data handling

[TABLE]
| S11.4.1 | P0   ·   R1 |
| As a | InfoSec reviewer |
| I want | a Data Handling screen that states exactly what reaches a model endpoint and lets me confirm it |
| So that | the inference boundary is a signed position, not an assurance |
[/TABLE]

Acceptance criteria
- Screen shows: provider(s) and endpoint location, the inference boundary table from §18.3 (what is sent: metadata, calc expressions, schema, error messages, patterns; what is never sent: row-level data, result sets, screenshots, credentials), retention terms, and the gateway's redaction rules
- 'Sign boundary' records the reviewer, the version of the position and the date; changing a provider or a redaction rule invalidates the signature and requires re-sign
- Boundary test: a CI and on-demand check sends sentinel row data through every agent path and asserts it never appears in a gateway request log

[TABLE]
| S11.4.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | the gateway to enforce the boundary, not rely on agents to respect it |
| So that | a prompt bug cannot leak data |
[/TABLE]

Acceptance criteria
- Gateway validates every request against the task class's allowed field schema; a request with an unexpected field or a field over size is refused
- Redaction pass removes literals that match data-like patterns (account numbers, emails, long numeric literals) from free-text fields, logging the redaction count
- All gateway requests and responses are logged with hashes; content logging is off by default and requires the InfoSec role to enable for a bounded window

[TABLE]
| S11.4.3 | P0   ·   R1 |
| As a | platform engineer |
| I want | prompt-injection defence on source content |
| So that | a hostile string in a workbook cannot steer an agent |
[/TABLE]

Acceptance criteria
- Source content enters prompts only inside typed fields, never as instructions; the assembler escapes and delimits it
- Gateway runs an injection classifier on typed content; hits are logged and the field is replaced with a placeholder plus an ExceptionCase for a human
- Model output is validated against the schema before any use; an output containing instructions or references outside the schema is rejected
- Red-team set of 200 injection cases runs in CI against the Transpiler and Mender paths; zero successful steering is the bar

## E12. Platform: orchestration, TokenOps, observability, APIs
Goal: the engine everything runs on — Temporal workflows per MU, the event bus, the model gateway with budgets and routing, metrics, the public API and SDKs. Spec §5, §14.1, §17.3, §20, §22.

### F12.1  Orchestration

[TABLE]
| S12.1.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | each MU to be a Temporal workflow that encodes the §3.2 state machine |
| So that | long-running, retried, resumable migration work with the state always known |
[/TABLE]

Acceptance criteria
- Workflow activities: agent runs, adapter calls, gate waits; timeouts yield INCONCLUSIVE not FAIL; compensation reverts artefact commits on repair failure
- Gate waits are durable signals; a G2 that takes three weeks holds without resources
- Every activity start and finish is a bus event and an evidence record
- Workflow versioning: a code deployment does not break in-flight MUs

[TABLE]
| S12.1.2 | P0   ·   R1 |
| As a | programme manager |
| I want | the wave scheduler to drive MUs by train with limits I set |
| So that | throughput is controlled, not accidental |
[/TABLE]

Acceptance criteria
- Scheduler admits MUs by train sequence subject to: family state (BUILT or later), executor concurrency per source site and per Fabric workspace, model-gateway budget, WIP per train
- Pause / resume per train and per site; a paused train holds MUs at their current state
- Scheduler decisions are visible on the Wave Board (why an MU is waiting)

### F12.2  Model gateway and TokenOps

[TABLE]
| S12.2.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a single model gateway with provider routing by task class and tenant policy |
| So that | provider choice is configuration and every call is accountable |
[/TABLE]

Acceptance criteria
- Providers: Anthropic (egress-controlled) and Azure OpenAI (in-tenant) in R1; provider adapters behind one interface with structured output, temperature and token caps
- Routing table: task class → ordered providers with fallbacks; a provider must pass the task class eval set before it is routable
- Every call records task class, provider, model, prompt hash, context hash, tokens in/out, latency, cost; returns a gateway request id used in provenance
- Prompt templates are versioned in Git; the version is part of the prompt hash

[TABLE]
| S12.2.2 | P0   ·   R1 |
| As a | programme manager |
| I want | token and cost budgets per programme, per train and per MU with alerts |
| So that | AI spend is planned and bounded |
[/TABLE]

Acceptance criteria
- Budgets configurable at three levels; consumption shown in real time on Model Gateway & TokenOps; soft alert at 80%, hard stop at 100% per MU (MU goes ESCALATED with reason BUDGET)
- Cost per accepted report by tier is computed and shown against the calibrated figure
- Weekly TokenOps summary is part of the Status Pack

[TABLE]
| S12.2.3 | P1   ·   R1 |
| As a | platform engineer |
| I want | caching and batching to reduce repeated inference |
| So that | identical work is not paid for twice |
[/TABLE]

Acceptance criteria
- Exact-match cache on (prompt hash, context hash) with tenant-scoped storage; a cache hit is recorded in provenance as such
- Batch endpoint use for non-interactive C3 generation where the provider supports it

### F12.3  Observability

[TABLE]
| S12.3.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | metrics, traces and logs for every component with the product's own metrics exposed |
| So that | the platform is operable and the accuracy and acceleration numbers are computed from telemetry, not entered |
[/TABLE]

Acceptance criteria
- OpenTelemetry across services and agents; traces link an MU workflow to its agent runs and gateway calls
- Product metrics from §16.6 and §17.2 (first-pass parity, passes to pass, Mender close rate, absorption, cycle time by state, inconclusive rate, cost per report) are computed from events and exported to the metrics store; the console reads the same series
- Alerts: adapter error rate, inconclusive rate, gateway error rate, budget thresholds, chain verification failure, scheduler starvation

[TABLE]
| S12.3.2 | P0   ·   R1 |
| As a | platform engineer |
| I want | a Platform Health screen |
| So that | one place to see whether the platform is well |
[/TABLE]

Acceptance criteria
- Adapter status per site and workspace, queue depths by state, executor latencies, gateway error rates, pattern promotions pending, scheduler pauses, last chain verification
- Actions: pause / resume adapter, drain queue, open workflow in Temporal UI

### F12.4  APIs, SDKs and extensibility

[TABLE]
| S12.4.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a documented public API for everything the console can do |
| So that | the console has no privileged path and integrations are possible |
[/TABLE]

Acceptance criteria
- GraphQL for reads (graph, projections), REST for commands (harvest, plan, run, decide, export) per §20; OpenAPI and GraphQL schema published; versioned
- Authentication via Entra tokens; the same role checks as the console
- Webhooks for the Appendix C event catalogue with signed payloads

[TABLE]
| S12.4.2 | P1   ·   R1 |
| As a | platform engineer |
| I want | Python and TypeScript SDKs for adapters, agents and event consumers |
| So that | the next adapter and the next integration are days, not weeks |
[/TABLE]

Acceptance criteria
- SDKs wrap the API with typed models generated from the schemas; the adapter SDK from F2.1 is part of the Python SDK
- Reference event consumer (ServiceNow ticket on ExceptionCase) ships as an example

[TABLE]
| S12.4.3 | P2   ·   R1.1 |
| As a | platform engineer |
| I want | MCP tool exposure for the graph and proof engine |
| So that | the platform's capabilities are usable by other governed agents |
[/TABLE]

Acceptance criteria
- Read-only graph queries, parity run status and evidence retrieval exposed as MCP tools with the same identity and role checks

## E13. Calibration Wave and accuracy metrics
Goal: the first-class programme object that turns planning assumptions into measured values, and the accuracy metrics the product publishes about itself. Spec §14.3, §16, §17.

### F13.1  Calibration Wave

[TABLE]
| S13.1.1 | P0   ·   R1 |
| As a | programme manager |
| I want | to define a calibration set and run the full pipeline on it with all gates active |
| So that | the fixed-price assumptions are tested on real workbooks in Month 2 |
[/TABLE]

Acceptance criteria
- Calibration set selection: default 40 workbooks stratified across the four tiers and at least two sites, weighted toward the highest-usage; the selection and its stratification are stored
- The wave runs harvest → cluster → G2 → generate → prove → mend → G3 on the set with normal gates; it is a ReleaseTrain flagged calibration: true
- Wave progress is shown on the Programme Board with the calibration-specific measures

[TABLE]
| S13.1.2 | P0   ·   R1 |
| As a | programme manager |
| I want | a Calibration Report generated at wave close |
| So that | the plan is re-baselined on evidence and signed |
[/TABLE]

Acceptance criteria
- Report contents: class mix per tier vs assumed 45/30/18/7, rule and pattern coverage, first-pass parity per tier, mean Mender passes, C4 rate with reasons, family count and reports per family vs the 150 / 7 assumption, adapter parse quality, executor strategy mix, cost per report by tier, elapsed time per stage, and the difference against the pre-calibration assumptions
- 'Sign report' by the Programme Manager and the client analytics lead writes the calibrated baseline that the Programme Board thereafter measures against
- Report exports to PDF for the steering committee

### F13.2  Accuracy metrics and calibration of confidence

[TABLE]
| S13.2.1 | P0   ·   R1 |
| As a | parity engineer |
| I want | the §16.6 accuracy metrics computed continuously and shown where the spec says |
| So that | accuracy is a number the client can see move, not a claim |
[/TABLE]

Acceptance criteria
- First-pass parity rate, mean passes to pass, Mender close rate, Class 3 proof rate, pattern retirement rate, waiver rate, regression escape rate, calibration error — each with the R1 target, computed from events, shown on the surfaces named in §16.6
- Each metric has a definition page reachable from the tile (formula, events used, window)

[TABLE]
| S13.2.2 | P1   ·   R1 |
| As a | platform engineer |
| I want | declared confidence calibrated per agent and task class |
| So that | routing decisions are based on measured reliability |
[/TABLE]

Acceptance criteria
- Calibration curve (declared vs observed proof pass) in ten buckets per agent per class; calibration error reported; a class whose error exceeds 0.2 is routed to the small-model-plus-proof path automatically and flagged on the Pattern Library

### F13.3  Golden corpus and evaluation sets

[TABLE]
| S13.3.1 | P0   ·   R1 |
| As a | platform engineer |
| I want | a golden corpus of Tableau workbooks with known-correct Power BI equivalents that runs in CI |
| So that | no change to a rule, pattern, prompt or adapter ships with a regression |
[/TABLE]

Acceptance criteria
- Corpus ships with the platform: at least 120 workbooks covering every C1 rule, every C2 rewrite, a set of C3 idioms and the C4 list, with hand-verified targets and expected parity results
- CI runs the corpus on every change to adapter grammar, rules, patterns, prompt templates, gateway routing; any regression blocks merge
- Client engagements may contribute anonymised structures (AST shapes, no data, no names) with written agreement; contribution is a governed action recorded in evidence

[TABLE]
| S13.3.2 | P1   ·   R1 |
| As a | platform engineer |
| I want | per-task-class eval sets that gate provider routing |
| So that | a new model or provider is admitted by test, not by reputation |
[/TABLE]

Acceptance criteria
- Eval sets for transpile_c3, mend_repair, modeller_proposal, compositor_doc; pass thresholds per class; results stored and shown on Model Gateway & TokenOps
- Routing a provider for a class without a passing eval is refused by configuration validation

### F13.4  Absorption and acceleration instrumentation

[TABLE]
| S13.4.1 | P0   ·   R1 |
| As a | programme manager |
| I want | absorption and cycle time computed from events per lifecycle stage and per tier |
| So that | the acceleration we sold is measured the way we priced it |
[/TABLE]

Acceptance criteria
- Absorption per §17.1: human time removed = (baseline stage hours from the calibrated model) − (measured human touch time from console sessions and decisions), per stage and tier; shown on the Programme Board against the calibrated baseline
- Cycle time per state transition per tier with p50/p90; shown on the Programme Board and in the Status Pack
- Human touch time is measured from console activity (session on an MU page, case, gate card) with an idle cutoff; it is a programme metric, not an individual performance metric, and is not shown per user

# 5. Non-functional stories
These apply across epics and are accepted at the R1 release, not per feature. Targets are from §22 of the specification.

[TABLE]
| ID | Area | Requirement | Verified by |
| N1 | Scale | 1,500 workbooks, 30,000 calculated fields, 250 model families, 200,000 parity cases per programme without degradation of the §22 latencies | Load test on synthetic estate in CI weekly |
| N2 | Throughput | 100 MUs in PROVING concurrently; 2,000 parity cases per hour per Fabric workspace at the default concurrency | Load test |
| N3 | Latency | Console p95: list screens < 2 s, MU page < 500 ms, gate card < 300 ms; graph neighbourhood query < 300 ms | Synthetic monitoring |
| N4 | Availability | 99.5% for the console and API during the programme; workflows resume after any component restart without loss | Chaos test: kill each service during a proving run; verify no lost state |
| N5 | Determinism | Same inputs, same rule/pattern/prompt versions → identical artefacts and identical verdicts | CI replay of the golden corpus twice, diff |
| N6 | Reproducibility | Any provenance record can be re-materialised: context hash matches; prompt template version resolvable | Nightly sample of 100 records |
| N7 | Security | No row-level data leaves the tenant; no data reaches a model endpoint; sandboxed execution; NHI per agent; secrets only in Key Vault | Boundary test, penetration test before GA |
| N8 | Evidence | Chain verifies; export completes in < 30 min at BlackRock scale; export verifies offline | Nightly and pre-release verification |
| N9 | Cost | Model cost per accepted report within 1.5× the calibrated figure per tier; budget hard stops work | TokenOps report |
| N10 | Accessibility | WCAG 2.2 AA on gate cards, Exception Desk, evidence views | axe in CI; manual audit |
| N11 | Operability | Single Helm/Terraform deployment; upgrade with in-flight MUs; runbooks for each alert | Upgrade rehearsal on the test tenant |
| N12 | Data retention | Result sets and screenshots per charter; evidence per tenant policy; deletion is logged | Retention job audit |
[/TABLE]


# 6. Release sequencing

## 6.1 Increments to R1
R1 is built in six increments of roughly six weeks. Each increment ends with something that runs end to end on the golden corpus, so the risk is retired in order of consequence: parse and prove first, generate second, govern third.

[TABLE]
| Increment | Ends when | Features |
| I1  Graph and adapter | The golden corpus is harvested into the graph with parse quality ≥ 0.98; the Tableau adapter passes discovery, parse and execution conformance | F1.1, F1.2, F2.1–F2.4, F12.1 (workflow skeleton), F12.3 (basic) |
| I2  Proof | A hand-written DAX measure is proved against the source for 50 corpus workbooks under a charter; verdicts and evidence bundles are stored | F7.1–F7.4, F11.2, F11.3, F1.3 |
| I3  Generate | C1/C2 rules and C3 generation produce artefacts for the corpus; first-pass parity ≥ 0.70 on the corpus; PBIR reports deploy to a dev workspace | F5.1–F5.4, F6.1, F12.2, F11.4, F4.3 (emission only) |
| I4  Mend and learn | Mender closes ≥ 60% of corpus failures within the bound; patterns promote and retire; Exception Desk usable | F8.1–F8.3, F5.5, F6.2, F10.1, F10.3 |
| I5  Govern | Cartographer, Foundry and all four gates work in the console with client roles; G2 and G3 cards; decision register; evidence export | F3.1, F3.2, F4.1, F4.2, F9.1, F10.2, F10.4, F11.1, F13.3 |
| I6  Release and calibrate | Steward promotes and decommissions on the test tenant; Calibration Wave runs end to end; accuracy and absorption metrics live; NFRs pass | F9.2, F9.3, F7.5–F7.7, F13.1, F13.2, F13.4, F12.4, F10.5, N1–N12 |
[/TABLE]


## 6.2 R1 definition of done
- Every P0 story in E1–E13 accepted; every P1 story accepted or explicitly deferred to R1.1 with the Programme Manager's agreement recorded.
- Golden corpus: 100% parse, first-pass parity ≥ 0.75, Mender close rate ≥ 0.70, zero regression escapes across the last 20 CI runs.
- Tableau adapter conformance report signed; both gateway providers passing the transpile_c3 eval set.
- Boundary test and penetration test passed; evidence chain verifies; export verifies offline.
- Calibration Wave executed on the test tenant end to end with all four gates exercised by client-role test users.
- Runbooks and the deployment bill of materials delivered.

## 6.3 After R1

[TABLE]
| Release | Theme | Items |
| R1.1 | Hardening from the first programme | Grammar and rule gaps found in the Calibration Wave; MCP exposure (S12.4.3); Mender TMDL edits under L2; per-user notification digests; Status Pack narrative improvements |
| R2 | Second source, second target | Cognos adapter (conformance-driven); Looker target adapter; visual parity scored on rendered images with a gate option; DirectQuery-heavy model strategies; multi-programme tenancy |
| R3 | Continuous estate governance | Post-migration mode: the graph as the living catalogue of the Fabric estate; drift detection between model and report; optimisation agent for model performance; Qlik and MicroStrategy adapters |
[/TABLE]


# 7. Dependencies, risks and open questions

## 7.1 External dependencies

[TABLE]
| Dependency | Needed by | Risk if late | Mitigation |
| Tableau Metadata API enabled on the client site | I1 | Discovery falls back to REST only: lineage and usage incomplete | Adapter supports REST-only mode with reduced parse quality; flag in the Estate surface |
| Fabric workspace with XMLA read/write and Git integration | I2 | No target execution; proof cannot run | Provision dev/test workspaces in the platform's own tenant for build; client tenant for the programme |
| Model provider agreement (Anthropic egress or Azure OpenAI) | I3 | C3 generation blocked; C1/C2 unaffected | Both providers built; eval sets decide routing |
| Client Entra groups for roles | I5 | Client gates cannot be exercised | Test tenant with synthetic users for I5; swap at deployment |
| Tableau REST image and view-data endpoints on the client version | I2 | Visual capture and view-data strategy unavailable | Extract-read and live-replay strategies; adapter capabilities record what is missing |
[/TABLE]


## 7.2 Delivery risks

[TABLE]
| Risk | Signal | Response |
| Grammar coverage below 98% on the real estate | Parse Quality Queue large after first harvest | Grammar sprint funded from the R1.1 buffer; ignorable-construct workflow keeps MUs moving |
| First-pass parity below 0.70 after I3 | Corpus proof rate | Widen rule set before relying on C3; raise the pattern promotion threshold; extend I4 |
| Provider eval sets fail for Azure OpenAI | Eval results | Anthropic route for C3 with the boundary signed; revisit at R1.1 |
| Fabric API changes (Git integration, deployment pipelines, PBIR schema) | Adapter conformance failures in CI | Target adapter versioned against Fabric API versions; weekly conformance run |
| Console scope grows beyond the seven surfaces | Stories added to E10 without an engine feature behind them | Rule: no screen without an engine feature; PM approves any E10 addition |
[/TABLE]


## 7.3 Open questions
- Extract read via Hyper API requires the extract file: confirm the client will permit download of .twbx with embedded extracts, or whether view-data is the primary strategy.
- Should the Mender be allowed to edit TMDL under L2 in R1, or is Foundry-only routing acceptable for the BlackRock programme? Current answer: Foundry-only in R1.
- External anchoring of evidence roots: is a client-side timestamping service available, or is the internal chain sufficient for the audit position?
- Human touch time measured from console sessions: confirm with the client that programme-level (not per-user) measurement is acceptable under their works-council or HR policies.
- Pattern contribution from client engagements to the golden corpus: legal review of the anonymised-structure clause.

# Assumptions and limits
- Story sizes are not estimated here; estimation happens in increment planning with the team that will build it.
- Acceptance criteria are written to be testable; where a threshold appears (0.98, 0.70, 5 working days) it is the R1 default and is configurable unless the criterion says otherwise.
- The BlackRock programme is the reference deployment for R1 sizing; the NFR targets are set so a 1,500-workbook estate fits with headroom.
- Everything in this backlog traces to the Product Specification v1.0; a story with no spec section behind it should be challenged.
