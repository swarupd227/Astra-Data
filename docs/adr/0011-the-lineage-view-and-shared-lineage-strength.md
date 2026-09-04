# ADR 0011 — The Lineage View computes the evidence, and defers to the Cartographer's

Status: accepted · 3 September 2026 · Story S1.4.2 (E1 / F1.4)

## Context

§15.3.2: "Force-directed graph of workbooks ↔ tables ↔ fields for a family or a selection;
edge weight = shared lineage strength; colour = state." S1.4.2 adds a node-type filter,
family highlighting and export to PNG and JSON.

The purpose is the part that decides the design: *so I can see why the Cartographer grouped
a family and challenge it*. Challenging a grouping means seeing the evidence it was made
from, and §12.1 defines that evidence exactly — for every workbook, the source tables it
reaches, the fields it encodes and the multiset of calculated-field AST shapes it defines,
scored as `0.5·J(tables) + 0.3·J(fields) + 0.2·shared_shapes / max_shapes`.

Nothing produces that evidence yet. `SHARES_LINEAGE`, `IN_FAMILY` and `ModelFamily` are all
in the ontology and all written by the Cartographer, which is E3.

## Decisions

### 1. Stored edges win; otherwise the same formula, computed read-only

When `SHARES_LINEAGE` edges exist the view shows **them**, because they are the numbers the
clustering actually used. A recomputed figure could differ, and an engineer challenging a
family needs the evidence that produced it, not a second opinion that happens to be close.

When they do not exist — which is every estate today — the read computes the same formula
from the same three inputs. The response says which, in `shared_lineage_origin`, and the
screen says it in words: "nothing has clustered yet, so strengths are computed by the same
§12.1 formula the Cartographer uses."

The alternative was an empty screen until E3. This is not scope creep into the Cartographer:
no families are proposed, no thresholds are applied, no clustering happens and nothing is
written. It is a read that scores pairs. When E3 lands, its edges take over, and if the two
ever disagree that discrepancy is itself worth seeing.

### 2. All three inputs are indexed, and the third is not optional

Pairs are found through an inverted index rather than by scoring 31,125 pairs of which
almost all share nothing.

The first version indexed tables and fields. That is a bug, not an optimisation: two
workbooks that share every calculation shape and no lineage at all score 0.2, which clears
the default threshold — and the pair was never proposed, so the link vanished with no
error. Found by pointing the screen at the fixture estate, whose workbooks all define the
same ratio, and getting an empty graph back from a call with the threshold at zero.

### 3. Force-directed, but deterministic

ADR 0010 laid the *mini*-graph out by depth precisely because a force simulation in a
360-pixel pane draws a different picture every render. §15.3.2 asks for force-directed here,
on a full screen, and the reconciliation is to run one but make it reproducible:

- seeded from the node ids, not `Math.random`, so the same graph lays out the same way on
  every machine, every reload and every export;
- run to a fixed iteration count and then rendered, not animated, so nothing settles
  differently because a laptop was busy and the PNG is the picture the reviewer saw.

Without that, "the cluster on the left" is a meaningless thing to say in a review.

### 4. Fruchterman–Reingold, because a constant does not scale

The first implementation used a fixed repulsion charge with attraction proportional to link
count. At 240 nodes and a thousand links it drew a knot in the middle of an empty canvas —
attraction grows with the graph and repulsion did not.

The replacement expresses everything relative to `k = √(area / n)`, the natural spacing for
this node count on this canvas: repulsion `k²/d`, attraction `d²/k`, and a cooling
temperature capping movement per step. Edge attraction is divided by each endpoint's degree,
without which a node with two hundred links is dragged two hundred times harder than one
with two and any dense cluster implodes.

A complete graph is a real case here, not a contrived one: an estate whose workbooks all
define the same calculation shape links every pair. Measured after the change, the middle
80% of nodes spans 316×378 px of a 960×640 canvas, against a knot before.

### 5. The threshold changes what is drawn, not where things are

Sweeping the strength threshold is the main thing a model engineer does on this screen. The
first version recomputed the layout on every change: four seconds, and — worse — the picture
rearranged under the person reading it.

The layout is now a function of the node set and *all* the links, and the threshold filters
only what is drawn. Sweeping it is instant and the graph holds still. The same distinction
applies to the node-type filter and the colouring: they are local, and only the scope
(site, project, family) costs a request.

### 6. Colour offers "Migration Unit state" by name and disables it

§15.3.2 asks for `colour = state`. The §3.2 state machine begins when the Cartographer
creates an MU. So the mode is listed, disabled, with the reason — rather than quietly
replaced by something else that happens to be colourful. Node type, parse status and model
family are offered and work.

### 7. Export is the graph and the picture, separately

JSON is the data, with §12.1's formula and weights and every link's three components
alongside — so a spreadsheet built from it can be *checked* rather than trusted. PNG is the
same SVG on screen, serialised with its computed colours inlined; without that inlining the
export comes out black-on-black in dark mode, which is the sort of thing nobody notices
until a client is sent one.

## Consequences

- One endpoint, `GET /v1/lineage`, and one module. No migration and no ontology change:
  the edges it prefers are already declared, and everything else is a read.
- The scope is capped at 250 workbooks. §15.3.2 scopes the view "for a family or a
  selection"; beyond a few hundred a force graph is a hairball whatever the layout, and the
  pairwise scoring is bounded by the same number. An unscoped call is allowed, capped and
  reported as truncated rather than refused — an engineer who has not yet chosen a scope
  should see a starting point, not an error.
- `ast_shape` from S1.3.1 is reused for the calculation shapes, so "these two workbooks
  share a calculation shape" means here exactly what it means to the Pattern Library.

## What using the screen found

1. **Zero shared links at a threshold of zero.** The candidate index covered tables and
   fields but not calculation shapes, so pairs sharing only §12.1's third term were never
   considered. 780 links appeared once it was fixed.
2. **A knot instead of a graph.** Fixed repulsion against link-count-proportional
   attraction; replaced with Fruchterman–Reingold.
3. **Four seconds and a jumping picture on every threshold change.** The layout depended on
   the thresholded link set.

## Open questions for the product owner

1. **Recomputed strengths will meet the Cartographer's.** When E3 writes `SHARES_LINEAGE`,
   the two numbers should agree — same formula, same inputs. If they do not, the view will
   show the stored one and the difference will be invisible. Whether a mismatch should be
   surfaced as a warning is a question about how much the platform should second-guess its
   own agent.
2. **Nothing here writes a family.** §12.1's output is "a proposal, not a decision", and
   §15.3.3's Foundry Workbench is where an engineer splits, merges and moves workbooks with
   an override and a reason. This screen shows the evidence and no controls. Whether
   challenging should be possible *from* this screen, or only from the Workbench, is a
   sequencing decision for E4.
3. **250 workbooks is a judgement.** It is where a force graph stops being readable rather
   than where it stops being computable. A programme with 1,067 workbooks in one family
   would need a different visualisation — aggregation by project, or edge bundling — and
   that is worth knowing before the Calibration Wave rather than after.
4. **The picture is not interactive beyond selection.** No pan, no zoom, no dragging a node
   to untangle a region. Each is small on its own; together they are the difference between
   reading a graph and working with one, and they should be prioritised against the
   remaining F1.4 screens rather than assumed.
