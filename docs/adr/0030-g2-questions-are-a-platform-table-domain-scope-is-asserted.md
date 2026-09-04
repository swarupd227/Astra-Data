# ADR 0030 — G2 questions are a platform table; domain scope is asserted, not resolved

Status: accepted · 4 September 2026 · Story S4.2.1 (E4 / F4.2)

## Context

S4.2.1 asks for the data owner's side of G2: *"Model Proposal (client view) renders: what
the model is, what reports use it, what changes for the business user, open questions with
owner and status, approve / request changes / ask a question. Approval requires the data
owner's role and domain scope; the Semantic Model Engineer countersigns; both are recorded
on the GateDecision. Request-changes returns the design to DRAFT with the comment attached;
the cycle count is stored. A question creates a thread visible to both sides; the design
cannot be approved with an unanswered question."*

Four questions decided the shape of the work: where a question-and-thread lives when §4.1's
ontology has no node for one; what "domain scope" can mean when nothing has ever assigned a
family a domain; how §13.3's nested `approver`/`countersign` objects fit an ontology that is
flat everywhere else; and what a client-facing read should actually look like next to the
Artizent-internal one S4.1.2 already built.

## Decisions

### 1. A question is a platform table (`public.g2_question`), the same footing `grammar_issue` already established

§4.1's ontology has no node for "a question and its thread" — clustering, design proposals
and gate decisions are all there, but a G2 conversation is not a fact about the source or
target estate, it is a fact about the *review*. `grammar_issue` (S1.4.3) already settled
this exact class of question — "raised as work, tracked with state, evidence copied in at
the moment it was raised" — for a construct the grammar cannot read; a G2 question is the
identical shape for a different kind of work. The thread itself is one `jsonb` column, not
a normalised child table: nothing this story needs ever queries one message on its own, the
same "no sub-query need" reasoning `SemanticModel.design_document` already relies on.

**Questions are seeded from the frozen design, not invented by this module.** The moment
`model_lifecycle.submit_for_review` freezes a version, the route calls `g2.seed_questions`
with that same read, promoting whatever `design_document["open_questions"]` the Modeller (or
a Semantic Model Engineer's own edits) raised into tracked, answerable rows. The two modules
stay independent — `g2.py` imports `model_lifecycle.require_transition`, never the reverse —
so the seeding call lives in the route, the same place `routes_provenance.py`'s
`confirm_family_count` already orchestrates more than one module's write.

### 2. Domain scope is an asserted header; an unset family domain is not refused

§18.1 is explicit that identity and role facts in this codebase are asserted via headers
until E11 resolves them from Entra ID for real — `X-Astra-Roles` has worked this way since
S1.1.1. `X-Astra-Domain-Scope` follows the identical shape: a comma-separated list the
caller states, checked against `ModelFamily.domain`. But `domain` has been declared since
S1.1.1 and, until this story, **nothing has ever written it** — no Cartographer clustering
step assigns one, and no prior story needed it. `model_lifecycle.update_domain` (added by
this story, DRAFT-only, the same shape as `update_grain_statement`) is the first thing that
ever can. Given that, refusing every approval outright until every family has a domain would
make the criterion impossible to demonstrate in practice; `g2.check_domain_scope` treats an
*unset* domain as open to any data owner (still gated on the `client_data_owner` role) and
enforces the scope only once a domain has actually been assigned — a disclosed gap (nobody
assigns one automatically yet), not a silent workaround.

### 3. `GateDecision`'s nested spec shape is flattened, the way every other node in this ontology already is

§13.3's own worked example nests `approver: {user, role, identity}` and
`countersign: {user, role}`. No node anywhere in this ontology carries a nested object —
`ReleaseTrain.gate_schedule` and `SemanticModel.design_document` are the closest precedent,
and both are JSON blobs for genuinely compound, not-yet-first-class *data*, not a scalar
pair like "who approved and in what role." `approver_role`, `countersigner`,
`countersigner_role` and `version_hash` are added as their own flat properties instead —
`GateDecision`'s first write since the node type was declared in S1.1.1. `evidence_ref` is
set to the `SemanticModel` id `version_hash` was frozen on: the record already carries which
artefact was approved without inventing a second reference field for it.

### 4. The client view is a different document, not the internal one with a role check

`g2.client_proposal_view` is deliberately **not** `modeller.read_design_document` reused
with client-facing filtering — it is its own assembly: plain family facts (name, domain,
state), a deterministic plain-language summary (`plain_language_summary` — table/measure
counts, whether RLS applies, the refresh cadence; facts the design already states, not an
ASSISTED judgement call, so a template is the honest and reproducible choice, the same
reasoning `modeller.draft_grain_statement` already gives its own sentence), the family's
member workbook names as "what reports use it," and the open questions with owner and
status. §15.2's own words are "client surfaces are calm... platform detail is Artizent-only
by default" — a shared document filtered by role would still carry Model Detail's
implementation vocabulary (`source_table_refs`, `mode_reason`, node ids); a separately
assembled one only ever carries what §15.2 asks a client to see. Both this route and
Model Detail's own reads require an identified caller (`PrincipalDep`); unlike
`GET /v1/families/{id}/design` (Artizent-internal, `ArtizentDep`-gated), the client view and
`GET /v1/families/{id}/questions` carry no role restriction at all — a data owner is exactly
who this data is for, and gating it to Artizent roles would make G2 impossible to serve.

### 5. Answering is a deliberate act, not inferred from a reply

A reply in a question's thread does not close it — replying is often a clarifying question
back, not a resolution. `answer_question` is a separate action (§15.2's "every action is a
record": marking a question answered is its own decision, worth its own timestamp and
actor), open to either side rather than restricted to the original asker, since in practice
either party may be satisfied first.

## Consequences

- A new platform table, `public.g2_question` (migration v0016), mirroring `grammar_issue`'s
  own shape (state, evidence snapshot, now a thread) rather than a normalised message table.
- Four new `GateDecision` properties and one new `ModelFamily` property
  (`g2_cycle_count`), all additive — schema version 13 → 14, no migration entry required.
- `ModelFamily.domain` moves from "declared, never written" to "written by this story's own
  `update_domain`" — the same trajectory `ModelTable`/`SemanticModel` took at S4.1.1 and
  `ReleaseTrain`/`Wave` took at S3.2.1.
- No approval or request-changes route requires the family to have a domain at all — a
  Semantic Model Engineer who never calls `update_domain` gets a design any data owner with
  the role may act on. This is the honest floor until a real domain-assignment mechanism
  exists (whose owner is not yet named in the backlog) — flagged here rather than silently
  assumed complete.

## Alternatives considered

**A `Question`/`Thread` ontology node.** Rejected — see decision 1. Nothing in §4.1
resembles a review conversation, and a graph node for it would answer a modelling question
nobody has asked yet, the same reasoning that kept `grammar_issue` a platform table rather
than an `ExceptionCase`-shaped graph node.

**Refuse every approval until a family has an assigned domain.** Rejected — see decision 2.
It would make the criterion untestable against real data (nothing assigns a domain
automatically), and "the role, checked; the domain, checked only once one exists" is the
honest reading of what this platform can actually enforce today.

**Reuse `modeller.read_design_document` for the client view, filtered by role.** Rejected —
see decision 4. A filtered internal document still carries the internal document's own
vocabulary and shape; §15.2 asks for a genuinely different, calmer surface, not a smaller
version of the dense one.

**Let a reply automatically mark a question answered.** Rejected — see decision 5. A
question is not resolved just because someone said something back; resolving it is its own
recorded decision.
