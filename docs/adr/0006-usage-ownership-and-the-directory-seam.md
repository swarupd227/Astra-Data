# ADR 0006 — Usage is recorded per viewer, and the directory is a seam with nothing behind it

Status: accepted · 2 September 2026 · Story S1.2.3 (E1 / F1.2)

## Context

S1.2.3 is the programme manager's story: order the waves by business impact, and send gate
requests to owners who exist. It asks for three things — views and distinct viewers over
the trailing 90 days per workbook *and per view*, owners linked to a `User` node resolved
against Entra ID "where possible" with the unresolved ones listed for assignment, and the
licence tier of the site and of each user "where the Metadata API exposes them".

S1.2.1 already carried a workbook-level usage aggregate. It also wrote one `VIEWED_BY`
edge per workbook and hung the aggregate on the **owner**, because the owner was the only
person the harvest knew about. Specification §4.1.2 gives `VIEWED_BY` a `views_90d` per
(workbook, user) pair, so that edge asserted something false: that the owner had viewed
the workbook 400 times. Nothing read it yet, which is why it survived S1.2.1.

## Decisions

### 1. Per-viewer usage is a separate, optional adapter call

`SourceAdapter` gains `viewers()` alongside `usage()`, returning `ViewerRecord` per
(asset, person). They are separate because sources price them differently: Tableau reports
aggregates from the Metadata API cheaply and per-viewer detail from the `historical_events`
admin views expensively, and §6.2 marks the latter "where available". A deployment that
cannot read them still gets the aggregate on the workbook; one that can gets `VIEWED_BY`
edges written from what the source reported rather than inferred from who owns the asset.

The S1.2.1 owner-aggregate edge is gone. An edge that states a falsehood is worse than no
edge, and the wave ordering that S1.2.3 exists to support would have read it.

### 2. `UsageRecord` carries a kind, and per-view usage lands on the view node

One record type covers both grains, tagged `WORKBOOK` or `VIEW`, because the shape is
identical and a source that reports one usually reports the other in the same response.
Per-view records are matched to `Worksheet` and `Dashboard` nodes by the name the parse
gave them, which is the name the source published them under.

The ontology gains `views_90d`, `distinct_viewers_90d` and `last_view` on both types as
declared deviations (schema version 5). §4.1.1 puts these properties on `Workbook` only.
The story asks for them "per workbook **and per view**", and there is no other node that a
view's usage belongs to; `SpecDeviation` entries record that, so the divergence is a
statement in the ontology rather than an accident in the harvester.

### 3. The directory is a seam, and today nothing is behind it

`DirectoryResolver` has one production implementation: `NullDirectoryResolver`, which
resolves nobody. Entra ID resolution needs a credential, a workload identity and a graph
permission, all of which belong to E11, and inventing a resolver now would mean inventing
its failure modes too.

The consequence is visible rather than hidden. `GET /v1/ownership/unresolved` returns
every owner, and reports `resolver: "null"` in the response — so the listing says why it
is long, and a deployment that *has* a resolver reports that instead. This is the honest
state of a platform that cannot reach a directory, and it is the state the migration is
in on day one of a real engagement anyway.

### 4. A `User` node is keyed on the source identity, never on the directory id

`derive_id(site, f"user:{upn}")`. Resolving a person **adds** `directory_id` and
`directory_resolved_at` to their node; it does not move them. If the id were derived from
the directory, resolving somebody would create a second node and orphan every `OWNED_BY`
and `VIEWED_BY` edge already pointing at the first — and unresolvable people would have no
stable identity at all, which is precisely the population the listing is for.

Assignment by hand (`POST /v1/ownership/assign`) goes through the ordinary write path, so
it emits an event like any other change and the principal who made the judgement is on the
record. It refuses to overwrite an existing link: re-assigning is a different operation
from assigning, and conflating them would let one request silently discard a resolution.

### 5. The listing is ordered by how many workbooks each unresolved owner holds

`owns` is the count of incoming `OWNED_BY` edges. That is what makes one unresolved owner
more urgent than another: it is the number of G3 gate requests (§13.1) that currently have
nobody to go to. Two reads back it — every `User` node, and the incoming counts for those
without a directory link — both served from the relational adjacency index for the reasons
in ADR 0002. A whole-label read is reasonable for `User`, which numbers in the hundreds on
a site; the repository method is bounded and no caller asks it for `Field`.

### 6. Site facts are folded into the workbook write, not written separately

The first implementation wrote `Site` nodes once, before the workbooks, carrying the
licence tier. Every workbook's parse also produces a `Site` fragment, and an upsert
replaces properties — so each workbook overwrote the tier back to nothing. Site facts now
travel with the workbook write, which means there is one place a `Site` node is written
and no ordering to get wrong.

## Consequences

- `VIEWED_BY` is now written per person, so a busy workbook has as many edges as it had
  distinct viewers. Bounded by the source's own reporting, which is per-user by
  construction; the fixture estate makes this concrete at 40 workbooks.
- `set_node_properties` had to stop stripping `side` for types that declare both sides.
  `User` is the only such type today, and assignment failed validation without it.
- Schema version 5. Nine declared deviations, up from six.

## Open questions for the product owner

1. **Per-user licence tier arrives only with an ownership record.** A viewer who owns
   nothing has no tier, because the adapter reports tiers alongside ownership and nowhere
   else. Tableau's Users endpoint lists tiers for everyone; whether to add a `users()` call
   to the adapter contract for that is an E2 decision, and it is the difference between
   "we know the tier of the 40 owners" and "we know the tier of all 900 people" for the
   licence-savings case in §14.
2. **90 days is fixed.** `window_days` is a parameter on the adapter calls but nothing
   varies it. Some clients will want the trailing 12 months for seasonal reporting, where a
   quarterly workbook looks dead for two months out of three. That is a per-tenant setting
   and §22 has no store for one yet.
3. **A workbook with an unresolved owner is not held.** Parse quality holds a workbook;
   an unplaceable owner does not, so a wave can be planned around assets nobody can be
   asked to sign off. Whether that should become a gate condition at G3 is a process
   decision, not a technical one.
4. **Re-assignment is refused, not versioned.** Assigning over an existing link returns an
   error. If owners are expected to change hands during a long engagement, this should
   become an explicit re-assign with the prior link retained in the event history rather
   than an operation the API declines.
