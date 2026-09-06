# console-web — the Migration Console

The console from specification §15. React and TypeScript, built by Vite, served by nginx as
static assets (§5.2: `console-web | SPA | static`).

Three screens so far: the **Estate Explorer** (§15.3.2, story S1.4.1), the **Lineage View**
(S1.4.2) and the **Parse Quality Queue** (S1.4.3). Usage & Ownership is the rest of F1.4 and
hangs off the same shell. Each screen has its own path — `/`, `/lineage`, `/quality` — so any
of them can be linked to.

A fourth surface, `/programme`, carries five panes from §15.3.1's **Programme Board**: planned
family count (150) against measured, with the delta, and the Programme Manager's "Confirm
family count" action (S3.1.3); since S3.2.3, a "Projected vs. planned" table — every
train's planned end against a throughput-based projection with a confidence band, a train
missing its date by more than 5 working days highlighted as flagged; since S4.2.2, "G2
reviews" — every family awaiting G2, days waiting, its approver and open-question count, a
wait past the same 5-working-day default highlighted the same way, and a "Send reminders"
action for the 3-/5-day reminders that story asks for (real and idempotent, not real
delivery — see graph-svc's own README); since S5.1.1, "Calculation classes" — every
calculated field's C1-C4 class against the calibration targets 45/30/18/7 (§9.1), how many
of the estate's fields are classified at all, and a "Re-classify" action (the parity
engineer's) reporting how many fields moved class; and since S5.2.1, "Rule coverage" — how
many of the estate's calculated fields the shipped deterministic rules engine has converted
into real DAX, by rule family, and an "Apply rules" action (the platform engineer's)
reporting how many fields were newly converted. None of this is the whole board — the KPI
strip, train swimlanes and milestones are story S10.2.1's; all five panes are built to be
absorbed into that screen rather than replaced by it.

A fifth surface, `/trains`, is the **Wave Board** (S3.2.2): trains as columns, MU cards
grouped by their §3.2 state within each, native HTML5 drag-and-drop to re-sequence within a
train or move between trains, a WIP-limit dialog per train, and a per-train recent-activity
feed. Since S3.2.3, each train's column header also carries its projected-finish badge (or
"no projection" when throughput cannot yet support one) — the same figure as the Programme
Board's table, placed where a Programme Manager is already looking while re-planning. Like
the Programme Board, it is the mechanics S3.2.2 asks for, not the wider frame
(family-dependency lines, milestones) S10.2.1 will eventually give it.

A sixth surface, `/models`, is **Model Detail** (S4.1.2): a family list (the minimal
navigation this screen needs — no Foundry Workbench exists yet to hand it a selection) next
to a six-tab panel — Design (tables, a deterministic box-and-line relationships diagram,
grain statement, conformed dimensions), Measures (candidate measures with class/pattern
shown as "pending" until the Transpiler exists), RLS, Open Questions, Build, and Versions.
Since S4.3.1, Build renders the family's most recent real build attempt — result, Git commit,
workspace, and every step's own pass/fail with its detail — reading `GET /v1/families/{id}/
build`; a design builds automatically the moment it is approved at G2, and "Build now"/
"Rebuild" here is the manual retry a failed attempt's own logged reason calls for. Since
S4.3.2, a `"conformance"` step among that log lists every rule violation with its
offending object when a build is refused before it ever reaches Git, and the family's own
header names which ruleset version it was last checked against. Since S4.3.3, **Versions**
lists every version this family has ever had — number, state, generated/published/deprecated
dates, newest first (`GET /v1/families/{id}/versions`) — not a diff view, which is later,
unclaimed scope; while PUBLISHED it offers "Request new version" (a reason of at least 10
characters, opening v(n+1) as DRAFT without touching the live v(n)), and while BUILT it
offers "Promote to PUBLISHED" (deploying for real before marking the version PUBLISHED and
its predecessor DEPRECATED with the date). A Semantic Model Engineer generates a proposal,
accepts the family into DRAFT, edits the grain statement / a table's storage mode / a
relationship's cardinality inline, submits for G2, and drives both version actions — each
gated to the role and, for editing, to the family being DRAFT.

An eighth surface, `/admin`, is **Admin** (S4.3.2) — one screen, the conformance ruleset:
six rules from §12.3, each an enable checkbox and whatever parameters it has, a version
number and who/when it was last saved. §15.3.7 names five Admin screens and none of them
is this one, and no other backlog story claims a rules-editing screen either, so this is a
sixth this story adds on its own. Editing is the Migration Architect's — the Save button
(and every rule's own checkbox/param field) is hidden, not disabled, for anyone else, the
same convention every other role-gated action in this console already follows.

A seventh surface, `/proposal`, is **Model Proposal** (S4.2.1) — the client data owner's
own screen, deliberately calmer than Model Detail: a "for review" family list (`GET
/v1/families:for-review`, not the internal, Artizent-only family list) next to what the
model is in plain language, what reports use it, the open questions with owner and status
(reply and mark-answered inline; asking a new one is a text field), and — while a design is
`IN_REVIEW` — approve, request changes, or nothing else. A small "your domain(s)" field
above the family list asserts `X-Astra-Domain-Scope` (the same "real until E11 maps it for
real" posture every other identity fact in this console already has); approving is disabled
while any question is still open. Every decision action is hidden, not merely disabled, for
anyone without the client data owner role.

A ninth surface, `/patterns`, is the **Pattern Library** (S5.5.1–S5.5.3) — a queue-plus-list
screen for what the platform has learned. The left pane holds "Candidates awaiting
promotion", sorted by how close each is to earning its own promotion (`distinct_passing_calcs`
descending, then fewest `applications`), above "All patterns, by class and state" (`GET
/v1/patterns`); selecting a row opens a detail panel on the right with every fact the AC
asks for — class, state, applications, pass/fail, first seen, provenance origin — plus,
since S5.5.3, its version and (when edited) what it superseded. Promote, retire with a
reason, and edit guards are the Platform Engineer's alone (§13.2's MA-11/MA-12) — hidden,
not disabled, for anyone else, the same convention Admin and Model Proposal already use;
retiring reuses the shared `ReasonDialog`, editing guards opens a purpose-built
`EditGuardsDialog` (guards plus a reason, modelled on the same dialog). Editing guards
writes a brand-new `Pattern` version rather than mutating the one shown — the row for the
version being edited disappears from the live list the instant its replacement lands, and
the new version starts its own observation ledger from zero, since guards are descriptive
text that changes nothing about matching or rendering, but a version's own proof history
should never be inherited from a form its predecessor may not have re-earned. Export is a
client-side JSON download of exactly what the screen has already loaded — no server route
exists for it, since the screen already holds everything an export needs.

## Running it

```bash
make dev-up && make migrate && make harvest
```

then, in two terminals:

```bash
cd services/graph-svc && python -m uvicorn astra_graph.main:app --port 8080
```

```bash
make console-dev
```

The console is at http://localhost:5173. Vite proxies `/v1` to graph-svc, so the browser
makes same-origin calls in development exactly as it does behind nginx in a deployment —
there is no CORS policy anywhere, which is the point.

```bash
make console-ci
```

type-checks, lints and runs the tests. It is part of `make ci`.

## The Estate Explorer

Three panes, from one request:

| Pane | Shows |
|---|---|
| Left | Site → project tree with workbook counts, held and unparsed flags, then the facets |
| Centre | The filtered workbook table: parse status, 90-day views, calculations, tier, owner |
| Right | The selected workbook, its lineage mini-graph, its scope history and the actions |

`GET /v1/estate` returns all three together. The screen needs them at once and they all
derive from one read of the estate; three endpoints would triple the work to draw one
screen, and the facet counts could disagree with the rows beside them.

### What it does not show, and why it says so

§15.3.2 lists tier, score, family, train, state and class mix. Five of those are Migration
Unit properties and the Cartographer creates the MU in E3; the class mix needs the
Transpiler (E5). Rather than a table of dashes, the filter pane names each missing facet
with the epic that fills it, and the calculations cell reads `3 · unclassified` rather than
implying a classification nothing has made.

"Open MU" is rendered **disabled with the reason**, not hidden. Somebody reading the
specification will look for it; an absent button teaches them nothing and a working one
would be a lie.

### Actions

| Action | Who | Notes |
|---|---|---|
| Open MU | — | Disabled. No Migration Unit exists until E3 creates one. |
| Re-harvest site | Any Artizent role | Starts a harvest of the selected workbook's site. |
| Re-tier | Programme Manager | Records a tier with a reason. |
| Withdraw / Reinstate | Programme Manager | Takes a workbook out of scope, or puts it back. |

Every scope action requires a reason of at least ten characters (§15.2: "a reason field
that is required, not optional"). The console asks for it, and the API enforces it — a
console that only hides a button is not a permission model, so the role is checked on the
endpoint too.

### Filters live in the URL

A programme manager who has narrowed a thousand workbooks to the eleven that are held and
unowned will want to send that view to somebody. `?parse_quality_band=poor&unowned_only=true`
is the whole state, so the view is shareable, bookmarkable and survives a reload.

### The lineage mini-graph

Hand-drawn SVG, laid out by distance from the workbook rather than by a force simulation.
§15.3.2 asks for force-directed on the full-screen *Lineage View*; in a 360-pixel pane a
simulation draws a different picture every render, so the same workbook would look
different each time you selected it. The layout here is a pure function of the data.

## The Lineage View

A force-directed graph of workbooks, the tables and fields behind them, and how much any two
workbooks share — so a model engineer can see why the Cartographer grouped a family and
challenge it.

### Deterministic, despite being force-directed

Seeded from the node ids rather than `Math.random`, and run to a fixed iteration count
before rendering rather than animated. The same graph lays out the same way on every
machine, every reload and every export — otherwise "the cluster on the left" is a
meaningless thing to say in a review, and the PNG is not the picture the reviewer saw.

The model is Fruchterman–Reingold, so spacing scales with the node count and the canvas
area. A fixed repulsion constant drew an unreadable knot once the graph got dense, and a
dense graph is a real case: an estate whose workbooks all define the same calculation shape
links every pair.

### What costs a request, and what does not

Only the scope — site, project, family — reads from the server. The node-type filter, the
strength threshold and the colouring are local, so they respond immediately.

The threshold in particular is deliberately **not** an input to the layout. Sweeping it is
the main thing anybody does here, and recomputing the layout each time both cost seconds and
rearranged the picture under the person reading it.

### Colour

Node type, parse status and model family. §15.3.2 also asks for Migration Unit state; the
§3.2 state machine begins when the Cartographer creates an MU (E3), so that mode is listed,
disabled, with the reason — rather than silently replaced.

### Export

**JSON** is the graph: every link with its three §12.1 components, the formula, the weights,
and whether the numbers came from the Cartographer or were computed. A spreadsheet built
from it can be checked rather than trusted.

**PNG** is the same SVG on screen, serialised with computed colours inlined — without the
inlining the export is black-on-black in dark mode, which nobody notices until a client is
sent one.

## The Parse Quality Queue

Where the grammar gaps get worked down before the Calibration Wave. A platform engineer's
screen, and the ordering is the whole point.

### Construct-first, not workbook-first

The centre pane is one row per unrecognised construct, ordered by **Releases** — how many
held workbooks have this as their only remaining gap, so fixing it alone lifts them above
the §4.1.4 threshold. One gap held 23 of the demo estate's 65 workbooks; working the queue
workbook-by-workbook would have opened the same gap 23 times.

Held workbooks are the left pane. "Which of mine is stuck" is a fair question and S1.4.3
asks for it, but it is not the working order.

### Two numbers that are easy to confuse

**Workbooks** is how many contain the construct at all, held or not. **Releases** is how many
held workbooks it would free on its own. A construct in thirty workbooks that releases none
is real work with no immediate payoff, and the panel says so rather than showing a dash: it
still has to be fixed, it is just not the one to start with.

### Three actions, two of which ask why first

- **Mark ignorable** — the grammar still cannot read it; you are deciding the platform may
  proceed anyway. Every workbook holding it is re-scored, and the reason is shown to whoever
  later asks why one was released.
- **Open grammar issue** — raises a record carrying the construct verbatim and every place
  it was found. Disabled once one is open: a second issue is not a second problem. If no work
  tracker is configured the screen says the issue is held here, rather than implying a ticket
  exists somewhere.
- **Request re-harvest** — per site, because a site is the harvester's unit of work, and the
  tooltip says so rather than implying a construct-scoped re-parse that does not exist.

## Design

A dense working surface, not a marketing page:

- one accent colour, used only for selection and focus, so status colour always means status;
- tabular figures throughout — a thousand-row table whose digits do not line up cannot be
  scanned, and scanning is what the screen is for;
- a visible focus ring on everything interactive, and table rows operable from the keyboard
  (§15.6 requires WCAG 2.2 AA and full keyboard operation);
- light and dark both first class;
- the environment is on the top bar in its own colour (§15.6: prod/test/dev visibly distinct).

The layout reflows before it crushes the pane that matters: three columns above 1240px, two
below it, one below 860px. A three-pane grid at 900px leaves the workbook table 270 pixels
between two fixed panes, which is how it started.

## Identity

There is none yet. The service reads `X-Astra-Principal` and `X-Astra-Roles` headers until
E11 brings Entra ID, so the top bar has a role selector with **not signed in** beside it. A
login screen that authenticated nobody would look like a security control to everybody who
saw a screenshot.

## Performance

S1.4.1 budgets two seconds for a 1,067-workbook site. The server's share is measured in
`graph-svc`'s benchmark: **308 ms median** over an estate of that size. The console reports
its own round trip in the status bar — a budget nobody can see is a budget nobody keeps.

## Tests

`vitest` with Testing Library, driving the rendered screen rather than component props:
what the story asks for is what a programme manager can see and do. The API is a fake of
the *contract*, not a mock of `fetch`, so a change to the response shape breaks these tests
as well as the types.

The suite is jsdom, which cannot see a layout that reflows wrongly or a focus ring that does
not render. Both were found by looking at the screen, and a real-browser suite is the
obvious next step — see ADR 0010's open questions.

## Container

Two stages: Node builds and type-checks, nginx serves the bundle. The runtime image has no
Node, no package manager and no source. nginx runs unprivileged, sets a CSP that permits no
external origin, caches hashed assets hard and `index.html` not at all, and proxies `/v1`
and `/graphql` to graph-svc.

nginx's own proxy resolves `graph-svc` per request rather than once at worker start (so a
recreated container's new address is picked up without a restart), which requires naming a
DNS server via a `resolver` directive — there is no single correct one across deployments,
so it is a third envsubst'd template token, `$DNS_RESOLVER`, alongside `$GRAPH_SVC_UPSTREAM`/
`$GRAPH_SVC_HOST`. The image's own default (`168.63.129.16`) is Azure Container Apps' own
internal resolver, matching where this platform is actually deployed (S5.3.2); local Docker
Compose overrides it to Docker's own embedded resolver (`127.0.0.11`) in `docker-compose.yml`.
**Story S5.5.3 found this the hard way**: every `/v1/*` call through a local `console-web`
container had silently 502'd since S5.3.2 introduced the Azure-only default, undetected
because no story before it had ever loaded a console screen through this exact proxy path in
a browser rather than curling `graph-svc`'s own exposed port directly. If a fresh local stack
ever 502s on every API call again, check this resolver before anything else.
