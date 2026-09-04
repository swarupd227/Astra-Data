# ADR 0010 — The console, and an Estate Explorer that says what it does not know

Status: accepted · 3 September 2026 · Story S1.4.1 (E1 / F1.4)

## Context

S1.4.1 is the first screen: the Estate Explorer from §15.3.2, with a site → project tree
carrying counts and parse status, a workbook table with faceted filters, a selected
workbook with a lineage mini-graph, four actions, and a 1,067-workbook site loading in
under two seconds.

It is also the first user interface in the repository, so it decides how the console is
built. §5.2 fixes the technology — `console-web | SPA | Migration Console (React/TypeScript)
| static` — and the standing constraint is open source, containerised, Azure-hostable.

## Decisions

### 1. The screen shows what the estate knows, and names what it does not

§15.3.2's centre pane lists tier, score, usage, family, train, state and C4 count. Five of
those do not exist. Tier, score, family, train and state are Migration Unit properties
(§3.1, §3.2) and the Cartographer creates the MU in E3; the class mix needs the Transpiler
(E5). Nothing has run.

So the table shows the columns the estate can answer — parse status, usage, calculation
count, owner, and a tier where somebody has declared one — and the filter pane carries a
short block naming each missing facet with the epic that fills it. A dropdown with no
options is worse than an explained absence: a user opens it, finds nothing, and tries
again next week.

Class mix is the same answer for a different reason. `CalculatedField.class` is absent at
harvest, so what is available is *how many* calculated fields a workbook has — real, and
cheap — rather than how they are classified, which does not exist. The cell says
"3 · unclassified" rather than three zeroes.

### 2. One request feeds three panes

`GET /v1/estate` returns the tree, the filtered page and the facet counts together. The
screen needs all three at once and all three derive from one read; three endpoints would
triple the work to draw one screen, and the facet counts could disagree with the rows
beside them.

The read is four queries, none of them per workbook: every Workbook node from the label
table, the CONTAINS edges that place them plus their Projects and Sites, the OWNED_BY edges
that name their owners, and a relational count of each workbook's calculated fields.
Filtering, banding and facet counting then happen in one pass in memory. A thousand
workbooks is a small object and a large number of round trips, so the trade is not close.

**Measured: 308 ms median over a 1,067-workbook estate, against a 2,000 ms budget.**

A facet's count is computed against the set filtered by *everything except that facet*, so
the number beside an option answers "how many would I get" rather than echoing the current
selection back.

### 3. Scope decisions are records, and they exist before the MU does

Re-tier and withdraw change what the programme has committed to deliver. Both are §15.2
actions — "every action is a record, with a reason field that is required, not optional" —
and both are properties of a Migration Unit that E3 has not built.

Rather than wait, the decision is its own row: what was decided, about what, by whom, why
and when, with the reason enforced at the API and a ten-character floor so "n/a" is not a
reason. The MU inherits these when the Cartographer creates it. A programme manager looking
at a freshly harvested estate already has judgements to record, and losing them until E3
ships would mean asking for them twice.

A tier is not a property on Workbook, and this is why: §4.1.1 declares none, because a tier
is a judgement the programme made rather than a fact about the client's estate. Writing one
onto the node would put a decision inside the record of what was found. The current state
is a fold over the decisions rather than a stored status, so nothing can sit beside them
and disagree.

Withdrawal takes a workbook out of scope without retiring or deleting it — the harvest
found it, and the estate should keep saying so — and it can be reinstated, because a
withdrawal that cannot be undone is a deletion with extra steps.

### 4. The role gate is at the API

S1.4.1 puts re-tier and withdraw behind the Programme Manager, so `ProgrammeManagerDep`
checks the role on the endpoint. The console disables the buttons and says why, but a
console that only hides a button is not a permission model.

"Open MU" is rendered *disabled with the reason the API returns*, not hidden. A programme
manager reading the specification will look for it; an absent button teaches them nothing,
and a working one would be a lie.

### 5. The console is a plain React SPA, and the API is the public one

React + TypeScript + Vite, no UI framework, no state library, no charting library. 168 KB
of JavaScript, 54 KB gzipped. The lineage mini-graph is hand-drawn SVG rather than a graph
library, and it is **layered rather than force-directed**: §15.3.2 asks for force-directed
on the full-screen *Lineage View*, but in a 360-pixel pane a force simulation draws a
different picture every render, so the same workbook would look different each time you
selected it. Layout by distance from the anchor is a pure function of the data.

Filters live in the URL. A programme manager who has narrowed a thousand workbooks to the
eleven that are held and unowned will want to send that view to somebody, and a screen
whose state is only in memory cannot be shared, bookmarked or reloaded.

The console calls the same public API an integration would (§12.4.1: "the console has no
privileged path"). nginx serves the bundle and proxies `/v1` to graph-svc so the browser
makes same-origin calls — which means no CORS policy exists to be widened later.

Identity is a role selector with "not signed in" beside it, because Entra ID is E11 and the
service reads headers until then. A fake login screen would look like a security control to
everybody who saw a screenshot.

## Consequences

- A second service, `services/console-web`, with its own CI job, Dockerfile and compose
  entry. `make console-ci` type-checks, lints and tests it; `make ci` now includes it.
- Migration 8: `scope_decision`. No ontology change — a decision is a platform record.
- The nginx image runs unprivileged and sets a CSP that permits no external origin, which
  is what stops the first person who wants a widget from quietly adding a CDN.

## What using the screen found

Every defect below came from running it, not from reading it:

1. **The calculation count was zero for every workbook in every estate.** §4.1.2 gives
   CONTAINS no Workbook→CalculatedField pair — a workbook reaches its calculations through
   `Worksheet -ENCODES->` — so the one-hop count could never match anything. A column of
   noughts looks like an empty estate rather than a broken query.
2. **Withdrawing a workbook emptied the panel.** The row left the default filter, the
   stale-selection guard cleared the selection, and the user lost both the decision they
   had just made and the Reinstate button that undoes it. Clearing is now tied to the
   filters changing, not to the data reloading.
3. **The tree and the table disagreed.** With "show withdrawn" on, the tree counted 64 and
   the list showed 65 — visible on screen, and exactly the disagreement §15.2 exists to
   prevent. The tree now follows that one toggle, and only that one: it ignores the site
   and project selection, because the tree is how you change that selection.
4. **The layout crushed the pane that matters.** At 900 pixels the three-pane grid left the
   workbook table 270 pixels between two fixed panes. It now reflows to two columns below
   1240 and one below 860.

## Open questions for the product owner

1. **A declared tier is not an assessed tier.** The first decision on a workbook records
   `from: null` — a programme manager declaring a tier before anything has assessed one.
   When E3's assessment lands, it needs a rule for what happens where a human has already
   declared one: does assessment overwrite, propose, or stay silent? The record keeps both
   either way, but the screen has to show one.
2. **The centre pane pages at 100 rows.** A programme manager scanning 1,067 workbooks will
   want to scroll, not page. Virtualised scrolling is the right answer and it is a day's
   work; whether it is worth doing before the Lineage View and the Parse Quality Queue
   (the rest of F1.4) is a sequencing call.
3. **Nothing is live.** §15.6 sets "board and queue updates ≤ 2 s from event", which needs
   the event stream pushed to the browser. The Explorer has a Refresh button. Streaming is
   E12's, and until then a harvest running in another tab is invisible here.
4. **The console has no tests against a real browser.** The suite is jsdom, which cannot
   see a layout that reflows wrongly or a focus ring that does not render — both of which
   were found by looking. A Playwright suite in CI is the obvious next step and it is the
   one thing in this story that was checked by hand rather than by a test.
